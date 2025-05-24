# app/actions/forms_actions.py
import logging
import requests # Para requests.exceptions.HTTPError
from typing import Dict, List, Optional, Any

from app.core.config import settings
from app.shared.helpers.http_client import AuthenticatedHttpClient
# Importar helpers de SharePoint si se van a buscar Forms en sitios de SharePoint
try:
    from app.actions.sharepoint_actions import _obtener_site_id_sp, _get_drive_id
except ImportError:
    logger.error("Error al importar helpers de sharepoint_actions.py. La búsqueda de Forms en SharePoint podría fallar.")
    def _obtener_site_id_sp(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> str:
        raise NotImplementedError("Helper _obtener_site_id_sp no disponible desde forms_actions.")
    def _get_drive_id(client: AuthenticatedHttpClient, site_id: str, drive_id_or_name_input: Optional[str] = None) -> str:
        raise NotImplementedError("Helper _get_drive_id no disponible desde forms_actions.")

logger = logging.getLogger(__name__)

def _handle_forms_api_error(e: Exception, action_name: str, params_for_log: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    log_message = f"Error en Forms Action '{action_name}'"
    if params_for_log:
        log_message += f" con params: {params_for_log}"
    logger.error(f"{log_message}: {type(e).__name__} - {str(e)}", exc_info=True)
    details_str = str(e); status_code_int = 500; graph_error_code = None
    if isinstance(e, requests.exceptions.HTTPError) and e.response is not None:
        status_code_int = e.response.status_code
        try:
            error_data = e.response.json(); error_info = error_data.get("error", {})
            details_str = error_info.get("message", e.response.text); graph_error_code = error_info.get("code")
        except Exception: details_str = e.response.text[:500] if e.response.text else "No response body"
    return {"status": "error", "action": action_name, "message": f"Error en {action_name}: {type(e).__name__}", "http_status": status_code_int, "details": details_str, "graph_error_code": graph_error_code}


def list_forms(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Busca archivos que podrían ser Microsoft Forms (.form) en OneDrive del usuario 
    o en un Drive de SharePoint específico.
    Devuelve metadatos de DriveItem.
    """
    action_name = "forms_list_forms"
    drive_scope: str = params.get('drive_scope', 'me').lower()
    search_text: Optional[str] = params.get('search_query') # Query adicional del usuario
    top: int = min(int(params.get('top', 25)), 200)

    # Query para buscar archivos que son Forms.
    # 'contentType:FormPackage' o buscar por nombre común.
    # El `filetype:form` no siempre es fiable.
    effective_search_query = search_text if search_text else 'contentType:FormPackage OR "Microsoft Form"'
    
    # Parámetros OData para la búsqueda (aplicados a los items devueltos, no al query de texto)
    api_query_odata_params = {
        '$top': top,
        '$select': params.get('select', 'id,name,webUrl,createdDateTime,lastModifiedDateTime,size,parentReference,file,package')
    }

    search_url_path_base: str # Path del drive o carpeta donde buscar, ej /me/drive/root
    log_location_description: str

    try:
        if drive_scope == 'me':
            drive_id_param = params.get("drive_id") # OneDrive específico del usuario
            if drive_id_param:
                search_url_path_base = f"/me/drives/{drive_id_param}/root"
                log_location_description = f"OneDrive del usuario (drive ID: {drive_id_param})"
            else: # OneDrive principal
                search_url_path_base = "/me/drive/root"
                log_location_description = "OneDrive del usuario (drive principal)"
        elif drive_scope == 'site':
            site_identifier = params.get('site_identifier', params.get('site_id')) # Nombre, path o ID del sitio
            drive_identifier = params.get('drive_identifier', params.get('drive_id_or_name')) # Nombre o ID del drive

            if not site_identifier or not drive_identifier:
                return {"status": "error", "action": action_name, "message": "Si 'drive_scope' es 'site', se requieren 'site_identifier' (o 'site_id') y 'drive_identifier' (o 'drive_id_or_name').", "http_status": 400}
            
            # Usar los helpers de sharepoint_actions para resolver IDs
            site_id = _obtener_site_id_sp(client, {"site_identifier": site_identifier})
            drive_id = _get_drive_id(client, site_id, drive_identifier)
            search_url_path_base = f"/sites/{site_id}/drives/{drive_id}/root"
            log_location_description = f"Drive '{drive_id}' en sitio '{site_id}'"
        else:
            return {"status": "error", "action": action_name, "message": "'drive_scope' debe ser 'me' o 'site'.", "http_status": 400}

        # El endpoint para buscar dentro de un drive/carpeta es: ...{base_path}/search(q='{query}')
        # Asegurarse que effective_search_query esté correctamente URL-encoded si se interpola.
        # La librería requests se encarga de esto para los parámetros en `params`, pero no para el path.
        # Aquí q se pasa en el path mismo.
        
        # Construir la URL completa para la búsqueda
        # Es importante que effective_search_query no contenga caracteres que rompan la URL aquí.
        # O se debe URL-encodear. Por simplicidad, se asume que es texto simple.
        # Para mayor robustez, usar urllib.parse.quote_plus(effective_search_query)
        import urllib.parse
        encoded_query = urllib.parse.quote_plus(effective_search_query)
        
        url = f"{settings.GRAPH_API_BASE_URL}{search_url_path_base}/search(q='{encoded_query}')"

        logger.info(f"Buscando Formularios (Query='{effective_search_query}') en {log_location_description}")
        
        response = client.get(url=url, scope=settings.GRAPH_API_DEFAULT_SCOPE, params=api_query_odata_params) # OData params se añaden a la URL
        search_results_data = response.json()
        
        items_found: List[Dict[str, Any]] = []
        raw_value = search_results_data.get('value', [])
        if isinstance(raw_value, list):
            for hit_or_container in raw_value:
                resource_item = None
                if isinstance(hit_or_container, dict) and 'resource' in hit_or_container and isinstance(hit_or_container['resource'], dict):
                    resource_item = hit_or_container['resource']
                elif isinstance(hit_or_container, dict) and 'id' in hit_or_container and 'name' in hit_or_container : # Formato directo de DriveItem
                    resource_item = hit_or_container
                
                if resource_item and (resource_item.get("package", {}).get("type") == "Form" or \
                                     resource_item.get("file", {}).get("mimeType") == "application/vnd.ms-form" or \
                                     ".form" in resource_item.get("name", "").lower()):
                    items_found.append(resource_item)

        logger.info(f"Se encontraron {len(items_found)} archivos que podrían ser Formularios en {log_location_description}.")
        return {"status": "success", "data": items_found, "total_retrieved": len(items_found)}

    except ValueError as ve: # Errores de _obtener_site_id_sp o _get_drive_id
         return {"status": "error", "action": action_name, "message": f"Error de configuración para búsqueda de Forms: {ve}", "http_status": 400}
    except NotImplementedError as nie:
        return {"status": "error", "action": action_name, "message": f"Dependencia no implementada: {nie}", "http_status": 501}
    except Exception as e:
        return _handle_forms_api_error(e, action_name, params)


def get_form(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Obtiene los metadatos de un DriveItem que se presume es un Microsoft Form.
    Requiere 'form_item_id' (ID del DriveItem), 'drive_id'.
    Opcional: 'site_id' (si el Drive es de SharePoint).
    """
    action_name = "forms_get_form"
    form_item_id: Optional[str] = params.get("form_item_id")
    drive_id: Optional[str] = params.get("drive_id")
    site_id: Optional[str] = params.get("site_id") # ID del sitio de SharePoint
    select_fields: str = params.get("select", "id,name,webUrl,createdDateTime,lastModifiedDateTime,size,parentReference,file,package")

    if not form_item_id or not drive_id:
        return {"status": "error", "action": action_name, "message": "'form_item_id' y 'drive_id' son requeridos.", "http_status": 400}

    url: str
    log_target: str
    if site_id:
        url = f"{settings.GRAPH_API_BASE_URL}/sites/{site_id}/drives/{drive_id}/items/{form_item_id}"
        log_target = f"item '{form_item_id}' en drive '{drive_id}' del sitio '{site_id}'"
    else: # Asumir OneDrive del usuario
        url = f"{settings.GRAPH_API_BASE_URL}/me/drives/{drive_id}/items/{form_item_id}"
        log_target = f"item '{form_item_id}' en drive '{drive_id}' del usuario (/me/drives/...)"
        
    api_query_odata_params = {"$select": select_fields}

    logger.info(f"Obteniendo metadatos del archivo de Formulario: {log_target}")
    try:
        response = client.get(url=url, scope=settings.GRAPH_API_DEFAULT_SCOPE, params=api_query_odata_params)
        form_file_metadata = response.json()
        
        is_confirmed_form = False
        if form_file_metadata.get("package", {}).get("type") == "Form":
            is_confirmed_form = True
            logger.info(f"Metadatos del Formulario '{form_item_id}' obtenidos. Confirmado como paquete tipo Form.")
        elif ".form" in form_file_metadata.get("name", "").lower():
            is_confirmed_form = True
            logger.info(f"Metadatos del archivo '{form_item_id}' obtenidos. Nombre sugiere que es un Form.")
        elif form_file_metadata.get("file"):
             logger.info(f"Metadatos del archivo '{form_item_id}' obtenidos. Verificar si es un Form por su contenido o nombre.")
        else:
            logger.warning(f"Item '{form_item_id}' obtenido, pero no parece ser un archivo (sin faceta 'file' o 'package').")

        return {"status": "success", "data": form_file_metadata, "is_confirmed_form_file": is_confirmed_form, "message": "Metadatos del archivo obtenidos."}
    except Exception as e:
        return _handle_forms_api_error(e, action_name, params)


def get_form_responses(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Esta función sigue siendo un placeholder funcional que devuelve 'not_supported'.
    La obtención directa de respuestas de Forms vía Graph API no es una funcionalidad estándar.
    Se recomienda usar Power Automate.
    """
    action_name = "forms_get_form_responses"
    form_id_param: Optional[str] = params.get("form_id") # ID del Form (no el DriveItem ID)
    
    message = (
        f"La obtención de respuestas para Microsoft Forms (ID: {form_id_param or 'desconocido'}) "
        "directamente a través de Microsoft Graph API no está soportada de forma estándar y fiable."
    )
    details = (
        "Solución Recomendada: Utilizar Power Automate.\n"
        "1. Crear un flujo en Power Automate que se active con 'Cuando se envía una respuesta nueva' (desde Microsoft Forms).\n"
        "2. Usar la acción 'Obtener los detalles de la respuesta' (de Microsoft Forms).\n"
        "3. Enviar los detalles de la respuesta (como JSON) mediante una acción 'HTTP POST' a esta aplicación FastAPI, "
        "invocando una acción personalizada diseñada para procesar dichos datos (ej. 'procesar_respuesta_form_powerautomate').\n"
        "Esta función actual solo devuelve metadatos del archivo del formulario si se encuentra en OneDrive/SharePoint, no sus respuestas."
    )
    logger.warning(f"Acción '{action_name}' llamada. {message}")
    return {
        "status": "not_supported",
        "action": action_name,
        "message": message,
        "details": details,
        "http_status": 501 # Not Implemented
    }