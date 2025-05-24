# app/actions/bookings_actions.py
# -*- coding: utf-8 -*-
import logging
import requests # Para requests.exceptions.HTTPError
from typing import Dict, List, Optional, Any

from app.core.config import settings # Para acceder a GRAPH_API_DEFAULT_SCOPE, GRAPH_API_BASE_URL
from app.shared.helpers.http_client import AuthenticatedHttpClient

logger = logging.getLogger(__name__)

# Helper para manejar errores de Bookings API
def _handle_bookings_api_error(e: Exception, action_name: str, params_for_log: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    log_message = f"Error en Bookings action '{action_name}'"
    if params_for_log:
        log_message += f" con params: {params_for_log}" # Asumir que no hay datos sensibles o filtrarlos
    
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
        except Exception:
            details_str = e.response.text[:500] if e.response.text else "No response body"
            
    return {
        "status": "error",
        "action": action_name,
        "message": f"Error ejecutando {action_name}: {type(e).__name__}",
        "http_status": status_code_int,
        "details": details_str,
        "graph_error_code": graph_error_code
    }

# --- Implementación de Acciones de Microsoft Bookings ---

def list_businesses(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Lista todos los negocios de Microsoft Bookings a los que el usuario autenticado tiene acceso.
    https://learn.microsoft.com/en-us/graph/api/bookingbusiness-list?view=graph-rest-1.0&tabs=http
    """
    action_name = "bookings_list_businesses"
    url = f"{settings.GRAPH_API_BASE_URL}/solutions/bookingBusinesses"
    
    query_params: Dict[str, Any] = {}
    if params.get("query"): # Para buscar por displayName
        query_params["query"] = params["query"] # Parece que la API espera 'query' no '$search' o '$filter' para este endpoint. Revisar docs.
                                                # Documentación sugiere que no hay params de query estándar como $filter.
        logger.warning("El parámetro 'query' para list_businesses puede no ser soportado directamente por la API /bookingBusinesses. La API devuelve todos los negocios accesibles.")


    logger.info("Listando negocios de Microsoft Bookings.")
    try:
        # El scope puede requerir Bookings.Read.All o similar, dependiendo de los permisos de la app.
        response = client.get(url, scope=settings.GRAPH_API_DEFAULT_SCOPE, params=query_params if query_params else None)
        return {"status": "success", "data": response.json().get("value", [])}
    except Exception as e:
        return _handle_bookings_api_error(e, action_name, params)

def get_business(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Obtiene los detalles de un negocio de Microsoft Bookings específico por su ID.
    Requiere 'business_id'.
    https://learn.microsoft.com/en-us/graph/api/bookingbusiness-get?view=graph-rest-1.0&tabs=http
    """
    action_name = "bookings_get_business"
    business_id = params.get("business_id")
    if not business_id:
        return {"status": "error", "action": action_name, "message": "'business_id' es requerido.", "http_status": 400}

    url = f"{settings.GRAPH_API_BASE_URL}/solutions/bookingBusinesses/{business_id}"
    
    logger.info(f"Obteniendo detalles del negocio de Bookings ID: '{business_id}'")
    try:
        response = client.get(url, scope=settings.GRAPH_API_DEFAULT_SCOPE)
        return {"status": "success", "data": response.json()}
    except Exception as e:
        return _handle_bookings_api_error(e, action_name, params)

def list_services(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Lista los servicios de un negocio de Microsoft Bookings específico.
    Requiere 'business_id'.
    https://learn.microsoft.com/en-us/graph/api/bookingbusiness-list-services?view=graph-rest-1.0&tabs=http
    """
    action_name = "bookings_list_services"
    business_id = params.get("business_id")
    if not business_id:
        return {"status": "error", "action": action_name, "message": "'business_id' es requerido.", "http_status": 400}

    url = f"{settings.GRAPH_API_BASE_URL}/solutions/bookingBusinesses/{business_id}/services"
    
    logger.info(f"Listando servicios para el negocio de Bookings ID: '{business_id}'")
    try:
        response = client.get(url, scope=settings.GRAPH_API_DEFAULT_SCOPE)
        return {"status": "success", "data": response.json().get("value", [])}
    except Exception as e:
        return _handle_bookings_api_error(e, action_name, params)

def list_staff(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Lista los miembros del personal de un negocio de Microsoft Bookings específico.
    Requiere 'business_id'.
    https://learn.microsoft.com/en-us/graph/api/bookingbusiness-list-staffmembers?view=graph-rest-1.0&tabs=http
    """
    action_name = "bookings_list_staff"
    business_id = params.get("business_id")
    if not business_id:
        return {"status": "error", "action": action_name, "message": "'business_id' es requerido.", "http_status": 400}

    url = f"{settings.GRAPH_API_BASE_URL}/solutions/bookingBusinesses/{business_id}/staffMembers"
    
    logger.info(f"Listando personal para el negocio de Bookings ID: '{business_id}'")
    try:
        response = client.get(url, scope=settings.GRAPH_API_DEFAULT_SCOPE)
        return {"status": "success", "data": response.json().get("value", [])}
    except Exception as e:
        return _handle_bookings_api_error(e, action_name, params)

def create_appointment(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Crea una nueva cita en un negocio de Microsoft Bookings.
    Requiere 'business_id' y un 'appointment_payload' (dict con los detalles de la cita).
    https://learn.microsoft.com/en-us/graph/api/bookingbusiness-post-appointments?view=graph-rest-1.0&tabs=http
    """
    action_name = "bookings_create_appointment"
    business_id = params.get("business_id")
    appointment_payload = params.get("appointment_payload")

    if not business_id:
        return {"status": "error", "action": action_name, "message": "'business_id' es requerido.", "http_status": 400}
    if not appointment_payload or not isinstance(appointment_payload, dict):
        return {"status": "error", "action": action_name, "message": "'appointment_payload' (dict) es requerido.", "http_status": 400}

    # Validar campos mínimos en appointment_payload (ej: customerId, serviceId, start, end)
    # Esto dependerá de la definición de bookingAppointment
    required_keys = ["customerEmailAddress", "serviceId", "start", "end"] # Ejemplo simplificado
    if not all(key in appointment_payload for key in required_keys):
         missing_keys = [key for key in required_keys if key not in appointment_payload]
         return {"status": "error", "action": action_name, "message": f"Faltan campos requeridos en 'appointment_payload': {missing_keys}", "http_status": 400}


    url = f"{settings.GRAPH_API_BASE_URL}/solutions/bookingBusinesses/{business_id}/appointments"
    
    logger.info(f"Creando cita para el negocio de Bookings ID: '{business_id}'")
    try:
        # Scope podría ser Bookings.ReadWrite.All o BookingsAppointment.ReadWrite.All
        response = client.post(url, scope=settings.GRAPH_API_DEFAULT_SCOPE, json_data=appointment_payload)
        return {"status": "success", "data": response.json()}
    except Exception as e:
        return _handle_bookings_api_error(e, action_name, params)

def list_appointments(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Lista las citas de un negocio de Microsoft Bookings, opcionalmente filtradas por fecha.
    Requiere 'business_id'.
    Opcional: 'start_datetime_str', 'end_datetime_str' (ISO 8601) para filtrar.
    https://learn.microsoft.com/en-us/graph/api/bookingbusiness-list-appointments?view=graph-rest-1.0&tabs=http
    """
    action_name = "bookings_list_appointments"
    business_id = params.get("business_id")
    start_datetime_str = params.get("start_datetime_str") # Ej: "2025-05-01T00:00:00Z"
    end_datetime_str = params.get("end_datetime_str")     # Ej: "2025-05-31T23:59:59Z"

    if not business_id:
        return {"status": "error", "action": action_name, "message": "'business_id' es requerido.", "http_status": 400}

    url = f"{settings.GRAPH_API_BASE_URL}/solutions/bookingBusinesses/{business_id}/appointments"
    
    odata_params: Dict[str, Any] = {}
    # La API de Bookings appointments usa un formato de filtro específico para fechas, no $filter estándar.
    # GET /solutions/bookingBusinesses/{id}/appointments?$filter=start/dateTime ge '2018-05-01T00:00:00Z' and end/dateTime le '2018-05-30T23:00:00Z'
    # Esta sintaxis es incorrecta para el endpoint. El endpoint correcto para filtrar por fecha es /calendarView
    # o se pasan parámetros de query directos. Por ahora, se usará /calendarView si hay fechas.

    if start_datetime_str and end_datetime_str:
        url = f"{settings.GRAPH_API_BASE_URL}/solutions/bookingBusinesses/{business_id}/calendarView"
        odata_params["start"] = start_datetime_str
        odata_params["end"] = end_datetime_str
        logger.info(f"Listando citas (calendarView) para negocio '{business_id}' entre {start_datetime_str} y {end_datetime_str}")
    else:
        logger.info(f"Listando todas las citas para el negocio de Bookings ID: '{business_id}'")
        if params.get("$top"): odata_params["$top"] = params["$top"]
        if params.get("$filter"): odata_params["$filter"] = params["$filter"] # Filtros OData estándar si se usa /appointments

    try:
        response = client.get(url, scope=settings.GRAPH_API_DEFAULT_SCOPE, params=odata_params)
        return {"status": "success", "data": response.json().get("value", [])}
    except Exception as e:
        return _handle_bookings_api_error(e, action_name, params)

def get_appointment(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Obtiene los detalles de una cita específica.
    Requiere 'business_id' y 'appointment_id'.
    https://learn.microsoft.com/en-us/graph/api/bookingappointment-get?view=graph-rest-1.0&tabs=http
    """
    action_name = "bookings_get_appointment"
    business_id = params.get("business_id")
    appointment_id = params.get("appointment_id")

    if not business_id:
        return {"status": "error", "action": action_name, "message": "'business_id' es requerido.", "http_status": 400}
    if not appointment_id:
        return {"status": "error", "action": action_name, "message": "'appointment_id' es requerido.", "http_status": 400}

    url = f"{settings.GRAPH_API_BASE_URL}/solutions/bookingBusinesses/{business_id}/appointments/{appointment_id}"
    
    logger.info(f"Obteniendo detalles de la cita ID '{appointment_id}' para el negocio '{business_id}'")
    try:
        response = client.get(url, scope=settings.GRAPH_API_DEFAULT_SCOPE)
        return {"status": "success", "data": response.json()}
    except Exception as e:
        return _handle_bookings_api_error(e, action_name, params)

def cancel_appointment(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Cancela una cita específica.
    Requiere 'business_id', 'appointment_id', y 'cancellation_message' (opcional).
    https://learn.microsoft.com/en-us/graph/api/bookingappointment-cancel?view=graph-rest-1.0&tabs=http
    """
    action_name = "bookings_cancel_appointment"
    business_id = params.get("business_id")
    appointment_id = params.get("appointment_id")
    cancellation_message = params.get("cancellation_message", "Esta cita ha sido cancelada.") # Mensaje por defecto

    if not business_id:
        return {"status": "error", "action": action_name, "message": "'business_id' es requerido.", "http_status": 400}
    if not appointment_id:
        return {"status": "error", "action": action_name, "message": "'appointment_id' es requerido.", "http_status": 400}

    url = f"{settings.GRAPH_API_BASE_URL}/solutions/bookingBusinesses/{business_id}/appointments/{appointment_id}/cancel"
    payload = {"cancellationMessage": cancellation_message}
    
    logger.info(f"Cancelando cita ID '{appointment_id}' del negocio '{business_id}' con mensaje: '{cancellation_message}'")
    try:
        # La operación de cancelación es un POST y devuelve 204 No Content si tiene éxito.
        response = client.post(url, scope=settings.GRAPH_API_DEFAULT_SCOPE, json_data=payload)
        if response.status_code == 204:
            return {"status": "success", "message": f"Cita '{appointment_id}' cancelada exitosamente."}
        else:
            # Esto no debería ocurrir si no hay excepción HTTPError, pero por si acaso.
            details = response.text if response.content else "Respuesta inesperada sin cuerpo."
            logger.error(f"Respuesta inesperada {response.status_code} al cancelar cita: {details}")
            return {"status": "error", "action": action_name, "message": f"Respuesta inesperada del servidor: {response.status_code}", "details": details, "http_status": response.status_code}
    except Exception as e:
        return _handle_bookings_api_error(e, action_name, params)