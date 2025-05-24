# app/actions/sharepoint_actions.py
import logging
import requests # Necesario para tipos de excepción y para PUT a uploadUrl de sesión
import json
import csv
from io import StringIO
from typing import Dict, List, Optional, Any, Union
from datetime import datetime, timezone as dt_timezone

# Importar la configuración y el cliente HTTP autenticado
from app.core.config import settings
from app.shared.helpers.http_client import AuthenticatedHttpClient

logger = logging.getLogger(__name__)

# --- Helper para validar si un input parece un Graph Site ID ---
def _is_valid_graph_site_id_format(site_id_string: str) -> bool:
    if not site_id_string:
        return False
    is_composite_id = ',' in site_id_string and site_id_string.count(',') >= 1
    is_server_relative_path_format = ':' in site_id_string and ('/sites/' in site_id_string or '/teams/' in site_id_string)
    is_graph_path_segment_format = site_id_string.startswith('sites/') and '{' in site_id_string and '}' in site_id_string
    is_root_keyword = site_id_string.lower() == "root"
    is_guid_like = len(site_id_string) == 36 and site_id_string.count('-') == 4
    return is_composite_id or is_server_relative_path_format or is_graph_path_segment_format or is_root_keyword or is_guid_like

# --- Helper Interno para Obtener Site ID (versión robusta) ---
def _obtener_site_id_sp(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> str:
    site_input: Optional[str] = params.get("site_id") or params.get("site_identifier")
    sharepoint_default_site_id_from_settings = getattr(settings, 'SHAREPOINT_DEFAULT_SITE_ID', None)

    if site_input:
        if _is_valid_graph_site_id_format(site_input):
            logger.debug(f"SP Site ID con formato Graph reconocido: '{site_input}'.")
            return site_input
        lookup_path = site_input
        if not ':' in site_input and (site_input.startswith("/sites/") or site_input.startswith("/teams/")):
             try:
                 sites_read_scope = getattr(settings, 'GRAPH_SCOPE_SITES_READ_ALL', settings.GRAPH_API_DEFAULT_SCOPE)
                 root_site_info_resp = client.get(f"{settings.GRAPH_API_BASE_URL}/sites/root?$select=siteCollection", scope=sites_read_scope)
                 root_site_hostname = root_site_info_resp.json().get("siteCollection", {}).get("hostname")
                 if root_site_hostname:
                     lookup_path = f"{root_site_hostname}:{site_input}"
                     logger.info(f"SP Path relativo '{site_input}' convertido a: '{lookup_path}'")
             except Exception as e_root_host:
                 logger.warning(f"Error obteniendo hostname para SP path relativo '{site_input}': {e_root_host}.")
        url_lookup = f"{settings.GRAPH_API_BASE_URL}/sites/{lookup_path}?$select=id,displayName,webUrl,siteCollection"
        logger.debug(f"Intentando obtener SP Site ID para '{lookup_path}'")
        try:
            sites_read_scope = getattr(settings, 'GRAPH_SCOPE_SITES_READ_ALL', settings.GRAPH_API_DEFAULT_SCOPE)
            response = client.get(url_lookup, scope=sites_read_scope)
            site_data = response.json(); resolved_site_id = site_data.get("id")
            if resolved_site_id:
                logger.info(f"SP Site ID resuelto para '{site_input}': '{resolved_site_id}' (Nombre: {site_data.get('displayName')})")
                return resolved_site_id
        except Exception as e:
            logger.warning(f"Error buscando SP sitio por '{lookup_path}': {e}. Intentando fallback.")

    if sharepoint_default_site_id_from_settings and _is_valid_graph_site_id_format(sharepoint_default_site_id_from_settings):
        logger.debug(f"Usando SP Site ID por defecto de settings: '{sharepoint_default_site_id_from_settings}'")
        return sharepoint_default_site_id_from_settings

    url_root_site = f"{settings.GRAPH_API_BASE_URL}/sites/root?$select=id,displayName"
    logger.debug(f"Intentando obtener SP sitio raíz como fallback.")
    try:
        sites_read_scope = getattr(settings, 'GRAPH_SCOPE_SITES_READ_ALL', settings.GRAPH_API_DEFAULT_SCOPE)
        response_root = client.get(url_root_site, scope=sites_read_scope)
        root_site_data = response_root.json(); root_site_id = root_site_data.get("id")
        if root_site_id:
            logger.info(f"Usando SP Site ID raíz como fallback: '{root_site_id}' (Nombre: {root_site_data.get('displayName')})")
            return root_site_id
    except Exception as e_root:
        raise ValueError(f"Fallo CRÍTICO al obtener SP Site ID: {e_root}")
    raise ValueError("No se pudo determinar SP Site ID.")

# --- Helper Interno para Obtener Drive ID ---
def _get_drive_id(client: AuthenticatedHttpClient, site_id: str, drive_id_or_name_input: Optional[str] = None) -> str:
    sharepoint_default_drive_name = getattr(settings, 'SHAREPOINT_DEFAULT_DRIVE_ID_OR_NAME', 'Documents')
    target_drive_identifier = drive_id_or_name_input or sharepoint_default_drive_name
    if not target_drive_identifier: raise ValueError("Se requiere nombre o ID de Drive.")

    is_likely_id = '!' in target_drive_identifier or (len(target_drive_identifier) > 30 and not any(c in target_drive_identifier for c in [' ', '/']))
    files_read_scope = getattr(settings, 'GRAPH_SCOPE_FILES_READ_ALL', settings.GRAPH_API_DEFAULT_SCOPE)
    if is_likely_id:
        url_drive_by_id = f"{settings.GRAPH_API_BASE_URL}/sites/{site_id}/drives/{target_drive_identifier}?$select=id,name"
        try:
            response = client.get(url_drive_by_id, scope=files_read_scope)
            drive_data = response.json(); drive_id = drive_data.get("id")
            if drive_id: return drive_id
        except Exception as e: logger.warning(f"Error obteniendo SP Drive por ID '{target_drive_identifier}': {e}. Buscando por nombre.")

    url_list_drives = f"{settings.GRAPH_API_BASE_URL}/sites/{site_id}/drives?$select=id,name,displayName,webUrl"
    try:
        response_drives = client.get(url_list_drives, scope=files_read_scope)
        drives_list = response_drives.json().get("value", [])
        for drive_obj in drives_list:
            if drive_obj.get("name", "").lower() == target_drive_identifier.lower() or \
               drive_obj.get("displayName", "").lower() == target_drive_identifier.lower():
                drive_id = drive_obj.get("id")
                if drive_id: return drive_id
        raise ValueError(f"SP Drive '{target_drive_identifier}' no encontrado en sitio '{site_id}'.")
    except Exception as e: raise ConnectionError(f"Error obteniendo SP Drive ID para '{target_drive_identifier}': {e}") from e

def _get_sp_item_endpoint_by_path(site_id: str, drive_id: str, item_path: str) -> str:
    safe_path = item_path.strip()
    if not safe_path or safe_path == '/': return f"{settings.GRAPH_API_BASE_URL}/sites/{site_id}/drives/{drive_id}/root"
    if safe_path.startswith('/'): safe_path = safe_path[1:]
    return f"{settings.GRAPH_API_BASE_URL}/sites/{site_id}/drives/{drive_id}/root:/{safe_path}"

def _get_sp_item_endpoint_by_id(site_id: str, drive_id: str, item_id: str) -> str:
    return f"{settings.GRAPH_API_BASE_URL}/sites/{site_id}/drives/{drive_id}/items/{item_id}"

def _handle_graph_api_error(e: Exception, action_name: str, params_for_log: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    log_message = f"Error en SharePoint action '{action_name}'"
    safe_params = {}
    if params_for_log:
        sensitive_keys = ['valor', 'content_bytes', 'nuevos_valores_campos', 'datos_campos',
                          'metadata_updates', 'password', 'columnas', 'update_payload',
                          'recipients_payload', 'body', 'payload']
        safe_params = {k: (v if k not in sensitive_keys else "[CONTENIDO OMITIDO]") for k, v in params_for_log.items()}
        log_message += f" con params: {safe_params}"
    logger.error(f"{log_message}: {type(e).__name__} - {str(e)}", exc_info=True)
    details = str(e); status_code = 500; graph_error_code = None
    if isinstance(e, requests.exceptions.HTTPError) and e.response is not None:
        status_code = e.response.status_code
        try:
            error_data = e.response.json(); error_info = error_data.get("error", {})
            details = error_info.get("message", e.response.text); graph_error_code = error_info.get("code")
        except json.JSONDecodeError: details = e.response.text # Corregido
    return {"status": "error", "action": action_name, "message": f"Error ejecutando {action_name}: {type(e).__name__}", "http_status": status_code, "details": details, "graph_error_code": graph_error_code}

def _get_current_timestamp_iso_z() -> str:
    return datetime.now(dt_timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

def _sp_paged_request(
    client: AuthenticatedHttpClient, url_base: str, scope: List[str],
    params_input: Dict[str, Any], query_api_params_initial: Dict[str, Any],
    max_items_total: Optional[int], action_name_for_log: str
) -> Dict[str, Any]:
    all_items: List[Dict[str, Any]] = []; current_url: Optional[str] = url_base; page_count = 0
    max_pages_to_fetch = getattr(settings, 'MAX_PAGING_PAGES', 20)
    top_value_initial = query_api_params_initial.get('$top', getattr(settings, 'DEFAULT_PAGING_SIZE', 50))
    logger.info(f"SP Paged Request para '{action_name_for_log}': Max total {max_items_total or 'todos'}, por pág {top_value_initial}")
    try:
        while current_url and (max_items_total is None or len(all_items) < max_items_total) and page_count < max_pages_to_fetch:
            page_count += 1; is_first_call = (page_count == 1)
            current_params = query_api_params_initial if is_first_call and current_url == url_base else None
            response = client.get(url=current_url, scope=scope, params=current_params)
            response_data = response.json(); page_items = response_data.get('value', [])
            if not isinstance(page_items, list): break
            for item in page_items:
                if max_items_total is None or len(all_items) < max_items_total: all_items.append(item)
                else: break
            current_url = response_data.get('@odata.nextLink')
            if not current_url or (max_items_total is not None and len(all_items) >= max_items_total): break
        return {"status": "success", "data": {"value": all_items, "@odata.count": len(all_items)}, "total_retrieved": len(all_items), "pages_processed": page_count}
    except Exception as e: return _handle_graph_api_error(e, action_name_for_log, params_input)

def _get_item_id_from_path_if_needed_sp(
    client: AuthenticatedHttpClient, item_path_or_id: str,
    site_id: str, drive_id: str
) -> Union[str, Dict[str, Any]]:
    is_likely_id = '!' in item_path_or_id or (len(item_path_or_id) > 40 and '/' not in item_path_or_id and '.' not in item_path_or_id) or item_path_or_id.startswith("driveItem_")
    if is_likely_id: return item_path_or_id
    metadata_params = {"site_id": site_id, "drive_id_or_name": drive_id, "item_id_or_path": item_path_or_id, "select": "id,name"}
    try:
        item_metadata_response = get_file_metadata(client, metadata_params) # Llama a la pública de este módulo
        if item_metadata_response.get("status") == "success":
            item_data = item_metadata_response.get("data", {}); item_id = item_data.get("id")
            if item_id: return item_id
            return {"status": "error", "message": f"ID no encontrado para SP path '{item_path_or_id}'.", "details": item_data, "http_status": 404}
        return {"status": "error", "message": f"Fallo al obtener metadata para SP path '{item_path_or_id}'.", "details": item_metadata_response, "http_status": item_metadata_response.get("http_status", 500)}
    except Exception as e_meta: return {"status": "error", "message": f"Excepción obteniendo ID para SP path '{item_path_or_id}': {e_meta}", "details": str(e_meta), "http_status": 500}

# ============================================
# ==== ACCIONES PÚBLICAS (Mapeadas) ====
# ============================================
def get_site_info(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Dict[str, Any]:
    select_fields: Optional[str] = params.get("select")
    try:
        target_site_identifier = _obtener_site_id_sp(client, params)
        url = f"{settings.GRAPH_API_BASE_URL}/sites/{target_site_identifier}"
        query_api_params: Dict[str, str] = {}
        if select_fields: query_api_params['$select'] = select_fields
        else: query_api_params['$select'] = "id,displayName,name,webUrl,createdDateTime,lastModifiedDateTime,description,siteCollection"
        sites_read_scope = getattr(settings, 'GRAPH_SCOPE_SITES_READ_ALL', settings.GRAPH_API_DEFAULT_SCOPE)
        response = client.get(url, scope=sites_read_scope, params=query_api_params if query_api_params else None)
        return {"status": "success", "data": response.json()}
    except Exception as e: return _handle_graph_api_error(e, "get_site_info", params)

def search_sites(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Dict[str, Any]:
    query_text: Optional[str] = params.get("query_text")
    if not query_text: return _handle_graph_api_error(ValueError("'query_text' requerido."), "search_sites", params)
    url = f"{settings.GRAPH_API_BASE_URL}/sites"
    api_query_params: Dict[str, Any] = {'search': query_text}
    if params.get("select"): api_query_params["$select"] = params["select"]
    if params.get("top"): api_query_params["$top"] = params["top"]
    sites_read_scope = getattr(settings, 'GRAPH_SCOPE_SITES_READ_ALL', settings.GRAPH_API_DEFAULT_SCOPE)
    try:
        response = client.get(url, scope=sites_read_scope, params=api_query_params)
        return {"status": "success", "data": response.json().get("value", [])}
    except Exception as e: return _handle_graph_api_error(e, "search_sites", params)

def create_list(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Dict[str, Any]:
    list_name: Optional[str] = params.get("nombre_lista"); columns_definition: Optional[List[Dict[str, Any]]] = params.get("columnas")
    list_template: str = params.get("template", "genericList")
    if not list_name: return _handle_graph_api_error(ValueError("'nombre_lista' requerido."), "create_list", params)
    try:
        target_site_id = _obtener_site_id_sp(client, params)
        url = f"{settings.GRAPH_API_BASE_URL}/sites/{target_site_id}/lists"
        body_payload: Dict[str, Any] = {"displayName": list_name, "list": {"template": list_template}}
        if columns_definition and isinstance(columns_definition, list): body_payload["columns"] = columns_definition
        sites_manage_scope = getattr(settings, 'GRAPH_SCOPE_SITES_MANAGE_ALL', settings.GRAPH_API_DEFAULT_SCOPE)
        response = client.post(url, scope=sites_manage_scope, json_data=body_payload)
        return {"status": "success", "data": response.json()}
    except Exception as e: return _handle_graph_api_error(e, "create_list", params)

def list_lists(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Dict[str, Any]:
    select_fields: str = params.get("select", "id,name,displayName,webUrl,list")
    top_per_page: int = min(int(params.get('top_per_page', 50)), getattr(settings, 'DEFAULT_PAGING_SIZE', 50))
    max_items_total: Optional[int] = params.get('max_items_total')
    filter_query: Optional[str] = params.get("filter_query"); order_by: Optional[str] = params.get("order_by")
    expand_fields: Optional[str] = params.get("expand")
    try:
        target_site_id = _obtener_site_id_sp(client, params)
        url_base = f"{settings.GRAPH_API_BASE_URL}/sites/{target_site_id}/lists"
        query_api_params_init: Dict[str, Any] = {'$top': top_per_page, '$select': select_fields}
        if filter_query: query_api_params_init['$filter'] = filter_query
        if order_by: query_api_params_init['$orderby'] = order_by
        if expand_fields: query_api_params_init['$expand'] = expand_fields
        sites_read_scope = getattr(settings, 'GRAPH_SCOPE_SITES_READ_ALL', settings.GRAPH_API_DEFAULT_SCOPE)
        return _sp_paged_request(client, url_base, sites_read_scope, params, query_api_params_init, max_items_total, "list_lists")
    except Exception as e: return _handle_graph_api_error(e, "list_lists", params)

def get_list(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Dict[str, Any]:
    list_id_or_name: Optional[str] = params.get("lista_id_o_nombre"); select_fields: Optional[str] = params.get("select")
    expand_fields: Optional[str] = params.get("expand")
    if not list_id_or_name: return _handle_graph_api_error(ValueError("'lista_id_o_nombre' requerido."), "get_list", params)
    try:
        target_site_id = _obtener_site_id_sp(client, params)
        url = f"{settings.GRAPH_API_BASE_URL}/sites/{target_site_id}/lists/{list_id_or_name}"
        query_api_params: Dict[str, str] = {}
        if select_fields: query_api_params['$select'] = select_fields
        if expand_fields: query_api_params['$expand'] = expand_fields
        sites_read_scope = getattr(settings, 'GRAPH_SCOPE_SITES_READ_ALL', settings.GRAPH_API_DEFAULT_SCOPE)
        response = client.get(url, scope=sites_read_scope, params=query_api_params if query_api_params else None)
        return {"status": "success", "data": response.json()}
    except Exception as e: return _handle_graph_api_error(e, "get_list", params)

def update_list(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Dict[str, Any]:
    list_id_or_name: Optional[str] = params.get("lista_id_o_nombre"); update_payload: Optional[Dict[str, Any]] = params.get("update_payload")
    if not list_id_or_name or not update_payload or not isinstance(update_payload, dict): return _handle_graph_api_error(ValueError("'lista_id_o_nombre' y 'update_payload' (dict) requeridos."), "update_list", params)
    try:
        target_site_id = _obtener_site_id_sp(client, params)
        url = f"{settings.GRAPH_API_BASE_URL}/sites/{target_site_id}/lists/{list_id_or_name}"
        sites_manage_scope = getattr(settings, 'GRAPH_SCOPE_SITES_MANAGE_ALL', settings.GRAPH_API_DEFAULT_SCOPE)
        response = client.patch(url, scope=sites_manage_scope, json_data=update_payload)
        return {"status": "success", "data": response.json()}
    except Exception as e: return _handle_graph_api_error(e, "update_list", params)

def delete_list(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Dict[str, Any]:
    list_id_or_name: Optional[str] = params.get("lista_id_o_nombre")
    if not list_id_or_name: return _handle_graph_api_error(ValueError("'lista_id_o_nombre' requerido."), "delete_list", params)
    try:
        target_site_id = _obtener_site_id_sp(client, params)
        url = f"{settings.GRAPH_API_BASE_URL}/sites/{target_site_id}/lists/{list_id_or_name}"
        sites_manage_scope = getattr(settings, 'GRAPH_SCOPE_SITES_MANAGE_ALL', settings.GRAPH_API_DEFAULT_SCOPE)
        response = client.delete(url, scope=sites_manage_scope)
        return {"status": "success", "message": f"Lista '{list_id_or_name}' eliminada.", "http_status": response.status_code}
    except Exception as e: return _handle_graph_api_error(e, "delete_list", params)

def add_list_item(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Dict[str, Any]:
    list_id_or_name: Optional[str] = params.get("lista_id_o_nombre"); fields_data: Optional[Dict[str, Any]] = params.get("datos_campos")
    if not list_id_or_name or not fields_data or not isinstance(fields_data, dict): return _handle_graph_api_error(ValueError("'lista_id_o_nombre' y 'datos_campos' (dict) requeridos."), "add_list_item", params)
    try:
        target_site_id = _obtener_site_id_sp(client, params)
        body_payload = {"fields": fields_data}
        url = f"{settings.GRAPH_API_BASE_URL}/sites/{target_site_id}/lists/{list_id_or_name}/items"
        sites_manage_scope = getattr(settings, 'GRAPH_SCOPE_SITES_MANAGE_ALL', settings.GRAPH_API_DEFAULT_SCOPE)
        response = client.post(url, scope=sites_manage_scope, json_data=body_payload)
        return {"status": "success", "data": response.json()}
    except Exception as e: return _handle_graph_api_error(e, "add_list_item", params)

def list_list_items(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Dict[str, Any]:
    list_id_or_name: Optional[str] = params.get("lista_id_o_nombre")
    if not list_id_or_name: return _handle_graph_api_error(ValueError("'lista_id_o_nombre' requerido."), "list_list_items", params)
    select_fields: Optional[str] = params.get("select"); filter_query: Optional[str] = params.get("filter_query")
    expand_fields: str = params.get("expand", "fields(select=*)")
    top_per_page: int = min(int(params.get('top_per_page', 50)), getattr(settings, 'DEFAULT_PAGING_SIZE', 50))
    max_items_total: Optional[int] = params.get('max_items_total'); order_by: Optional[str] = params.get("orderby")
    try:
        target_site_id = _obtener_site_id_sp(client, params)
        url_base = f"{settings.GRAPH_API_BASE_URL}/sites/{target_site_id}/lists/{list_id_or_name}/items"
        query_api_params_init: Dict[str, Any] = {'$top': top_per_page}
        if select_fields: query_api_params_init["$select"] = select_fields
        if filter_query: query_api_params_init["$filter"] = filter_query
        if expand_fields: query_api_params_init["$expand"] = expand_fields
        if order_by: query_api_params_init["$orderby"] = order_by
        sites_read_scope = getattr(settings, 'GRAPH_SCOPE_SITES_READ_ALL', settings.GRAPH_API_DEFAULT_SCOPE)
        return _sp_paged_request(client, url_base, sites_read_scope, params, query_api_params_init, max_items_total, "list_list_items")
    except Exception as e: return _handle_graph_api_error(e, "list_list_items", params)

def get_list_item(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Dict[str, Any]:
    list_id_or_name: Optional[str] = params.get("lista_id_o_nombre"); item_id: Optional[str] = params.get("item_id")
    select_fields: Optional[str] = params.get("select"); expand_fields: Optional[str] = params.get("expand", "fields(select=*)")
    if not list_id_or_name or not item_id: return _handle_graph_api_error(ValueError("'lista_id_o_nombre' e 'item_id' requeridos."), "get_list_item", params)
    try:
        target_site_id = _obtener_site_id_sp(client, params)
        url = f"{settings.GRAPH_API_BASE_URL}/sites/{target_site_id}/lists/{list_id_or_name}/items/{item_id}"
        query_api_params: Dict[str, str] = {}
        if select_fields: query_api_params["$select"] = select_fields
        if expand_fields: query_api_params["$expand"] = expand_fields
        sites_read_scope = getattr(settings, 'GRAPH_SCOPE_SITES_READ_ALL', settings.GRAPH_API_DEFAULT_SCOPE)
        response = client.get(url, scope=sites_read_scope, params=query_api_params if query_api_params else None)
        return {"status": "success", "data": response.json()}
    except Exception as e: return _handle_graph_api_error(e, "get_list_item", params)

def update_list_item(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Dict[str, Any]:
    list_id_or_name: Optional[str] = params.get("lista_id_o_nombre"); item_id: Optional[str] = params.get("item_id")
    fields_to_update: Optional[Dict[str, Any]] = params.get("nuevos_valores_campos"); etag: Optional[str] = params.get("etag")
    if not list_id_or_name or not item_id or not fields_to_update or not isinstance(fields_to_update, dict): return _handle_graph_api_error(ValueError("'lista_id_o_nombre', 'item_id', 'nuevos_valores_campos' (dict) requeridos."), "update_list_item", params)
    try:
        target_site_id = _obtener_site_id_sp(client, params)
        url = f"{settings.GRAPH_API_BASE_URL}/sites/{target_site_id}/lists/{list_id_or_name}/items/{item_id}/fields"
        request_headers = {'If-Match': etag} if etag else {}
        sites_manage_scope = getattr(settings, 'GRAPH_SCOPE_SITES_MANAGE_ALL', settings.GRAPH_API_DEFAULT_SCOPE)
        response = client.patch(url, scope=sites_manage_scope, json_data=fields_to_update, headers=request_headers)
        return {"status": "success", "data": response.json()}
    except Exception as e: return _handle_graph_api_error(e, "update_list_item", params)

def delete_list_item(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Dict[str, Any]:
    list_id_or_name: Optional[str] = params.get("lista_id_o_nombre"); item_id: Optional[str] = params.get("item_id")
    etag: Optional[str] = params.get("etag")
    if not list_id_or_name or not item_id: return _handle_graph_api_error(ValueError("'lista_id_o_nombre' e 'item_id' requeridos."), "delete_list_item", params)
    try:
        target_site_id = _obtener_site_id_sp(client, params)
        url = f"{settings.GRAPH_API_BASE_URL}/sites/{target_site_id}/lists/{list_id_or_name}/items/{item_id}"
        request_headers = {'If-Match': etag} if etag else {}
        sites_manage_scope = getattr(settings, 'GRAPH_SCOPE_SITES_MANAGE_ALL', settings.GRAPH_API_DEFAULT_SCOPE)
        response = client.delete(url, scope=sites_manage_scope, headers=request_headers)
        return {"status": "success", "message": f"Item '{item_id}' eliminado.", "http_status": response.status_code}
    except Exception as e: return _handle_graph_api_error(e, "delete_list_item", params)

def search_list_items(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Dict[str, Any]:
    list_id_or_name: Optional[str] = params.get("lista_id_o_nombre"); query_text_as_filter: Optional[str] = params.get("query_text")
    select_fields: Optional[str] = params.get("select"); max_results: Optional[int] = params.get("top")
    if not list_id_or_name or not query_text_as_filter: return _handle_graph_api_error(ValueError("'lista_id_o_nombre' y 'query_text' (como $filter) requeridos."), "search_list_items", params)
    logger.warning("Función 'search_list_items' usa 'query_text' como $filter.")
    try:
        target_site_id = _obtener_site_id_sp(client, params)
        list_items_params = {"site_id": target_site_id, "lista_id_o_nombre": list_id_or_name, "filter_query": query_text_as_filter, "select": select_fields, "max_items_total": max_results, "expand": params.get("expand", "fields(select=*)")}
        return list_list_items(client, list_items_params)
    except Exception as e: return _handle_graph_api_error(e, "search_list_items", params)

def list_document_libraries(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Dict[str, Any]:
    select_fields: str = params.get("select", "id,name,displayName,webUrl,driveType,quota,owner")
    top_per_page: int = min(int(params.get('top_per_page', 50)), getattr(settings, 'DEFAULT_PAGING_SIZE', 50))
    max_items_total: Optional[int] = params.get('max_items_total'); filter_query: Optional[str] = params.get("filter_query")
    try:
        target_site_id = _obtener_site_id_sp(client, params)
        url_base = f"{settings.GRAPH_API_BASE_URL}/sites/{target_site_id}/drives"
        query_api_params_init: Dict[str, Any] = {'$top': top_per_page, '$select': select_fields}
        if filter_query: query_api_params_init['$filter'] = filter_query
        else: query_api_params_init['$filter'] = "driveType eq 'documentLibrary'"
        files_read_scope = getattr(settings, 'GRAPH_SCOPE_FILES_READ_ALL', settings.GRAPH_API_DEFAULT_SCOPE)
        return _sp_paged_request(client, url_base, files_read_scope, params, query_api_params_init, max_items_total, "list_document_libraries")
    except Exception as e: return _handle_graph_api_error(e, "list_document_libraries", params)

def list_folder_contents(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Dict[str, Any]:
    folder_path_or_id: str = params.get("folder_path_or_id", ""); drive_id_or_name_input: Optional[str] = params.get("drive_id_or_name")
    select_fields: Optional[str] = params.get("select"); expand_fields: Optional[str] = params.get("expand")
    top_per_page: int = min(int(params.get('top_per_page', 50)), 200)
    max_items_total: Optional[int] = params.get('max_items_total'); order_by: Optional[str] = params.get("orderby")
    try:
        target_site_id = _obtener_site_id_sp(client, params)
        target_drive_id = _get_drive_id(client, target_site_id, drive_id_or_name_input)
        is_folder_id = not ('/' in folder_path_or_id) and (len(folder_path_or_id) > 40 or '!' in folder_path_or_id)
        item_segment = f"items/{folder_path_or_id}" if is_folder_id else ("root" if not folder_path_or_id or folder_path_or_id == "/" else f"root:/{folder_path_or_id.strip('/')}")
        url_base = f"{settings.GRAPH_API_BASE_URL}/sites/{target_site_id}/drives/{target_drive_id}/{item_segment}/children"
        query_api_params_init: Dict[str, Any] = {'$top': top_per_page}
        query_api_params_init["$select"] = select_fields or "id,name,webUrl,size,createdDateTime,lastModifiedDateTime,file,folder,package,parentReference"
        if expand_fields: query_api_params_init["$expand"] = expand_fields
        if order_by: query_api_params_init["$orderby"] = order_by
        files_read_scope = getattr(settings, 'GRAPH_SCOPE_FILES_READ_ALL', settings.GRAPH_API_DEFAULT_SCOPE)
        return _sp_paged_request(client, url_base, files_read_scope, params, query_api_params_init, max_items_total, "list_folder_contents")
    except Exception as e: return _handle_graph_api_error(e, "list_folder_contents", params)

def get_file_metadata(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Dict[str, Any]:
    item_id_or_path: Optional[str] = params.get("item_id_or_path"); drive_id_or_name_input: Optional[str] = params.get("drive_id_or_name")
    select_fields: Optional[str] = params.get("select"); expand_fields: Optional[str] = params.get("expand")
    if not item_id_or_path: return _handle_graph_api_error(ValueError("'item_id_or_path' requerido."),"get_file_metadata", params)
    try:
        target_site_id = _obtener_site_id_sp(client, params)
        target_drive_id = _get_drive_id(client, target_site_id, drive_id_or_name_input)
        is_item_id = not ('/' in item_id_or_path) and (len(item_id_or_path) > 40 or '!' in item_id_or_path)
        base_url_item = _get_sp_item_endpoint_by_id(target_site_id, target_drive_id, item_id_or_path) if is_item_id else _get_sp_item_endpoint_by_path(target_site_id, target_drive_id, item_id_or_path)
        query_api_params: Dict[str, str] = {}
        query_api_params["$select"] = select_fields or "id,name,webUrl,size,createdDateTime,lastModifiedDateTime,file,folder,package,parentReference,listItem"
        if expand_fields: query_api_params["$expand"] = expand_fields
        files_read_scope = getattr(settings, 'GRAPH_SCOPE_FILES_READ_ALL', settings.GRAPH_API_DEFAULT_SCOPE)
        response = client.get(base_url_item, scope=files_read_scope, params=query_api_params if query_api_params else None)
        return {"status": "success", "data": response.json()}
    except Exception as e: return _handle_graph_api_error(e, "get_file_metadata", params)

def upload_document(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Dict[str, Any]:
    filename: Optional[str] = params.get("filename"); content_bytes: Optional[bytes] = params.get("content_bytes")
    folder_path: str = params.get("folder_path", ""); drive_id_or_name_input: Optional[str] = params.get("drive_id_or_name")
    conflict_behavior: str = params.get("conflict_behavior", "rename")
    if not filename or content_bytes is None: return _handle_graph_api_error(ValueError("'filename' y 'content_bytes' requeridos."), "upload_document", params)
    if not isinstance(content_bytes, bytes): return _handle_graph_api_error(TypeError("'content_bytes' debe ser bytes."), "upload_document", params)
    try:
        target_site_id = _obtener_site_id_sp(client, params)
        target_drive_id = _get_drive_id(client, target_site_id, drive_id_or_name_input)
        path_segment = folder_path.strip("/"); target_item_path = f"{path_segment}/{filename}" if path_segment else filename
        item_upload_base_url = _get_sp_item_endpoint_by_path(target_site_id, target_drive_id, target_item_path)
        file_size_bytes = len(content_bytes)
        files_rw_scope = getattr(settings, 'GRAPH_SCOPE_FILES_READ_WRITE_ALL', settings.GRAPH_API_DEFAULT_SCOPE)
        if file_size_bytes <= 4 * 1024 * 1024:
            upload_url = f"{item_upload_base_url}/content"; put_query_params = {"@microsoft.graph.conflictBehavior": conflict_behavior}
            response = client.put(upload_url, scope=files_rw_scope, data=content_bytes, headers={"Content-Type": "application/octet-stream"}, params=put_query_params)
            return {"status": "success", "data": response.json()}
        else:
            session_url = f"{item_upload_base_url}/createUploadSession"
            session_body = {"item": {"@microsoft.graph.conflictBehavior": conflict_behavior, "name": filename}}
            session_response = client.post(session_url, scope=files_rw_scope, json_data=session_body)
            upload_session_data = session_response.json(); upload_url_session = upload_session_data.get("uploadUrl")
            if not upload_url_session: raise ValueError("No se pudo obtener 'uploadUrl' de sesión.")
            chunk_size = 5 * 1024 * 1024; start_byte = 0; final_response_json = None
            while start_byte < file_size_bytes:
                end_byte = min(start_byte + chunk_size - 1, file_size_bytes - 1); current_chunk = content_bytes[start_byte : end_byte + 1]
                headers_chunk = {"Content-Length": str(len(current_chunk)), "Content-Range": f"bytes {start_byte}-{end_byte}/{file_size_bytes}"}
                chunk_resp = requests.put(upload_url_session, data=current_chunk, headers=headers_chunk, timeout=settings.DEFAULT_API_TIMEOUT * 2)
                chunk_resp.raise_for_status()
                if chunk_resp.status_code in (200, 201): final_response_json = chunk_resp.json(); break
                start_byte = end_byte + 1
            if final_response_json: return {"status": "success", "data": final_response_json, "message": "Archivo subido (sesión)."}
            check_params = {"item_id_or_path": target_item_path, "drive_id_or_name": target_drive_id, "site_id": target_site_id}
            check_meta = get_file_metadata(client, check_params)
            if check_meta.get("status") == "success": return {"status": "success", "data": check_meta["data"], "message": "Archivo subido (sesión, verificado)."}
            raise Exception("Subida de sesión completada pero item final no verificado.")
    except Exception as e: return _handle_graph_api_error(e, "upload_document", params)

def download_document(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Union[bytes, Dict[str, Any]]:
    item_id_or_path: Optional[str] = params.get("item_id_or_path"); drive_id_or_name_input: Optional[str] = params.get("drive_id_or_name")
    if not item_id_or_path: return _handle_graph_api_error(ValueError("'item_id_or_path' requerido."), "download_document", params)
    try:
        target_site_id = _obtener_site_id_sp(client, params)
        target_drive_id = _get_drive_id(client, target_site_id, drive_id_or_name_input)
        item_actual_id = _get_item_id_from_path_if_needed_sp(client, item_id_or_path, target_site_id, target_drive_id)
        if isinstance(item_actual_id, dict): return item_actual_id
        url_content = f"{_get_sp_item_endpoint_by_id(target_site_id, target_drive_id, str(item_actual_id))}/content"
        files_read_scope = getattr(settings, 'GRAPH_SCOPE_FILES_READ_ALL', settings.GRAPH_API_DEFAULT_SCOPE)
        response = client.get(url_content, scope=files_read_scope, stream=True)
        return response.content
    except Exception as e: return _handle_graph_api_error(e, "download_document", params)

def delete_document(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Dict[str, Any]:
    return delete_item(client, params) # Alias

def delete_item(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Dict[str, Any]: # Renombrado en original sp_delete_item
    item_id_or_path: Optional[str] = params.get("item_id_or_path"); drive_id_or_name_input: Optional[str] = params.get("drive_id_or_name")
    etag: Optional[str] = params.get("etag")
    if not item_id_or_path: return _handle_graph_api_error(ValueError("'item_id_or_path' requerido."),"delete_item", params)
    try:
        target_site_id = _obtener_site_id_sp(client, params)
        target_drive_id = _get_drive_id(client, target_site_id, drive_id_or_name_input)
        item_actual_id = _get_item_id_from_path_if_needed_sp(client, item_id_or_path, target_site_id, target_drive_id)
        if isinstance(item_actual_id, dict): return item_actual_id
        url_item = _get_sp_item_endpoint_by_id(target_site_id, target_drive_id, str(item_actual_id))
        request_headers = {'If-Match': etag} if etag else {}
        files_rw_scope = getattr(settings, 'GRAPH_SCOPE_FILES_READ_WRITE_ALL', settings.GRAPH_API_DEFAULT_SCOPE)
        response = client.delete(url_item, scope=files_rw_scope, headers=request_headers)
        return {"status": "success", "message": f"Item '{item_actual_id}' eliminado.", "http_status": response.status_code}
    except Exception as e: return _handle_graph_api_error(e, "delete_item", params)

def create_folder(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Dict[str, Any]:
    folder_name: Optional[str] = params.get("folder_name"); parent_folder_path_or_id: str = params.get("parent_folder_path_or_id", "")
    drive_id_or_name_input: Optional[str] = params.get("drive_id_or_name"); conflict_behavior: str = params.get("conflict_behavior", "fail")
    if not folder_name: return _handle_graph_api_error(ValueError("'folder_name' requerido."), "create_folder", params)
    try:
        target_site_id = _obtener_site_id_sp(client, params)
        target_drive_id = _get_drive_id(client, target_site_id, drive_id_or_name_input)
        parent_is_id = not ('/' in parent_folder_path_or_id) and (len(parent_folder_path_or_id) > 40 or '!' in parent_folder_path_or_id)
        parent_endpoint = _get_sp_item_endpoint_by_id(target_site_id, target_drive_id, parent_folder_path_or_id) if parent_is_id else _get_sp_item_endpoint_by_path(target_site_id, target_drive_id, parent_folder_path_or_id)
        url_create_folder = f"{parent_endpoint}/children"
        body_payload = {"name": folder_name, "folder": {}, "@microsoft.graph.conflictBehavior": conflict_behavior}
        files_rw_scope = getattr(settings, 'GRAPH_SCOPE_FILES_READ_WRITE_ALL', settings.GRAPH_API_DEFAULT_SCOPE)
        response = client.post(url_create_folder, scope=files_rw_scope, json_data=body_payload)
        return {"status": "success", "data": response.json()}
    except Exception as e: return _handle_graph_api_error(e, "create_folder", params)

def move_item(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Dict[str, Any]:
    item_id_or_path: Optional[str] = params.get("item_id_or_path"); target_parent_folder_id: Optional[str] = params.get("target_parent_folder_id")
    new_name_after_move: Optional[str] = params.get("new_name")
    source_drive_id_or_name: Optional[str] = params.get("drive_id_or_name") or params.get("source_drive_id_or_name")
    target_drive_id_param: Optional[str] = params.get("target_drive_id")
    if not item_id_or_path or not target_parent_folder_id: return _handle_graph_api_error(ValueError("'item_id_or_path' y 'target_parent_folder_id' requeridos."), "move_item", params)
    try:
        target_site_id = _obtener_site_id_sp(client, params) # Asume que el sitio es el mismo o se pasa target_site_id
        source_drive_id_resolved = _get_drive_id(client, target_site_id, source_drive_id_or_name)
        item_actual_id = _get_item_id_from_path_if_needed_sp(client, item_id_or_path, target_site_id, source_drive_id_resolved)
        if isinstance(item_actual_id, dict): return item_actual_id
        url_patch_item = _get_sp_item_endpoint_by_id(target_site_id, source_drive_id_resolved, str(item_actual_id))
        payload_move: Dict[str, Any] = {"parentReference": {"id": target_parent_folder_id}}
        if target_drive_id_param: payload_move["parentReference"]["driveId"] = target_drive_id_param
        if params.get("target_site_id"): payload_move["parentReference"]["siteId"] = params.get("target_site_id")
        if new_name_after_move: payload_move["name"] = new_name_after_move
        files_rw_scope = getattr(settings, 'GRAPH_SCOPE_FILES_READ_WRITE_ALL', settings.GRAPH_API_DEFAULT_SCOPE)
        response = client.patch(url_patch_item, scope=files_rw_scope, json_data=payload_move)
        return {"status": "success", "data": response.json()}
    except Exception as e: return _handle_graph_api_error(e, "move_item", params)

def copy_item(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Dict[str, Any]:
    item_id_or_path: Optional[str] = params.get("item_id_or_path"); target_parent_folder_id: Optional[str] = params.get("target_parent_folder_id")
    new_name_for_copy: Optional[str] = params.get("new_name"); source_site_id_param: Optional[str] = params.get("source_site_id")
    source_drive_id_or_name: Optional[str] = params.get("source_drive_id_or_name"); target_site_id_param: Optional[str] = params.get("target_site_id")
    target_drive_id_param: Optional[str] = params.get("target_drive_id")
    if not item_id_or_path or not target_parent_folder_id: return _handle_graph_api_error(ValueError("'item_id_or_path' y 'target_parent_folder_id' requeridos."), "copy_item", params)
    try:
        source_site_id_resolved = _obtener_site_id_sp(client, {"site_id": source_site_id_param, **params} if source_site_id_param else params)
        source_drive_id_resolved = _get_drive_id(client, source_site_id_resolved, source_drive_id_or_name)
        item_actual_id = _get_item_id_from_path_if_needed_sp(client, item_id_or_path, source_site_id_resolved, source_drive_id_resolved)
        if isinstance(item_actual_id, dict): return item_actual_id
        url_copy_action = f"{_get_sp_item_endpoint_by_id(source_site_id_resolved, source_drive_id_resolved, str(item_actual_id))}/copy"
        parent_reference_payload: Dict[str, str] = {"id": target_parent_folder_id}
        if target_drive_id_param:
            parent_reference_payload["driveId"] = target_drive_id_param
            if target_site_id_param:
                dest_site_id_resolved = _obtener_site_id_sp(client, {"site_id": target_site_id_param, **params})
                parent_reference_payload["siteId"] = dest_site_id_resolved
        body_payload: Dict[str, Any] = {"parentReference": parent_reference_payload}
        if new_name_for_copy: body_payload["name"] = new_name_for_copy
        files_rw_scope = getattr(settings, 'GRAPH_SCOPE_FILES_READ_WRITE_ALL', settings.GRAPH_API_DEFAULT_SCOPE)
        response = client.post(url_copy_action, scope=files_rw_scope, json_data=body_payload)
        if response.status_code == 202:
            monitor_url = response.headers.get("Location"); response_data = response.json() if response.content else {}
            return {"status": "pending", "message": "Solicitud de copia aceptada.", "monitor_url": monitor_url, "data": response_data, "http_status": 202}
        return {"status": "success" if response.ok else "error", "data": response.json() if response.content else None, "http_status": response.status_code}
    except Exception as e: return _handle_graph_api_error(e, "copy_item", params)

def update_file_metadata(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Dict[str, Any]:
    item_id_or_path: Optional[str] = params.get("item_id_or_path"); drive_id_or_name_input: Optional[str] = params.get("drive_id_or_name")
    metadata_updates_payload: Optional[Dict[str, Any]] = params.get("metadata_updates"); etag: Optional[str] = params.get("etag")
    if not item_id_or_path or not metadata_updates_payload or not isinstance(metadata_updates_payload, dict): return _handle_graph_api_error(ValueError("'item_id_or_path' y 'metadata_updates' (dict) requeridos."), "update_file_metadata", params)
    try:
        target_site_id = _obtener_site_id_sp(client, params)
        target_drive_id = _get_drive_id(client, target_site_id, drive_id_or_name_input)
        item_actual_id = _get_item_id_from_path_if_needed_sp(client, item_id_or_path, target_site_id, target_drive_id)
        if isinstance(item_actual_id, dict): return item_actual_id
        url_update = _get_sp_item_endpoint_by_id(target_site_id, target_drive_id, str(item_actual_id))
        request_headers = {'If-Match': etag} if etag else {}
        files_rw_scope = getattr(settings, 'GRAPH_SCOPE_FILES_READ_WRITE_ALL', settings.GRAPH_API_DEFAULT_SCOPE)
        response = client.patch(url_update, scope=files_rw_scope, json_data=metadata_updates_payload, headers=request_headers)
        return {"status": "success", "data": response.json()}
    except Exception as e: return _handle_graph_api_error(e, "update_file_metadata", params)

def get_sharing_link(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Dict[str, Any]:
    item_id_or_path: Optional[str] = params.get("item_id_or_path"); drive_id_or_name_input: Optional[str] = params.get("drive_id_or_name")
    link_type: str = params.get("link_type", "view"); scope_param: str = params.get("scope", "organization")
    password_link: Optional[str] = params.get("password"); expiration_datetime_str: Optional[str] = params.get("expiration_datetime")
    recipients_payload: Optional[List[Dict[str,str]]] = params.get("recipients")
    if not item_id_or_path: return _handle_graph_api_error(ValueError("'item_id_or_path' requerido."), "get_sharing_link", params)
    try:
        target_site_id = _obtener_site_id_sp(client, params)
        target_drive_id = _get_drive_id(client, target_site_id, drive_id_or_name_input)
        item_actual_id = _get_item_id_from_path_if_needed_sp(client, item_id_or_path, target_site_id, target_drive_id)
        if isinstance(item_actual_id, dict): return item_actual_id
        url_action_createlink = f"{_get_sp_item_endpoint_by_id(target_site_id, target_drive_id, str(item_actual_id))}/createLink"
        body_payload_link: Dict[str, Any] = {"type": link_type, "scope": scope_param}
        if password_link: body_payload_link["password"] = password_link
        if expiration_datetime_str: body_payload_link["expirationDateTime"] = expiration_datetime_str
        if scope_param == "users" and recipients_payload: body_payload_link["recipients"] = recipients_payload
        elif scope_param == "users" and not recipients_payload: return _handle_graph_api_error(ValueError("Si scope es 'users', 'recipients' es requerido."), "get_sharing_link", params)
        files_rw_scope = getattr(settings, 'GRAPH_SCOPE_FILES_READ_WRITE_ALL', settings.GRAPH_API_DEFAULT_SCOPE)
        response = client.post(url_action_createlink, scope=files_rw_scope, json_data=body_payload_link)
        return {"status": "success", "data": response.json()}
    except Exception as e: return _handle_graph_api_error(e, "get_sharing_link", params)

def list_item_permissions(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Dict[str, Any]:
    item_id_or_path: Optional[str] = params.get("item_id_or_path"); drive_id_or_name_input: Optional[str] = params.get("drive_id_or_name")
    list_id_o_nombre: Optional[str] = params.get("list_id_o_nombre"); list_item_id_param: Optional[str] = params.get("list_item_id")
    if not item_id_or_path and not (list_id_o_nombre and list_item_id_param): return _handle_graph_api_error(ValueError("Se requiere 'item_id_or_path' o ('list_id_o_nombre' y 'list_item_id')."), "list_item_permissions", params)
    try:
        target_site_id = _obtener_site_id_sp(client, params); url_item_permissions: str; log_item_description: str
        if item_id_or_path:
            target_drive_id = _get_drive_id(client, target_site_id, drive_id_or_name_input)
            item_actual_id = _get_item_id_from_path_if_needed_sp(client, item_id_or_path, target_site_id, target_drive_id)
            if isinstance(item_actual_id, dict): return item_actual_id
            url_item_permissions = f"{_get_sp_item_endpoint_by_id(target_site_id, target_drive_id, str(item_actual_id))}/permissions"
            log_item_description = f"DriveItem ID '{item_actual_id}'"
        else:
            if not list_id_o_nombre or not list_item_id_param: return _handle_graph_api_error(ValueError("ListItem: 'list_id_o_nombre' y 'list_item_id' requeridos."),"list_item_permissions", params)
            url_item_permissions = f"{settings.GRAPH_API_BASE_URL}/sites/{target_site_id}/lists/{list_id_o_nombre}/items/{list_item_id_param}/permissions"
            log_item_description = f"ListItem ID '{list_item_id_param}' en lista '{list_id_o_nombre}'"
        perm_scope = getattr(settings, 'GRAPH_SCOPE_SITES_FULLCONTROL_ALL', settings.GRAPH_API_DEFAULT_SCOPE)
        response = client.get(url_item_permissions, scope=perm_scope)
        return {"status": "success", "data": response.json().get("value", [])}
    except Exception as e: return _handle_graph_api_error(e, "list_item_permissions", params)

def add_item_permissions(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Dict[str, Any]:
    item_id_or_path: Optional[str] = params.get("item_id_or_path"); drive_id_or_name: Optional[str] = params.get("drive_id_or_name")
    list_id_o_nombre: Optional[str] = params.get("list_id_o_nombre"); list_item_id: Optional[str] = params.get("list_item_id")
    recipients_payload: Optional[List[Dict[str,Any]]] = params.get("recipients"); roles_payload: Optional[List[str]] = params.get("roles")
    require_signin: bool = params.get("requireSignIn", True); send_invitation: bool = params.get("sendInvitation", True)
    message_invitation: Optional[str] = params.get("message"); expiration_datetime_str: Optional[str] = params.get("expirationDateTime")
    if (not item_id_or_path and not (list_id_o_nombre and list_item_id)) or not recipients_payload or not roles_payload: return _handle_graph_api_error(ValueError("Faltan: identificador de item, 'recipients' y 'roles'."), "add_item_permissions", params)
    try:
        target_site_id = _obtener_site_id_sp(client, params); url_action_invite: str; log_item_desc: str
        body_invite_payload: Dict[str, Any] = {"recipients": recipients_payload, "roles": roles_payload, "requireSignIn": require_signin, "sendInvitation": send_invitation}
        if message_invitation: body_invite_payload["message"] = message_invitation
        if expiration_datetime_str: body_invite_payload["expirationDateTime"] = expiration_datetime_str
        if item_id_or_path:
            target_drive_id = _get_drive_id(client, target_site_id, drive_id_or_name)
            item_actual_id = _get_item_id_from_path_if_needed_sp(client, item_id_or_path, target_site_id, target_drive_id)
            if isinstance(item_actual_id, dict) and item_actual_id.get("status") == "error": return item_actual_id
            item_actual_id_str = str(item_actual_id)
            url_action_invite = f"{_get_sp_item_endpoint_by_id(target_site_id, target_drive_id, item_actual_id_str)}/invite"
            log_item_desc = f"DriveItem ID '{item_actual_id_str}'"
        else:
            if not list_id_o_nombre or not list_item_id: return _handle_graph_api_error(ValueError("ListItem: 'list_id_o_nombre' y 'list_item_id' requeridos."),"add_item_permissions", params)
            url_action_invite = f"{settings.GRAPH_API_BASE_URL}/sites/{target_site_id}/lists/{list_id_o_nombre}/items/{list_item_id}/invite"
            log_item_desc = f"ListItem ID '{list_item_id}'"
        perm_scope = getattr(settings, 'GRAPH_SCOPE_SITES_FULLCONTROL_ALL', settings.GRAPH_API_DEFAULT_SCOPE)
        response = client.post(url_action_invite, scope=perm_scope, json_data=body_invite_payload)
        return {"status": "success", "data": response.json().get("value", [])}
    except Exception as e: return _handle_graph_api_error(e, "add_item_permissions", params)

def remove_item_permissions(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Dict[str, Any]:
    item_id_or_path: Optional[str] = params.get("item_id_or_path"); drive_id_or_name: Optional[str] = params.get("drive_id_or_name")
    list_id_o_nombre: Optional[str] = params.get("list_id_o_nombre"); list_item_id: Optional[str] = params.get("list_item_id")
    permission_id: Optional[str] = params.get("permission_id")
    if (not item_id_or_path and not (list_id_o_nombre and list_item_id)) or not permission_id: return _handle_graph_api_error(ValueError("Faltan: identificador de item y 'permission_id'."), "remove_item_permissions", params)
    try:
        target_site_id = _obtener_site_id_sp(client, params); url_delete_perm: str; log_item_desc: str
        if item_id_or_path:
            target_drive_id = _get_drive_id(client, target_site_id, drive_id_or_name)
            item_actual_id = _get_item_id_from_path_if_needed_sp(client, item_id_or_path, target_site_id, target_drive_id)
            if isinstance(item_actual_id, dict) and item_actual_id.get("status") == "error": return item_actual_id
            item_actual_id_str = str(item_actual_id)
            url_delete_perm = f"{_get_sp_item_endpoint_by_id(target_site_id, target_drive_id, item_actual_id_str)}/permissions/{permission_id}"
            log_item_desc = f"DriveItem ID '{item_actual_id_str}'"
        else:
            if not list_id_o_nombre or not list_item_id: return _handle_graph_api_error(ValueError("ListItem: 'list_id_o_nombre' y 'list_item_id' requeridos."),"remove_item_permissions", params)
            url_delete_perm = f"{settings.GRAPH_API_BASE_URL}/sites/{target_site_id}/lists/{list_id_o_nombre}/items/{list_item_id}/permissions/{permission_id}"
            log_item_desc = f"ListItem ID '{list_item_id}'"
        perm_scope = getattr(settings, 'GRAPH_SCOPE_SITES_FULLCONTROL_ALL', settings.GRAPH_API_DEFAULT_SCOPE)
        response = client.delete(url_delete_perm, scope=perm_scope)
        return {"status": "success", "message": f"Permiso '{permission_id}' eliminado de {log_item_desc}.", "http_status": response.status_code}
    except Exception as e: return _handle_graph_api_error(e, "remove_item_permissions", params)

MEMORIA_LIST_NAME_FROM_SETTINGS = settings.MEMORIA_LIST_NAME

def _ensure_memory_list_exists(client: AuthenticatedHttpClient, site_id: str) -> bool:
    try:
        url_get_list = f"{settings.GRAPH_API_BASE_URL}/sites/{site_id}/lists/{MEMORIA_LIST_NAME_FROM_SETTINGS}?$select=id"
        sites_read_scope = getattr(settings, 'GRAPH_SCOPE_SITES_READ_ALL', settings.GRAPH_API_DEFAULT_SCOPE)
        try: client.get(url_get_list, scope=sites_read_scope); return True
        except requests.exceptions.HTTPError as http_err:
            if http_err.response is not None and http_err.response.status_code == 404:
                columnas_default = [{"name": "SessionID", "text": {}}, {"name": "Clave", "text": {}}, {"name": "Valor", "text": {"allowMultipleLines": True, "textType": "plain"}}, {"name": "Timestamp", "dateTime": {"displayAs": "default", "format": "dateTime"}}]
                create_params = {"site_id": site_id, "nombre_lista": MEMORIA_LIST_NAME_FROM_SETTINGS, "columnas": columnas_default, "template": "genericList"}
                creation_response = create_list(client, create_params)
                return creation_response.get("status") == "success"
            else: raise
    except Exception as e: logger.error(f"Error crítico asegurando lista memoria: {e}", exc_info=True); return False

def memory_ensure_list(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Dict[str, Any]:
    try:
        target_site_id = _obtener_site_id_sp(client, params)
        success = _ensure_memory_list_exists(client, target_site_id)
        if success: return {"status": "success", "message": f"Lista memoria '{MEMORIA_LIST_NAME_FROM_SETTINGS}' asegurada en '{target_site_id}'."}
        return {"status": "error", "message": f"No se pudo asegurar/crear lista memoria '{MEMORIA_LIST_NAME_FROM_SETTINGS}'."}
    except Exception as e: return _handle_graph_api_error(e, "memory_ensure_list", params)

def memory_save(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Dict[str, Any]:
    session_id: Optional[str] = params.get("session_id"); clave: Optional[str] = params.get("clave"); valor: Any = params.get("valor")
    if not session_id or not clave or valor is None: return _handle_graph_api_error(ValueError("'session_id', 'clave', 'valor' requeridos."),"memory_save", params)
    try:
        target_site_id = _obtener_site_id_sp(client, params)
        if _ensure_memory_list_exists(client, target_site_id) is not True: return {"status": "error", "message": f"No se pudo asegurar/crear lista memoria '{MEMORIA_LIST_NAME_FROM_SETTINGS}'."}
        valor_str = json.dumps(valor); filter_q = f"fields/SessionID eq '{session_id}' and fields/Clave eq '{clave}'"
        list_params = {"site_id": target_site_id, "lista_id_o_nombre": MEMORIA_LIST_NAME_FROM_SETTINGS, "filter_query": filter_q, "top_per_page": 1, "max_items_total": 1, "select": "id,@odata.etag"}
        existing_items_response = list_list_items(client, list_params)
        item_id, item_etag = None, None
        if existing_items_response.get("status") == "success":
            items_value = existing_items_response.get("data", {}).get("value", [])
            if items_value: item_info = items_value[0]; item_id = item_info.get("id"); item_etag = item_info.get("@odata.etag")
        datos_campos_payload = {"SessionID": session_id, "Clave": clave, "Valor": valor_str, "Timestamp": _get_current_timestamp_iso_z()}
        if item_id:
            update_params = {"site_id": target_site_id, "lista_id_o_nombre": MEMORIA_LIST_NAME_FROM_SETTINGS, "item_id": item_id, "nuevos_valores_campos": datos_campos_payload, "etag": item_etag}
            return update_list_item(client, update_params)
        else:
            add_params = {"site_id": target_site_id, "lista_id_o_nombre": MEMORIA_LIST_NAME_FROM_SETTINGS, "datos_campos": datos_campos_payload}
            return add_list_item(client, add_params)
    except Exception as e: return _handle_graph_api_error(e, "memory_save", params)

def memory_get(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Dict[str, Any]:
    session_id: Optional[str] = params.get("session_id"); clave: Optional[str] = params.get("clave")
    if not session_id: return _handle_graph_api_error(ValueError("'session_id' requerido."),"memory_get", params)
    try:
        target_site_id = _obtener_site_id_sp(client, params)
        filter_parts = [f"fields/SessionID eq '{session_id}'"]
        if clave: filter_parts.append(f"fields/Clave eq '{clave}'")
        list_params = {"site_id": target_site_id, "lista_id_o_nombre": MEMORIA_LIST_NAME_FROM_SETTINGS, "filter_query": " and ".join(filter_parts), "select": "fields/Clave,fields/Valor,fields/Timestamp", "orderby": "fields/Timestamp desc", "max_items_total": None if not clave else 1}
        items_response = list_list_items(client, list_params)
        if items_response.get("status") != "success": return items_response
        retrieved_data: Any = {} if not clave else None; items = items_response.get("data", {}).get("value", [])
        if not items: return {"status": "success", "data": retrieved_data, "message": "No data found."}
        if clave:
            valor_str = items[0].get("fields", {}).get("Valor")
            try: retrieved_data = json.loads(valor_str) if valor_str else None
            except json.JSONDecodeError: retrieved_data = valor_str
        else:
            for item in items:
                item_fields = item.get("fields", {}); current_clave = item_fields.get("Clave"); valor_str = item_fields.get("Valor")
                if current_clave and current_clave not in retrieved_data:
                    try: retrieved_data[current_clave] = json.loads(valor_str) if valor_str else None
                    except json.JSONDecodeError: retrieved_data[current_clave] = valor_str
        return {"status": "success", "data": retrieved_data}
    except Exception as e: return _handle_graph_api_error(e, "memory_get", params)

def memory_delete(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Dict[str, Any]:
    session_id: Optional[str] = params.get("session_id"); clave: Optional[str] = params.get("clave")
    if not session_id: return _handle_graph_api_error(ValueError("'session_id' requerido."), "memory_delete", params)
    try:
        target_site_id = _obtener_site_id_sp(client, params)
        filter_parts = [f"fields/SessionID eq '{session_id}'"]; log_action_detail = f"sesión '{session_id}'"
        if clave: filter_parts.append(f"fields/Clave eq '{clave}'"); log_action_detail = f"clave '{clave}' de sesión '{session_id}'"
        list_params = {"site_id": target_site_id, "lista_id_o_nombre": MEMORIA_LIST_NAME_FROM_SETTINGS, "filter_query": " and ".join(filter_parts), "select": "id", "max_items_total": None }
        items_to_delete_resp = list_list_items(client, list_params)
        if items_to_delete_resp.get("status") != "success": return items_to_delete_resp
        items = items_to_delete_resp.get("data", {}).get("value", [])
        if not items: return {"status": "success", "message": f"No se encontró {log_action_detail} para eliminar."}
        deleted_count = 0; errors_on_delete = []
        for item in items:
            item_id = item.get("id")
            if item_id:
                del_params = {"site_id": target_site_id, "lista_id_o_nombre": MEMORIA_LIST_NAME_FROM_SETTINGS, "item_id": item_id}
                del_response = delete_list_item(client, del_params)
                if del_response.get("status") == "success": deleted_count += 1
                else: errors_on_delete.append(del_response.get("details", f"Error borrando item {item_id}"))
        if errors_on_delete: return {"status": "partial_error", "message": f"{deleted_count} items de {log_action_detail} borrados, con errores.", "details": errors_on_delete}
        return {"status": "success", "message": f"Memoria para {log_action_detail} eliminada. {deleted_count} items borrados."}
    except Exception as e: return _handle_graph_api_error(e, "memory_delete", params)

def memory_list_keys(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Dict[str, Any]:
    session_id: Optional[str] = params.get("session_id")
    if not session_id: return _handle_graph_api_error(ValueError("'session_id' requerido."), "memory_list_keys", params)
    try:
        target_site_id = _obtener_site_id_sp(client, params)
        list_params = {"site_id": target_site_id, "lista_id_o_nombre": MEMORIA_LIST_NAME_FROM_SETTINGS, "filter_query": f"fields/SessionID eq '{session_id}'", "select": "fields/Clave", "max_items_total": None }
        items_response = list_list_items(client, list_params)
        if items_response.get("status") != "success": return items_response
        keys = list(set(item.get("fields", {}).get("Clave") for item in items_response.get("data", {}).get("value", []) if item.get("fields", {}).get("Clave")))
        return {"status": "success", "data": keys}
    except Exception as e: return _handle_graph_api_error(e, "memory_list_keys", params)

def memory_export_session(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Union[str, Dict[str, Any]]:
    session_id: Optional[str] = params.get("session_id"); export_format: str = params.get("format", "json").lower()
    if not session_id: return _handle_graph_api_error(ValueError("'session_id' requerido."), "memory_export_session", params)
    if export_format not in ["json", "csv"]: return _handle_graph_api_error(ValueError("Formato debe ser 'json' o 'csv'."), "memory_export_session", params)
    export_params = {"site_id": params.get("site_id"), "lista_id_o_nombre": MEMORIA_LIST_NAME_FROM_SETTINGS, "format": export_format, "filter_query": f"fields/SessionID eq '{session_id}'", "select_fields": "SessionID,Clave,Valor,Timestamp", "max_items_total": None}
    return sp_export_list_to_format(client, export_params)

def sp_export_list_to_format(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Union[str, Dict[str, Any]]:
    lista_id_o_nombre: Optional[str] = params.get("lista_id_o_nombre"); export_format: str = params.get("format", "json").lower()
    filter_query: Optional[str] = params.get("filter_query"); select_fields: Optional[str] = params.get("select_fields")
    max_items_total: Optional[int] = params.get('max_items_total')
    if not lista_id_o_nombre: return _handle_graph_api_error(ValueError("'lista_id_o_nombre' requerido."), "sp_export_list_to_format", params)
    if export_format not in ["json", "csv"]: return _handle_graph_api_error(ValueError("Formato no válido. Use 'json' o 'csv'."), "sp_export_list_to_format", params)
    try:
        target_site_id = _obtener_site_id_sp(client, params)
        list_items_params: Dict[str, Any] = {"site_id": target_site_id, "lista_id_o_nombre": lista_id_o_nombre, "max_items_total": max_items_total}
        if filter_query: list_items_params["filter_query"] = filter_query
        expand_val = "fields"; select_val = None
        if select_fields: expand_val = f"fields(select={select_fields})"; select_val = "id,@odata.etag"
        list_items_params["expand"] = expand_val
        if select_val : list_items_params["select"] = select_val
        items_response = list_list_items(client, list_items_params)
        if items_response.get("status") != "success": return items_response
        items_data = items_response.get("data", {}).get("value", [])
        processed_items = []
        for item in items_data:
            fields = item.get("fields", {}); fields["_ListItemID_"] = item.get("id"); fields["_ListItemETag_"] = item.get("@odata.etag")
            processed_items.append(fields)
        if not processed_items: return {"status": "success", "data": []} if export_format == "json" else "" # Devuelve dict para JSON, string para CSV
        if export_format == "json": return {"status": "success", "data": processed_items} # Devuelve dict para JSON
        output = StringIO(); all_keys = set()
        for item_fields in processed_items: all_keys.update(item_fields.keys())
        fieldnames_ordered = sorted(list(all_keys))
        if "_ListItemID_" in fieldnames_ordered: fieldnames_ordered.insert(0, fieldnames_ordered.pop(fieldnames_ordered.index("_ListItemID_")))
        if "_ListItemETag_" in fieldnames_ordered and "_ListItemETag_" in all_keys:
            idx = fieldnames_ordered.index("_ListItemETag_");
            if idx != -1 : fieldnames_ordered.insert(1, fieldnames_ordered.pop(idx))
        writer = csv.DictWriter(output, fieldnames=fieldnames_ordered, extrasaction='ignore', quoting=csv.QUOTE_ALL)
        writer.writeheader(); writer.writerows(processed_items)
        return output.getvalue() # Devuelve string CSV
    except Exception as e: return _handle_graph_api_error(e, "sp_export_list_to_format", params)

# --- FIN DEL MÓDULO actions/sharepoint_actions.py ---