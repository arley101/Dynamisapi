# app/actions/users_actions.py
import logging
import requests # Para requests.exceptions.HTTPError
import json # Para el helper de error
from typing import Dict, List, Optional, Any, Union

# Importar la configuración y el cliente HTTP autenticado
from app.core.config import settings
from app.shared.helpers.http_client import AuthenticatedHttpClient

logger = logging.getLogger(__name__)

# Helper de error (puede ser un helper común si se repite mucho)
def _handle_users_directory_api_error(e: Exception, action_name: str) -> Dict[str, Any]:
    logger.error(f"Error en Users/Directory action '{action_name}': {type(e).__name__} - {e}", exc_info=True)
    details = str(e)
    status_code = 500
    graph_error_code = None # Específico para errores de Graph
    if isinstance(e, requests.exceptions.HTTPError) and e.response is not None:
        status_code = e.response.status_code
        try:
            error_data = e.response.json()
            error_info = error_data.get("error", {})
            details = error_info.get("message", e.response.text)
            graph_error_code = error_info.get("code")
        except json.JSONDecodeError:
            details = e.response.text
    return {
        "status": "error", "action": action_name,
        "message": f"Error en {action_name}", "details": details,
        "http_status": status_code, "graph_error_code": graph_error_code
    }

# Helper de paginación (adaptado de otros módulos)
def _directory_paged_request(
    client: AuthenticatedHttpClient, url_base: str, scope: List[str],
    params_input: Dict[str, Any], query_api_params_initial: Dict[str, Any],
    max_items_total: int, action_name_for_log: str,
    custom_headers_first_call: Optional[Dict[str, str]] = None
) -> Dict[str, Any]:
    all_items: List[Dict[str, Any]] = []
    current_url: Optional[str] = url_base
    page_count = 0
    max_pages = getattr(settings, 'MAX_PAGING_PAGES', 20)
    top_value = query_api_params_initial.get('$top', getattr(settings, 'DEFAULT_PAGING_SIZE', 50))

    logger.info(f"Iniciando solicitud paginada para '{action_name_for_log}' desde '{url_base.split('?')[0]}...'. Max total: {max_items_total}, por pág: {top_value}")
    try:
        while current_url and len(all_items) < max_items_total and page_count < max_pages:
            page_count += 1
            is_first_call = (page_count == 1 and current_url == url_base)
            current_call_params = query_api_params_initial if is_first_call else None
            current_headers = custom_headers_first_call if is_first_call else None
            
            response = client.get(url=current_url, scope=scope, params=current_call_params, headers=current_headers)
            response_data = response.json()
            page_items = response_data.get('value', [])
            if not isinstance(page_items, list): break
            for item in page_items:
                if len(all_items) < max_items_total: all_items.append(item)
                else: break
            current_url = response_data.get('@odata.nextLink')
            if not current_url or len(all_items) >= max_items_total: break
        logger.info(f"'{action_name_for_log}' recuperó {len(all_items)} items en {page_count} páginas.")
        return {"status": "success", "data": all_items, "total_retrieved": len(all_items), "pages_processed": page_count}
    except Exception as e:
        return _handle_users_directory_api_error(e, action_name_for_log)


# ============================================
# ==== FUNCIONES DE ACCIÓN PARA USUARIOS (Directory) ====
# ============================================
def list_users(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Dict[str, Any]:
    url = f"{settings.GRAPH_API_BASE_URL}/users"
    api_query_params: Dict[str, Any] = {}
    api_query_params['$select'] = params.get('select', "id,displayName,userPrincipalName,mail,jobTitle,officeLocation,accountEnabled")
    if params.get('filter'): api_query_params['$filter'] = params['filter']
    if params.get('search'): api_query_params['$search'] = params['search'] # Requiere ConsistencyLevel y $count
    if params.get('orderby'): api_query_params['$orderby'] = params['orderby']
    
    max_graph_top_users = getattr(settings, 'MAX_GRAPH_TOP_VALUE_PAGING_USERS', 999)
    top_per_page: int = min(int(params.get('top_per_page', 25)), max_graph_top_users)
    max_items_total: int = int(params.get('max_items_total', 100))
    api_query_params['$top'] = top_per_page
    custom_headers = {}
    if params.get('search'):
        api_query_params['$count'] = "true"
        custom_headers['ConsistencyLevel'] = 'eventual'

    scope_to_use = getattr(settings, 'GRAPH_SCOPE_USER_READ_ALL', settings.GRAPH_API_DEFAULT_SCOPE)
    return _directory_paged_request(client, url, scope_to_use, params, api_query_params, max_items_total, "list_users", custom_headers_first_call=custom_headers)

def get_user(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Dict[str, Any]:
    user_id_or_upn: Optional[str] = params.get("user_id") or params.get("user_principal_name")
    if not user_id_or_upn:
        return {"status": "error", "message": "Se requiere 'user_id' o 'user_principal_name'.", "http_status": 400}
    url = f"{settings.GRAPH_API_BASE_URL}/users/{user_id_or_upn}"
    api_query_params: Dict[str, Any] = {}
    api_query_params['$select'] = params.get('select', "id,displayName,userPrincipalName,mail,jobTitle,officeLocation,accountEnabled,businessPhones,mobilePhone,department,employeeId,givenName,surname")
    logger.info(f"Obteniendo usuario '{user_id_or_upn}'")
    scope_to_use = getattr(settings, 'GRAPH_SCOPE_USER_READ_ALL', settings.GRAPH_API_DEFAULT_SCOPE)
    try:
        response = client.get(url, scope=scope_to_use, params=api_query_params if api_query_params else None)
        return {"status": "success", "data": response.json()}
    except requests.exceptions.HTTPError as http_err:
        if http_err.response and http_err.response.status_code == 404:
            return {"status": "error", "message": f"Usuario '{user_id_or_upn}' no encontrado.", "http_status": 404, "details": http_err.response.text}
        return _handle_users_directory_api_error(http_err, "get_user")
    except Exception as e:
        return _handle_users_directory_api_error(e, "get_user")

def create_user(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Dict[str, Any]:
    user_payload: Optional[Dict[str, Any]] = params.get("user_payload")
    if not user_payload or not isinstance(user_payload, dict):
        return {"status": "error", "message": "Parámetro 'user_payload' (dict) es requerido.", "http_status": 400}
    required_fields = ["accountEnabled", "displayName", "mailNickname", "userPrincipalName", "passwordProfile"]
    if not all(field in user_payload for field in required_fields):
        missing = [field for field in required_fields if field not in user_payload]
        return {"status": "error", "message": f"Faltan campos requeridos en 'user_payload': {', '.join(missing)}.", "http_status": 400}
    if not isinstance(user_payload.get("passwordProfile"), dict) or "password" not in user_payload["passwordProfile"]:
        return {"status": "error", "message": "'passwordProfile' debe ser un dict y contener 'password'.", "http_status": 400}
    url = f"{settings.GRAPH_API_BASE_URL}/users"
    logger.info(f"Creando nuevo usuario con UPN: {user_payload.get('userPrincipalName', 'N/A')}")
    scope_to_use = getattr(settings, 'GRAPH_SCOPE_USER_READWRITE_ALL', settings.GRAPH_API_DEFAULT_SCOPE)
    try:
        response = client.post(url, scope=scope_to_use, json_data=user_payload)
        return {"status": "success", "data": response.json(), "message": "Usuario creado."}
    except Exception as e:
        return _handle_users_directory_api_error(e, "create_user")

def update_user(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Dict[str, Any]:
    user_id_or_upn: Optional[str] = params.get("user_id") or params.get("user_principal_name")
    update_payload: Optional[Dict[str, Any]] = params.get("update_payload")
    if not user_id_or_upn:
        return {"status": "error", "message": "Se requiere 'user_id' o 'user_principal_name'.", "http_status": 400}
    if not update_payload or not isinstance(update_payload, dict) or not update_payload:
        return {"status": "error", "message": "Parámetro 'update_payload' (dict no vacío) es requerido.", "http_status": 400}
    url = f"{settings.GRAPH_API_BASE_URL}/users/{user_id_or_upn}"
    logger.info(f"Actualizando usuario '{user_id_or_upn}' con payload: {update_payload}")
    scope_to_use = getattr(settings, 'GRAPH_SCOPE_USER_READWRITE_ALL', settings.GRAPH_API_DEFAULT_SCOPE)
    try:
        response = client.patch(url, scope=scope_to_use, json_data=update_payload)
        if response.status_code == 204:
            logger.info(f"Usuario '{user_id_or_upn}' actualizado (204).")
            get_user_params = {"user_id_or_upn": user_id_or_upn} # Corregir param name
            if params.get("select_after_update"): get_user_params["select"] = params["select_after_update"]
            updated_user_info = get_user(client, get_user_params)
            if updated_user_info["status"] == "success":
                return {"status": "success", "message": "Usuario actualizado.", "data": updated_user_info["data"]}
            return {"status": "success", "message": "Usuario actualizado (204), pero falló la re-obtención.", "data": {"id": user_id_or_upn}}
        else:
            logger.warning(f"Usuario '{user_id_or_upn}' actualizado con status {response.status_code}.")
            return {"status": "success", "message": f"Usuario actualizado con status {response.status_code}.", "data": response.json() if response.content else None, "http_status": response.status_code}
    except Exception as e:
        return _handle_users_directory_api_error(e, "update_user")

def delete_user(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Dict[str, Any]:
    user_id_or_upn: Optional[str] = params.get("user_id") or params.get("user_principal_name")
    if not user_id_or_upn:
        return {"status": "error", "message": "Se requiere 'user_id' o 'user_principal_name'.", "http_status": 400}
    url = f"{settings.GRAPH_API_BASE_URL}/users/{user_id_or_upn}"
    logger.info(f"Eliminando usuario '{user_id_or_upn}'")
    scope_to_use = getattr(settings, 'GRAPH_SCOPE_USER_READWRITE_ALL', settings.GRAPH_API_DEFAULT_SCOPE)
    try:
        response = client.delete(url, scope=scope_to_use)
        return {"status": "success", "message": f"Usuario '{user_id_or_upn}' eliminado.", "http_status": response.status_code}
    except Exception as e:
        return _handle_users_directory_api_error(e, "delete_user")

# ============================================
# ==== FUNCIONES DE ACCIÓN PARA GRUPOS (Directory) ====
# ============================================
def list_groups(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Dict[str, Any]:
    url = f"{settings.GRAPH_API_BASE_URL}/groups"
    api_query_params: Dict[str, Any] = {}
    api_query_params['$select'] = params.get('select', "id,displayName,description,mailEnabled,securityEnabled,groupTypes,visibility")
    if params.get('filter'): api_query_params['$filter'] = params['filter']
    if params.get('search'): api_query_params['$search'] = params['search']
    if params.get('orderby'): api_query_params['$orderby'] = params['orderby']
    max_graph_top = getattr(settings, 'MAX_GRAPH_TOP_VALUE_PAGING', 100)
    top_per_page: int = min(int(params.get('top_per_page', 25)), max_graph_top)
    max_items_total: int = int(params.get('max_items_total', 100))
    api_query_params['$top'] = top_per_page
    custom_headers = {}
    if params.get('search'):
        api_query_params['$count'] = "true"
        custom_headers['ConsistencyLevel'] = 'eventual'
    scope_to_use = getattr(settings, 'GRAPH_SCOPE_GROUP_READ_ALL', settings.GRAPH_API_DEFAULT_SCOPE)
    return _directory_paged_request(client, url, scope_to_use, params, api_query_params, max_items_total, "list_groups", custom_headers_first_call=custom_headers)

def get_group(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Dict[str, Any]:
    group_id: Optional[str] = params.get("group_id")
    if not group_id:
        return {"status": "error", "message": "Se requiere 'group_id'.", "http_status": 400}
    url = f"{settings.GRAPH_API_BASE_URL}/groups/{group_id}"
    api_query_params: Dict[str, Any] = {}
    api_query_params['$select'] = params.get('select', "id,displayName,description,mailEnabled,securityEnabled,groupTypes,visibility,createdDateTime")
    logger.info(f"Obteniendo grupo '{group_id}'")
    scope_to_use = getattr(settings, 'GRAPH_SCOPE_GROUP_READ_ALL', settings.GRAPH_API_DEFAULT_SCOPE)
    try:
        response = client.get(url, scope=scope_to_use, params=api_query_params if api_query_params else None)
        return {"status": "success", "data": response.json()}
    except requests.exceptions.HTTPError as http_err:
        if http_err.response and http_err.response.status_code == 404:
            return {"status": "error", "message": f"Grupo '{group_id}' no encontrado.", "http_status": 404, "details": http_err.response.text}
        return _handle_users_directory_api_error(http_err, "get_group")
    except Exception as e:
        return _handle_users_directory_api_error(e, "get_group")

def list_group_members(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Dict[str, Any]:
    group_id: Optional[str] = params.get("group_id")
    if not group_id:
        return {"status": "error", "message": "Se requiere 'group_id'.", "http_status": 400}
    member_type_filter: Optional[str] = params.get("member_type")
    url_segment = "/members"
    if member_type_filter:
        if member_type_filter.lower() == "user": url_segment = "/members/microsoft.graph.user"
        elif member_type_filter.lower() == "group": url_segment = "/members/microsoft.graph.group"
    url_base = f"{settings.GRAPH_API_BASE_URL}/groups/{group_id}{url_segment}"
    api_query_params: Dict[str, Any] = {}
    api_query_params['$select'] = params.get('select', "id,displayName,userPrincipalName,mail")
    if params.get('filter'): api_query_params['$filter'] = params['filter']
    max_top_members = getattr(settings, 'MAX_GRAPH_TOP_VALUE_PAGING_GROUP_MEMBERS', 999)
    top_per_page: int = min(int(params.get('top_per_page', 25)), max_top_members)
    max_items_total: int = int(params.get('max_items_total', 100))
    api_query_params['$top'] = top_per_page
    scope_to_use = getattr(settings, 'GRAPH_SCOPE_GROUPMEMBER_READ_ALL', settings.GRAPH_API_DEFAULT_SCOPE)
    return _directory_paged_request(client, url_base, scope_to_use, params, api_query_params, max_items_total, f"list_group_members for group {group_id}")

def add_group_member(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Dict[str, Any]:
    group_id: Optional[str] = params.get("group_id")
    member_id: Optional[str] = params.get("member_id")
    if not group_id or not member_id:
        return {"status": "error", "message": "Se requieren 'group_id' y 'member_id'.", "http_status": 400}
    url = f"{settings.GRAPH_API_BASE_URL}/groups/{group_id}/members/$ref"
    payload = {"@odata.id": f"{settings.GRAPH_API_BASE_URL}/directoryObjects/{member_id}"}
    logger.info(f"Añadiendo miembro '{member_id}' al grupo '{group_id}'")
    scope_to_use = getattr(settings, 'GRAPH_SCOPE_GROUPMEMBER_READWRITE_ALL', settings.GRAPH_API_DEFAULT_SCOPE)
    try:
        response = client.post(url, scope=scope_to_use, json_data=payload)
        return {"status": "success", "message": f"Miembro '{member_id}' añadido al grupo '{group_id}'.", "http_status": response.status_code}
    except Exception as e:
        return _handle_users_directory_api_error(e, "add_group_member")

def remove_group_member(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Dict[str, Any]:
    group_id: Optional[str] = params.get("group_id")
    member_id: Optional[str] = params.get("member_id")
    if not group_id or not member_id:
        return {"status": "error", "message": "Se requieren 'group_id' y 'member_id'.", "http_status": 400}
    url = f"{settings.GRAPH_API_BASE_URL}/groups/{group_id}/members/{member_id}/$ref"
    logger.info(f"Eliminando miembro '{member_id}' del grupo '{group_id}'")
    scope_to_use = getattr(settings, 'GRAPH_SCOPE_GROUPMEMBER_READWRITE_ALL', settings.GRAPH_API_DEFAULT_SCOPE)
    try:
        response = client.delete(url, scope=scope_to_use)
        return {"status": "success", "message": f"Miembro '{member_id}' eliminado del grupo '{group_id}'.", "http_status": response.status_code}
    except Exception as e:
        return _handle_users_directory_api_error(e, "remove_group_member")

def check_group_membership(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Dict[str, Any]:
    user_id: Optional[str] = params.get("user_id")
    group_ids_input: Optional[Union[str, List[str]]] = params.get("group_ids")
    if not user_id or not group_ids_input:
        return {"status": "error", "message": "Se requieren 'user_id' y 'group_ids' (string o lista).", "http_status": 400}
    url = f"{settings.GRAPH_API_BASE_URL}/users/{user_id}/checkMemberGroups"
    group_ids_list: List[str]
    if isinstance(group_ids_input, str): group_ids_list = [group_ids_input]
    elif isinstance(group_ids_input, list): group_ids_list = group_ids_input
    else: return {"status": "error", "message": "'group_ids' debe ser string o lista.", "http_status": 400}
    payload = {"groupIds": group_ids_list}
    logger.info(f"Verificando pertenencia del usuario '{user_id}' a los grupos: {payload['groupIds']}")
    scope_to_use = getattr(settings, 'GRAPH_SCOPE_GROUPMEMBER_READ_ALL', settings.GRAPH_API_DEFAULT_SCOPE)
    try:
        response = client.post(url, scope=scope_to_use, json_data=payload)
        member_of_group_ids = response.json().get("value", [])
        results: Dict[str, bool] = {gid: (gid in member_of_group_ids) for gid in payload["groupIds"]}
        return {"status": "success", "data": results, "message": "Verificación de membresía completada."}
    except Exception as e:
        return _handle_users_directory_api_error(e, "check_group_membership")

# --- FIN DEL MÓDULO actions/users_actions.py ---