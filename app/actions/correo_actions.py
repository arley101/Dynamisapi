# app/actions/correo_actions.py
import logging
import requests # Para requests.exceptions.HTTPError
import json
from typing import Dict, List, Optional, Union, Any

from app.core.config import settings
from app.shared.helpers.http_client import AuthenticatedHttpClient

logger = logging.getLogger(__name__)

# Scopes (asumiendo que settings.GRAPH_API_DEFAULT_SCOPE es el .default)
MAIL_READ_SCOPE = getattr(settings, "GRAPH_SCOPE_MAIL_READ", settings.GRAPH_API_DEFAULT_SCOPE)
MAIL_SEND_SCOPE = getattr(settings, "GRAPH_SCOPE_MAIL_SEND", settings.GRAPH_API_DEFAULT_SCOPE)
MAIL_READ_WRITE_SCOPE = getattr(settings, "GRAPH_SCOPE_MAIL_READ_WRITE", settings.GRAPH_API_DEFAULT_SCOPE)

def _handle_email_api_error(e: Exception, action_name: str, params_for_log: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    log_message = f"Error en Email action '{action_name}'"
    safe_params = {}
    if params_for_log:
        sensitive_keys = ['message', 'comment', 'attachments', 'message_payload_override', 'final_sendmail_payload', 'draft_message_payload']
        safe_params = {k: (v if k not in sensitive_keys else "[CONTENIDO OMITIDO]") for k, v in params_for_log.items()}
        log_message += f" con params: {safe_params}"
    
    logger.error(f"{log_message}: {type(e).__name__} - {str(e)}", exc_info=True)
    details_str = str(e); status_code_int = 500; graph_error_code = None
    if isinstance(e, requests.exceptions.HTTPError) and e.response is not None:
        status_code_int = e.response.status_code
        try:
            error_data = e.response.json(); error_info = error_data.get("error", {})
            details_str = error_info.get("message", e.response.text); graph_error_code = error_info.get("code")
        except json.JSONDecodeError: details_str = e.response.text[:500] if e.response.text else "No response body"
    return {"status": "error", "action": action_name, "message": f"Error en {action_name}: {type(e).__name__}", "http_status": status_code_int, "details": details_str, "graph_error_code": graph_error_code}

def _email_paged_request(
    client: AuthenticatedHttpClient, url_base: str, scope_list: List[str],
    params: Dict[str, Any], query_api_params_initial: Dict[str, Any],
    max_items_total: Optional[int], action_name_for_log: str
) -> Dict[str, Any]:
    all_items: List[Dict[str, Any]] = []
    current_url: Optional[str] = url_base
    page_count = 0
    max_pages_to_fetch = getattr(settings, "MAX_PAGING_PAGES", 20)
    top_per_page = query_api_params_initial.get('$top', getattr(settings, "DEFAULT_PAGING_SIZE_MAIL", 25))

    logger.info(f"Iniciando solicitud paginada para '{action_name_for_log}' desde '{url_base.split('?')[0]}...'. Max total: {max_items_total or 'todos'}, por pág: {top_per_page}, max_págs: {max_pages_to_fetch}")
    try:
        while current_url and (max_items_total is None or len(all_items) < max_items_total) and page_count < max_pages_to_fetch:
            page_count += 1
            is_first_call = (current_url == url_base and page_count == 1)
            current_params_for_call = query_api_params_initial if is_first_call else None
            logger.debug(f"Página {page_count} para '{action_name_for_log}': GET {current_url.split('?')[0]} con params: {current_params_for_call}")
            response = client.get(url=current_url, scope=scope_list, params=current_params_for_call)
            response_data = response.json()
            page_items = response_data.get('value', [])
            if not isinstance(page_items, list): break
            for item in page_items:
                if max_items_total is None or len(all_items) < max_items_total: all_items.append(item)
                else: break
            current_url = response_data.get('@odata.nextLink')
            if not current_url or (max_items_total is not None and len(all_items) >= max_items_total): break
        logger.info(f"'{action_name_for_log}' recuperó {len(all_items)} items en {page_count} páginas.")
        return {"status": "success", "data": {"value": all_items, "@odata.count": len(all_items)}, "total_retrieved": len(all_items), "pages_processed": page_count}
    except Exception as e:
        return _handle_email_api_error(e, action_name_for_log, params)

def _normalize_recipients(rec_input: Optional[Union[str, List[str], List[Dict[str, Any]]]], type_name: str = "destinatario") -> List[Dict[str, Any]]:
    recipients_list: List[Dict[str, Any]] = []
    if rec_input is None: return recipients_list
    input_list_to_process: List[Any] = []
    if isinstance(rec_input, str):
        emails_from_string = [email.strip() for email in rec_input.replace(';', ',').split(',') if email.strip() and "@" in email]
        input_list_to_process.extend(emails_from_string)
    elif isinstance(rec_input, list):
        input_list_to_process = rec_input
    else:
        logger.warning(f"Entrada para '{type_name}' inválida (tipo {type(rec_input)}), se esperaba str o List. Se ignorará.")
        return []
    for item in input_list_to_process:
        if isinstance(item, str) and item.strip() and "@" in item:
            recipients_list.append({"emailAddress": {"address": item.strip()}})
        elif isinstance(item, dict) and isinstance(item.get("emailAddress"), dict) and isinstance(item["emailAddress"].get("address"), str) and item["emailAddress"]["address"].strip() and "@" in item["emailAddress"]["address"]:
            recipients_list.append(item)
        else:
            logger.warning(f"Item '{item}' en lista de '{type_name}' no es email válido o formato Graph esperado. Se ignorará.")
    if not recipients_list and rec_input:
        logger.warning(f"Entrada '{rec_input}' para '{type_name}' no resultó en destinatarios válidos.")
    return recipients_list

# --- Acciones de Correo ---

def list_messages(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Dict[str, Any]:
    action_name = "email_list_messages"
    mailbox: str = params.get('mailbox', settings.MAILBOX_USER_ID)
    folder_id: str = params.get('folder_id', 'Inbox')
    top_per_page: int = min(int(params.get('top_per_page', 25)), getattr(settings, "DEFAULT_PAGING_SIZE_MAIL", 50))
    max_items_total: Optional[int] = params.get('max_items_total')
    select_fields: Optional[str] = params.get('select')
    filter_query: Optional[str] = params.get('filter_query')
    order_by: Optional[str] = params.get('order_by', 'receivedDateTime desc')
    search_query: Optional[str] = params.get('search')

    user_path = "me" if mailbox.lower() == "me" else f"users/{mailbox}"
    url_base = f"{settings.GRAPH_API_BASE_URL}/{user_path}/mailFolders/{folder_id}/messages"
    
    query_api_params: Dict[str, Any] = {'$top': top_per_page}
    query_api_params['$select'] = select_fields or "id,receivedDateTime,subject,sender,from,toRecipients,ccRecipients,isRead,hasAttachments,importance,webLink"
    if search_query:
        query_api_params['$search'] = f'"{search_query}"' # Encerrar entre comillas
        if order_by: logger.info("'$orderby' se ignora cuando se usa '$search' en mensajes.")
    elif filter_query:
        query_api_params['$filter'] = filter_query
        if order_by: query_api_params['$orderby'] = order_by # orderby solo si no hay search
    elif order_by:
         query_api_params['$orderby'] = order_by


    return _email_paged_request(client, url_base, MAIL_READ_SCOPE, params, query_api_params, max_items_total, action_name)

def get_message(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Dict[str, Any]:
    action_name = "email_get_message"
    mailbox: str = params.get('mailbox', settings.MAILBOX_USER_ID)
    message_id: Optional[str] = params.get('message_id')
    select_fields: Optional[str] = params.get('select')
    expand_fields: Optional[str] = params.get('expand')

    if not message_id: return {"status": "error", "action": action_name, "message": "'message_id' es requerido.", "http_status": 400}
    user_path = "me" if mailbox.lower() == "me" else f"users/{mailbox}"
    url = f"{settings.GRAPH_API_BASE_URL}/{user_path}/messages/{message_id}"
    query_api_params: Dict[str, Any] = {}
    query_api_params['$select'] = select_fields or "id,receivedDateTime,subject,sender,from,toRecipients,ccRecipients,bccRecipients,body,bodyPreview,importance,isRead,isDraft,hasAttachments,webLink,conversationId,parentFolderId"
    if expand_fields: query_api_params['$expand'] = expand_fields
    
    logger.info(f"Leyendo correo '{message_id}' para '{mailbox}'")
    try:
        response = client.get(url, scope=MAIL_READ_SCOPE, params=query_api_params)
        return {"status": "success", "data": response.json()}
    except Exception as e:
        return _handle_email_api_error(e, action_name, params)

def send_message(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Dict[str, Any]:
    action_name = "email_send_message"
    mailbox: str = params.get('mailbox', settings.MAILBOX_USER_ID)
    to_recipients_in = params.get('to_recipients')
    subject: Optional[str] = params.get('subject')
    body_content: Optional[str] = params.get('body_content')
    body_type: str = params.get('body_type', 'HTML').upper()
    cc_recipients_in = params.get('cc_recipients')
    bcc_recipients_in = params.get('bcc_recipients')
    attachments_payload: Optional[List[dict]] = params.get('attachments')
    save_to_sent_items: bool = str(params.get('save_to_sent_items', "true")).lower() == "true"

    if not to_recipients_in or subject is None or body_content is None:
        return {"status": "error", "action": action_name, "message": "'to_recipients', 'subject', y 'body_content' son requeridos.", "http_status": 400}
    if body_type not in ["HTML", "TEXT"]: return {"status": "error", "action": action_name, "message": "'body_type' debe ser HTML o TEXT.", "http_status": 400}

    to_list = _normalize_recipients(to_recipients_in, "to_recipients")
    if not to_list: return {"status": "error", "action": action_name, "message": "Se requiere al menos un destinatario válido en 'to_recipients'.", "http_status": 400}
    
    message_obj: Dict[str, Any] = {"subject": subject, "body": {"contentType": body_type, "content": body_content}, "toRecipients": to_list}
    if cc_recipients_in: message_obj["ccRecipients"] = _normalize_recipients(cc_recipients_in, "cc_recipients")
    if bcc_recipients_in: message_obj["bccRecipients"] = _normalize_recipients(bcc_recipients_in, "bcc_recipients")
    if attachments_payload and isinstance(attachments_payload, list): message_obj["attachments"] = attachments_payload
    
    sendmail_payload = {"message": message_obj, "saveToSentItems": save_to_sent_items}
    user_path = "me" if mailbox.lower() == "me" else f"users/{mailbox}"
    url = f"{settings.GRAPH_API_BASE_URL}/{user_path}/sendMail"
    
    logger.info(f"Enviando correo desde '{mailbox}'. Asunto: '{subject}'")
    try:
        response = client.post(url, scope=MAIL_SEND_SCOPE, json_data=sendmail_payload)
        return {"status": "success", "message": "Solicitud de envío de correo aceptada.", "http_status": response.status_code} # 202 Accepted
    except Exception as e:
        return _handle_email_api_error(e, action_name, params)

def reply_message(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Dict[str, Any]:
    action_name = "email_reply_message" # o email_reply_all_message
    mailbox: str = params.get('mailbox', settings.MAILBOX_USER_ID)
    message_id: Optional[str] = params.get('message_id')
    comment_content: Optional[str] = params.get('comment')
    reply_all: bool = str(params.get('reply_all', "false")).lower() == "true"
    message_payload_override: Optional[Dict[str, Any]] = params.get("message_payload_override")

    if not message_id or comment_content is None:
        return {"status": "error", "action": action_name, "message": "'message_id' y 'comment' son requeridos.", "http_status": 400}

    action_segment = "replyAll" if reply_all else "reply"
    user_path = "me" if mailbox.lower() == "me" else f"users/{mailbox}"
    url = f"{settings.GRAPH_API_BASE_URL}/{user_path}/messages/{message_id}/{action_segment}"
    
    payload_reply: Dict[str, Any] = {"comment": comment_content}
    if message_payload_override and isinstance(message_payload_override, dict):
        payload_reply["message"] = message_payload_override
    
    logger.info(f"{'Respondiendo a todos' if reply_all else 'Respondiendo'} al correo '{message_id}' para '{mailbox}'")
    try:
        response = client.post(url, scope=MAIL_SEND_SCOPE, json_data=payload_reply)
        return {"status": "success", "message": f"Solicitud de {'respuesta a todos' if reply_all else 'respuesta'} aceptada.", "http_status": response.status_code} # 202 Accepted
    except Exception as e:
        return _handle_email_api_error(e, action_name, params)

def forward_message(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Dict[str, Any]:
    action_name = "email_forward_message"
    mailbox: str = params.get('mailbox', settings.MAILBOX_USER_ID)
    message_id: Optional[str] = params.get('message_id')
    to_recipients_in = params.get('to_recipients')
    comment_content: str = params.get('comment', "")
    message_payload_override: Optional[Dict[str, Any]] = params.get("message_payload_override")

    if not message_id or not to_recipients_in:
        return {"status": "error", "action": action_name, "message": "'message_id' y 'to_recipients' son requeridos.", "http_status": 400}
    
    to_list = _normalize_recipients(to_recipients_in, "to_recipients (forward)")
    if not to_list: return {"status": "error", "action": action_name, "message": "Se requiere al menos un destinatario válido en 'to_recipients' para reenviar.", "http_status": 400}

    user_path = "me" if mailbox.lower() == "me" else f"users/{mailbox}"
    url = f"{settings.GRAPH_API_BASE_URL}/{user_path}/messages/{message_id}/forward"
    payload_forward: Dict[str, Any] = {"toRecipients": to_list, "comment": comment_content}
    if message_payload_override and isinstance(message_payload_override, dict):
        payload_forward["message"] = message_payload_override
        
    logger.info(f"Reenviando correo '{message_id}' para '{mailbox}'")
    try:
        response = client.post(url, scope=MAIL_SEND_SCOPE, json_data=payload_forward)
        return {"status": "success", "message": "Solicitud de reenvío aceptada.", "http_status": response.status_code} # 202 Accepted
    except Exception as e:
        return _handle_email_api_error(e, action_name, params)

def delete_message(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Dict[str, Any]:
    action_name = "email_delete_message"
    mailbox: str = params.get('mailbox', settings.MAILBOX_USER_ID)
    message_id: Optional[str] = params.get('message_id')
    if not message_id: return {"status": "error", "action": action_name, "message": "'message_id' es requerido.", "http_status": 400}
    user_path = "me" if mailbox.lower() == "me" else f"users/{mailbox}"
    url = f"{settings.GRAPH_API_BASE_URL}/{user_path}/messages/{message_id}"
    logger.info(f"Eliminando correo '{message_id}' para '{mailbox}'")
    try:
        response = client.delete(url, scope=MAIL_READ_WRITE_SCOPE)
        return {"status": "success", "message": "Correo movido a elementos eliminados.", "http_status": response.status_code} # 204 No Content
    except Exception as e:
        return _handle_email_api_error(e, action_name, params)

def move_message(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Dict[str, Any]:
    action_name = "email_move_message"
    mailbox: str = params.get('mailbox', settings.MAILBOX_USER_ID)
    message_id: Optional[str] = params.get('message_id')
    destination_folder_id: Optional[str] = params.get('destination_folder_id')
    if not message_id or not destination_folder_id:
        return {"status": "error", "action": action_name, "message": "'message_id' y 'destination_folder_id' son requeridos.", "http_status": 400}
    user_path = "me" if mailbox.lower() == "me" else f"users/{mailbox}"
    url = f"{settings.GRAPH_API_BASE_URL}/{user_path}/messages/{message_id}/move"
    payload = {"destinationId": destination_folder_id}
    logger.info(f"Moviendo correo '{message_id}' para '{mailbox}' a carpeta '{destination_folder_id}'")
    try:
        response = client.post(url, scope=MAIL_READ_WRITE_SCOPE, json_data=payload)
        return {"status": "success", "data": response.json(), "message": "Correo movido."} # Devuelve el mensaje movido
    except Exception as e:
        return _handle_email_api_error(e, action_name, params)

def list_folders(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Dict[str, Any]:
    action_name = "email_list_folders"
    mailbox: str = params.get('mailbox', settings.MAILBOX_USER_ID)
    parent_folder_id: Optional[str] = params.get('parent_folder_id')
    top_per_page: int = min(int(params.get('top_per_page', 10)), getattr(settings, "DEFAULT_PAGING_SIZE", 25)) # Mail folders no suelen ser tantas
    max_items_total: Optional[int] = params.get('max_items_total')
    select_fields: Optional[str] = params.get('select')
    filter_query: Optional[str] = params.get('filter_query')
    # include_hidden_folders no es un param OData estándar, ver si Graph lo soporta para mailFolders.

    user_path = "me" if mailbox.lower() == "me" else f"users/{mailbox}"
    if parent_folder_id:
        url_base = f"{settings.GRAPH_API_BASE_URL}/{user_path}/mailFolders/{parent_folder_id}/childFolders"
    else:
        url_base = f"{settings.GRAPH_API_BASE_URL}/{user_path}/mailFolders"
        
    query_api_params: Dict[str, Any] = {'$top': top_per_page}
    query_api_params['$select'] = select_fields or "id,displayName,parentFolderId,childFolderCount,unreadItemCount,totalItemCount"
    if filter_query: query_api_params['$filter'] = filter_query
    
    log_ctx = f"carpetas para '{mailbox}'" + (f" bajo '{parent_folder_id}'" if parent_folder_id else "")
    return _email_paged_request(client, url_base, MAIL_READ_SCOPE, params, query_api_params, max_items_total, f"{action_name} ({log_ctx})")

def create_folder(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Dict[str, Any]:
    action_name = "email_create_folder"
    mailbox: str = params.get('mailbox', settings.MAILBOX_USER_ID)
    folder_name: Optional[str] = params.get('folder_name')
    parent_folder_id: Optional[str] = params.get('parent_folder_id')
    if not folder_name: return {"status": "error", "action": action_name, "message": "'folder_name' es requerido.", "http_status": 400}
    
    payload = {"displayName": folder_name}
    # if params.get('is_hidden') is not None: payload['isHidden'] = bool(params['is_hidden']) # Si se quiere setear
    
    user_path = "me" if mailbox.lower() == "me" else f"users/{mailbox}"
    url: str
    if parent_folder_id:
        url = f"{settings.GRAPH_API_BASE_URL}/{user_path}/mailFolders/{parent_folder_id}/childFolders"
    else:
        url = f"{settings.GRAPH_API_BASE_URL}/{user_path}/mailFolders"
        
    logger.info(f"Creando carpeta de correo '{folder_name}' para '{mailbox}'" + (f" bajo '{parent_folder_id}'" if parent_folder_id else ""))
    try:
        response = client.post(url, scope=MAIL_READ_WRITE_SCOPE, json_data=payload)
        return {"status": "success", "data": response.json(), "message": "Carpeta de correo creada."}
    except Exception as e:
        return _handle_email_api_error(e, action_name, params)

def search_messages(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Dict[str, Any]:
    action_name = "email_search_messages"
    # Esta acción es un wrapper para list_messages con el parámetro '$search'.
    mailbox: str = params.get('mailbox', settings.MAILBOX_USER_ID)
    search_query_kql: Optional[str] = params.get('query') # KQL query
    if not search_query_kql:
        return {"status": "error", "action": action_name, "message": "'query' de búsqueda es requerido.", "http_status": 400}

    # Preparar params para list_messages
    list_params = params.copy()
    list_params['search'] = search_query_kql # Asignar a 'search'
    if 'query' in list_params: del list_params['query']
    # folder_id para list_messages por defecto es 'Inbox', se puede sobreescribir si se pasa en params
    # Si se quiere buscar en todo el buzón, el endpoint de Graph es /me/messages sin mailFolders/{id}
    # y con $search. Esto requeriría una lógica un poco diferente a list_messages que asume un folder_id.
    # Por ahora, se buscará en la carpeta especificada por folder_id (o Inbox por defecto).
    
    # Para buscar en todo el buzón:
    # La función list_messages actual construye la URL con /mailFolders/{folder_id}/messages.
    # Para buscar en todo el buzón, la URL debería ser /me/messages o /users/{id}/messages.
    # Haremos una adaptación aquí si folder_id es explícitamente "all_mailbox" o no se provee un folder_id específico
    # y se desea buscar en todo.
    
    folder_id_for_search = params.get('folder_id', 'Inbox_ALL_MAILBOX_SEARCH_HACK') 
    # Si folder_id no se pasa, o es un valor especial, modificamos la URL base para list_messages
    
    user_path = "me" if mailbox.lower() == "me" else f"users/{mailbox}"
    if folder_id_for_search == 'Inbox_ALL_MAILBOX_SEARCH_HACK' or params.get('search_all_mailbox'):
        # Hack: list_messages internamente usa la URL base.
        # La forma más limpia sería tener una función search_all_messages_in_mailbox separada.
        # O modificar list_messages para manejar esto. Por ahora, un log y se procederá sobre Inbox.
        logger.warning("Búsqueda en todo el buzón no implementada directamente por search_messages, se buscará en 'Inbox' o folder_id provisto. Para buscar en todo el buzón, considere una acción específica o un 'graph_generic_get' a /me/messages?$search=...")
        # Se podría modificar la url_base aquí si list_messages lo permitiera, pero no es el caso.
        # La llamada actual a list_messages buscará dentro de la carpeta especificada (o Inbox).
    
    logger.info(f"Iniciando búsqueda de mensajes para '{mailbox}' con query: '{search_query_kql}' (vía list_messages)")
    return list_messages(client, list_params)