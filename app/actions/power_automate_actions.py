# app/actions/power_automate_actions.py
import logging
import os
import requests # Para ejecutar_flow (llamada directa a trigger) y tipos de excepción
import json
from typing import Dict, Optional, Any

# Importar la configuración y el cliente HTTP autenticado
from app.core.config import settings
from app.shared.helpers.http_client import AuthenticatedHttpClient # Para llamadas ARM

logger = logging.getLogger(__name__)

# API Version para Logic Apps (Power Automate flows son Logic Apps bajo el capó)
LOGIC_APPS_API_VERSION = "2019-05-01" # Esto podría ir a settings

def _handle_pa_api_error(e: Exception, action_name: str) -> Dict[str, Any]:
    logger.error(f"Error en Power Automate action '{action_name}': {type(e).__name__} - {e}", exc_info=True)
    details = str(e)
    status_code = 500
    if isinstance(e, requests.exceptions.HTTPError) and e.response is not None:
        status_code = e.response.status_code
        try:
            details = e.response.json()
        except json.JSONDecodeError:
            details = e.response.text
    return {
        "status": "error", "action": action_name,
        "message": f"Error en {action_name}: {type(e).__name__}",
        "http_status": status_code, "details": details
    }


# ---- FUNCIONES DE ACCIÓN PARA POWER AUTOMATE (Workflows/Logic Apps) ----
# Estas funciones usarán AuthenticatedHttpClient con el scope de Azure Management.

def listar_flows(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Dict[str, Any]:
    """Lista workflows (flujos) en una suscripción y grupo de recursos."""
    suscripcion_id = params.get('suscripcion_id', settings.AZURE_SUBSCRIPTION_ID)
    grupo_recurso = params.get('grupo_recurso', settings.AZURE_RESOURCE_GROUP)

    if not suscripcion_id or not grupo_recurso:
        msg = "Parámetros 'suscripcion_id' y 'grupo_recurso' (o sus equivalentes en settings) son requeridos."
        logger.error(f"listar_flows: {msg}")
        return {"status": "error", "message": msg, "http_status": 400}

    # El scope para Azure Management API ya está en settings.AZURE_MGMT_DEFAULT_SCOPE
    url = f"{settings.AZURE_MGMT_API_BASE_URL}/subscriptions/{suscripcion_id}/resourceGroups/{grupo_recurso}/providers/Microsoft.Logic/workflows?api-version={LOGIC_APPS_API_VERSION}"
    logger.info(f"Listando flujos en Suscripción '{suscripcion_id}', GrupoRecursos '{grupo_recurso}'")
    try:
        response = client.get(url, scope=settings.AZURE_MGMT_DEFAULT_SCOPE, timeout=settings.DEFAULT_API_TIMEOUT)
        response_data = response.json()
        return {"status": "success", "data": response_data.get("value", [])}
    except Exception as e:
        return _handle_pa_api_error(e, "listar_flows")


def obtener_flow(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Dict[str, Any]:
    nombre_flow: Optional[str] = params.get("nombre_flow")
    if not nombre_flow:
        return {"status": "error", "message": "'nombre_flow' es requerido.", "http_status": 400}

    suscripcion_id = params.get('suscripcion_id', settings.AZURE_SUBSCRIPTION_ID)
    grupo_recurso = params.get('grupo_recurso', settings.AZURE_RESOURCE_GROUP)
    if not suscripcion_id or not grupo_recurso:
        msg = "Parámetros 'suscripcion_id' y 'grupo_recurso' (o sus equivalentes en settings) son requeridos."
        logger.error(f"obtener_flow: {msg}")
        return {"status": "error", "message": msg, "http_status": 400}

    url = f"{settings.AZURE_MGMT_API_BASE_URL}/subscriptions/{suscripcion_id}/resourceGroups/{grupo_recurso}/providers/Microsoft.Logic/workflows/{nombre_flow}?api-version={LOGIC_APPS_API_VERSION}"
    logger.info(f"Obteniendo flow '{nombre_flow}' en RG '{grupo_recurso}'")
    try:
        response = client.get(url, scope=settings.AZURE_MGMT_DEFAULT_SCOPE, timeout=settings.DEFAULT_API_TIMEOUT)
        flow_data = response.json()
        return {"status": "success", "data": flow_data}
    except requests.exceptions.HTTPError as http_err:
        if http_err.response is not None and http_err.response.status_code == 404:
            return {"status": "error", "message": f"Flow '{nombre_flow}' no encontrado.", "details": http_err.response.text, "http_status": 404}
        return _handle_pa_api_error(http_err, "obtener_flow")
    except Exception as e:
        return _handle_pa_api_error(e, "obtener_flow")


def ejecutar_flow(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Ejecuta un flujo de Power Automate que tiene un trigger HTTP.
    La URL del trigger y cualquier autenticación específica del trigger deben ser proporcionadas.
    El `client` (AuthenticatedHttpClient) NO se usa aquí porque es una llamada HTTP directa al trigger del flow.
    """
    flow_trigger_url: Optional[str] = params.get("flow_trigger_url")
    payload: Optional[Dict[str, Any]] = params.get("payload")
    # Los headers para el trigger del flow se pueden pasar en params si son necesarios (ej. API Key)
    custom_headers_for_trigger: Optional[Dict[str, str]] = params.get("trigger_headers")

    if not flow_trigger_url:
        return {"status": "error", "message": "Parámetro 'flow_trigger_url' (URL del trigger HTTP del flujo) es requerido.", "http_status": 400}

    request_headers = custom_headers_for_trigger or {}
    if payload and 'Content-Type' not in request_headers:
        # Solo añadir Content-Type si no está ya en los custom_headers
        request_headers.setdefault('Content-Type', 'application/json')


    logger.info(f"Ejecutando trigger de Power Automate flow: POST {flow_trigger_url}")
    try:
        # Usar requests.post directamente, no el client autenticado para Graph/ARM
        response = requests.post(
            flow_trigger_url,
            headers=request_headers,
            json=payload if payload and request_headers.get('Content-Type') == 'application/json' else None,
            data=json.dumps(payload) if payload and request_headers.get('Content-Type') != 'application/json' else None, # Asegurar data es string si no es json
            timeout=max(settings.DEFAULT_API_TIMEOUT, 120) # Timeout más largo para triggers de flow
        )
        response.raise_for_status() # Lanza HTTPError para 4xx/5xx

        logger.info(f"Trigger de flow '{flow_trigger_url}' invocado. Status: {response.status_code}")
        try:
            response_body = response.json()
        except json.JSONDecodeError:
            response_body = response.text if response.text else "Respuesta vacía del trigger del flow."

        # POST a un trigger de flow puede devolver 200 OK, 202 Accepted, u otros.
        return {
            "status": "success" if response.ok else "accepted", # "accepted" para 202
            "message": f"Respuesta del trigger del flujo: {response.reason}",
            "http_status": response.status_code,
            "response_body": response_body,
            "response_headers": dict(response.headers) # Incluir headers de respuesta puede ser útil
        }
    except requests.exceptions.RequestException as e:
        error_body = e.response.text[:500] if e.response is not None else str(e)
        logger.error(f"Error Request ejecutando trigger de flow '{flow_trigger_url}': {e}. Respuesta: {error_body}", exc_info=True)
        status_code_err = e.response.status_code if e.response is not None else 500
        return {"status": "error", "message": f"Error API ejecutando trigger de flow: {type(e).__name__}", "details": error_body, "http_status": status_code_err}
    except Exception as e:
        logger.error(f"Error inesperado ejecutando trigger de flow '{flow_trigger_url}': {e}", exc_info=True)
        return {"status": "error", "message": f"Error inesperado al ejecutar trigger de flow: {type(e).__name__}", "details": str(e), "http_status":500}


def obtener_estado_ejecucion_flow(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Dict[str, Any]:
    nombre_flow: Optional[str] = params.get("nombre_flow")
    run_id: Optional[str] = params.get("run_id")
    if not nombre_flow or not run_id:
        return {"status": "error", "message": "Parámetros 'nombre_flow' y 'run_id' son requeridos.", "http_status": 400}

    suscripcion_id = params.get('suscripcion_id', settings.AZURE_SUBSCRIPTION_ID)
    grupo_recurso = params.get('grupo_recurso', settings.AZURE_RESOURCE_GROUP)
    if not suscripcion_id or not grupo_recurso:
        msg = "Parámetros 'suscripcion_id' y 'grupo_recurso' (o sus equivalentes en settings) son requeridos."
        logger.error(f"obtener_estado_ejecucion_flow: {msg}")
        return {"status": "error", "message": msg, "http_status": 400}

    url = f"{settings.AZURE_MGMT_API_BASE_URL}/subscriptions/{suscripcion_id}/resourceGroups/{grupo_recurso}/providers/Microsoft.Logic/workflows/{nombre_flow}/runs/{run_id}?api-version={LOGIC_APPS_API_VERSION}"
    logger.info(f"Obteniendo estado de ejecución '{run_id}' del flow '{nombre_flow}'")
    try:
        response = client.get(url, scope=settings.AZURE_MGMT_DEFAULT_SCOPE, timeout=settings.DEFAULT_API_TIMEOUT)
        run_data = response.json()
        return {"status": "success", "data": run_data}
    except Exception as e:
        return _handle_pa_api_error(e, "obtener_estado_ejecucion_flow")

# --- FIN DEL MÓDULO actions/power_automate_actions.py ---