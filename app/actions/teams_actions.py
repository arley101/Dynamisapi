# app/actions/teams_actions.py
import logging
import requests # Para requests.exceptions.HTTPError
import json
from typing import Dict, List, Optional, Any, Union
from datetime import datetime # Para schedule_meeting

# Importar la configuración y el cliente HTTP autenticado
from app.core.config import settings
from app.shared.helpers.http_client import AuthenticatedHttpClient

logger = logging.getLogger(__name__)

# Scopes (se leerán de settings si están definidos, sino fallback a GRAPH_API_DEFAULT_SCOPE)
# Ejemplo: GRAPH_SCOPE_TEAMS_READ_BASIC_ALL = getattr(settings, 'GRAPH_SCOPE_TEAMS_READ_BASIC_ALL', settings.GRAPH_API_DEFAULT_SCOPE)

def _handle_teams_api_error(e: Exception, action_name: str, params_for_log: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    log_message = f"Error en Teams action '{action_name}'"
    safe_params = {}
    if params_for_log:
        sensitive_keys = ['message', 'content', 'body', 'payload']
        safe_params = {k: (v if k not in sensitive_keys else "[CONTENIDO OMITIDO]") for k, v in params_for_log.items()}
        log_message += f" con params: {safe_params}"
    logger.error(f"{log_message}: {type(e).__name__} - {str(e)}", exc_info=True)
    details = str(e); status_code = 500; graph_error_code = None
    if isinstance(e, requests.exceptions.HTTPError) and e.response is not None:
        status_code = e.response.status_code
        try:
            error_data = e.response.json(); error_info = error_data.get("error", {})
            details = error_info.get("message", e.response.text); graph_error_code = error_info.get("code")
        except json.JSONDecodeError: details = e.response.text
    return {"status": "error", "action": action_name, "message": f"Error ejecutando {action_name}: {type(e).__name__}", "http_status": status_code, "details": details, "graph_error_code": graph_error_code}

def _teams_paged_request(
    client: AuthenticatedHttpClient, url_base: str, scope: List[str],
    params_input: Dict[str, Any], query_api_params_initial: Dict[str, Any],
    max_items_total: int, action_name_for_log: str
) -> Dict[str, Any]:
    all_items: List[Dict[str, Any]] = []; current_url: Optional[str] = url_base; page_count = 0
    max_pages_to_fetch = getattr(settings, 'MAX_PAGING_PAGES', 20)
    top_value = query_api_params_initial.get('$top', getattr(settings, 'DEFAULT_PAGING_SIZE', 50))
    logger.info(f"Iniciando solicitud paginada para '{action_name_for_log}' desde '{url_base.split('?')[0]}...'. Max total: {max_items_total}, por pág: {top_value}, max_págs: {max_pages_to_fetch}")
    try:
        while current_url and len(all_items) < max_items_total and page_count < max_pages_to_fetch:
            page_count += 1; is_first_call = (page_count == 1 and current_url == url_base)
            current_call_params = query_api_params_initial if is_first_call else None
            logger.debug(f"Página {page_count} para '{action_name_for_log}': GET {current_url.split('?')[0]} con params: {current_call_params}")
            response = client.get(url=current_url, scope=scope, params=current_call_params)
            response_data = response.json(); page_items = response_data.get('value', [])
            if not isinstance(page_items, list): break
            for item in page_items:
                if len(all_items) < max_items_total: all_items.append(item)
                else: break
            current_url = response_data.get('@odata.nextLink')
            if not current_url or len(all_items) >= max_items_total: break
        logger.info(f"'{action_name_for_log}' recuperó {len(all_items)} items en {page_count} páginas.")
        return {"status": "success", "data": {"value": all_items, "@odata.count": len(all_items)}, "total_retrieved": len(all_items), "pages_processed": page_count}
    except Exception as e:
        return _handle_teams_api_error(e, action_name_for_log, params_input)

def list_joined_teams(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Dict[str, Any]:
    url_base = f"{settings.GRAPH_API_BASE_URL}/me/joinedTeams"
    top_per_page: int = min(int(params.get('top_per_page', 25)), getattr(settings, 'DEFAULT_PAGING_SIZE', 50))
    max_items_total: int = int(params.get('max_items_total', 100))
    query_api_params: Dict[str, Any] = {'$top': top_per_page}
    query_api_params['$select'] = params.get('select', "id,displayName,description,isArchived,webUrl")
    if params.get('filter_query'): query_api_params['$filter'] = params['filter_query']
    teams_read_scope = getattr(settings, 'GRAPH_SCOPE_TEAMS_READ_BASIC_ALL', settings.GRAPH_API_DEFAULT_SCOPE)
    return _teams_paged_request(client, url_base, teams_read_scope, params, query_api_params, max_items_total, "list_joined_teams")

def get_team(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Dict[str, Any]:
    team_id: Optional[str] = params.get("team_id")
    if not team_id: return _handle_teams_api_error(ValueError("'team_id' es requerido."), "get_team", params)
    url = f"{settings.GRAPH_API_BASE_URL}/teams/{team_id}"
    query_params = {'$select': params['select']} if params.get("select") else None
    logger.info(f"Obteniendo detalles del equipo '{team_id}'")
    teams_read_scope = getattr(settings, 'GRAPH_SCOPE_TEAMS_READ_BASIC_ALL', settings.GRAPH_API_DEFAULT_SCOPE)
    try:
        response = client.get(url, scope=teams_read_scope, params=query_params)
        return {"status": "success", "data": response.json()}
    except Exception as e: return _handle_teams_api_error(e, "get_team", params)

def list_channels(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Dict[str, Any]:
    team_id: Optional[str] = params.get("team_id")
    if not team_id: return _handle_teams_api_error(ValueError("'team_id' es requerido."), "list_channels", params)
    url_base = f"{settings.GRAPH_API_BASE_URL}/teams/{team_id}/channels"
    top_per_page: int = min(int(params.get('top_per_page', 25)), getattr(settings, 'DEFAULT_PAGING_SIZE', 50))
    max_items_total: int = int(params.get('max_items_total', 100))
    query_api_params: Dict[str, Any] = {'$top': top_per_page}
    query_api_params['$select'] = params.get('select', "id,displayName,description,webUrl,email,membershipType")
    if params.get('filter_query'): query_api_params['$filter'] = params['filter_query']
    channel_read_scope = getattr(settings, 'GRAPH_SCOPE_CHANNEL_READ_ALL', settings.GRAPH_API_DEFAULT_SCOPE)
    return _teams_paged_request(client, url_base, channel_read_scope, params, query_api_params, max_items_total, f"list_channels (team: {team_id})")

def get_channel(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Dict[str, Any]:
    team_id: Optional[str] = params.get("team_id"); channel_id: Optional[str] = params.get("channel_id")
    if not team_id or not channel_id: return _handle_teams_api_error(ValueError("'team_id' y 'channel_id' requeridos."), "get_channel", params)
    url = f"{settings.GRAPH_API_BASE_URL}/teams/{team_id}/channels/{channel_id}"
    query_params = {'$select': params['select']} if params.get("select") else None
    logger.info(f"Obteniendo detalles del canal '{channel_id}' en equipo '{team_id}'")
    channel_read_scope = getattr(settings, 'GRAPH_SCOPE_CHANNEL_READ_ALL', settings.GRAPH_API_DEFAULT_SCOPE)
    try:
        response = client.get(url, scope=channel_read_scope, params=query_params)
        return {"status": "success", "data": response.json()}
    except Exception as e: return _handle_teams_api_error(e, "get_channel", params)

def send_channel_message(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Dict[str, Any]:
    team_id: Optional[str] = params.get("team_id"); channel_id: Optional[str] = params.get("channel_id")
    message_content: Optional[str] = params.get("content"); content_type: str = params.get("content_type", "HTML").upper()
    if not team_id or not channel_id or message_content is None: return _handle_teams_api_error(ValueError("'team_id', 'channel_id', 'content' requeridos."), "send_channel_message", params)
    if content_type not in ["HTML", "TEXT"]: return _handle_teams_api_error(ValueError("'content_type' debe ser HTML o TEXT."), "send_channel_message", params)
    url = f"{settings.GRAPH_API_BASE_URL}/teams/{team_id}/channels/{channel_id}/messages"
    payload = {"body": {"contentType": content_type, "content": message_content}}
    if params.get("subject"): payload["subject"] = params["subject"]
    logger.info(f"Enviando mensaje al canal '{channel_id}' del equipo '{team_id}'")
    message_send_scope = getattr(settings, 'GRAPH_SCOPE_CHANNEL_MESSAGE_SEND', settings.GRAPH_API_DEFAULT_SCOPE)
    try:
        response = client.post(url, scope=message_send_scope, json_data=payload)
        return {"status": "success", "data": response.json(), "message": "Mensaje enviado al canal."}
    except Exception as e: return _handle_teams_api_error(e, "send_channel_message", params)

def list_channel_messages(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Dict[str, Any]:
    team_id: Optional[str] = params.get("team_id"); channel_id: Optional[str] = params.get("channel_id")
    if not team_id or not channel_id: return _handle_teams_api_error(ValueError("'team_id' y 'channel_id' requeridos."), "list_channel_messages", params)
    url_base = f"{settings.GRAPH_API_BASE_URL}/teams/{team_id}/channels/{channel_id}/messages"
    top_per_page: int = min(int(params.get('top_per_page', 25)), 50)
    max_items_total: int = int(params.get('max_items_total', 100))
    query_api_params: Dict[str, Any] = {'$top': top_per_page}
    query_api_params['$select'] = params.get('select', "id,subject,summary,body,from,createdDateTime,lastModifiedDateTime,importance,webUrl")
    if str(params.get('expand_replies', "false")).lower() == "true": query_api_params['$expand'] = "replies"
    action_log_name = f"list_channel_messages (team: {team_id}, channel: {channel_id})"
    channel_read_scope = getattr(settings, 'GRAPH_SCOPE_CHANNEL_READ_ALL', settings.GRAPH_API_DEFAULT_SCOPE)
    return _teams_paged_request(client, url_base, channel_read_scope, params, query_api_params, max_items_total, action_log_name)

def reply_to_message(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Dict[str, Any]:
    team_id: Optional[str] = params.get("team_id"); channel_id: Optional[str] = params.get("channel_id"); message_id: Optional[str] = params.get("message_id")
    reply_content: Optional[str] = params.get("content"); content_type: str = params.get("content_type", "HTML").upper()
    if not team_id or not channel_id or not message_id or reply_content is None: return _handle_teams_api_error(ValueError("'team_id', 'channel_id', 'message_id', 'content' requeridos."), "reply_to_message", params)
    url = f"{settings.GRAPH_API_BASE_URL}/teams/{team_id}/channels/{channel_id}/messages/{message_id}/replies"
    payload = {"body": {"contentType": content_type, "content": reply_content}}
    logger.info(f"Enviando respuesta al mensaje '{message_id}' en canal '{channel_id}', equipo '{team_id}'")
    message_send_scope = getattr(settings, 'GRAPH_SCOPE_CHANNEL_MESSAGE_SEND', settings.GRAPH_API_DEFAULT_SCOPE)
    try:
        response = client.post(url, scope=message_send_scope, json_data=payload)
        return {"status": "success", "data": response.json(), "message": "Respuesta enviada."}
    except Exception as e: return _handle_teams_api_error(e, "reply_to_message", params)

def list_chats(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Dict[str, Any]:
    url_base = f"{settings.GRAPH_API_BASE_URL}/me/chats"
    top_per_page: int = min(int(params.get('top_per_page', 25)), 50)
    max_items_total: int = int(params.get('max_items_total', 100))
    query_api_params: Dict[str, Any] = {'$top': top_per_page}
    query_api_params['$select'] = params.get('select', "id,topic,chatType,createdDateTime,lastUpdatedDateTime,webUrl")
    if params.get('filter_query'): query_api_params['$filter'] = params['filter_query']
    if str(params.get('expand_members', "false")).lower() == "true": query_api_params['$expand'] = "members"
    chat_rw_scope = getattr(settings, 'GRAPH_SCOPE_CHAT_READ_WRITE', settings.GRAPH_API_DEFAULT_SCOPE)
    return _teams_paged_request(client, url_base, chat_rw_scope, params, query_api_params, max_items_total, "list_chats")

def get_chat(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Dict[str, Any]:
    chat_id: Optional[str] = params.get("chat_id")
    if not chat_id: return _handle_teams_api_error(ValueError("'chat_id' es requerido."), "get_chat", params)
    url = f"{settings.GRAPH_API_BASE_URL}/chats/{chat_id}"
    query_api_params: Dict[str, Any] = {}
    if params.get("select"): query_api_params['$select'] = params['select']
    if str(params.get('expand_members', "false")).lower() == "true": query_api_params['$expand'] = "members"
    logger.info(f"Obteniendo detalles del chat '{chat_id}'")
    chat_rw_scope = getattr(settings, 'GRAPH_SCOPE_CHAT_READ_WRITE', settings.GRAPH_API_DEFAULT_SCOPE)
    try:
        response = client.get(url, scope=chat_rw_scope, params=query_api_params if query_api_params else None)
        return {"status": "success", "data": response.json()}
    except Exception as e: return _handle_teams_api_error(e, "get_chat", params)

def create_chat(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Dict[str, Any]:
    chat_type: str = params.get("chat_type", "group").lower()
    members_payload: Optional[List[Dict[str, Any]]] = params.get("members"); topic: Optional[str] = params.get("topic")
    if not members_payload or not isinstance(members_payload, list) or len(members_payload) < (1 if chat_type == "oneonone" else 2): return _handle_teams_api_error(ValueError(f"'members' (lista) requerido con al menos {'1' if chat_type == 'oneonone' else '2'} miembros."), "create_chat", params)
    if chat_type == "group" and not topic: return _handle_teams_api_error(ValueError("'topic' es requerido para chats grupales."), "create_chat", params)
    if chat_type not in ["oneonone", "group"]: return _handle_teams_api_error(ValueError("'chat_type' debe ser 'oneOnOne' o 'group'."), "create_chat", params)
    url = f"{settings.GRAPH_API_BASE_URL}/chats"
    payload: Dict[str, Any] = {"chatType": chat_type, "members": members_payload}
    if chat_type == "group" and topic: payload["topic"] = topic
    logger.info(f"Creando chat tipo '{chat_type}'" + (f" con tópico '{topic}'" if topic else ""))
    chat_rw_scope = getattr(settings, 'GRAPH_SCOPE_CHAT_READ_WRITE', settings.GRAPH_API_DEFAULT_SCOPE)
    try:
        response = client.post(url, scope=chat_rw_scope, json_data=payload)
        return {"status": "success", "data": response.json(), "message": "Chat creado."}
    except Exception as e: return _handle_teams_api_error(e, "create_chat", params)

def send_chat_message(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Dict[str, Any]:
    chat_id: Optional[str] = params.get("chat_id"); message_content: Optional[str] = params.get("content")
    content_type: str = params.get("content_type", "HTML").upper()
    if not chat_id or message_content is None: return _handle_teams_api_error(ValueError("'chat_id' y 'content' son requeridos."), "send_chat_message", params)
    url = f"{settings.GRAPH_API_BASE_URL}/chats/{chat_id}/messages"
    payload = {"body": {"contentType": content_type, "content": message_content}}
    logger.info(f"Enviando mensaje al chat '{chat_id}'")
    chat_send_scope = getattr(settings, 'GRAPH_SCOPE_CHAT_SEND', settings.GRAPH_API_DEFAULT_SCOPE)
    try:
        response = client.post(url, scope=chat_send_scope, json_data=payload)
        return {"status": "success", "data": response.json(), "message": "Mensaje enviado al chat."}
    except Exception as e: return _handle_teams_api_error(e, "send_chat_message", params)

def list_chat_messages(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Dict[str, Any]:
    chat_id: Optional[str] = params.get("chat_id")
    if not chat_id: return _handle_teams_api_error(ValueError("'chat_id' es requerido."), "list_chat_messages", params)
    url_base = f"{settings.GRAPH_API_BASE_URL}/chats/{chat_id}/messages"
    top_per_page: int = min(int(params.get('top_per_page', 25)), 50)
    max_items_total: int = int(params.get('max_items_total', 100))
    query_api_params: Dict[str, Any] = {'$top': top_per_page}
    query_api_params['$select'] = params.get('select', "id,subject,body,from,createdDateTime,lastModifiedDateTime,importance,webUrl")
    action_log_name = f"list_chat_messages (chat: {chat_id})"
    chat_rw_scope = getattr(settings, 'GRAPH_SCOPE_CHAT_READ_WRITE', settings.GRAPH_API_DEFAULT_SCOPE)
    return _teams_paged_request(client, url_base, chat_rw_scope, params, query_api_params, max_items_total, action_log_name)

def schedule_meeting(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Dict[str, Any]:
    subject: Optional[str] = params.get("subject"); start_datetime_str: Optional[str] = params.get("start_datetime")
    end_datetime_str: Optional[str] = params.get("end_datetime"); timezone: Optional[str] = params.get("timezone", "UTC")
    attendees_payload: Optional[List[Dict[str, Any]]] = params.get("attendees")
    body_content: Optional[str] = params.get("body_content"); body_type: str = params.get("body_type", "HTML").upper()
    if not subject or not start_datetime_str or not end_datetime_str: return _handle_teams_api_error(ValueError("'subject', 'start_datetime', 'end_datetime' requeridos."), "schedule_meeting", params)
    try:
        start_obj = datetime.fromisoformat(start_datetime_str.replace('Z', '+00:00'))
        end_obj = datetime.fromisoformat(end_datetime_str.replace('Z', '+00:00'))
    except ValueError as ve: return _handle_teams_api_error(ValueError(f"Formato de fecha inválido: {ve}"), "schedule_meeting", params)
    url = f"{settings.GRAPH_API_BASE_URL}/me/events"
    payload = {"subject": subject, "start": {"dateTime": start_obj.isoformat(), "timeZone": timezone}, "end": {"dateTime": end_obj.isoformat(), "timeZone": timezone}, "isOnlineMeeting": True, "onlineMeetingProvider": "teamsForBusiness"}
    if attendees_payload and isinstance(attendees_payload, list): payload["attendees"] = attendees_payload
    if body_content: payload["body"] = {"contentType": body_type, "content": body_content}
    logger.info(f"Programando reunión de Teams: '{subject}'")
    meeting_rw_scope = getattr(settings, 'GRAPH_SCOPE_ONLINE_MEETINGS_READ_WRITE', getattr(settings, 'GRAPH_SCOPE_CALENDARS_READ_WRITE', settings.GRAPH_API_DEFAULT_SCOPE))
    try:
        response = client.post(url, scope=meeting_rw_scope, json_data=payload)
        return {"status": "success", "data": response.json(), "message": "Reunión programada."}
    except Exception as e: return _handle_teams_api_error(e, "schedule_meeting", params)

def get_meeting_details(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Dict[str, Any]:
    event_id: Optional[str] = params.get("event_id")
    if not event_id: return _handle_teams_api_error(ValueError("'event_id' es requerido."), "get_meeting_details", params)
    url = f"{settings.GRAPH_API_BASE_URL}/me/events/{event_id}"
    query_params = {'$select': 'id,subject,start,end,organizer,attendees,body,onlineMeeting,webLink'}
    logger.info(f"Obteniendo detalles de reunión (evento) '{event_id}'")
    meeting_read_scope = getattr(settings, 'GRAPH_SCOPE_ONLINE_MEETINGS_READ', getattr(settings, 'GRAPH_SCOPE_CALENDARS_READ', settings.GRAPH_API_DEFAULT_SCOPE))
    try:
        response = client.get(url, scope=meeting_read_scope, params=query_params)
        event_data = response.json()
        if not event_data.get("onlineMeeting"): return {"status": "warning", "data": event_data, "message": "Evento obtenido, pero no parece ser una reunión online de Teams."}
        return {"status": "success", "data": event_data}
    except Exception as e: return _handle_teams_api_error(e, "get_meeting_details", params)

def list_members(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Dict[str, Any]:
    team_id: Optional[str] = params.get("team_id"); chat_id: Optional[str] = params.get("chat_id")
    if not team_id and not chat_id: return _handle_teams_api_error(ValueError("Se requiere 'team_id' o 'chat_id'."), "list_members", params)
    if team_id and chat_id: return _handle_teams_api_error(ValueError("Proporcione 'team_id' O 'chat_id', no ambos."), "list_members", params)
    parent_type = "equipo" if team_id else "chat"; parent_id = team_id if team_id else chat_id
    url_base: str; scope_to_use: List[str]
    if team_id:
        url_base = f"{settings.GRAPH_API_BASE_URL}/teams/{team_id}/members"
        scope_to_use = getattr(settings, 'GRAPH_SCOPE_GROUP_READ_ALL', settings.GRAPH_API_DEFAULT_SCOPE) # TeamMember.Read.All or Group.Read.All
    else: # chat_id
        url_base = f"{settings.GRAPH_API_BASE_URL}/chats/{chat_id}/members"
        scope_to_use = getattr(settings, 'GRAPH_SCOPE_CHAT_MEMBER_READ', settings.GRAPH_API_DEFAULT_SCOPE) # ChatMember.Read.All or Chat.ReadBasic
    top_per_page: int = min(int(params.get('top_per_page', 25)), getattr(settings, 'DEFAULT_PAGING_SIZE', 50))
    max_items_total: int = int(params.get('max_items_total', 100))
    query_api_params: Dict[str, Any] = {'$top': top_per_page}
    query_api_params['$select'] = params.get('select', "id,displayName,userId,email,roles")
    if params.get('filter_query'): query_api_params['$filter'] = params['filter_query']
    action_log_name = f"list_members ({parent_type}: {parent_id})"
    return _teams_paged_request(client, url_base, scope_to_use, params, query_api_params, max_items_total, action_log_name)

# --- FIN DEL MÓDULO actions/teams_actions.py ---