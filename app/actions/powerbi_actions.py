# app/actions/powerbi_actions.py
import logging
import requests
import json
import time
from typing import Dict, List, Optional, Any

from azure.identity import ClientSecretCredential, CredentialUnavailableError

from app.core.config import settings
# AÑADIR ESTA LÍNEA:
from app.shared.helpers.http_client import AuthenticatedHttpClient # Para type hinting

logger = logging.getLogger(__name__)
# ... (el resto del archivo powerbi_actions.py que te di antes sigue igual) ...

# --- Constantes y Configuración Específica para Power BI API ---
PBI_API_BASE_URL_MYORG = "https://api.powerbi.com/v1.0/myorg"
# Scope específico para la API REST de Power BI
PBI_API_DEFAULT_SCOPE = settings.POWER_BI_DEFAULT_SCOPE # Usar el scope desde settings
# Timeout para llamadas a Power BI API
PBI_API_CALL_TIMEOUT = max(settings.DEFAULT_API_TIMEOUT, 120)

# --- Helper de Autenticación (Específico para Power BI API con Client Credentials) ---
_pbi_credential_instance: Optional[ClientSecretCredential] = None
# _pbi_last_token_info: Optional[Dict[str, Any]] = None # Cache manual no es estrictamente necesario aquí

def _get_powerbi_api_token(parametros_auth_override: Optional[Dict[str, Any]] = None) -> str:
    global _pbi_credential_instance

    auth_params = parametros_auth_override or {}
    # Leer credenciales desde settings (que a su vez las lee de variables de entorno)
    tenant_id = auth_params.get("pbi_tenant_id", settings.PBI_TENANT_ID)
    client_id = auth_params.get("pbi_client_id", settings.PBI_CLIENT_ID)
    client_secret = auth_params.get("pbi_client_secret", settings.PBI_CLIENT_SECRET)

    if not all([tenant_id, client_id, client_secret]):
        missing = [name for name, var in [("PBI_TENANT_ID", tenant_id),
                                          ("PBI_CLIENT_ID", client_id),
                                          ("PBI_CLIENT_SECRET", client_secret)] if not var]
        msg = f"Faltan configuraciones (PBI_TENANT_ID, PBI_CLIENT_ID, PBI_CLIENT_SECRET) para Power BI API. Verifique settings o .env. Faltantes: {', '.join(missing)}"
        logger.critical(msg)
        raise ValueError(msg)

    # Recrear instancia si no existe o si los IDs han cambiado (improbable en este flujo, pero robusto)
    if _pbi_credential_instance is None or \
       (_pbi_credential_instance._tenant_id != tenant_id or _pbi_credential_instance._client_id != client_id): # type: ignore
        logger.info("Creando/Recreando instancia ClientSecretCredential para Power BI API.")
        try:
            _pbi_credential_instance = ClientSecretCredential(
                tenant_id=tenant_id,
                client_id=client_id,
                client_secret=client_secret
            )
        except Exception as cred_err:
            logger.critical(f"Error al crear ClientSecretCredential para Power BI: {cred_err}", exc_info=True)
            raise ConnectionError(f"Error configurando credencial para Power BI: {cred_err}") from cred_err

    try:
        logger.info(f"Solicitando token para Power BI API con scope: {PBI_API_DEFAULT_SCOPE[0]}")
        token_credential = _pbi_credential_instance.get_token(PBI_API_DEFAULT_SCOPE[0])
        logger.info("Token para Power BI API obtenido exitosamente.")
        return token_credential.token
    except CredentialUnavailableError as cred_unavailable_err:
        logger.critical(f"Credencial no disponible para obtener token Power BI: {cred_unavailable_err}", exc_info=True)
        raise ConnectionAbortedError(f"Credencial para Power BI no disponible: {cred_unavailable_err}") from cred_unavailable_err
    except Exception as token_err:
        logger.error(f"Error inesperado obteniendo token Power BI: {token_err}", exc_info=True)
        raise ConnectionRefusedError(f"Error obteniendo token para Power BI: {token_err}") from token_err

def _get_pbi_auth_headers(parametros_auth_override: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
    try:
        token = _get_powerbi_api_token(parametros_auth_override)
        return {'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'}
    except Exception as e:
        raise e # Propagar el error para ser manejado por la función de acción

def _handle_pbi_api_error(e: Exception, action_name: str) -> Dict[str, Any]: # Helper específico para PBI
    logger.error(f"Error en Power BI action '{action_name}': {type(e).__name__} - {e}", exc_info=True)
    details = str(e)
    status_code = 500
    if isinstance(e, requests.exceptions.HTTPError) and e.response is not None:
        status_code = e.response.status_code
        try:
            error_data = e.response.json() # Power BI errores pueden tener otra estructura
            details = error_data.get("error", {}).get("message", e.response.text)
        except json.JSONDecodeError:
            details = e.response.text
    elif isinstance(e, (ValueError, ConnectionError, ConnectionAbortedError, ConnectionRefusedError)):
        status_code = 401 # Asumir error de autenticación/configuración
    return {
        "status": "error", "action": action_name,
        "message": f"Error en {action_name}: {type(e).__name__}",
        "http_status": status_code, "details": details
    }

# ---- FUNCIONES DE ACCIÓN PARA POWER BI ----
# El parámetro 'client: AuthenticatedHttpClient' se ignora aquí.

def list_reports(client: Optional[AuthenticatedHttpClient], params: Dict[str, Any]) -> Dict[str, Any]:
    workspace_id: Optional[str] = params.get("workspace_id")
    try:
        pbi_headers = _get_pbi_auth_headers(params.get("auth_override"))
    except Exception as auth_err:
        return _handle_pbi_api_error(auth_err, "list_reports")

    log_owner: str
    if workspace_id:
        url = f"{PBI_API_BASE_URL_MYORG}/groups/{workspace_id}/reports"
        log_owner = f"workspace '{workspace_id}'"
    else:
        url = f"{PBI_API_BASE_URL_MYORG}/reports"
        log_owner = "la organización (accesibles por la App)"
        if not workspace_id:
             logger.warning("Listando reports a nivel de organización sin workspace_id.")
    logger.info(f"Listando reports Power BI en {log_owner}")
    try:
        response = requests.get(url, headers=pbi_headers, timeout=PBI_API_CALL_TIMEOUT)
        response.raise_for_status()
        response_data = response.json()
        return {"status": "success", "data": response_data.get("value", [])}
    except Exception as e:
        return _handle_pbi_api_error(e, f"list_reports in {log_owner}")

def export_report(client: Optional[AuthenticatedHttpClient], params: Dict[str, Any]) -> Dict[str, Any]:
    report_id: Optional[str] = params.get("report_id")
    workspace_id: Optional[str] = params.get("workspace_id")
    export_format: str = params.get("format", "PDF").upper()
    if not report_id:
        return {"status": "error", "message": "Parámetro 'report_id' es requerido.", "http_status": 400}
    if export_format not in ["PDF", "PPTX", "PNG"]:
        return {"status": "error", "message": "Parámetro 'format' debe ser PDF, PPTX, o PNG.", "http_status": 400}
    try:
        pbi_headers = _get_pbi_auth_headers(params.get("auth_override"))
    except Exception as auth_err:
        return _handle_pbi_api_error(auth_err, "export_report")

    log_context: str
    if workspace_id:
        url = f"{PBI_API_BASE_URL_MYORG}/groups/{workspace_id}/reports/{report_id}/ExportToFile"
        log_context = f"reporte '{report_id}' en workspace '{workspace_id}'"
    else:
        url = f"{PBI_API_BASE_URL_MYORG}/reports/{report_id}/ExportToFile"
        log_context = f"reporte '{report_id}' (My Workspace/Org)"
        logger.warning(f"Exportando reporte '{report_id}' sin workspace_id.")

    payload: Dict[str, Any] = {"format": export_format}
    # Aquí se podrían añadir más configuraciones para la exportación desde params.
    logger.info(f"Iniciando exportación de {log_context} a formato {export_format}")
    try:
        response = requests.post(url, headers=pbi_headers, json=payload, timeout=PBI_API_CALL_TIMEOUT)
        if response.status_code == 202:
            export_job_details = response.json()
            export_id = export_job_details.get("id")
            logger.info(f"Exportación iniciada para {log_context}. Export ID: {export_id}. Estado: {export_job_details.get('status')}")
            return {
                "status": "success", "message": "Exportación de reporte iniciada.",
                "export_id": export_id, "report_id": report_id,
                "current_status": export_job_details.get('status'),
                "details": export_job_details, "http_status": 202
            }
        else:
            response.raise_for_status()
            return {"status": "warning", "message": f"Respuesta inesperada {response.status_code} al iniciar exportación.", "details": response.text, "http_status": response.status_code}
    except Exception as e:
        return _handle_pbi_api_error(e, f"export_report for {log_context}")

def list_dashboards(client: Optional[AuthenticatedHttpClient], params: Dict[str, Any]) -> Dict[str, Any]:
    workspace_id: Optional[str] = params.get("workspace_id")
    try:
        pbi_headers = _get_pbi_auth_headers(params.get("auth_override"))
    except Exception as auth_err:
        return _handle_pbi_api_error(auth_err, "list_dashboards")
    log_owner: str
    if workspace_id:
        url = f"{PBI_API_BASE_URL_MYORG}/groups/{workspace_id}/dashboards"
        log_owner = f"workspace '{workspace_id}'"
    else:
        url = f"{PBI_API_BASE_URL_MYORG}/dashboards"
        log_owner = "la organización (accesibles por la App)"
        logger.warning("Listando dashboards a nivel de organización sin workspace_id.")
    logger.info(f"Listando dashboards Power BI en {log_owner}")
    try:
        response = requests.get(url, headers=pbi_headers, timeout=PBI_API_CALL_TIMEOUT)
        response.raise_for_status()
        response_data = response.json()
        return {"status": "success", "data": response_data.get("value", [])}
    except Exception as e:
        return _handle_pbi_api_error(e, f"list_dashboards in {log_owner}")

def list_datasets(client: Optional[AuthenticatedHttpClient], params: Dict[str, Any]) -> Dict[str, Any]:
    workspace_id: Optional[str] = params.get("workspace_id")
    try:
        pbi_headers = _get_pbi_auth_headers(params.get("auth_override"))
    except Exception as auth_err:
        return _handle_pbi_api_error(auth_err, "list_datasets")
    log_owner: str
    if workspace_id:
        url = f"{PBI_API_BASE_URL_MYORG}/groups/{workspace_id}/datasets"
        log_owner = f"workspace '{workspace_id}'"
    else:
        url = f"{PBI_API_BASE_URL_MYORG}/datasets"
        log_owner = "la organización (accesibles por la App)"
        logger.warning("Listando datasets a nivel de organización sin workspace_id.")
    logger.info(f"Listando datasets Power BI en {log_owner}")
    try:
        response = requests.get(url, headers=pbi_headers, timeout=PBI_API_CALL_TIMEOUT)
        response.raise_for_status()
        response_data = response.json()
        return {"status": "success", "data": response_data.get("value", [])}
    except Exception as e:
        return _handle_pbi_api_error(e, f"list_datasets in {log_owner}")

def refresh_dataset(client: Optional[AuthenticatedHttpClient], params: Dict[str, Any]) -> Dict[str, Any]:
    dataset_id: Optional[str] = params.get("dataset_id")
    workspace_id: Optional[str] = params.get("workspace_id")
    notify_option: str = params.get("notify_option", "MailOnCompletion")
    if not dataset_id:
        return {"status": "error", "message": "Parámetro 'dataset_id' es requerido.", "http_status": 400}
    try:
        pbi_headers = _get_pbi_auth_headers(params.get("auth_override"))
    except Exception as auth_err:
        return _handle_pbi_api_error(auth_err, "refresh_dataset")

    log_owner: str
    if workspace_id:
        url = f"{PBI_API_BASE_URL_MYORG}/groups/{workspace_id}/datasets/{dataset_id}/refreshes"
        log_owner = f"workspace '{workspace_id}'"
    else:
        url = f"{PBI_API_BASE_URL_MYORG}/datasets/{dataset_id}/refreshes"
        log_owner = "dataset a nivel de organización"
        logger.warning(f"Iniciando refresco para dataset '{dataset_id}' sin workspace_id.")

    payload = {"notifyOption": notify_option} if notify_option in ["MailOnCompletion", "MailOnFailure", "NoNotification"] else {}
    logger.info(f"Iniciando refresco para dataset PBI '{dataset_id}' en {log_owner} con Notify: {notify_option}")
    try:
        response = requests.post(url, headers=pbi_headers, json=payload, timeout=PBI_API_CALL_TIMEOUT)
        if response.status_code == 202:
            request_id_pbi = response.headers.get("RequestId")
            logger.info(f"Solicitud de refresco para dataset '{dataset_id}' aceptada (202). PBI RequestId: {request_id_pbi}")
            return {"status": "success", "message": "Refresco de dataset iniciado.", "dataset_id": dataset_id, "pbi_request_id": request_id_pbi, "http_status": 202}
        else:
            response.raise_for_status()
            return {"status": "warning", "message": f"Respuesta inesperada {response.status_code} al iniciar refresco.", "details": response.text, "http_status": response.status_code}
    except Exception as e:
        return _handle_pbi_api_error(e, f"refresh_dataset for {dataset_id}")

# (La función "obtener_estado_refresco_dataset" no estaba en tu mapping_actions.py original para powerbi,
# pero si la necesitas, se puede añadir de forma similar a las otras).

# --- FIN DEL MÓDULO actions/powerbi_actions.py ---