# app/actions/openai_actions.py
import logging
import requests # Para requests.exceptions.HTTPError
# import json # No se usa directamente json.loads o .dumps si AuthenticatedHttpClient maneja .json()
from typing import Dict, List, Optional, Any, Union

# Importar la configuración y el cliente HTTP autenticado
from app.core.config import settings
from app.shared.helpers.http_client import AuthenticatedHttpClient

logger = logging.getLogger(__name__)

# Validar configuración esencial al cargar el módulo
# settings.AZURE_OPENAI_RESOURCE_ENDPOINT y settings.AZURE_OPENAI_API_VERSION son Optional,
# así que el chequeo debe ser si su valor es None o no.
# El validador en config.py ya construye OPENAI_API_DEFAULT_SCOPE si el endpoint existe.

def _check_openai_config() -> bool:
    if not settings.AZURE_OPENAI_RESOURCE_ENDPOINT:
        logger.critical("CRÍTICO: Configuración 'AZURE_OPENAI_RESOURCE_ENDPOINT' no definida (vía settings). Las acciones de OpenAI no funcionarán.")
        return False
    if not settings.AZURE_OPENAI_API_VERSION: # Este es un string, así que no debería ser None si está en .env o default.
        logger.critical("CRÍTICO: Configuración 'AZURE_OPENAI_API_VERSION' no definida (vía settings). Las acciones de OpenAI no funcionarán.")
        return False
    if not settings.OPENAI_API_DEFAULT_SCOPE: # Este es List[str] y se construye en config.py
        logger.critical("CRÍTICO: Scope 'OPENAI_API_DEFAULT_SCOPE' no pudo ser construido (vía settings), probablemente falta AZURE_OPENAI_RESOURCE_ENDPOINT.")
        return False
    return True

# ---- FUNCIONES DE ACCIÓN PARA AZURE OPENAI ----

def chat_completion(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Dict[str, Any]:
    if not _check_openai_config():
        return {"status": "error", "message": "Configuración de Azure OpenAI incompleta en el servidor.", "http_status": 500}

    deployment_id: Optional[str] = params.get("deployment_id")
    messages: Optional[List[Dict[str, str]]] = params.get("messages")

    if not deployment_id:
        return {"status": "error", "message": "Parámetro 'deployment_id' (nombre del despliegue OpenAI) es requerido.", "http_status": 400}
    if not messages or not isinstance(messages, list) or not all(isinstance(m, dict) and 'role' in m and 'content' in m for m in messages):
        return {"status": "error", "message": "Parámetro 'messages' (lista de {'role': '...', 'content': '...'}) es requerido y debe tener formato válido.", "http_status": 400}

    if params.get("stream", False):
        logger.warning(f"Solicitud de Chat Completion para despliegue '{deployment_id}' con stream=true. Esta acción no soporta streaming actualmente y procederá de forma síncrona.")

    # Asegurar que el endpoint no tenga doble //
    base_url = str(settings.AZURE_OPENAI_RESOURCE_ENDPOINT).rstrip('/')
    url = f"{base_url}/openai/deployments/{deployment_id}/chat/completions?api-version={settings.AZURE_OPENAI_API_VERSION}"

    payload: Dict[str, Any] = {"messages": messages}
    allowed_api_params = [
        "temperature", "max_tokens", "top_p", "frequency_penalty",
        "presence_penalty", "stop", "logit_bias", "user", "n",
        "logprobs", "top_logprobs", "response_format", "seed", "tools", "tool_choice"
    ]
    for param_key, value in params.items():
        if param_key in allowed_api_params and value is not None:
            payload[param_key] = value

    logger.info(f"Enviando petición de Chat Completion a AOAI despliegue '{deployment_id}' ({len(messages)} mensajes).")
    logger.debug(f"Payload Chat Completion (sin 'messages'): { {k:v for k,v in payload.items() if k != 'messages'} }")

    try:
        response = client.post(
            url=url,
            scope=settings.OPENAI_API_DEFAULT_SCOPE, # Usar el scope de settings
            json_data=payload,
            timeout=settings.DEFAULT_API_TIMEOUT
        )
        response_data = response.json()
        return {"status": "success", "data": response_data}
    except requests.exceptions.HTTPError as http_err:
        error_details = http_err.response.text if http_err.response else "No response body"
        status_code = http_err.response.status_code if http_err.response else 500
        logger.error(f"Error HTTP en Chat Completion AOAI '{deployment_id}': {status_code} - {error_details[:500]}", exc_info=False)
        return {"status": "error", "message": f"Error HTTP: {status_code}", "details": error_details, "http_status": status_code}
    except ValueError as val_err: # Puede ser de _get_access_token o JSON malformado en respuesta
        logger.error(f"Error de Valor (auth/JSON) en Chat Completion AOAI '{deployment_id}': {val_err}", exc_info=True)
        return {"status": "error", "message": "Error de autenticación, configuración o formato de respuesta JSON.", "details": str(val_err), "http_status":500}
    except Exception as e:
        logger.error(f"Error inesperado en Chat Completion AOAI '{deployment_id}': {type(e).__name__} - {e}", exc_info=True)
        return {"status": "error", "message": f"Error inesperado en Chat Completion: {type(e).__name__}", "details": str(e), "http_status":500}

def get_embedding(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Dict[str, Any]:
    if not _check_openai_config():
        return {"status": "error", "message": "Configuración de Azure OpenAI incompleta en el servidor.", "http_status": 500}

    deployment_id: Optional[str] = params.get("deployment_id")
    input_data: Optional[Union[str, List[str]]] = params.get("input")
    user_param: Optional[str] = params.get("user")
    input_type_param: Optional[str] = params.get("input_type")

    if not deployment_id:
        return {"status": "error", "message": "Parámetro 'deployment_id' (nombre del despliegue OpenAI Embeddings) es requerido.", "http_status": 400}
    if not input_data:
        return {"status": "error", "message": "Parámetro 'input' (string o lista de strings) es requerido.", "http_status": 400}

    base_url = str(settings.AZURE_OPENAI_RESOURCE_ENDPOINT).rstrip('/')
    url = f"{base_url}/openai/deployments/{deployment_id}/embeddings?api-version={settings.AZURE_OPENAI_API_VERSION}"

    payload: Dict[str, Any] = {"input": input_data}
    if user_param: payload["user"] = user_param
    if input_type_param: payload["input_type"] = input_type_param

    log_input_type = "lista de strings" if isinstance(input_data, list) else "string"
    logger.info(f"Generando Embeddings AOAI con despliegue '{deployment_id}' para entrada tipo '{log_input_type}'.")
    logger.debug(f"Payload Embeddings: {payload}")

    try:
        response = client.post(
            url=url,
            scope=settings.OPENAI_API_DEFAULT_SCOPE,
            json_data=payload,
            timeout=settings.DEFAULT_API_TIMEOUT
        )
        response_data = response.json()
        return {"status": "success", "data": response_data}
    except requests.exceptions.HTTPError as http_err:
        error_details = http_err.response.text if http_err.response else "No response body"
        status_code = http_err.response.status_code if http_err.response else 500
        logger.error(f"Error HTTP generando Embeddings AOAI '{deployment_id}': {status_code} - {error_details[:500]}", exc_info=False)
        return {"status": "error", "message": f"Error HTTP: {status_code}", "details": error_details, "http_status": status_code}
    except ValueError as val_err:
        logger.error(f"Error de Valor (auth/JSON) generando Embeddings AOAI '{deployment_id}': {val_err}", exc_info=True)
        return {"status": "error", "message": "Error de autenticación, configuración o formato de respuesta JSON.", "details": str(val_err), "http_status":500}
    except Exception as e:
        logger.error(f"Error inesperado generando Embeddings AOAI '{deployment_id}': {type(e).__name__} - {e}", exc_info=True)
        return {"status": "error", "message": f"Error inesperado en Embeddings: {type(e).__name__}", "details": str(e), "http_status":500}

def completion(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Dict[str, Any]:
    if not _check_openai_config():
        return {"status": "error", "message": "Configuración de Azure OpenAI incompleta en el servidor.", "http_status": 500}

    deployment_id: Optional[str] = params.get("deployment_id")
    prompt: Optional[Union[str, List[str]]] = params.get("prompt")

    if not deployment_id:
        return {"status": "error", "message": "Parámetro 'deployment_id' es requerido.", "http_status": 400}
    if not prompt:
        return {"status": "error", "message": "Parámetro 'prompt' (string o lista de strings) es requerido.", "http_status": 400}

    base_url = str(settings.AZURE_OPENAI_RESOURCE_ENDPOINT).rstrip('/')
    url = f"{base_url}/openai/deployments/{deployment_id}/completions?api-version={settings.AZURE_OPENAI_API_VERSION}"

    payload: Dict[str, Any] = {"prompt": prompt}
    allowed_api_params = [
        "max_tokens", "temperature", "top_p", "frequency_penalty",
        "presence_penalty", "stop", "logit_bias", "user", "n",
        "logprobs", "echo", "best_of"
    ]
    for param_key, value in params.items():
        if param_key in allowed_api_params and value is not None:
            payload[param_key] = value

    logger.info(f"Enviando petición de Completion a AOAI despliegue '{deployment_id}'.")
    logger.debug(f"Payload Completion (sin 'prompt'): { {k:v for k,v in payload.items() if k != 'prompt'} }")

    try:
        response = client.post(
            url=url,
            scope=settings.OPENAI_API_DEFAULT_SCOPE,
            json_data=payload,
            timeout=settings.DEFAULT_API_TIMEOUT
        )
        response_data = response.json()
        return {"status": "success", "data": response_data}
    except requests.exceptions.HTTPError as http_err:
        error_details = http_err.response.text if http_err.response else "No response body"
        status_code = http_err.response.status_code if http_err.response else 500
        logger.error(f"Error HTTP en Completion AOAI '{deployment_id}': {status_code} - {error_details[:500]}", exc_info=False)
        return {"status": "error", "message": f"Error HTTP: {status_code}", "details": error_details, "http_status": status_code}
    except ValueError as val_err:
        logger.error(f"Error de Valor (auth/JSON) en Completion AOAI '{deployment_id}': {val_err}", exc_info=True)
        return {"status": "error", "message": "Error de autenticación, configuración o formato de respuesta JSON.", "details": str(val_err), "http_status":500}
    except Exception as e:
        logger.error(f"Error inesperado en Completion AOAI '{deployment_id}': {type(e).__name__} - {e}", exc_info=True)
        return {"status": "error", "message": f"Error inesperado en Completion: {type(e).__name__}", "details": str(e), "http_status":500}

def list_models(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Dict[str, Any]:
    if not _check_openai_config(): # Solo necesita endpoint y api-version para esta llamada
        if not settings.AZURE_OPENAI_RESOURCE_ENDPOINT or not settings.AZURE_OPENAI_API_VERSION:
             return {"status": "error", "message": "Configuración de Azure OpenAI incompleta para listar modelos (endpoint o api-version).", "http_status": 500}
        # No necesita el scope específico aquí si el cliente HTTP no lo usa para llamadas a /models,
        # pero AuthenticatedHttpClient lo requiere, así que el scope debe ser válido.

    base_url = str(settings.AZURE_OPENAI_RESOURCE_ENDPOINT).rstrip('/')
    url = f"{base_url}/openai/models?api-version={settings.AZURE_OPENAI_API_VERSION}"

    logger.info(f"Listando modelos disponibles en el recurso Azure OpenAI: {settings.AZURE_OPENAI_RESOURCE_ENDPOINT}")
    try:
        response = client.get(
            url=url,
            scope=settings.OPENAI_API_DEFAULT_SCOPE, # Aunque /models puede no necesitar autenticación de token de recurso específico, el cliente lo requiere
            timeout=settings.DEFAULT_API_TIMEOUT
        )
        response_data = response.json()
        return {"status": "success", "data": response_data.get("data", [])}
    except requests.exceptions.HTTPError as http_err:
        error_details = http_err.response.text if http_err.response else "No response body"
        status_code = http_err.response.status_code if http_err.response else 500
        logger.error(f"Error HTTP listando modelos AOAI: {status_code} - {error_details[:500]}", exc_info=False)
        return {"status": "error", "message": f"Error HTTP: {status_code}", "details": error_details, "http_status": status_code}
    except ValueError as val_err:
        logger.error(f"Error de Valor (auth/JSON) listando modelos AOAI: {val_err}", exc_info=True)
        return {"status": "error", "message": "Error de autenticación, configuración o formato de respuesta JSON.", "details": str(val_err), "http_status":500}
    except Exception as e:
        logger.error(f"Error inesperado listando modelos AOAI: {type(e).__name__} - {e}", exc_info=True)
        return {"status": "error", "message": f"Error inesperado listando modelos: {type(e).__name__}", "details": str(e), "http_status":500}

# --- FIN DEL MÓDULO actions/openai_actions.py ---