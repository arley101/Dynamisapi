# app/actions/graph_actions.py
# -*- coding: utf-8 -*-
import logging
import requests # Para requests.exceptions.HTTPError
from typing import Dict, List, Optional, Any

from app.core.config import settings # Para acceder a GRAPH_API_DEFAULT_SCOPE, GRAPH_API_BASE_URL
from app.shared.helpers.http_client import AuthenticatedHttpClient

logger = logging.getLogger(__name__)

def _handle_generic_graph_api_error(e: Exception, action_name: str, params_for_log: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Helper para manejar errores genéricos de Graph API."""
    log_message = f"Error en Graph Action '{action_name}'"
    if params_for_log:
        # Evitar loguear el payload si es muy grande o sensible
        safe_params = {k: v for k, v in params_for_log.items() if k not in ['payload', 'json_data', 'data']}
        if 'payload' in params_for_log or 'json_data' in params_for_log or 'data' in params_for_log:
            safe_params["payload_type"] = type(params_for_log.get('payload') or params_for_log.get('json_data') or params_for_log.get('data')).__name__
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
        except Exception:
            details_str = e.response.text[:500] if e.response.text else "No response body"
            
    return {
        "status": "error",
        "action": action_name,
        "message": f"Error ejecutando acción genérica de Graph '{action_name}': {type(e).__name__}",
        "http_status": status_code_int,
        "details": details_str,
        "graph_error_code": graph_error_code
    }

def generic_get(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Realiza una solicitud GET genérica a Microsoft Graph API.
    Requiere 'graph_path' (ej. "/me/messages", "users/{id}/drive").
    Opcional: 'base_url' (si es diferente a GRAPH_API_BASE_URL), 'api_version' (para beta, etc.),
              'query_params' (dict para OData como $select, $filter), 'custom_scope' (lista de strings).
    """
    action_name = "graph_generic_get"
    graph_path: Optional[str] = params.get("graph_path")
    
    if not graph_path:
        return {"status": "error", "action": action_name, "message": "'graph_path' es requerido (ej. '/me', '/users/{id}/drive/root/children').", "http_status": 400}

    base_url_override = params.get("base_url", settings.GRAPH_API_BASE_URL)
    # Permitir especificar beta endpoint o una versión diferente
    if params.get("api_version") == "beta":
        base_url_override = "https://graph.microsoft.com/beta"
    
    full_url = f"{str(base_url_override).rstrip('/')}/{graph_path.lstrip('/')}"
    
    query_api_params: Optional[Dict[str, Any]] = params.get("query_params")
    custom_scope_list: Optional[List[str]] = params.get("custom_scope")
    scope_to_use = custom_scope_list if custom_scope_list else settings.GRAPH_API_DEFAULT_SCOPE

    logger.info(f"Ejecutando GET genérico a Graph: {full_url} con scope: {scope_to_use} y params: {query_api_params}")
    try:
        response = client.get(full_url, scope=scope_to_use, params=query_api_params)
        # Intentar devolver JSON, si falla, devolver texto crudo.
        try:
            data = response.json()
        except requests.exceptions.JSONDecodeError:
            data = response.text
            logger.info(f"Respuesta GET genérica a Graph para {full_url} no es JSON, devolviendo texto. Status: {response.status_code}")
        return {"status": "success", "data": data, "http_status": response.status_code}
    except Exception as e:
        return _handle_generic_graph_api_error(e, action_name, params)

def generic_post(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Realiza una solicitud POST genérica a Microsoft Graph API.
    Requiere 'graph_path' y 'payload' (dict para el cuerpo JSON).
    Opcional: 'base_url', 'api_version', 'custom_scope', 'custom_headers'.
    """
    action_name = "generic_post"
    graph_path: Optional[str] = params.get("graph_path")
    payload: Optional[Dict[str, Any]] = params.get("payload")

    if not graph_path:
        return {"status": "error", "action": action_name, "message": "'graph_path' es requerido.", "http_status": 400}
    # El payload puede ser opcional para algunos POSTs que solo disparan una acción sin cuerpo.
    # if payload is None: # Comentado para permitir POST sin payload
    #     return {"status": "error", "action": action_name, "message": "'payload' (dict) es requerido para POST.", "http_status": 400}


    base_url_override = params.get("base_url", settings.GRAPH_API_BASE_URL)
    if params.get("api_version") == "beta":
        base_url_override = "https://graph.microsoft.com/beta"
        
    full_url = f"{str(base_url_override).rstrip('/')}/{graph_path.lstrip('/')}"
    
    custom_scope_list: Optional[List[str]] = params.get("custom_scope")
    scope_to_use = custom_scope_list if custom_scope_list else settings.GRAPH_API_DEFAULT_SCOPE
    
    # Permitir pasar cabeceras personalizadas si es necesario (ej. Content-Type diferente, If-Match)
    # El AuthenticatedHttpClient ya añade Authorization y Content-Type: application/json por defecto si hay payload.
    custom_headers: Optional[Dict[str, str]] = params.get("custom_headers")

    logger.info(f"Ejecutando POST genérico a Graph: {full_url} con scope: {scope_to_use}. Payload presente: {bool(payload)}")
    try:
        # AuthenticatedHttpClient maneja json_data internamente
        response = client.post(full_url, scope=scope_to_use, json_data=payload, headers=custom_headers)
        
        # Intentar devolver JSON, si falla (ej. 202 Accepted o 204 No Content), devolver estado y mensaje.
        if response.status_code in [201, 200] and response.content:
            try:
                data = response.json()
                return {"status": "success", "data": data, "http_status": response.status_code}
            except requests.exceptions.JSONDecodeError:
                logger.info(f"Respuesta POST genérica a Graph para {full_url} no es JSON (status {response.status_code}), devolviendo texto.")
                return {"status": "success", "data": response.text, "http_status": response.status_code}
        elif response.status_code in [202, 204]: # Accepted o No Content
             logger.info(f"Solicitud POST genérica a Graph para {full_url} exitosa con status {response.status_code} (sin contenido de respuesta esperado).")
             return {"status": "success", "message": f"Operación POST completada con estado {response.status_code}.", "http_status": response.status_code, "data": None}
        else: # Otros códigos de éxito con posible contenido no JSON
            logger.info(f"Respuesta POST genérica a Graph para {full_url} con status {response.status_code}. Contenido: {response.text[:100]}...")
            return {"status": "success", "data": response.text, "http_status": response.status_code}

    except Exception as e:
        return _handle_generic_graph_api_error(e, action_name, params)

# Se podrían añadir generic_patch, generic_put, generic_delete siguiendo el mismo patrón.
# Por ahora, nos centramos en los que estaban definidos en el action_mapper original.