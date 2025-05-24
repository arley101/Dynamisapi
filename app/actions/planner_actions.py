# app/actions/planner_actions.py
import logging
import requests # Solo para tipos de excepción
from typing import Dict, List, Optional, Any
from datetime import datetime, timezone as dt_timezone

# Importar la configuración y el cliente HTTP autenticado
from app.core.config import settings
from app.shared.helpers.http_client import AuthenticatedHttpClient

logger = logging.getLogger(__name__)

# --- Helper para parsear y formatear datetimes ---
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

# --- Helper para manejar errores ---
def _handle_planner_api_error(e: Exception, action_name: str) -> Dict[str, Any]:
    logger.error(f"Error en Planner action '{action_name}': {type(e).__name__} - {e}", exc_info=True)
    details = str(e)
    status_code = 500
    if isinstance(e, requests.exceptions.HTTPError) and e.response is not None:
        status_code = e.response.status_code
        try:
            error_data = e.response.json().get("error", {})
            details = error_data.get("message", e.response.text)
        except json.JSONDecodeError:
            details = e.response.text
    return {
        "status": "error", "action": action_name,
        "message": f"Error en {action_name}: {type(e).__name__}",
        "http_status": status_code, "details": details
    }

# ---- FUNCIONES DE ACCIÓN PARA PLANNER ----
def list_plans(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Dict[str, Any]:
    owner_type: str = params.get("owner_type", "user").lower()
    owner_id: Optional[str] = params.get("owner_id")

    if owner_type == "group" and not owner_id:
        return {"status": "error", "message": "Si 'owner_type' es 'group', se requiere 'owner_id'.", "http_status": 400}

    url: str
    log_owner_description: str
    if owner_type == "user":
        url = f"{settings.GRAPH_API_BASE_URL}/me/planner/plans"
        log_owner_description = "usuario actual (/me)"
    elif owner_type == "group":
        url = f"{settings.GRAPH_API_BASE_URL}/groups/{owner_id}/planner/plans"
        log_owner_description = f"grupo '{owner_id}'"
    else:
        return {"status": "error", "message": "Parámetro 'owner_type' debe ser 'user' o 'group'.", "http_status": 400}

    top: int = min(int(params.get("top", 25)), getattr(settings, 'MAX_GRAPH_TOP_VALUE', 100))
    query_api_params: Dict[str, Any] = {'$top': top}
    default_select = "id,title,owner,createdDateTime,container"
    query_api_params['$select'] = params.get('select', default_select)
    if params.get('filter'):
        query_api_params['$filter'] = params.get('filter')

    logger.info(f"Listando planes de Planner para {log_owner_description} (Top: {top}, Select: {query_api_params['$select']})")
    try:
        response = client.get(url, scope=settings.GRAPH_API_DEFAULT_SCOPE, params=query_api_params)
        plans_data = response.json()
        return {"status": "success", "data": plans_data.get("value", [])}
    except Exception as e:
        return _handle_planner_api_error(e, "list_plans")

def get_plan(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Dict[str, Any]:
    plan_id: Optional[str] = params.get("plan_id")
    if not plan_id:
        return {"status": "error", "message": "Parámetro 'plan_id' es requerido.", "http_status": 400}

    url = f"{settings.GRAPH_API_BASE_URL}/planner/plans/{plan_id}"
    query_api_params: Dict[str, Any] = {}
    select_fields = params.get('select', "id,title,owner,createdDateTime,container,details")
    query_api_params['$select'] = select_fields

    logger.info(f"Obteniendo detalles del plan de Planner '{plan_id}'")
    try:
        response = client.get(url, scope=settings.GRAPH_API_DEFAULT_SCOPE, params=query_api_params if query_api_params else None)
        plan_data = response.json()
        return {"status": "success", "data": plan_data}
    except Exception as e:
        return _handle_planner_api_error(e, "get_plan")

def list_tasks(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Dict[str, Any]:
    plan_id: Optional[str] = params.get("plan_id")
    if not plan_id:
        return {"status": "error", "message": "Parámetro 'plan_id' es requerido para listar tareas.", "http_status": 400}

    top_per_page: int = min(int(params.get('top_per_page', 25)), getattr(settings, 'MAX_GRAPH_TOP_VALUE_PAGING', 100))
    max_items_total: int = int(params.get('max_items_total', 100))
    select: Optional[str] = params.get('select')
    filter_query: Optional[str] = params.get('filter_query')
    order_by: Optional[str] = params.get('order_by')

    url_base = f"{settings.GRAPH_API_BASE_URL}/planner/plans/{plan_id}/tasks"
    query_api_params_initial: Dict[str, Any] = {'$top': top_per_page}
    if select: query_api_params_initial['$select'] = select
    else: query_api_params_initial['$select'] = "id,title,percentComplete,priority,dueDateTime,assigneePriority,assignments,bucketId,planId,orderHint"
    if filter_query: query_api_params_initial['$filter'] = filter_query
    if order_by: query_api_params_initial['$orderby'] = order_by

    all_tasks: List[Dict[str, Any]] = []
    current_url: Optional[str] = url_base
    page_count = 0
    max_pages = getattr(settings, 'MAX_PAGING_PAGES', 20)

    logger.info(f"Listando tareas del plan Planner '{plan_id}' (Max total: {max_items_total}, Por pág: {top_per_page})")
    try:
        while current_url and len(all_tasks) < max_items_total and page_count < max_pages:
            page_count += 1
            current_call_params = query_api_params_initial if page_count == 1 else None
            logger.debug(f"Obteniendo página {page_count} de tareas desde: {current_url} con params: {current_call_params}")
            response = client.get(current_url, scope=settings.GRAPH_API_DEFAULT_SCOPE, params=current_call_params)
            response_data = response.json()
            page_items = response_data.get('value', [])
            if not isinstance(page_items, list): break
            for item in page_items:
                if len(all_tasks) < max_items_total: all_tasks.append(item)
                else: break
            current_url = response_data.get('@odata.nextLink')
            if not current_url or len(all_tasks) >= max_items_total: break
        logger.info(f"Total tareas Planner recuperadas para plan '{plan_id}': {len(all_tasks)} ({page_count} pág procesadas).")
        return {"status": "success", "data": all_tasks, "total_retrieved": len(all_tasks), "pages_processed": page_count}
    except Exception as e:
        return _handle_planner_api_error(e, "list_tasks")

def create_task(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Dict[str, Any]:
    plan_id: Optional[str] = params.get("plan_id")
    title: Optional[str] = params.get("title")
    if not plan_id or not title:
        return {"status": "error", "message": "Parámetros 'plan_id' y 'title' son requeridos.", "http_status": 400}

    bucket_id: Optional[str] = params.get("bucket_id")
    assignments: Optional[Dict[str, Any]] = params.get("assignments")
    due_datetime_str: Optional[str] = params.get("dueDateTime")
    details_payload: Optional[Dict[str, Any]] = params.get("details_payload")

    url_task = f"{settings.GRAPH_API_BASE_URL}/planner/tasks"
    body: Dict[str, Any] = {"planId": plan_id, "title": title}
    if bucket_id: body["bucketId"] = bucket_id
    if assignments and isinstance(assignments, dict): body["assignments"] = assignments
    if due_datetime_str:
        try: body["dueDateTime"] = _parse_and_utc_datetime_str(due_datetime_str, "dueDateTime")
        except ValueError as ve: return {"status": "error", "message": f"Formato inválido para 'dueDateTime': {ve}", "http_status": 400}

    optional_fields = ["priority", "percentComplete", "startDateTime", "assigneePriority", "orderHint"]
    for field in optional_fields:
        if params.get(field) is not None:
            if field.endswith("DateTime"):
                try: body[field] = _parse_and_utc_datetime_str(params[field], field)
                except ValueError as ve: return {"status": "error", "message": f"Formato inválido para '{field}': {ve}", "http_status": 400}
            else:
                body[field] = params[field]
    logger.info(f"Creando tarea Planner '{title}' en plan '{plan_id}'")
    try:
        response_task = client.post(url_task, scope=settings.GRAPH_API_DEFAULT_SCOPE, json_data=body)
        task_data = response_task.json()
        task_id = task_data.get("id")
        if details_payload and isinstance(details_payload, dict) and task_id:
            logger.info(f"Tarea Planner '{task_id}' creada. Procediendo a actualizar detalles.")
            details_url = f"{settings.GRAPH_API_BASE_URL}/planner/tasks/{task_id}/details"
            etag_details = task_data.get("details", {}).get("@odata.etag")
            if not etag_details:
                try:
                    logger.debug(f"Obteniendo ETag para detalles de tarea '{task_id}'.")
                    get_details_response = client.get(details_url, scope=settings.GRAPH_API_DEFAULT_SCOPE, params={"$select": "@odata.etag"})
                    etag_details = get_details_response.json().get("@odata.etag")
                except requests.exceptions.HTTPError as http_e_details:
                    if http_e_details.response and http_e_details.response.status_code == 404:
                        etag_details = None
                        logger.info(f"Detalles para tarea '{task_id}' no encontrados (404), se crearán con PATCH.")
                    else: raise
                except Exception as get_etag_err:
                    logger.warning(f"Error obteniendo ETag para detalles de tarea '{task_id}': {get_etag_err}. Se intentará PATCH sin ETag.")
                    etag_details = None
            details_custom_headers = {'If-Match': etag_details} if etag_details else {}
            details_response = client.patch(details_url, scope=settings.GRAPH_API_DEFAULT_SCOPE, json_data=details_payload, headers=details_custom_headers)
            task_data["details"] = details_response.json()
            task_data["details_update_status"] = "success"
            logger.info(f"Detalles de tarea Planner '{task_id}' actualizados/creados.")
        return {"status": "success", "data": task_data, "message": "Tarea Planner creada."}
    except Exception as e:
        return _handle_planner_api_error(e, "create_task")

def get_task(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Dict[str, Any]:
    task_id: Optional[str] = params.get("task_id")
    if not task_id:
        return {"status": "error", "message": "Parámetro 'task_id' es requerido.", "http_status": 400}
    url = f"{settings.GRAPH_API_BASE_URL}/planner/tasks/{task_id}"
    query_api_params: Dict[str, Any] = {}
    if params.get('select'): query_api_params['$select'] = params.get('select')
    if params.get('expand_details', str(params.get('expand', "")).lower() == 'details'):
        query_api_params['$expand'] = 'details'
        if query_api_params.get('$select') and 'details' not in query_api_params['$select']:
            query_api_params['$select'] += ",details"
    logger.info(f"Obteniendo tarea Planner '{task_id}'")
    try:
        response = client.get(url, scope=settings.GRAPH_API_DEFAULT_SCOPE, params=query_api_params if query_api_params else None)
        task_data = response.json()
        return {"status": "success", "data": task_data}
    except Exception as e:
        return _handle_planner_api_error(e, "get_task")

def update_task(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Dict[str, Any]:
    task_id: Optional[str] = params.get("task_id")
    if not task_id:
        return {"status": "error", "message": "Parámetro 'task_id' es requerido.", "http_status": 400}
    update_payload_task: Optional[Dict[str, Any]] = params.get("update_payload_task")
    update_payload_details: Optional[Dict[str, Any]] = params.get("update_payload_details")
    etag_task: Optional[str] = params.get("etag_task")
    etag_details: Optional[str] = params.get("etag_details")
    if not update_payload_task and not update_payload_details:
        return {"status": "success", "message": "No se especificaron cambios.", "data": {"id": task_id}}

    final_task_data_response: Dict[str, Any] = {"id": task_id}
    if update_payload_task and isinstance(update_payload_task, dict):
        url_task = f"{settings.GRAPH_API_BASE_URL}/planner/tasks/{task_id}"
        current_etag_task = etag_task or update_payload_task.pop('@odata.etag', None)
        custom_headers_task = {'If-Match': current_etag_task} if current_etag_task else {}
        for field in ["dueDateTime", "startDateTime"]:
            if field in update_payload_task and update_payload_task[field]:
                try: update_payload_task[field] = _parse_and_utc_datetime_str(update_payload_task[field], field)
                except ValueError as ve: return {"status": "error", "message": f"Formato inválido para '{field}': {ve}", "http_status": 400}
        logger.info(f"Actualizando tarea Planner '{task_id}' (campos principales). ETag usado: {current_etag_task or 'Ninguno'}")
        try:
            response_task = client.patch(url_task, scope=settings.GRAPH_API_DEFAULT_SCOPE, json_data=update_payload_task, headers=custom_headers_task)
            if response_task.status_code == 204:
                logger.info(f"Tarea Planner '{task_id}' actualizada (204). Re-obteniendo.")
                get_task_result = get_task(client, {"task_id": task_id, "expand_details": bool(update_payload_details)})
                if get_task_result["status"] == "success": final_task_data_response = get_task_result["data"]
            else: final_task_data_response = response_task.json()
            final_task_data_response["task_update_status"] = "success"
        except Exception as e_task:
            _handle_planner_api_error(e_task, "update_task (task part)") # Log error
            # Continue to details if task update failed but details update is requested? Or fail fast?
            # For now, let's assume if task update fails, we return error
            return _handle_planner_api_error(e_task, "update_task (task part)")


    if update_payload_details and isinstance(update_payload_details, dict):
        url_details = f"{settings.GRAPH_API_BASE_URL}/planner/tasks/{task_id}/details"
        current_etag_details = etag_details or update_payload_details.pop('@odata.etag', None)
        if not current_etag_details and final_task_data_response.get("details"):
            current_etag_details = final_task_data_response.get("details",{}).get("@odata.etag")
        if not current_etag_details:
            try:
                get_details_response = client.get(url_details, scope=settings.GRAPH_API_DEFAULT_SCOPE, params={"$select": "@odata.etag"})
                current_etag_details = get_details_response.json().get("@odata.etag")
            except Exception as get_etag_err:
                logger.warning(f"No se pudo obtener ETag para detalles de tarea '{task_id}': {get_etag_err}.")
        custom_headers_details = {'If-Match': current_etag_details} if current_etag_details else {}
        logger.info(f"Actualizando detalles para tarea Planner '{task_id}'. ETag usado: {current_etag_details or 'Ninguno'}")
        try:
            response_details = client.patch(url_details, scope=settings.GRAPH_API_DEFAULT_SCOPE, json_data=update_payload_details, headers=custom_headers_details)
            updated_details_data = {}
            if response_details.status_code == 204:
                logger.info(f"Detalles de tarea Planner '{task_id}' actualizados (204). Re-obteniendo.")
                get_task_result_for_details = get_task(client, {"task_id": task_id, "expand_details": True})
                if get_task_result_for_details["status"] == "success":
                    updated_details_data = get_task_result_for_details["data"].get("details", {})
            else:  updated_details_data = response_details.json()

            if isinstance(final_task_data_response, dict): # Ensure it's a dict before assigning
                final_task_data_response.setdefault("data", {})["details"] = updated_details_data # If 'data' key doesn't exist
                final_task_data_response["details_update_status"] = "success"
            else: # Should not happen if task update was successful
                final_task_data_response = {"id": task_id, "details": updated_details_data, "details_update_status": "success"}

        except Exception as e_details:
            _handle_planner_api_error(e_details, "update_task (details part)")
            if isinstance(final_task_data_response, dict):
                final_task_data_response["details_update_status"] = f"error: {type(e_details).__name__}"
            # If only details were being updated and it failed
            if not update_payload_task:
                return _handle_planner_api_error(e_details, "update_task (details part)")

    return {"status": "success", "data": final_task_data_response, "message": "Actualización procesada."}


def delete_task(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Dict[str, Any]:
    task_id: Optional[str] = params.get("task_id")
    etag: Optional[str] = params.get("etag")
    if not task_id:
        return {"status": "error", "message": "Parámetro 'task_id' es requerido.", "http_status": 400}
    url = f"{settings.GRAPH_API_BASE_URL}/planner/tasks/{task_id}"
    custom_headers = {'If-Match': etag} if etag else {}
    if not etag: logger.warning(f"Eliminando tarea Planner '{task_id}' sin ETag.")
    logger.info(f"Intentando eliminar tarea Planner '{task_id}'")
    try:
        response = client.delete(url, scope=settings.GRAPH_API_DEFAULT_SCOPE, headers=custom_headers)
        return {"status": "success", "message": f"Tarea Planner '{task_id}' eliminada.", "http_status": response.status_code}
    except Exception as e:
        return _handle_planner_api_error(e, "delete_task")

def list_buckets(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Dict[str, Any]:
    plan_id: Optional[str] = params.get("plan_id")
    if not plan_id:
        return {"status": "error", "message": "Parámetro 'plan_id' es requerido.", "http_status": 400}
    url = f"{settings.GRAPH_API_BASE_URL}/planner/plans/{plan_id}/buckets"
    query_api_params: Dict[str, Any] = {}
    query_api_params['$select'] = params.get('select', "id,name,orderHint,planId")
    if params.get('filter'): query_api_params['$filter'] = params.get('filter')
    logger.info(f"Listando buckets para el plan Planner '{plan_id}'")
    try:
        response = client.get(url, scope=settings.GRAPH_API_DEFAULT_SCOPE, params=query_api_params if query_api_params else None)
        buckets_data = response.json()
        return {"status": "success", "data": buckets_data.get("value", [])}
    except Exception as e:
        return _handle_planner_api_error(e, "list_buckets")

def create_bucket(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Dict[str, Any]:
    plan_id: Optional[str] = params.get("plan_id")
    name: Optional[str] = params.get("name")
    if not plan_id or not name:
        return {"status": "error", "message": "Parámetros 'plan_id' y 'name' son requeridos.", "http_status": 400}
    order_hint: Optional[str] = params.get("orderHint")
    url = f"{settings.GRAPH_API_BASE_URL}/planner/buckets"
    body: Dict[str, Any] = {"name": name, "planId": plan_id}
    if order_hint: body["orderHint"] = order_hint
    logger.info(f"Creando bucket '{name}' en plan Planner '{plan_id}'")
    try:
        response = client.post(url, scope=settings.GRAPH_API_DEFAULT_SCOPE, json_data=body)
        bucket_data = response.json()
        return {"status": "success", "data": bucket_data, "message": "Bucket creado."}
    except Exception as e:
        return _handle_planner_api_error(e, "create_bucket")

# --- FIN DEL MÓDULO actions/planner_actions.py ---