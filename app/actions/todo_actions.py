# app/actions/todo_actions.py
import logging
import requests # Para requests.exceptions.HTTPError
import json # Para el helper de error
from typing import Dict, List, Optional, Any
from datetime import datetime, timezone as dt_timezone

# Importar la configuración y el cliente HTTP autenticado
from app.core.config import settings
from app.shared.helpers.http_client import AuthenticatedHttpClient

logger = logging.getLogger(__name__)

# Helper de _parse_and_utc_datetime_str (copiado del original)
def _parse_and_utc_datetime_str(datetime_str: Any, field_name_for_log: str) -> str:
    if isinstance(datetime_str, datetime):
        dt_obj = datetime_str
    elif isinstance(datetime_str, str):
        try:
            if datetime_str.endswith('Z'):
                dt_obj = datetime.fromisoformat(datetime_str.replace('Z', '+00:00'))
            elif '+' in datetime_str[10:] or '-' in datetime_str[10:]:
                 dt_obj = datetime.fromisoformat(datetime_str)
            else:
                dt_obj = datetime.fromisoformat(datetime_str)
        except ValueError as e:
            logger.error(f"Formato de fecha/hora inválido para '{field_name_for_log}': '{datetime_str}'. Error: {e}")
            raise ValueError(f"Formato de fecha/hora inválido para '{field_name_for_log}': '{datetime_str}'. Se esperaba ISO 8601.") from e
    else:
        raise ValueError(f"Tipo inválido para '{field_name_for_log}': se esperaba string o datetime.")

    if dt_obj.tzinfo is None or dt_obj.tzinfo.utcoffset(dt_obj) is None:
        logger.debug(f"Fecha/hora '{datetime_str}' para '{field_name_for_log}' es naive. Asumiendo y estableciendo a UTC.")
        dt_obj_utc = dt_obj.replace(tzinfo=dt_timezone.utc)
    else:
        dt_obj_utc = dt_obj.astimezone(dt_timezone.utc)
    return dt_obj_utc.isoformat(timespec='seconds').replace('+00:00', 'Z')

def _handle_todo_api_error(e: Exception, action_name: str) -> Dict[str, Any]:
    logger.error(f"Error en ToDo action '{action_name}': {type(e).__name__} - {e}", exc_info=True)
    details = str(e)
    status_code = 500
    if isinstance(e, requests.exceptions.HTTPError) and e.response is not None:
        status_code = e.response.status_code
        try:
            error_data = e.response.json()
            details = error_data.get("error", {}).get("message", e.response.text)
        except json.JSONDecodeError:
            details = e.response.text
    return {"status": "error", "message": f"Error en {action_name}", "details": details, "http_status": status_code}

# =================================
# ==== FUNCIONES ACCIÓN TO-DO  ====
# =================================

def list_task_lists(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Dict[str, Any]:
    url_base = f"{settings.GRAPH_API_BASE_URL}/me/todo/lists"
    top_per_page: int = min(int(params.get('top_per_page', 25)), getattr(settings, 'MAX_GRAPH_TOP_VALUE_PAGING', 100))
    max_items_total: int = int(params.get('max_items_total', 100))
    query_api_params_initial: Dict[str, Any] = {'$top': top_per_page}
    query_api_params_initial['$select'] = params.get('select', "id,displayName,isOwner,isShared,wellknownListName")
    if params.get('filter_query'): query_api_params_initial['$filter'] = params.get('filter_query')
    if params.get('order_by'): query_api_params_initial['$orderby'] = params.get('order_by')

    all_lists: List[Dict[str, Any]] = []
    current_url: Optional[str] = url_base
    page_count = 0
    max_pages = getattr(settings, 'MAX_PAGING_PAGES', 20)
    logger.info(f"Listando listas de ToDo para /me (Max total: {max_items_total}, Por pág: {top_per_page})")
    todo_read_scope = getattr(settings, 'GRAPH_SCOPE_TASKS_READ', settings.GRAPH_API_DEFAULT_SCOPE)
    try:
        while current_url and len(all_lists) < max_items_total and page_count < max_pages :
            page_count += 1
            current_call_params = query_api_params_initial if page_count == 1 else None
            response = client.get(current_url, scope=todo_read_scope, params=current_call_params)
            response_data = response.json()
            page_items = response_data.get('value', [])
            if not isinstance(page_items, list): break
            for item in page_items:
                if len(all_lists) < max_items_total: all_lists.append(item)
                else: break
            current_url = response_data.get('@odata.nextLink')
            if not current_url or len(all_lists) >= max_items_total: break
        logger.info(f"Total listas ToDo recuperadas: {len(all_lists)} ({page_count} pág procesadas).")
        return {"status": "success", "data": all_lists, "total_retrieved": len(all_lists), "pages_processed": page_count}
    except Exception as e:
        return _handle_todo_api_error(e, "list_task_lists")

def create_task_list(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Dict[str, Any]:
    displayName: Optional[str] = params.get("displayName")
    if not displayName:
        return {"status": "error", "message": "Parámetro 'displayName' es requerido.", "http_status": 400}
    url = f"{settings.GRAPH_API_BASE_URL}/me/todo/lists"
    body = {"displayName": displayName}
    logger.info(f"Creando lista de ToDo '{displayName}' para /me")
    todo_rw_scope = getattr(settings, 'GRAPH_SCOPE_TASKS_READWRITE', settings.GRAPH_API_DEFAULT_SCOPE)
    try:
        response = client.post(url, scope=todo_rw_scope, json_data=body)
        list_data = response.json()
        return {"status": "success", "data": list_data, "message": "Lista ToDo creada."}
    except Exception as e:
        return _handle_todo_api_error(e, "create_task_list")

def list_tasks(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Dict[str, Any]:
    list_id: Optional[str] = params.get("list_id")
    if not list_id:
        return {"status": "error", "message": "Parámetro 'list_id' es requerido.", "http_status": 400}
    top_per_page: int = min(int(params.get('top_per_page', 25)), getattr(settings, 'MAX_GRAPH_TOP_VALUE_PAGING', 100))
    max_items_total: int = int(params.get('max_items_total', 100))
    query_api_params_initial: Dict[str, Any] = {'$top': top_per_page}
    query_api_params_initial['$select'] = params.get('select', "id,title,status,importance,isReminderOn,createdDateTime,lastModifiedDateTime,dueDateTime,completedDateTime")
    if params.get('filter_query'): query_api_params_initial['$filter'] = params.get('filter_query')
    if params.get('order_by'): query_api_params_initial['$orderby'] = params.get('order_by')
    url_base = f"{settings.GRAPH_API_BASE_URL}/me/todo/lists/{list_id}/tasks"
    all_tasks: List[Dict[str, Any]] = []
    current_url: Optional[str] = url_base
    page_count = 0
    max_pages = getattr(settings, 'MAX_PAGING_PAGES', 20)
    logger.info(f"Listando tareas ToDo de lista '{list_id}' (Max total: {max_items_total}, Por pág: {top_per_page})")
    todo_read_scope = getattr(settings, 'GRAPH_SCOPE_TASKS_READ', settings.GRAPH_API_DEFAULT_SCOPE)
    try:
        while current_url and len(all_tasks) < max_items_total and page_count < max_pages:
            page_count += 1
            current_call_params = query_api_params_initial if page_count == 1 else None
            response = client.get(current_url, scope=todo_read_scope, params=current_call_params)
            response_data = response.json()
            page_items = response_data.get('value', [])
            if not isinstance(page_items, list): break
            for item in page_items:
                if len(all_tasks) < max_items_total: all_tasks.append(item)
                else: break
            current_url = response_data.get('@odata.nextLink')
            if not current_url or len(all_tasks) >= max_items_total: break
        logger.info(f"Total tareas ToDo recuperadas de lista '{list_id}': {len(all_tasks)} ({page_count} pág procesadas).")
        return {"status": "success", "data": all_tasks, "total_retrieved": len(all_tasks), "pages_processed": page_count}
    except Exception as e:
        return _handle_todo_api_error(e, "list_tasks")

def create_task(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Dict[str, Any]:
    list_id: Optional[str] = params.get("list_id"); title: Optional[str] = params.get("title")
    if not list_id or not title:
        return {"status": "error", "message": "Parámetros 'list_id' y 'title' son requeridos.", "http_status": 400}
    url = f"{settings.GRAPH_API_BASE_URL}/me/todo/lists/{list_id}/tasks"
    body: Dict[str, Any] = {"title": title}
    optional_fields_direct = ["importance", "isReminderOn", "status"]
    for field in optional_fields_direct:
        if params.get(field) is not None: body[field] = params[field]
    if params.get("body_content"):
        body["body"] = {"content": params["body_content"], "contentType": params.get("body_contentType", "text")}
    datetime_fields = {"dueDateTime": params.get("dueDateTime"), "reminderDateTime": params.get("reminderDateTime"),
                       "startDateTime": params.get("startDateTime"), "completedDateTime": params.get("completedDateTime")}
    for field_name, dt_input in datetime_fields.items():
        if dt_input:
            try:
                dt_val_str = dt_input.get("dateTime") if isinstance(dt_input, dict) else dt_input
                body[field_name] = {"dateTime": _parse_and_utc_datetime_str(dt_val_str, field_name), "timeZone": "UTC"}
            except ValueError as ve: return {"status": "error", "message": f"Formato inválido para '{field_name}': {ve}", "http_status": 400}
    logger.info(f"Creando tarea ToDo '{title}' en lista '{list_id}'")
    todo_rw_scope = getattr(settings, 'GRAPH_SCOPE_TASKS_READWRITE', settings.GRAPH_API_DEFAULT_SCOPE)
    try:
        response = client.post(url, scope=todo_rw_scope, json_data=body)
        return {"status": "success", "data": response.json(), "message": "Tarea ToDo creada."}
    except Exception as e:
        return _handle_todo_api_error(e, "create_task")

def get_task(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Dict[str, Any]:
    list_id: Optional[str] = params.get("list_id"); task_id: Optional[str] = params.get("task_id")
    if not list_id or not task_id:
        return {"status": "error", "message": "Parámetros 'list_id' y 'task_id' requeridos.", "http_status": 400}
    url = f"{settings.GRAPH_API_BASE_URL}/me/todo/lists/{list_id}/tasks/{task_id}"
    query_api_params: Dict[str, Any] = {}
    if params.get('select'): query_api_params['$select'] = params.get('select')
    logger.info(f"Obteniendo tarea ToDo '{task_id}' de lista '{list_id}'")
    todo_read_scope = getattr(settings, 'GRAPH_SCOPE_TASKS_READ', settings.GRAPH_API_DEFAULT_SCOPE)
    try:
        response = client.get(url, scope=todo_read_scope, params=query_api_params if query_api_params else None)
        return {"status": "success", "data": response.json()}
    except Exception as e:
        return _handle_todo_api_error(e, "get_task")

def update_task(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Dict[str, Any]:
    list_id: Optional[str] = params.get("list_id"); task_id: Optional[str] = params.get("task_id")
    update_payload: Optional[Dict[str, Any]] = params.get("update_payload")
    if not list_id or not task_id or not update_payload or not isinstance(update_payload, dict):
        return {"status": "error", "message": "'list_id', 'task_id', y 'update_payload' (dict) requeridos.", "http_status": 400}
    url = f"{settings.GRAPH_API_BASE_URL}/me/todo/lists/{list_id}/tasks/{task_id}"
    body_update = update_payload.copy()
    try:
        datetime_fields = ["dueDateTime", "reminderDateTime", "startDateTime", "completedDateTime"]
        for field_name in datetime_fields:
            if field_name in body_update and body_update[field_name]:
                dt_input = body_update[field_name]
                dt_val_str = dt_input.get("dateTime") if isinstance(dt_input, dict) else dt_input
                body_update[field_name] = {"dateTime": _parse_and_utc_datetime_str(dt_val_str, f"update.{field_name}"), "timeZone": "UTC"}
            elif field_name in body_update and body_update[field_name] is None: body_update[field_name] = None
    except ValueError as ve: return {"status": "error", "message": f"Error en fecha en 'update_payload': {ve}", "http_status": 400}
    logger.info(f"Actualizando tarea ToDo '{task_id}' en lista '{list_id}'")
    todo_rw_scope = getattr(settings, 'GRAPH_SCOPE_TASKS_READWRITE', settings.GRAPH_API_DEFAULT_SCOPE)
    try:
        response = client.patch(url, scope=todo_rw_scope, json_data=body_update)
        return {"status": "success", "data": response.json(), "message": f"Tarea ToDo '{task_id}' actualizada."}
    except Exception as e:
        return _handle_todo_api_error(e, "update_task")

def delete_task(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Dict[str, Any]:
    list_id: Optional[str] = params.get("list_id"); task_id: Optional[str] = params.get("task_id")
    if not list_id or not task_id:
        return {"status": "error", "message": "'list_id' y 'task_id' requeridos.", "http_status": 400}
    url = f"{settings.GRAPH_API_BASE_URL}/me/todo/lists/{list_id}/tasks/{task_id}"
    logger.info(f"Eliminando tarea ToDo '{task_id}' de lista '{list_id}'")
    todo_rw_scope = getattr(settings, 'GRAPH_SCOPE_TASKS_READWRITE', settings.GRAPH_API_DEFAULT_SCOPE)
    try:
        response = client.delete(url, scope=todo_rw_scope)
        return {"status": "success", "message": f"Tarea ToDo '{task_id}' eliminada.", "http_status": response.status_code}
    except Exception as e:
        return _handle_todo_api_error(e, "delete_task")

# --- FIN DEL MÓDULO actions/todo_actions.py ---