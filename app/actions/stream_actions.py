# app/actions/stream_actions.py
import logging
import requests # Para requests.exceptions.HTTPError
from typing import Dict, List, Optional, Any

from app.core.config import settings
from app.shared.helpers.http_client import AuthenticatedHttpClient
# Importar helpers de sharepoint_actions para resolver site_id y drive_id si es necesario
# Esto crea una dependencia entre módulos de acción, lo cual es aceptable si las funciones son helpers genéricos.
# Asegúrate de que sharepoint_actions.py esté implementado y estas funciones existan y sean robustas.
try:
    from app.actions.sharepoint_actions import _obtener_site_id_sp, _get_drive_id, _get_item_id_from_path_if_needed_sp
except ImportError:
    logger.error("Error al importar helpers de sharepoint_actions.py. Las funciones de Stream que dependen de ellos podrían fallar.")
    # Definir placeholders para que el módulo cargue, pero las funciones fallarán si se llaman.
    def _obtener_site_id_sp(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> str:
        raise NotImplementedError("Helper _obtener_site_id_sp no disponible desde stream_actions.")
    def _get_drive_id(client: AuthenticatedHttpClient, site_id: str, drive_id_or_name_input: Optional[str] = None) -> str:
        raise NotImplementedError("Helper _get_drive_id no disponible desde stream_actions.")
    def _get_item_id_from_path_if_needed_sp(client: AuthenticatedHttpClient, item_path_or_id: str, site_id: str, drive_id: str, params_for_metadata: Optional[Dict[str, Any]] = None) -> str:
        raise NotImplementedError("Helper _get_item_id_from_path_if_needed_sp no disponible desde stream_actions.")


logger = logging.getLogger(__name__)

# Timeout más largo para búsquedas o descargas de video si es necesario
VIDEO_ACTION_TIMEOUT = max(settings.DEFAULT_API_TIMEOUT, 180) # Ej. 3 minutos

def _handle_stream_api_error(e: Exception, action_name: str, params_for_log: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    log_message = f"Error en Stream Action '{action_name}'"
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


def listar_videos(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Busca archivos de video (.mp4, .mov, etc.) en OneDrive del usuario o en un Drive de SharePoint.
    Devuelve metadatos de archivo (DriveItems) que tienen la faceta 'video'.
    """
    action_name = "stream_listar_videos"
    drive_scope: str = params.get('drive_scope', 'me').lower() # 'me' (OneDrive) o 'site' (SharePoint)
    search_folder_path: str = params.get('search_folder_path', '/') # Ruta de la carpeta donde buscar, ej: "/Videos"
    user_query: Optional[str] = params.get('query') # Query adicional del usuario para filtrar por nombre, etc.
    top: int = min(int(params.get('top', 25)), 200) # Límite para la API de búsqueda de Graph ($search)

    # Construir query de búsqueda base para tipos comunes de video y la faceta 'video'
    # Esta es una búsqueda de DriveItems, no específica de la antigua API de Stream.
    video_file_types_filter = "filetype:mp4 OR filetype:mov OR filetype:wmv OR filetype:avi OR filetype:mkv OR filetype:webm OR filetype:mpeg"
    # Es mejor buscar por la faceta 'video' si está disponible y es confiable
    # El query para search es texto plano.
    final_search_query = f"({video_file_types_filter})"
    if user_query:
        final_search_query = f"({user_query}) AND {final_search_query}"

    # El query para el endpoint /search es `q`. No se pueden combinar $filter y $search directamente.
    # Para filtrar por faceta 'video' existente, se puede hacer post-procesamiento o un query más complejo si la API lo soporta.
    # Por ahora, filtramos por tipo de archivo y el usuario puede añadir palabras clave.
    # El select incluirá la faceta 'video' para poder filtrar en el cliente si es necesario.

    api_query_odata_params = {
        '$top': top,
        '$select': params.get('select', 'id,name,webUrl,video,size,file,createdDateTime,lastModifiedDateTime,parentReference')
    }

    search_base_url_segment: str
    log_location_description: str
    effective_site_id: Optional[str] = None
    effective_drive_id: Optional[str] = None

    try:
        if drive_scope == 'me':
            # OneDrive del usuario actual. El drive_id puede ser el principal o uno específico del usuario.
            drive_id_param = params.get("drive_id")
            if drive_id_param:
                search_base_url_segment = f"/me/drives/{drive_id_param}"
                effective_drive_id = drive_id_param
                log_location_description = f"Drive específico del usuario '{drive_id_param}'"
            else: # Drive principal del usuario
                search_base_url_segment = "/me/drive"
                log_location_description = "OneDrive del usuario (drive principal)"
            
            if search_folder_path and search_folder_path != '/':
                search_base_url_segment += f"/root:{search_folder_path.strip('/')}:"
            else:
                search_base_url_segment += "/root"

        elif drive_scope == 'site':
            # Drive de un sitio de SharePoint
            # Usar los helpers de sharepoint_actions para obtener site_id y drive_id
            # Esto asume que los params para _obtener_site_id_sp y _get_drive_id son pasados
            # (ej. 'site_identifier' para el nombre/path del sitio, 'drive_id_or_name' para el nombre/id del drive)
            effective_site_id = _obtener_site_id_sp(client, params) # Puede levantar ValueError
            effective_drive_id = _get_drive_id(client, effective_site_id, params.get("drive_id_or_name")) # Puede levantar ValueError

            search_base_url_segment = f"/sites/{effective_site_id}/drives/{effective_drive_id}"
            log_location_description = f"Drive '{effective_drive_id}' en sitio '{effective_site_id}'"
            if search_folder_path and search_folder_path != '/':
                 search_base_url_segment += f"/root:{search_folder_path.strip('/')}:"
            else:
                search_base_url_segment += "/root"
        else:
            return {"status": "error", "action": action_name, "message": "'drive_scope' debe ser 'me' o 'site'.", "http_status": 400}
        
        # Endpoint de búsqueda: /{drive-base}/search(q='{queryText}')
        search_api_url = f"{settings.GRAPH_API_BASE_URL}{search_base_url_segment}/search(q='{final_search_query}')"

        logger.info(f"Buscando videos (Query='{final_search_query}') en {log_location_description}")
        
        # La paginación con /search usa @odata.nextLink. Hacemos una llamada inicial.
        # El cliente _sp_paged_request de SharePoint podría adaptarse si la estructura de respuesta es similar.
        # Por ahora, una implementación directa para la primera página.
        response = client.get(url=search_api_url, scope=settings.GRAPH_API_DEFAULT_SCOPE, params=api_query_odata_params, timeout=VIDEO_ACTION_TIMEOUT)
        search_results = response.json()
        
        items_found: List[Dict[str, Any]] = []
        # La respuesta de /search anida los resultados.
        # El formato es: { "value": [ { "hitsContainers": [ { "hits": [ { "resource": {DriveItem} } ] } ] } ] }
        # O a veces directamente una lista de DriveItems en 'value' si el search es sobre un item específico.
        raw_value = search_results.get('value', [])
        if isinstance(raw_value, list):
            for hit_or_container in raw_value:
                if isinstance(hit_or_container, dict) and 'resource' in hit_or_container and isinstance(hit_or_container['resource'], dict): # Formato directo de DriveItem
                    if hit_or_container['resource'].get("video"): # Filtrar solo los que tienen la faceta video
                        items_found.append(hit_or_container['resource'])
                elif isinstance(hit_or_container, dict) and 'hitsContainers' in hit_or_container: # Formato anidado de búsqueda
                    for container in hit_or_container.get('hitsContainers', []):
                        for hit in container.get('hits', []):
                            if isinstance(hit, dict) and 'resource' in hit and isinstance(hit['resource'], dict) and hit['resource'].get("video"):
                                items_found.append(hit['resource'])
        
        # Aquí se podría implementar paginación si search_results contiene '@odata.nextLink'

        logger.info(f"Se encontraron {len(items_found)} archivos con faceta de video en {log_location_description}.")
        return {"status": "success", "data": items_found, "total_retrieved": len(items_found)}

    except ValueError as ve: # Errores de _obtener_site_id_sp o _get_drive_id
         return {"status": "error", "action": action_name, "message": f"Error de configuración para búsqueda de videos: {ve}", "http_status": 400}
    except NotImplementedError as nie: # Si los helpers de SP no están disponibles
        return {"status": "error", "action": action_name, "message": f"Dependencia no implementada: {nie}", "http_status": 501}
    except Exception as e:
        return _handle_stream_api_error(e, action_name, params)


def obtener_metadatos_video(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Obtiene los metadatos de un archivo de video (DriveItem), incluyendo la faceta 'video'.
    Requiere 'item_id_or_path'.
    Opcional: 'drive_scope' ('me' o 'site'), 'site_identifier', 'drive_id_or_name'.
    """
    action_name = "stream_obtener_metadatos_video"
    item_id_or_path: Optional[str] = params.get("item_id_or_path")
    drive_scope: str = params.get('drive_scope', 'me').lower()
    
    select_fields: str = params.get('select', "id,name,webUrl,size,createdDateTime,lastModifiedDateTime,file,video,parentReference,@microsoft.graph.downloadUrl")
    # Asegurar que 'video' y '@microsoft.graph.downloadUrl' estén en el select si no se provee uno custom
    if "video" not in select_fields.lower(): select_fields += ",video"
    if "@microsoft.graph.downloadurl" not in select_fields.lower(): select_fields += ",@microsoft.graph.downloadUrl"


    if not item_id_or_path:
        return {"status": "error", "action": action_name, "message": "'item_id_or_path' es requerido.", "http_status": 400}

    item_url_base: str
    log_item_description: str

    try:
        if drive_scope == 'me':
            drive_id_param = params.get("drive_id") # ID específico del drive del usuario
            if drive_id_param:
                base_drive_path = f"/me/drives/{drive_id_param}"
            else: # Drive principal del usuario
                base_drive_path = "/me/drive"
            
            # Resolver path a ID si es necesario para consistencia, o construir URL por path/ID
            # Usaremos el helper de SharePoint (adaptado para /me/drive) si es un path
            # El helper _get_item_id_from_path_if_needed_sp está pensado para SP,
            # necesitaríamos uno similar para OneDrive o ajustar la lógica.
            # Por ahora, construimos la URL directamente.
            is_likely_id = not ("/" in item_id_or_path) and (len(item_id_or_path) > 30 or '!' in item_id_or_path)
            if is_likely_id:
                item_url_base = f"{settings.GRAPH_API_BASE_URL}{base_drive_path}/items/{item_id_or_path}"
            else:
                clean_path = item_id_or_path.strip('/')
                item_url_base = f"{settings.GRAPH_API_BASE_URL}{base_drive_path}/root:/{clean_path}"
            log_item_description = f"item '{item_id_or_path}' en OneDrive del usuario"

        elif drive_scope == 'site':
            effective_site_id = _obtener_site_id_sp(client, params)
            effective_drive_id = _get_drive_id(client, effective_site_id, params.get("drive_id_or_name"))
            # Para SP, _get_item_id_from_path_if_needed_sp resuelve el ID si se da un path
            item_actual_id = _get_item_id_from_path_if_needed_sp(client, item_id_or_path, effective_site_id, effective_drive_id, params)
            if isinstance(item_actual_id, dict) and item_actual_id.get("status") == "error": return item_actual_id # Propagar error

            item_url_base = f"{settings.GRAPH_API_BASE_URL}/sites/{effective_site_id}/drives/{effective_drive_id}/items/{item_actual_id}"
            log_item_description = f"item '{item_id_or_path}' (ID: {item_actual_id}) en SharePoint"
        else:
            return {"status": "error", "action": action_name, "message": "'drive_scope' debe ser 'me' o 'site'.", "http_status": 400}
        
        api_query_params = {"$select": select_fields}
        logger.info(f"Obteniendo metadatos de video para {log_item_description}")
        
        response = client.get(url=item_url_base, scope=settings.GRAPH_API_DEFAULT_SCOPE, params=api_query_params, timeout=settings.DEFAULT_API_TIMEOUT)
        video_metadata = response.json()
        
        if not video_metadata.get('video') and not video_metadata.get('file', {}).get('mimeType','').startswith('video/'):
             return {"status": "warning", "action": action_name, "data": video_metadata, "message": "Metadatos obtenidos, pero el item podría no ser un video (sin faceta 'video' o MIME type de video)."}
        
        return {"status": "success", "data": video_metadata}
        
    except ValueError as ve:
         return {"status": "error", "action": action_name, "message": f"Error de configuración para obtener metadatos de video: {ve}", "http_status": 400}
    except NotImplementedError as nie:
        return {"status": "error", "action": action_name, "message": f"Dependencia no implementada: {nie}", "http_status": 501}
    except Exception as e:
        return _handle_stream_api_error(e, action_name, params)


def get_video_playback_url(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Obtiene una URL de descarga para un archivo de video, que a menudo se puede usar para reproducción.
    Reutiliza la lógica de obtener_metadatos_video para obtener la propiedad '@microsoft.graph.downloadUrl'.
    """
    action_name = "stream_get_video_playback_url"
    # Los parámetros son los mismos que para obtener_metadatos_video
    # 'item_id_or_path', opcional 'drive_scope', 'site_identifier', 'drive_id_or_name'
    
    logger.info(f"Intentando obtener URL de reproducción/descarga para video (llamando a obtener_metadatos_video). Params: {params}")
    try:
        metadata_response = obtener_metadatos_video(client, params)

        if metadata_response.get("status") != "success":
            # Propagar el error de obtener_metadatos_video
            return metadata_response 
        
        item_data = metadata_response.get("data", {})
        download_url = item_data.get("@microsoft.graph.downloadUrl")
        
        if not download_url:
            logger.warning(f"No se encontró '@microsoft.graph.downloadUrl' para el video. Item data: {item_data.get('id', 'ID no disponible')}")
            return {
                "status": "error", 
                "action": action_name,
                "message": "No se pudo obtener la URL de descarga/reproducción para el video.", 
                "details": "La propiedad @microsoft.graph.downloadUrl no está presente en los metadatos del item.", 
                "data_source_metadata": item_data, # Devolver metadatos por si son útiles para depurar
                "http_status": 404 # O 500 si el item se encontró pero no tiene la URL
            }
        
        logger.info(f"URL de descarga/reproducción obtenida para video ID '{item_data.get('id')}'.")
        return {
            "status": "success", 
            "data": {
                "id": item_data.get("id"), 
                "name": item_data.get("name"), 
                "webUrl": item_data.get("webUrl"), 
                "playback_url": download_url, # Esta es la URL de descarga directa
                "video_info": item_data.get("video"), 
                "file_info": item_data.get("file") 
            }
        }
    except Exception as e: # Captura cualquier excepción no manejada por obtener_metadatos_video
        return _handle_stream_api_error(e, action_name, params)

def obtener_transcripcion_video(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Placeholder indicando que la transcripción directa vía Graph para archivos no es estándar.
    Se debe buscar un archivo .vtt asociado o usar servicios externos.
    """
    action_name = "stream_obtener_transcripcion_video"
    video_item_id = params.get("item_id_or_path", "ID no especificado")
    message = (
        f"La obtención/generación de transcripciones de video para '{video_item_id}' no es una función directa "
        "de Microsoft Graph API para archivos genéricos de video en OneDrive/SharePoint."
    )
    details = (
        "Para obtener transcripciones: \n"
        "1. Verifique si un archivo de transcripción (ej. .vtt) existe junto al video en OneDrive/SharePoint "
        "y descárguelo usando las acciones de archivo (ej. onedrive_download_file o sp_download_document).\n"
        "2. Si el video fue procesado por Microsoft Stream (en SharePoint), la transcripción podría estar disponible a través "
        "de la interfaz de Stream o como un archivo asociado.\n"
        "3. Alternativamente, use servicios como Azure AI Video Indexer para procesar el video y obtener la transcripción, "
        "y luego envíe los resultados a esta API mediante una acción personalizada."
    )
    logger.warning(f"Acción '{action_name}' llamada para '{video_item_id}'. {message}")
    return {
        "status": "not_supported",
        "action": action_name,
        "message": message,
        "details": details,
        "http_status": 501 # Not Implemented
    }