# app/api/routes/dynamics_actions.py
import logging
import json # Importado para el manejo de errores HTTP en auth_http_client
from fastapi import APIRouter, Request, HTTPException, BackgroundTasks, status as http_status_codes
from fastapi.responses import JSONResponse, StreamingResponse, Response
from azure.identity import DefaultAzureCredential, CredentialUnavailableError
from azure.core.exceptions import ClientAuthenticationError # <--- CAMBIO AQUÍ
from typing import Any, Optional

from app.api.schemas import ActionRequest, ErrorResponse # Modelos Pydantic
from app.core.action_mapper import ACTION_MAP # Diccionario de acciones
from app.core.config import settings # Configuraciones de la aplicación
from app.shared.helpers.http_client import AuthenticatedHttpClient

logger = logging.getLogger(__name__)
router = APIRouter()

# Helper para crear la respuesta de error estandarizada
def create_error_response(
    status_code: int,
    action: Optional[str] = None,
    message: str = "Error procesando la solicitud.",
    details: Optional[Any] = None,
    graph_error_code: Optional[str] = None
) -> JSONResponse:
    error_content = ErrorResponse(
        action=action,
        message=message,
        http_status=status_code,
        details=details,
        graph_error_code=graph_error_code
    ).model_dump(exclude_none=True)
    
    return JSONResponse(status_code=status_code, content=error_content)

@router.post(
    "/dynamics", 
    summary="Procesa una acción dinámica basada en la solicitud.",
    description="Recibe un nombre de acción y sus parámetros, y ejecuta la lógica de negocio correspondiente.",
    response_description="El resultado de la acción ejecutada o un mensaje de error."
)
async def process_dynamic_action(
    request: Request,
    action_request: ActionRequest,
    background_tasks: BackgroundTasks
):
    action_name = action_request.action
    params_req = action_request.params
    invocation_id = request.headers.get("x-ms-invocation-id", "N/A") 
    logging_prefix = f"[InvocationId: {invocation_id}] [Action: {action_name}]"

    logger.info(f"{logging_prefix} Petición recibida. Params keys: {list(params_req.keys())}")

    try:
        credential = DefaultAzureCredential()
        try:
            token_test_scope = settings.GRAPH_API_DEFAULT_SCOPE 
            token_info = credential.get_token(*token_test_scope)
            logger.debug(f"{logging_prefix} DefaultAzureCredential validada. Token para {token_test_scope[0]} expira en {token_info.expires_on}")
        except CredentialUnavailableError as cred_err:
            logger.error(f"{logging_prefix} Credencial de Azure no disponible: {cred_err}")
            return create_error_response(
                status_code=http_status_codes.HTTP_500_INTERNAL_SERVER_ERROR,
                action=action_name,
                message="Error de autenticación: Credencial de Azure no disponible.",
                details=str(cred_err)
            )
        except ClientAuthenticationError as client_auth_err: # Usando la importación corregida
            logger.error(f"{logging_prefix} Error de autenticación del cliente de Azure: {client_auth_err}")
            return create_error_response(
                status_code=http_status_codes.HTTP_500_INTERNAL_SERVER_ERROR,
                action=action_name,
                message="Error de autenticación: Fallo al autenticar el cliente de Azure.",
                details=str(client_auth_err)
            )
        except Exception as token_ex:
            logger.error(f"{logging_prefix} Error inesperado al obtener token inicial: {token_ex}")
            return create_error_response(
                status_code=http_status_codes.HTTP_500_INTERNAL_SERVER_ERROR,
                action=action_name,
                message="Error de autenticación: Fallo inesperado al obtener token.",
                details=str(token_ex)
            )
            
        auth_http_client = AuthenticatedHttpClient(credential=credential)
        logger.info(f"{logging_prefix} AuthenticatedHttpClient inicializado.")

    except Exception as auth_setup_ex:
        logger.exception(f"{logging_prefix} Excepción durante la configuración de autenticación: {auth_setup_ex}")
        return create_error_response(
            status_code=http_status_codes.HTTP_500_INTERNAL_SERVER_ERROR,
            action=action_name,
            message="Error interno de configuración de autenticación.",
            details=str(auth_setup_ex)
        )

    action_function = ACTION_MAP.get(action_name)
    if not action_function:
        logger.warning(f"{logging_prefix} Acción '{action_name}' no encontrada en ACTION_MAP.")
        return create_error_response(
            status_code=http_status_codes.HTTP_400_BAD_REQUEST,
            action=action_name,
            message=f"La acción '{action_name}' no es válida o no está implementada."
        )

    logger.info(f"{logging_prefix} Ejecutando función {action_function.__name__} del módulo {action_function.__module__}")
    
    try:
        result = action_function(auth_http_client, params_req)

        if isinstance(result, bytes):
            logger.info(f"{logging_prefix} Acción devolvió datos binarios.")
            media_type = "application/octet-stream" 
            if "photo" in action_name.lower() or action_name.endswith("_get_my_photo"):
                media_type = "image/jpeg"
            elif action_name.endswith("_download_document") or action_name.endswith("_export_report"):
                filename_for_download = params_req.get("filename", params_req.get("item_id_or_path", "downloaded_file"))
                if isinstance(filename_for_download, str) and "." in filename_for_download:
                    ext = filename_for_download.split(".")[-1].lower()
                    if ext == "pdf": media_type = "application/pdf"
                    elif ext in ["xlsx", "xls"]: media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                    elif ext in ["docx", "doc"]: media_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                    elif ext == "csv": media_type = "text/csv"
                    elif ext == "png": media_type = "image/png"
                
            return Response(content=result, media_type=media_type)

        elif isinstance(result, str) and (action_name == "memory_export_session" and params_req.get("format") == "csv"):
            logger.info(f"{logging_prefix} Acción devolvió CSV como string.")
            return Response(content=result, media_type="text/csv", headers={"Content-Disposition": "attachment; filename=export.csv"})

        elif isinstance(result, dict):
            if result.get("status") == "error":
                logger.error(f"{logging_prefix} Acción resultó en error: {result.get('message')}, Detalles: {result.get('details')}")
                error_status_code = result.get("http_status", http_status_codes.HTTP_500_INTERNAL_SERVER_ERROR)
                if 200 <= error_status_code < 300: error_status_code = http_status_codes.HTTP_500_INTERNAL_SERVER_ERROR
                
                return create_error_response(
                    status_code=error_status_code,
                    action=action_name,
                    message=result.get("message", "Error desconocido en la acción."),
                    details=result.get("details"),
                    graph_error_code=result.get("graph_error_code")
                )
            else: 
                logger.info(f"{logging_prefix} Acción completada exitosamente.")
                success_status_code = result.get("http_status", http_status_codes.HTTP_200_OK)
                if not (200 <= success_status_code < 300): success_status_code = http_status_codes.HTTP_200_OK
                return JSONResponse(status_code=success_status_code, content=result)
        else:
            logger.error(f"{logging_prefix} La acción devolvió un tipo de resultado inesperado: {type(result)}")
            return create_error_response(
                status_code=http_status_codes.HTTP_500_INTERNAL_SERVER_ERROR,
                action=action_name,
                message="La acción devolvió un tipo de resultado inesperado."
            )
            
    except Exception as e:
        logger.exception(f"{logging_prefix} Excepción no controlada durante la ejecución de la acción: {e}")
        return create_error_response(
            status_code=http_status_codes.HTTP_500_INTERNAL_SERVER_ERROR,
            action=action_name,
            message="Error interno del servidor al ejecutar la acción.",
            details=str(e)
        )