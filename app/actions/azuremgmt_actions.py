# app/actions/azuremgmt_actions.py
# -*- coding: utf-8 -*-
import logging
import requests # Para requests.exceptions.HTTPError
from typing import Dict, List, Optional, Any

from app.core.config import settings # Para acceder a AZURE_MGMT_DEFAULT_SCOPE, AZURE_MGMT_API_BASE_URL
from app.shared.helpers.http_client import AuthenticatedHttpClient

logger = logging.getLogger(__name__)

# Helper para manejar errores de ARM API de forma centralizada
def _handle_azure_mgmt_api_error(e: Exception, action_name: str, params_for_log: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    log_message = f"Error en Azure Management action '{action_name}'"
    if params_for_log:
        safe_params = {k: v for k, v in params_for_log.items() if k not in ['deployment_properties', 'template', 'parameters']}
        log_message += f" con params: {safe_params}"
    
    logger.error(f"{log_message}: {type(e).__name__} - {str(e)}", exc_info=True)
    
    details_str = str(e)
    status_code_int = 500
    arm_error_code = None

    if isinstance(e, requests.exceptions.HTTPError) and e.response is not None:
        status_code_int = e.response.status_code
        try:
            error_data = e.response.json()
            error_info = error_data.get("error", {})
            details_str = error_info.get("message", e.response.text)
            arm_error_code = error_info.get("code")
        except Exception: # Si la respuesta de error no es JSON o no tiene la estructura esperada
            details_str = e.response.text[:500] if e.response.text else "No response body"
            
    return {
        "status": "error",
        "action": action_name,
        "message": f"Error ejecutando {action_name}: {type(e).__name__}",
        "http_status": status_code_int,
        "details": details_str,
        "arm_error_code": arm_error_code
    }

# --- Implementación de Acciones de Azure Management ---

def list_resource_groups(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Lista los grupos de recursos en una suscripción.
    Requiere 'subscription_id' en params o configurado en settings.
    """
    action_name = "azure_list_resource_groups"
    subscription_id = params.get("subscription_id", settings.AZURE_SUBSCRIPTION_ID)
    if not subscription_id:
        return {"status": "error", "action": action_name, "message": "'subscription_id' es requerido.", "http_status": 400}

    api_version = params.get("api_version", "2021-04-01") # Versión común para RGs
    url = f"{settings.AZURE_MGMT_API_BASE_URL}/subscriptions/{subscription_id}/resourcegroups?api-version={api_version}"
    
    odata_params: Dict[str, Any] = {}
    if params.get("$top"): odata_params["$top"] = params["$top"]
    if params.get("$filter"): odata_params["$filter"] = params["$filter"]
    
    logger.info(f"Listando grupos de recursos para la suscripción '{subscription_id}' con OData params: {odata_params}")
    try:
        response = client.get(url, scope=settings.AZURE_MGMT_DEFAULT_SCOPE, params=odata_params)
        return {"status": "success", "data": response.json().get("value", [])}
    except Exception as e:
        return _handle_azure_mgmt_api_error(e, action_name, params)

def list_resources_in_rg(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Lista los recursos dentro de un grupo de recursos específico.
    Requiere 'subscription_id', 'resource_group_name'.
    """
    action_name = "azure_list_resources_in_rg"
    subscription_id = params.get("subscription_id", settings.AZURE_SUBSCRIPTION_ID)
    resource_group_name = params.get("resource_group_name", settings.AZURE_RESOURCE_GROUP)

    if not subscription_id:
        return {"status": "error", "action": action_name, "message": "'subscription_id' es requerido.", "http_status": 400}
    if not resource_group_name:
        return {"status": "error", "action": action_name, "message": "'resource_group_name' es requerido.", "http_status": 400}

    api_version = params.get("api_version", "2021-04-01")
    url = f"{settings.AZURE_MGMT_API_BASE_URL}/subscriptions/{subscription_id}/resourceGroups/{resource_group_name}/resources?api-version={api_version}"
    
    odata_params: Dict[str, Any] = {}
    if params.get("$top"): odata_params["$top"] = params["$top"]
    if params.get("$filter"): odata_params["$filter"] = params["$filter"] # Ej: "resourceType eq 'Microsoft.Web/sites'"
    
    logger.info(f"Listando recursos en RG '{resource_group_name}', suscripción '{subscription_id}'")
    try:
        response = client.get(url, scope=settings.AZURE_MGMT_DEFAULT_SCOPE, params=odata_params)
        return {"status": "success", "data": response.json().get("value", [])}
    except Exception as e:
        return _handle_azure_mgmt_api_error(e, action_name, params)

def get_resource(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Obtiene detalles de un recurso específico por su ID completo de ARM.
    Requiere 'resource_id'.
    """
    action_name = "azure_get_resource"
    resource_id = params.get("resource_id") # Ej: /subscriptions/.../resourceGroups/.../providers/Microsoft.Web/sites/...
    api_version = params.get("api_version") # La api-version depende del tipo de recurso

    if not resource_id:
        return {"status": "error", "action": action_name, "message": "'resource_id' (ID completo de ARM) es requerido.", "http_status": 400}
    if not api_version:
        return {"status": "error", "action": action_name, "message": "'api_version' específica para el tipo de recurso es requerida.", "http_status": 400}

    url = f"{settings.AZURE_MGMT_API_BASE_URL}{resource_id}?api-version={api_version}"
    
    logger.info(f"Obteniendo detalles del recurso ARM ID: '{resource_id}'")
    try:
        response = client.get(url, scope=settings.AZURE_MGMT_DEFAULT_SCOPE)
        return {"status": "success", "data": response.json()}
    except Exception as e:
        return _handle_azure_mgmt_api_error(e, action_name, params)

def restart_function_app(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Reinicia una Azure Function App.
    Requiere 'subscription_id', 'resource_group_name', 'function_app_name'.
    """
    action_name = "azure_restart_function_app"
    subscription_id = params.get("subscription_id", settings.AZURE_SUBSCRIPTION_ID)
    resource_group_name = params.get("resource_group_name", settings.AZURE_RESOURCE_GROUP)
    function_app_name = params.get("function_app_name")

    if not subscription_id: return {"status": "error", "action": action_name, "message": "'subscription_id' es requerido.", "http_status": 400}
    if not resource_group_name: return {"status": "error", "action": action_name, "message": "'resource_group_name' es requerido.", "http_status": 400}
    if not function_app_name: return {"status": "error", "action": action_name, "message": "'function_app_name' es requerido.", "http_status": 400}

    api_version = "2022-03-01" # API version para Microsoft.Web/sites
    url = f"{settings.AZURE_MGMT_API_BASE_URL}/subscriptions/{subscription_id}/resourceGroups/{resource_group_name}/providers/Microsoft.Web/sites/{function_app_name}/restart?api-version={api_version}"
    
    logger.info(f"Reiniciando Function App '{function_app_name}' en RG '{resource_group_name}'")
    try:
        # Restart es una operación POST que no devuelve cuerpo en éxito (204 o similar), o a veces 200 OK.
        response = client.post(url, scope=settings.AZURE_MGMT_DEFAULT_SCOPE) # No necesita json_data
        # La respuesta de un restart exitoso puede ser un 204 No Content, o 200 OK con info.
        # Si es 204, response.json() fallará.
        if response.status_code == 204:
             return {"status": "success", "message": f"Function App '{function_app_name}' reiniciada exitosamente (204 No Content)."}
        return {"status": "success", "message": f"Solicitud de reinicio para Function App '{function_app_name}' enviada.", "data": response.json() if response.content else None, "http_status": response.status_code}
    except Exception as e:
        return _handle_azure_mgmt_api_error(e, action_name, params)

# --- Implementaciones Adicionales (Ejemplos, requieren que el usuario provea parámetros detallados) ---

def list_functions(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Lista las funciones dentro de una Azure Function App.
    Requiere 'subscription_id', 'resource_group_name', 'function_app_name'.
    """
    action_name = "azure_list_functions"
    subscription_id = params.get("subscription_id", settings.AZURE_SUBSCRIPTION_ID)
    resource_group_name = params.get("resource_group_name", settings.AZURE_RESOURCE_GROUP)
    function_app_name = params.get("function_app_name")

    if not all([subscription_id, resource_group_name, function_app_name]):
        return {"status": "error", "action": action_name, "message": "Se requieren 'subscription_id', 'resource_group_name', y 'function_app_name'.", "http_status": 400}

    api_version = "2022-03-01" # API version para Microsoft.Web/sites/functions
    url = f"{settings.AZURE_MGMT_API_BASE_URL}/subscriptions/{subscription_id}/resourceGroups/{resource_group_name}/providers/Microsoft.Web/sites/{function_app_name}/functions?api-version={api_version}"
    
    logger.info(f"Listando funciones para la Function App '{function_app_name}'")
    try:
        response = client.get(url, scope=settings.AZURE_MGMT_DEFAULT_SCOPE)
        return {"status": "success", "data": response.json().get("value", [])}
    except Exception as e:
        return _handle_azure_mgmt_api_error(e, action_name, params)

def get_function_status(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Obtiene el estado de una función específica dentro de una Azure Function App.
    Requiere 'subscription_id', 'resource_group_name', 'function_app_name', 'function_name'.
    """
    action_name = "azure_get_function_status"
    subscription_id = params.get("subscription_id", settings.AZURE_SUBSCRIPTION_ID)
    resource_group_name = params.get("resource_group_name", settings.AZURE_RESOURCE_GROUP)
    function_app_name = params.get("function_app_name")
    function_name = params.get("function_name")

    if not all([subscription_id, resource_group_name, function_app_name, function_name]):
        return {"status": "error", "action": action_name, "message": "Se requieren 'subscription_id', 'resource_group_name', 'function_app_name', y 'function_name'.", "http_status": 400}

    # El estado de una función individual (habilitada/deshabilitada) se obtiene de sus propiedades.
    api_version = "2022-03-01"
    url = f"{settings.AZURE_MGMT_API_BASE_URL}/subscriptions/{subscription_id}/resourceGroups/{resource_group_name}/providers/Microsoft.Web/sites/{function_app_name}/functions/{function_name}?api-version={api_version}"
    
    logger.info(f"Obteniendo estado de la función '{function_name}' en Function App '{function_app_name}'")
    try:
        response = client.get(url, scope=settings.AZURE_MGMT_DEFAULT_SCOPE)
        # El estado está en properties.isDisabled
        function_properties = response.json().get("properties", {})
        is_disabled = function_properties.get("isDisabled", False)
        return {"status": "success", "data": {"name": function_name, "isDisabled": is_disabled, "status": "Disabled" if is_disabled else "Enabled", "properties": function_properties}}
    except Exception as e:
        return _handle_azure_mgmt_api_error(e, action_name, params)

# --- Placeholders para el resto de funciones que estaban en el archivo original ---
# Se necesitará más información o contexto para implementarlas completamente.

def create_deployment(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> dict:
    action_name = "azure_create_deployment"
    # Esta función es compleja: requiere subscription_id, resource_group_name, deployment_name,
    # y un cuerpo con 'properties' que incluya 'template' (ARM/Bicep JSON) y 'parameters'.
    # La plantilla y parámetros pueden ser inline o links a archivos.
    logger.warning(f"Acción '{action_name}' requiere implementación detallada de ARM/Bicep deployment.")
    return {"status": "not_implemented", "message": f"Acción '{action_name}' no implementada. Requiere plantilla ARM/Bicep y parámetros.", "service_module": __name__, "http_status": 501}

def list_logic_apps(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> dict:
    action_name = "azure_list_logic_apps"
    subscription_id = params.get("subscription_id", settings.AZURE_SUBSCRIPTION_ID)
    resource_group_name = params.get("resource_group_name", settings.AZURE_RESOURCE_GROUP) # Opcional, para listar en un RG

    if not subscription_id:
        return {"status": "error", "action": action_name, "message": "'subscription_id' es requerido.", "http_status": 400}

    api_version = "2019-05-01" # Microsoft.Logic/workflows
    
    if resource_group_name:
        url = f"{settings.AZURE_MGMT_API_BASE_URL}/subscriptions/{subscription_id}/resourceGroups/{resource_group_name}/providers/Microsoft.Logic/workflows?api-version={api_version}"
        logger.info(f"Listando Logic Apps en RG '{resource_group_name}', suscripción '{subscription_id}'")
    else:
        url = f"{settings.AZURE_MGMT_API_BASE_URL}/subscriptions/{subscription_id}/providers/Microsoft.Logic/workflows?api-version={api_version}"
        logger.info(f"Listando todas las Logic Apps en la suscripción '{subscription_id}'")
        
    try:
        response = client.get(url, scope=settings.AZURE_MGMT_DEFAULT_SCOPE)
        return {"status": "success", "data": response.json().get("value", [])}
    except Exception as e:
        return _handle_azure_mgmt_api_error(e, action_name, params)


def trigger_logic_app(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> dict:
    action_name = "azure_trigger_logic_app"
    # Para disparar una Logic App con trigger HTTP, usualmente se llama directamente a la URL del trigger,
    # no a través de la API de Management (a menos que sea para obtener esa URL).
    # Si es para obtener la URL del callback:
    # GET /subscriptions/{subscriptionId}/resourceGroups/{resourceGroupName}/providers/Microsoft.Logic/workflows/{workflowName}/triggers/{triggerName}/listCallbackUrl?api-version=2019-05-01
    # Y luego un POST a esa URL.
    # Si es para ejecutar una acción 'Run' en una Logic App:
    # POST /subscriptions/{subscriptionId}/resourceGroups/{resourceGroupName}/providers/Microsoft.Logic/workflows/{workflowName}/runs/{runName}?api-version=2019-05-01
    # Esta función necesita más especificación.
    logger.warning(f"Acción '{action_name}' requiere especificación de si es obtener URL de trigger o ejecutar un run.")
    return {"status": "not_implemented", "message": f"Acción '{action_name}' no implementada. Requiere más detalles sobre la operación deseada.", "service_module": __name__, "http_status": 501}

def get_logic_app_run_history(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> dict:
    action_name = "azure_get_logic_app_run_history"
    subscription_id = params.get("subscription_id", settings.AZURE_SUBSCRIPTION_ID)
    resource_group_name = params.get("resource_group_name", settings.AZURE_RESOURCE_GROUP)
    workflow_name = params.get("workflow_name") # Nombre de la Logic App

    if not all([subscription_id, resource_group_name, workflow_name]):
        return {"status": "error", "action": action_name, "message": "Se requieren 'subscription_id', 'resource_group_name', y 'workflow_name'.", "http_status": 400}
    
    api_version = "2019-05-01"
    url = f"{settings.AZURE_MGMT_API_BASE_URL}/subscriptions/{subscription_id}/resourceGroups/{resource_group_name}/providers/Microsoft.Logic/workflows/{workflow_name}/runs?api-version={api_version}"
    
    odata_params: Dict[str, Any] = {}
    if params.get("$top"): odata_params["$top"] = params["$top"]
    if params.get("$filter"): odata_params["$filter"] = params["$filter"] # Ej: "status eq 'Failed'"

    logger.info(f"Obteniendo historial de ejecuciones para Logic App '{workflow_name}'")
    try:
        response = client.get(url, scope=settings.AZURE_MGMT_DEFAULT_SCOPE, params=odata_params)
        return {"status": "success", "data": response.json().get("value", [])}
    except Exception as e:
        return _handle_azure_mgmt_api_error(e, action_name, params)