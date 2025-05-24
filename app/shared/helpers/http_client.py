# app/shared/helpers/http_client.py
import logging
import requests
import json # Importado para el manejo de errores HTTP
from azure.identity import DefaultAzureCredential, CredentialUnavailableError
from azure.core.exceptions import ClientAuthenticationError # <--- CAMBIO AQUÍ
from typing import List, Optional, Any, Dict

# Importar la configuración de la aplicación
from app.core.config import settings

logger = logging.getLogger(__name__)

class AuthenticatedHttpClient:
    def __init__(self, credential: DefaultAzureCredential, default_timeout: Optional[int] = None):
        if not isinstance(credential, DefaultAzureCredential):
            raise TypeError("Se requiere una instancia de DefaultAzureCredential.")
        self.credential = credential
        self.session = requests.Session()

        # Usar configuraciones de la instancia 'settings'
        self.default_timeout = default_timeout if default_timeout is not None else settings.DEFAULT_API_TIMEOUT
        
        self.session.headers.update({
            'User-Agent': f'{settings.APP_NAME}/{settings.APP_VERSION}',
            'Accept': 'application/json'
        })
        logger.info(f"AuthenticatedHttpClient inicializado. User-Agent: {settings.APP_NAME}/{settings.APP_VERSION}, Default Timeout: {self.default_timeout}s")

    def _get_access_token(self, scope: List[str]) -> Optional[str]:
        if not scope:
            logger.error("Se requiere un scope para obtener el token de acceso.")
            return None
        try:
            logger.debug(f"Solicitando token para scope: {scope}")
            token_result = self.credential.get_token(*scope) # Desempaquetar la lista de scopes
            logger.debug(f"Token obtenido exitosamente para scope: {scope}. Expiración: {token_result.expires_on}")
            return token_result.token
        except CredentialUnavailableError as e:
            logger.error(f"Error de credencial al obtener token para {scope}: {e}.")
            return None
        except ClientAuthenticationError as e: # Usando la importación corregida
            logger.error(f"Error de autenticación del cliente al obtener token para {scope}: {e}.")
            return None
        except Exception as e:
            logger.exception(f"Error inesperado al obtener token para {scope}: {e}") # Usar logger.exception para traceback
            return None

    def request(self, method: str, url: str, scope: List[str], **kwargs: Any) -> requests.Response:
        access_token = self._get_access_token(scope)
        if not access_token:
            # Considerar un error más específico o propagar el error de _get_access_token
            raise ValueError(f"No se pudo obtener el token de acceso para el scope {scope}.")

        request_headers = kwargs.pop('headers', {}).copy()
        request_headers['Authorization'] = f'Bearer {access_token}'

        # Asegurar Content-Type si hay cuerpo JSON/data, a menos que ya esté seteado
        if 'json' in kwargs or 'data' in kwargs:
            if 'Content-Type' not in request_headers:
                request_headers['Content-Type'] = 'application/json'

        timeout = kwargs.pop('timeout', self.default_timeout)

        logger.debug(f"Realizando solicitud {method} a {url} con scope {scope}")
        try:
            response = self.session.request(
                method=method, url=url, headers=request_headers, timeout=timeout, **kwargs
            )
            response.raise_for_status() # Lanza HTTPError para respuestas 4xx/5xx
            logger.debug(f"Solicitud {method} a {url} exitosa (Status: {response.status_code})")
            return response
        except requests.exceptions.HTTPError as http_err:
            # Loguear más detalles del error HTTP
            error_message = f"Error HTTP en {method} {url}: {http_err.response.status_code}"
            try:
                # Intentar obtener detalles del error de la respuesta JSON de Graph u otras APIs
                error_details_json = http_err.response.json()
                error_info = error_details_json.get("error", {})
                error_details_msg = error_info.get("message")
                if error_details_msg:
                    error_message += f" - {error_details_msg}"
                else: # Si no hay un error.message, usar el texto crudo
                    error_message += f" - {http_err.response.text[:500]}..."
            except json.JSONDecodeError: # Si el cuerpo del error no es JSON
                error_message += f" - {http_err.response.text[:500]}..."
            
            logger.error(error_message)
            raise # Re-lanzar la excepción para que sea manejada por el llamador (módulo de acción)
        except requests.exceptions.RequestException as req_err:
            logger.error(f"Error de conexión en {method} {url}: {req_err}")
            raise # Re-lanzar
        except Exception as e:
            logger.exception(f"Error inesperado durante la solicitud {method} a {url}: {e}")
            raise # Re-lanzar

    def get(self, url: str, scope: List[str], **kwargs: Any) -> requests.Response:
        return self.request('GET', url, scope, **kwargs)

    def post(self, url: str, scope: List[str], **kwargs: Any) -> requests.Response:
        return self.request('POST', url, scope, **kwargs)

    def put(self, url: str, scope: List[str], **kwargs: Any) -> requests.Response:
        return self.request('PUT', url, scope, **kwargs)

    def delete(self, url: str, scope: List[str], **kwargs: Any) -> requests.Response: # Corregido para devolver Response consistentemente
        return self.request('DELETE', url, scope, **kwargs)

    def patch(self, url: str, scope: List[str], **kwargs: Any) -> requests.Response:
        return self.request('PATCH', url, scope, **kwargs)