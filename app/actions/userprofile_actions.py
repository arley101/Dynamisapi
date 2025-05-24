# app/actions/userprofile_actions.py
import logging
import requests # Para requests.exceptions.HTTPError y json.JSONDecodeError
import json # Para el helper de error
from typing import Dict, List, Optional, Any, Union

# Importar la configuración y el cliente HTTP autenticado
from app.core.config import settings
from app.shared.helpers.http_client import AuthenticatedHttpClient

logger = logging.getLogger(__name__)

def _handle_userprofile_api_error(e: Exception, action_name: str) -> Dict[str, Any]: # Helper de error
    logger.error(f"Error en UserProfile action '{action_name}': {type(e).__name__} - {e}", exc_info=True)
    details = str(e)
    status_code = 500
    if isinstance(e, requests.exceptions.HTTPError) and e.response is not None:
        status_code = e.response.status_code
        try:
            error_data = e.response.json().get("error", {})
            details = error_data.get("message", e.response.text)
        except json.JSONDecodeError: # Corregido
            details = e.response.text
    return {"status": "error", "message": f"Error en {action_name}", "details": details, "http_status": status_code}


# ---- FUNCIONES DE ACCIÓN PARA PERFIL DE USUARIO (/me) ----
# Nombres de función ajustados para coincidir con mapping_actions.py

def profile_get_my_profile(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Dict[str, Any]:
    select_fields: Optional[str] = params.get('select')
    url = f"{settings.GRAPH_API_BASE_URL}/me"
    query_api_params = {'$select': select_fields} if select_fields else None
    logger.info(f"Obteniendo perfil de /me (Select: {select_fields or 'default'})")
    # Scope User.Read es suficiente
    user_read_scope = getattr(settings, 'GRAPH_SCOPE_USER_READ', settings.GRAPH_API_DEFAULT_SCOPE)
    try:
        response = client.get(url, scope=user_read_scope, params=query_api_params)
        profile_data = response.json()
        return {"status": "success", "data": profile_data}
    except Exception as e:
        return _handle_userprofile_api_error(e, "profile_get_my_profile")

def profile_get_my_manager(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Dict[str, Any]:
    select_fields: Optional[str] = params.get('select')
    url = f"{settings.GRAPH_API_BASE_URL}/me/manager"
    query_api_params = {'$select': select_fields} if select_fields else None
    logger.info(f"Obteniendo manager de /me (Select: {select_fields or 'default'})")
    # Scope User.Read.All o Directory.Read.All para leer manager
    manager_read_scope = getattr(settings, 'GRAPH_SCOPE_USER_READ_ALL', settings.GRAPH_API_DEFAULT_SCOPE)
    try:
        response = client.get(url, scope=manager_read_scope, params=query_api_params)
        manager_data = response.json()
        return {"status": "success", "data": manager_data}
    except requests.exceptions.HTTPError as http_err: # Captura específica para 404
        if http_err.response is not None and http_err.response.status_code == 404:
            logger.info("No se encontró manager para el usuario.")
            return {"status": "success", "data": None, "message": "No se encontró manager.", "http_status": 404}
        return _handle_userprofile_api_error(http_err, "profile_get_my_manager")
    except Exception as e:
        return _handle_userprofile_api_error(e, "profile_get_my_manager")

def profile_get_my_direct_reports(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Dict[str, Any]:
    select_fields: Optional[str] = params.get('select')
    # MAX_GRAPH_TOP_VALUE_PAGING_USERS no existe en settings, usar un valor o definirlo
    max_graph_top_users = getattr(settings, 'MAX_GRAPH_TOP_VALUE_PAGING_USERS', 999)
    top: int = min(int(params.get('top', 25)), max_graph_top_users)


    url_base = f"{settings.GRAPH_API_BASE_URL}/me/directReports"
    query_api_params_initial: Dict[str, Any] = {'$top': top}
    if select_fields: query_api_params_initial['$select'] = select_fields

    all_reports: List[Dict[str, Any]] = []
    current_url: Optional[str] = url_base
    page_count = 0
    max_internal_pages = getattr(settings, 'MAX_PAGING_PAGES', 5) # Límite de páginas para esta función específica
    logger.info(f"Listando reportes directos de /me (Select: {select_fields or 'default'}, Top: {top})")
    user_read_all_scope = getattr(settings, 'GRAPH_SCOPE_USER_READ_ALL', settings.GRAPH_API_DEFAULT_SCOPE)
    try:
        while current_url and page_count < max_internal_pages:
            page_count += 1
            current_query_params_for_call = query_api_params_initial if current_url == url_base and page_count == 1 else None
            response = client.get(url=current_url, scope=user_read_all_scope, params=current_query_params_for_call)
            response_data = response.json()
            if 'value' in response_data:
                items_in_page = response_data.get('value', [])
                if not isinstance(items_in_page, list): break
                all_reports.extend(items_in_page)
                current_url = response_data.get('@odata.nextLink')
                if not current_url or len(all_reports) >= top: # Comparar con top, no con max_items_total si no se define
                    break
            else: break # No 'value' key
        if page_count >= max_internal_pages and current_url:
            logger.warning(f"Límite interno de {max_internal_pages} páginas alcanzado listando reportes directos.")
        logger.info(f"Total reportes directos recuperados: {len(all_reports)}")
        return {"status": "success", "data": all_reports[:top], "total_retrieved": len(all_reports), "pages_processed": page_count}
    except Exception as e:
        return _handle_userprofile_api_error(e, "profile_get_my_direct_reports")

def profile_get_my_photo(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Union[bytes, Dict[str, Any]]:
    size: Optional[str] = params.get('size')
    endpoint = "/me/photo/$value" if not size else f"/me/photos/{size}/$value"
    url = f"{settings.GRAPH_API_BASE_URL}{endpoint}"
    logger.info(f"Obteniendo foto de perfil de /me (Tamaño: {size or 'default'}) desde {url.replace(str(settings.GRAPH_API_BASE_URL), '')}")
    # User.ReadBasic.All o User.Read son suficientes
    user_read_basic_scope = getattr(settings, 'GRAPH_SCOPE_USER_READBASIC_ALL', settings.GRAPH_API_DEFAULT_SCOPE)
    try:
        response = client.get(url, scope=user_read_basic_scope, stream=True)
        photo_bytes = response.content
        if photo_bytes:
            logger.info(f"Foto de perfil obtenida ({len(photo_bytes)} bytes).")
            return photo_bytes
        else:
            logger.info("No se encontró contenido en la respuesta de la foto de perfil.")
            # Si la API devuelve 200 OK con cuerpo vacío, o 204.
            # Graph normalmente devuelve 404 si no hay foto.
            return {"status": "success", "data": None, "message": "No se encontró contenido en la foto de perfil (respuesta vacía)."}
    except requests.exceptions.HTTPError as http_err: # Específico para 404
        if http_err.response is not None and http_err.response.status_code == 404:
            return {"status": "success", "data": None, "message": "El usuario no tiene foto de perfil o el tamaño no existe.", "http_status": 404}
        return _handle_userprofile_api_error(http_err, "profile_get_my_photo") # Otro error HTTP
    except Exception as e: # Otros errores
        return _handle_userprofile_api_error(e, "profile_get_my_photo")

def profile_update_my_profile(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Dict[str, Any]:
    # Esta función estaba como placeholder en el original. Manteniendo placeholder.
    update_payload: Optional[Dict[str, Any]] = params.get("update_payload")
    if not update_payload or not isinstance(update_payload, dict):
        return _handle_userprofile_api_error(ValueError("'update_payload' (dict) es requerido."), "profile_update_my_profile")

    action_name_log = "profile_update_my_profile"
    logger.warning(f"Acción '{action_name_log}' del servicio '{__name__}' no implementada todavía.")
    return {
        "status": "not_implemented",
        "message": f"Acción '{action_name_log}' no implementada todavía.",
        "service_module": __name__,
        "http_status": 501
    }

# --- FIN DEL MÓDULO actions/userprofile_actions.py ---docker build -t elitedynamicsapi_image .