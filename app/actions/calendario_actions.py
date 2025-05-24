# app/actions/calendario_actions.py
import logging
import requests # Para requests.exceptions.HTTPError
import json
from typing import Dict, List, Optional, Any

from app.core.config import settings # Para acceder a GRAPH_API_BASE_URL y scopes
from app.shared.helpers.http_client import AuthenticatedHttpClient

logger = logging.getLogger(__name__)

# Scopes específicos para calendario (si se definen en settings, de lo contrario usa el default)
# Asumiendo que settings.GRAPH_API_DEFAULT_SCOPE es ["https://graph.microsoft.com/.default"]
# y que la App Registration tiene los permisos Calendars.Read, Calendars.ReadWrite, Calendars.Read.Shared
CALENDARS_READ_SCOPE = getattr(settings, "GRAPH_SCOPE_CALENDARS_READ", settings.GRAPH_API_DEFAULT_SCOPE)
CALENDARS_READ_WRITE_SCOPE = getattr(settings, "GRAPH_SCOPE_CALENDARS_READ_WRITE", settings.GRAPH_API_DEFAULT_SCOPE)
CALENDARS_READ_SHARED_SCOPE = getattr(settings, "GRAPH_SCOPE_CALENDARS_READ_SHARED", settings.GRAPH_API_DEFAULT_SCOPE)


def _handle_calendar_api_error(e: Exception, action_name: str, params_for_log: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Helper para manejar errores de Calendar API de forma centralizada."""
    log_message = f"Error en Calendar action '{action_name}'"
    if params_for_log:
        # Filtrar datos potencialmente grandes o sensibles del log
        safe_params = {k: v for k, v in params_for_log.items() if k not in ['event_payload', 'update_payload', 'meeting_params_body', 'schedule_params_body', 'attendees']}
        log_message += f" con params: {safe_params}"
    
    logger.error(f"{log_message}: {type(e).__name__} - {str(e)}", exc_info=True)
    
    details_str = str(e)
    status_code_int = 500
    graph_error_code = None

    if isinstance(e, requests.exceptions.HTTPError) and e.response is not None:
        status_code_int = e.response.status_code
        try:
            error_data = e.response.json()
            error_info = error_data.get("error", {})
            details_str = error_info.get("message", e.response.text)
            graph_error_code = error_info.get("code")
        except json.JSONDecodeError: # Si la respuesta de error no es JSON
            details_str = e.response.text[:500] if e.response.text else "No response body"
            
    return {
        "status": "error",
        "action": action_name,
        "message": f"Error en {action_name}: {type(e).__name__}",
        "http_status": status_code_int,
        "details": details_str,
        "graph_error_code": graph_error_code
    }

def _calendar_paged_request(
    client: AuthenticatedHttpClient,
    url_base: str,
    scope_list: List[str], # Cambiado a List[str]
    params: Dict[str, Any], 
    query_api_params_initial: Dict[str, Any],
    max_items_total: Optional[int], # Permitir None para obtener todos los items hasta el límite de páginas
    action_name_for_log: str
) -> Dict[str, Any]:
    """Helper común para paginación de resultados de Calendario."""
    all_items: List[Dict[str, Any]] = []
    current_url: Optional[str] = url_base
    page_count = 0
    # Usar un límite de páginas configurable o un default razonable
    max_pages_to_fetch = getattr(settings, "MAX_PAGING_PAGES", 20) 
    
    top_per_page = query_api_params_initial.get('$top', 50) # Default $top si no se especifica

    logger.info(f"Iniciando solicitud paginada para '{action_name_for_log}' desde '{url_base.split('?')[0]}...'. Max total items: {max_items_total or 'todos'}, por página: {top_per_page}, max_páginas: {max_pages_to_fetch}")
    try:
        while current_url and (max_items_total is None or len(all_items) < max_items_total) and page_count < max_pages_to_fetch:
            page_count += 1
            is_first_call = (current_url == url_base and page_count == 1)
            
            current_params_for_call = query_api_params_initial if is_first_call else None
            logger.debug(f"Página {page_count} para '{action_name_for_log}': GET {current_url.split('?')[0]} con params: {current_params_for_call}")
            
            response = client.get(url=current_url, scope=scope_list, params=current_params_for_call)
            response_data = response.json()
            
            page_items = response_data.get('value', [])
            if not isinstance(page_items, list):
                logger.warning(f"Respuesta inesperada en paginación para '{action_name_for_log}', 'value' no es una lista. Respuesta: {response_data}")
                break 
            
            for item in page_items:
                if max_items_total is None or len(all_items) < max_items_total:
                    all_items.append(item)
                else:
                    break 
            
            current_url = response_data.get('@odata.nextLink')
            if not current_url or (max_items_total is not None and len(all_items) >= max_items_total):
                logger.debug(f"'{action_name_for_log}': Fin de paginación. nextLink: {'Sí' if current_url else 'No'}, Items actuales: {len(all_items)}.")
                break
        
        if page_count >= max_pages_to_fetch and current_url:
            logger.warning(f"'{action_name_for_log}' alcanzó el límite de {max_pages_to_fetch} páginas procesadas. Pueden existir más resultados no recuperados.")

        logger.info(f"'{action_name_for_log}' recuperó {len(all_items)} items en {page_count} páginas.")
        # Devolver los datos en un formato consistente, ej. {"value": all_items, "@odata.count": len(all_items)}
        return {"status": "success", "data": {"value": all_items, "@odata.count": len(all_items)}, "total_retrieved": len(all_items), "pages_processed": page_count}
    except Exception as e:
        return _handle_calendar_api_error(e, action_name_for_log, params)


def calendar_list_events(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Dict[str, Any]:
    action_name = "calendar_list_events"
    mailbox: str = params.get("mailbox", settings.MAILBOX_USER_ID) # 'me' o userPrincipalName/ID
    calendar_id: Optional[str] = params.get("calendar_id") # Opcional, para un calendario específico

    # Parámetros para /calendarView
    start_datetime_str: Optional[str] = params.get('start_datetime') # ISO 8601
    end_datetime_str: Optional[str] = params.get('end_datetime')   # ISO 8601
    
    # Parámetros OData generales
    top_per_page: int = min(int(params.get('top_per_page', 25)), 100) # Límite típico para Graph
    max_items_total: Optional[int] = params.get('max_items_total') # Límite total a devolver, None para todos
    select_fields: Optional[str] = params.get('select')
    filter_query: Optional[str] = params.get('filter') # Para /events, no para /calendarView generalmente
    order_by: Optional[str] = params.get('orderby', 'start/dateTime') # Default order

    query_api_params: Dict[str, Any] = {'$top': top_per_page}
    if select_fields: 
        query_api_params['$select'] = select_fields
    else: # Select por defecto útil
        query_api_params['$select'] = "id,subject,bodyPreview,start,end,organizer,attendees,location,isAllDay,webLink,onlineMeeting"

    if order_by: # $orderby es útil para /events y /calendarView
        query_api_params['$orderby'] = order_by

    # Determinar el endpoint base
    user_path_segment = "me" if mailbox.lower() == "me" else f"users/{mailbox}"
    calendar_path_segment = f"calendars/{calendar_id}" if calendar_id else "calendar" # 'calendar' es el default

    url_base: str
    log_action_detail: str

    if start_datetime_str and end_datetime_str:
        # Usar /calendarView si se proveen fechas de inicio y fin
        query_api_params['startDateTime'] = start_datetime_str
        query_api_params['endDateTime'] = end_datetime_str
        # $filter no se usa típicamente con calendarView, las fechas son los filtros principales.
        if '$filter' in query_api_params: 
            logger.warning("Parámetro '$filter' provisto con start/end datetime para calendarView; $filter podría no ser aplicado. El filtro principal son las fechas.")
            # del query_api_params['$filter'] # Opcional: removerlo para evitar confusión
        
        url_base = f"{settings.GRAPH_API_BASE_URL}/{user_path_segment}/{calendar_path_segment}/calendarView"
        log_action_detail = f"eventos ({calendar_path_segment}/calendarView) para '{mailbox}' entre {start_datetime_str} y {end_datetime_str}"
    else:
        # Usar /events si no hay rango de fechas específico
        if filter_query: 
            query_api_params['$filter'] = filter_query
        url_base = f"{settings.GRAPH_API_BASE_URL}/{user_path_segment}/{calendar_path_segment}/events"
        log_action_detail = f"eventos ({calendar_path_segment}/events) para '{mailbox}'"
    
    # Llamar al helper de paginación
    paged_result = _calendar_paged_request(
        client, url_base, CALENDARS_READ_SCOPE, 
        params, query_api_params, max_items_total, 
        f"{action_name} ({log_action_detail})"
    )
    # El helper ya devuelve {"status": "success", "data": {"value": ..., "@odata.count": ...}}
    # o un error formateado.
    return paged_result


def calendar_create_event(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Dict[str, Any]:
    action_name = "calendar_create_event"
    mailbox: str = params.get("mailbox", settings.MAILBOX_USER_ID)
    calendar_id: Optional[str] = params.get("calendar_id")
    event_payload: Optional[Dict[str, Any]] = params.get("event_payload")

    if not event_payload or not isinstance(event_payload, dict):
        return {"status": "error", "action": action_name, "message": "'event_payload' (dict) es requerido.", "http_status": 400}

    required_fields = ["subject", "start", "end"] # Mínimos para un evento
    if not all(field in event_payload for field in required_fields):
        missing = [field for field in required_fields if field not in event_payload]
        return {"status": "error", "action": action_name, "message": f"Faltan campos requeridos en 'event_payload': {missing}.", "http_status": 400}
    
    for field_name in ["start", "end"]: # Validar estructura de start/end
        if not isinstance(event_payload.get(field_name), dict) or \
           not event_payload[field_name].get("dateTime") or \
           not event_payload[field_name].get("timeZone"):
            return {"status": "error", "action": action_name, "message": f"Campo '{field_name}' en 'event_payload' debe ser un dict con 'dateTime' y 'timeZone'.", "http_status": 400}

    user_path_segment = "me" if mailbox.lower() == "me" else f"users/{mailbox}"
    calendar_path_segment = f"calendars/{calendar_id}" if calendar_id else "calendar"
    url = f"{settings.GRAPH_API_BASE_URL}/{user_path_segment}/{calendar_path_segment}/events"
    
    logger.info(f"Creando evento en {calendar_path_segment} para '{mailbox}'. Asunto: {event_payload.get('subject')}")
    try:
        response = client.post(url, scope=CALENDARS_READ_WRITE_SCOPE, json_data=event_payload)
        created_event = response.json()
        logger.info(f"Evento '{event_payload.get('subject')}' creado con ID: {created_event.get('id')}")
        return {"status": "success", "data": created_event}
    except Exception as e:
        return _handle_calendar_api_error(e, action_name, params)


def get_event(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Dict[str, Any]:
    action_name = "calendar_get_event"
    mailbox: str = params.get("mailbox", settings.MAILBOX_USER_ID)
    event_id: Optional[str] = params.get("event_id")
    select_fields: Optional[str] = params.get("select")

    if not event_id:
        return {"status": "error", "action": action_name, "message": "'event_id' es requerido.", "http_status": 400}
    
    user_path_segment = "me" if mailbox.lower() == "me" else f"users/{mailbox}"
    # No se necesita calendar_id para obtener un evento por su ID global.
    url = f"{settings.GRAPH_API_BASE_URL}/{user_path_segment}/events/{event_id}"
    
    query_api_params = {'$select': select_fields} if select_fields else None
    logger.info(f"Obteniendo evento ID '{event_id}' para '{mailbox}' (Select: {select_fields or 'default'})")
    try:
        response = client.get(url, scope=CALENDARS_READ_SCOPE, params=query_api_params)
        return {"status": "success", "data": response.json()}
    except Exception as e:
        return _handle_calendar_api_error(e, action_name, params)


def update_event(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Dict[str, Any]:
    action_name = "calendar_update_event"
    mailbox: str = params.get("mailbox", settings.MAILBOX_USER_ID)
    event_id: Optional[str] = params.get("event_id")
    update_payload: Optional[Dict[str, Any]] = params.get("update_payload")

    if not event_id:
        return {"status": "error", "action": action_name, "message": "'event_id' es requerido.", "http_status": 400}
    if not update_payload or not isinstance(update_payload, dict) or not update_payload: # Payload no puede ser vacío
        return {"status": "error", "action": action_name, "message": "'update_payload' (dict con campos a actualizar) es requerido y no puede estar vacío.", "http_status": 400}

    for field_name in ["start", "end"]: # Validar estructura si se actualizan start/end
        if field_name in update_payload:
            field_value = update_payload[field_name]
            if not isinstance(field_value, dict) or \
               not field_value.get("dateTime") or \
               not field_value.get("timeZone"):
                return {"status": "error", "action": action_name, "message": f"Si actualiza '{field_name}', debe ser un dict con 'dateTime' y 'timeZone'.", "http_status": 400}

    user_path_segment = "me" if mailbox.lower() == "me" else f"users/{mailbox}"
    url = f"{settings.GRAPH_API_BASE_URL}/{user_path_segment}/events/{event_id}"
    
    logger.info(f"Actualizando evento ID '{event_id}' para '{mailbox}'")
    try:
        response = client.patch(url, scope=CALENDARS_READ_WRITE_SCOPE, json_data=update_payload)
        return {"status": "success", "data": response.json()} # PATCH devuelve el objeto actualizado
    except Exception as e:
        return _handle_calendar_api_error(e, action_name, params)


def delete_event(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Dict[str, Any]:
    action_name = "calendar_delete_event"
    mailbox: str = params.get("mailbox", settings.MAILBOX_USER_ID)
    event_id: Optional[str] = params.get("event_id")

    if not event_id:
        return {"status": "error", "action": action_name, "message": "'event_id' es requerido.", "http_status": 400}

    user_path_segment = "me" if mailbox.lower() == "me" else f"users/{mailbox}"
    url = f"{settings.GRAPH_API_BASE_URL}/{user_path_segment}/events/{event_id}"
    
    logger.info(f"Eliminando evento ID '{event_id}' para '{mailbox}'")
    try:
        response = client.delete(url, scope=CALENDARS_READ_WRITE_SCOPE) # Devuelve 204 No Content
        return {"status": "success", "message": f"Evento '{event_id}' eliminado exitosamente.", "http_status": response.status_code}
    except Exception as e:
        return _handle_calendar_api_error(e, action_name, params)


def find_meeting_times(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Dict[str, Any]:
    action_name = "calendar_find_meeting_times"
    user_id: str = params.get("user_id", settings.MAILBOX_USER_ID) # ID del usuario para quien se buscan horarios
    # El payload para /findMeetingTimes es complejo, se espera que el usuario lo provea en 'meeting_time_suggestion_payload'
    meeting_time_suggestion_payload: Optional[Dict[str, Any]] = params.get("meeting_time_suggestion_payload")

    if not meeting_time_suggestion_payload or not isinstance(meeting_time_suggestion_payload, dict):
        return {"status": "error", "action": action_name, "message": "'meeting_time_suggestion_payload' (dict) es requerido.", "http_status": 400}
    
    # Validar campos mínimos en el payload (ej. timeConstraint, attendees)
    if not meeting_time_suggestion_payload.get("timeConstraint"):
        return {"status": "error", "action": action_name, "message": "Campo 'timeConstraint' es requerido en 'meeting_time_suggestion_payload'.", "http_status": 400}
    # 'attendees' es opcional para buscar solo la disponibilidad del organizador, pero usualmente se incluye.
    # if not meeting_time_suggestion_payload.get("attendees"):
    #     logger.info("Buscando horarios sin especificar asistentes; se buscará la disponibilidad del organizador.")


    user_path_segment = "me" if user_id.lower() == "me" else f"users/{user_id}"
    url = f"{settings.GRAPH_API_BASE_URL}/{user_path_segment}/findMeetingTimes"
    
    logger.info(f"Buscando horarios de reunión (findMeetingTimes) para usuario '{user_id}'")
    try:
        response = client.post(url, scope=CALENDARS_READ_SCOPE, json_data=meeting_time_suggestion_payload)
        # La respuesta contiene 'meetingTimeSuggestions' o 'emptySuggestionsReason'
        return {"status": "success", "data": response.json()}
    except Exception as e:
        return _handle_calendar_api_error(e, action_name, params)


def get_schedule(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Dict[str, Any]:
    action_name = "calendar_get_schedule"
    # El payload para /calendar/getSchedule es un objeto con 'schedules' (lista de emails),
    # 'startTime', 'endTime', y 'availabilityViewInterval' (en minutos).
    schedule_information_payload: Optional[Dict[str, Any]] = params.get("schedule_information_payload")

    if not schedule_information_payload or not isinstance(schedule_information_payload, dict):
        return {"status": "error", "action": action_name, "message": "'schedule_information_payload' (dict) es requerido.", "http_status": 400}

    required_payload_keys = ["schedules", "startTime", "endTime"]
    if not all(key in schedule_information_payload for key in required_payload_keys):
        missing = [key for key in required_payload_keys if key not in schedule_information_payload]
        return {"status": "error", "action": action_name, "message": f"Faltan campos requeridos en 'schedule_information_payload': {missing}.", "http_status": 400}
    if not isinstance(schedule_information_payload.get("schedules"), list) or not schedule_information_payload["schedules"]:
        return {"status": "error", "action": action_name, "message": "'schedules' debe ser una lista no vacía de direcciones de correo.", "http_status": 400}

    # El endpoint es sobre /me/calendar/getSchedule, no sobre un usuario específico por path.
    # La disponibilidad se pide para las personas listadas en 'schedules' en el payload.
    url = f"{settings.GRAPH_API_BASE_URL}/me/calendar/getSchedule"
    
    logger.info(f"Obteniendo información de calendario (getSchedule) para los usuarios en el payload.")
    try:
        # Requiere Calendars.Read.Shared o Calendars.Read
        response = client.post(url, scope=CALENDARS_READ_SHARED_SCOPE, json_data=schedule_information_payload)
        return {"status": "success", "data": response.json().get("value", [])} # Devuelve una lista de scheduleInformation
    except Exception as e:
        return _handle_calendar_api_error(e, action_name, params)