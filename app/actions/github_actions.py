# app/actions/github_actions.py
import logging
import requests # Usaremos requests directamente para la API de GitHub
import json
from typing import Dict, List, Optional, Any, Union

from app.core.config import settings # Para acceder a GITHUB_PAT y DEFAULT_API_TIMEOUT

logger = logging.getLogger(__name__)

GITHUB_API_BASE_URL = "https://api.github.com" # Podría moverse a settings si se prevén múltiples URLs de GitHub

def _get_github_auth_headers() -> Dict[str, str]:
    """Construye las cabeceras de autenticación para GitHub API usando PAT."""
    if not settings.GITHUB_PAT:
        msg = "Variable de entorno GITHUB_PAT no configurada. No se puede autenticar con GitHub API."
        logger.critical(msg)
        raise ValueError(msg) # Este error será capturado por el manejador genérico de errores

    headers = {
        'Authorization': f'Bearer {settings.GITHUB_PAT}',
        'Accept': 'application/vnd.github.v3+json', # Buena práctica, aunque la API suele devolver JSON por defecto
        'X-GitHub-Api-Version': '2022-11-28' # Recomendado por GitHub
    }
    return headers

def _handle_github_api_error(e: Exception, action_name: str, params_for_log: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Helper para manejar errores de GitHub API."""
    log_message = f"Error en GitHub Action '{action_name}'"
    if params_for_log:
        safe_params = {k: v for k, v in params_for_log.items() if k not in ['body', 'content', 'payload']}
        log_message += f" con params: {safe_params}"
    
    logger.error(f"{log_message}: {type(e).__name__} - {str(e)}", exc_info=True)
    
    details_str = str(e)
    status_code_int = 500
    github_error_message = None # Para mensajes específicos de la API de GitHub

    if isinstance(e, requests.exceptions.HTTPError) and e.response is not None:
        status_code_int = e.response.status_code
        try:
            error_data = e.response.json()
            details_str = error_data.get("message", e.response.text) # GitHub suele usar 'message'
            # GitHub también puede tener 'errors' con más detalles:
            if "errors" in error_data:
                details_str += f" (Detalles adicionales: {error_data['errors']})"
        except json.JSONDecodeError:
            details_str = e.response.text[:500] if e.response.text else "No response body"
            
    return {
        "status": "error",
        "action": action_name,
        "message": f"Error en {action_name}: {details_str}", # Usar el mensaje de error de GitHub si está disponible
        "http_status": status_code_int,
        "details": {"raw_exception": str(e), "github_message": details_str if github_error_message is None else github_error_message}
    }

# --- Implementación de Acciones de GitHub ---
# El parámetro 'client: AuthenticatedHttpClient' pasado por el router se ignora aquí.

def github_list_repos(client: Optional[AuthenticatedHttpClient], params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Lista los repositorios del usuario autenticado (asociado al PAT) o de una organización.
    Si 'org_name' está en params, lista repos de esa org. Sino, del usuario del PAT.
    """
    action_name = "github_list_repos"
    org_name: Optional[str] = params.get("org_name")

    api_query_params: Dict[str, Any] = {}
    # Parámetros comunes para listar repos
    api_query_params['type'] = params.get('type', 'all') # owner, all, member
    api_query_params['sort'] = params.get('sort', 'pushed') # created, updated, pushed, full_name
    api_query_params['direction'] = params.get('direction', 'desc')
    api_query_params['per_page'] = min(int(params.get('per_page', 30)), 100) # Max 100
    api_query_params['page'] = int(params.get('page', 1))

    if org_name:
        url = f"{GITHUB_API_BASE_URL}/orgs/{org_name}/repos"
        logger.info(f"Listando repositorios de GitHub para la organización '{org_name}'")
    else:
        url = f"{GITHUB_API_BASE_URL}/user/repos" # Repos del usuario autenticado por el PAT
        # Para /user/repos, 'type' puede ser 'all', 'owner', 'public', 'private', 'member'.
        # 'affiliation' es más antiguo, 'type' es el recomendado ahora.
        # Si se usa /user/repos, 'type' puede ser más específico.
        # Si 'type' no es 'all', 'owner', o 'member', la API puede dar error.
        # Por seguridad, si es /user/repos y type no es uno de estos, cambiar a 'all'.
        if api_query_params['type'] not in ['all', 'owner', 'member', 'public', 'private']:
            logger.warning(f"Tipo de repositorio '{api_query_params['type']}' inválido para /user/repos. Usando 'all'.")
            api_query_params['type'] = 'all'
        logger.info("Listando repositorios de GitHub para el usuario del PAT.")
    
    try:
        github_headers = _get_github_auth_headers()
        response = requests.get(url, headers=github_headers, params=api_query_params, timeout=settings.DEFAULT_API_TIMEOUT)
        response.raise_for_status()
        return {"status": "success", "data": response.json()}
    except ValueError as val_err: # Error de _get_github_auth_headers
        return {"status": "error", "action": action_name, "message": str(val_err), "http_status": 401}
    except Exception as e:
        return _handle_github_api_error(e, action_name, params)


def github_create_issue(client: Optional[AuthenticatedHttpClient], params: Dict[str, Any]) -> Dict[str, Any]:
    """Crea un nuevo issue en un repositorio específico."""
    action_name = "github_create_issue"
    owner: Optional[str] = params.get("owner")
    repo: Optional[str] = params.get("repo")
    title: Optional[str] = params.get("title")
    body_content: Optional[str] = params.get("body") # 'body' es el nombre del campo en la API de GitHub
    assignees: Optional[List[str]] = params.get("assignees") # Lista de logins de usuario
    labels: Optional[List[str]] = params.get("labels") # Lista de nombres de etiquetas
    milestone_number: Optional[int] = params.get("milestone") # Número del milestone

    if not all([owner, repo, title]):
        return {"status": "error", "action": action_name, "message": "'owner', 'repo', y 'title' son requeridos.", "http_status": 400}

    payload: Dict[str, Any] = {"title": title}
    if body_content is not None: payload["body"] = body_content
    if assignees: payload["assignees"] = assignees
    if labels: payload["labels"] = labels
    if milestone_number is not None:
        if not isinstance(milestone_number, int):
            return {"status": "error", "action": action_name, "message": "'milestone' debe ser un número entero.", "http_status": 400}
        payload["milestone"] = milestone_number
    
    url = f"{GITHUB_API_BASE_URL}/repos/{owner}/{repo}/issues"
    logger.info(f"Creando issue en GitHub repo '{owner}/{repo}' con título '{title}'")
    try:
        github_headers = _get_github_auth_headers()
        response = requests.post(url, headers=github_headers, json=payload, timeout=settings.DEFAULT_API_TIMEOUT)
        response.raise_for_status()
        return {"status": "success", "data": response.json()}
    except ValueError as val_err: # Error de _get_github_auth_headers
        return {"status": "error", "action": action_name, "message": str(val_err), "http_status": 401}
    except Exception as e:
        return _handle_github_api_error(e, action_name, params)

def github_list_issues(client: Optional[AuthenticatedHttpClient], params: Dict[str, Any]) -> Dict[str, Any]:
    """Lista issues de un repositorio específico."""
    action_name = "github_list_issues"
    owner: Optional[str] = params.get("owner")
    repo: Optional[str] = params.get("repo")

    if not owner or not repo:
        return {"status": "error", "action": action_name, "message": "'owner' y 'repo' son requeridos.", "http_status": 400}

    api_query_params: Dict[str, Any] = {}
    # Parámetros de filtrado y paginación de la API de GitHub Issues
    if params.get("milestone"): api_query_params["milestone"] = params["milestone"] # número o '*' o 'none'
    if params.get("state"): api_query_params["state"] = params["state"] # open, closed, all. Default: open
    if params.get("assignee"): api_query_params["assignee"] = params["assignee"] # login, '*' o 'none'
    if params.get("creator"): api_query_params["creator"] = params["creator"] # login
    if params.get("mentioned"): api_query_params["mentioned"] = params["mentioned"] # login
    if params.get("labels"): api_query_params["labels"] = ",".join(params["labels"]) if isinstance(params["labels"], list) else params["labels"]
    if params.get("sort"): api_query_params["sort"] = params["sort"] # created, updated, comments. Default: created
    if params.get("direction"): api_query_params["direction"] = params["direction"] # asc, desc. Default: desc
    if params.get("since"): api_query_params["since"] = params["since"] # ISO 8601 timestamp
    
    api_query_params['per_page'] = min(int(params.get('per_page', 30)), 100)
    api_query_params['page'] = int(params.get('page', 1))

    url = f"{GITHUB_API_BASE_URL}/repos/{owner}/{repo}/issues"
    logger.info(f"Listando issues de GitHub para '{owner}/{repo}' con filtros: {api_query_params}")
    try:
        github_headers = _get_github_auth_headers()
        response = requests.get(url, headers=github_headers, params=api_query_params, timeout=settings.DEFAULT_API_TIMEOUT)
        response.raise_for_status()
        return {"status": "success", "data": response.json()}
    except ValueError as val_err: # Error de _get_github_auth_headers
        return {"status": "error", "action": action_name, "message": str(val_err), "http_status": 401}
    except Exception as e:
        return _handle_github_api_error(e, action_name, params)

# --- Aquí se pueden añadir las demás funciones de GitHub que estaban en tu mapping_actions original ---
# (github_get_repo, github_get_repo_content, github_create_repo, github_get_issue,
#  github_update_issue, github_add_comment_issue, github_list_prs, github_get_pr,
#  github_create_pr, github_merge_pr, github_list_workflows, github_trigger_workflow,
#  github_get_workflow_run)
# Todas seguirían un patrón similar:
# 1. Definir la URL del endpoint de la API de GitHub.
# 2. Construir el payload (para POST/PATCH) o los query_params (para GET) a partir de `params`.
# 3. Llamar a `_get_github_auth_headers()`.
# 4. Usar `requests.request(method, url, headers=..., json=..., params=..., timeout=...)`.
# 5. Llamar a `response.raise_for_status()`.
# 6. Devolver `{"status": "success", "data": response.json()}` o manejar excepciones con `_handle_github_api_error`.

# Por ahora, se implementan las que estaban explícitamente indicadas para implementación.
# El resto quedarían como no implementadas si se llaman desde el action_mapper.
# Si necesitas que implemente alguna más de esta lista ahora, por favor, indícamelo.