# app/actions/vivainsights_actions.py
import logging
import requests # Para requests.exceptions.HTTPError
from typing import Dict, List, Optional, Any

from app.core.config import settings
from app.shared.helpers.http_client import AuthenticatedHttpClient

logger = logging.getLogger(__name__)

def _handle_viva_insights_api_error(e: Exception, action_name: str, params_for_log: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Helper para manejar errores de Viva Insights API."""
    log_message = f"Error en Viva Insights Action '{action_name}'"
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
            
    return {
        "status": "error",
        "action": action_name,
        "message": f"Error en {action_name}: {type(e).__name__}",
        "http_status": status_code_int,
        "details": details_str,
        "graph_error_code": graph_error_code
    }

# --- FUNCIONES DE ACCIÓN PARA VIVA INSIGHTS ---

def get_my_analytics(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Obtiene las estadísticas de actividad del usuario autenticado desde Viva Insights.
    Corresponde al endpoint /me/analytics/activityStatistics.
    """
    action_name = "viva_get_my_analytics" # Coincide con el action_mapper
    
    # Parámetros OData para el endpoint /me/analytics/activityStatistics
    # (ej. $select, $filter si son soportados por este endpoint específico)
    odata_params: Dict[str, Any] = {}
    if params.get("$select"): # Si el usuario pasa un $select específico
        odata_params["$select"] = params["$select"]
    # La documentación no indica soporte para $filter directamente en /activityStatistics
    # pero sí para /analytics/activitystatistics/{id}.
    # Si se quiere filtrar, se haría sobre los resultados devueltos.

    url = f"{settings.GRAPH_API_BASE_URL}/me/analytics/activityStatistics"
    
    logger.info(f"Obteniendo estadísticas de actividad de Viva Insights para el usuario actual (/me/analytics/activityStatistics)")
    try:
        # El scope para Viva Insights puede ser específico, ej. "Analytics.Read",
        # pero settings.GRAPH_API_DEFAULT_SCOPE (.default) debería cubrirlo si los permisos están concedidos.
        response = client.get(url, scope=settings.GRAPH_API_DEFAULT_SCOPE, params=odata_params if odata_params else None)
        analytics_data = response.json()
        # La respuesta es una colección de objetos activityStatistic bajo la clave "value"
        return {"status": "success", "data": analytics_data.get("value", [])}
    except requests.exceptions.HTTPError as http_err:
        # Manejo específico para 403 Forbidden, común si Viva Insights no está habilitado/licenciado.
        if http_err.response is not None and http_err.response.status_code == 403:
            details = http_err.response.text if http_err.response.text else "Acceso prohibido."
            logger.error(f"Acceso prohibido (403) a Viva Insights: {details[:300]}")
            return {
                "status": "error", 
                "action": action_name,
                "message": "Acceso prohibido a Viva Insights. Verifique la licencia y configuración del servicio.", 
                "http_status": 403, 
                "details": details,
                "graph_error_code": "AccessDenied" # O el código de error específico de Graph
            }
        return _handle_viva_insights_api_error(http_err, action_name, params) # Para otros errores HTTP
    except Exception as e:
        return _handle_viva_insights_api_error(e, action_name, params)

def get_focus_plan(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Obtiene información relacionada con el tiempo de concentración (focus time) del usuario.
    Actualmente, esto se basa en filtrar las 'activityStatistics' que incluyen 'focusHours'.
    La API de Graph no expone un "plan de concentración" detallado como un objeto separado.
    """
    action_name = "viva_get_focus_plan" # Coincide con el action_mapper
    logger.info("Intentando obtener información del plan de concentración (basado en estadísticas de actividad de 'focus').")

    # Reutilizar get_my_analytics para obtener todas las estadísticas.
    # Los params para get_my_analytics (como $select) se pueden pasar si es necesario.
    analytics_params = {}
    if params.get("$select_analytics"): # Permite un select específico para la llamada de get_my_analytics
        analytics_params["$select"] = params["$select_analytics"]

    analytics_result = get_my_analytics(client, analytics_params)

    if analytics_result.get("status") == "success":
        all_activities_stats = analytics_result.get("data", [])
        focus_stats_entries: List[Dict[str, Any]] = []
        
        if isinstance(all_activities_stats, list):
            for stat_entry in all_activities_stats:
                # Cada stat_entry es un activityStatistic
                # (ej. {"activity": "collaboration", "duration": "PT21H47M4S", ...})
                if isinstance(stat_entry, dict) and stat_entry.get("activity", "").lower() == "focus":
                    focus_stats_entries.append(stat_entry)
        
        if focus_stats_entries:
            logger.info(f"Estadísticas de tiempo de concentración ('focus') encontradas: {len(focus_stats_entries)} entrada(s).")
            return {
                "status": "success", 
                "data": focus_stats_entries, 
                "message": (
                    "Estadísticas de tiempo de concentración obtenidas. Para ver eventos de calendario de focus, "
                    "use la acción 'calendar_list_events' con el filtro de categoría o asunto apropiado."
                )
            }
        else:
            logger.info("No se encontraron estadísticas específicas para la actividad 'focus' en los datos de analíticas.")
            return {
                "status": "success", # La llamada a analytics fue exitosa, pero no hay datos de focus.
                "data": [],
                "message": "No se encontraron estadísticas de tiempo de concentración. El plan podría no estar activo o no haber datos recientes."
            }
    else:
        # Propagar el error de get_my_analytics
        logger.error(f"No se pudo obtener la información del plan de concentración porque falló la obtención de analíticas: {analytics_result.get('message')}")
        # Re-empaquetar el error para que la acción original sea 'viva_get_focus_plan'
        propagated_error = analytics_result.copy()
        propagated_error["action"] = action_name
        return propagated_error