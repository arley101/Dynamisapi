# app/actions/office_actions.py
import logging
import requests # Para requests.exceptions.HTTPError y manejar respuestas binarias
import json
from typing import Dict, List, Optional, Union, Any

from app.core.config import settings
from app.shared.helpers.http_client import AuthenticatedHttpClient

logger = logging.getLogger(__name__)

# Helper para construir URLs de items en OneDrive del usuario actual (/me/drive)
def _get_me_drive_item_content_url(item_path_or_id: str) -> str:
    """Devuelve la URL para el contenido de un item en /me/drive."""
    base_url = f"{settings.GRAPH_API_BASE_URL}/me/drive"
    if "/" in item_path_or_id or ("." in item_path_or_id and not item_path_or_id.startswith("driveItem_") and len(item_path_or_id) < 70):
        # Asumir que es una ruta relativa a la raíz del drive
        clean_path = item_path_or_id.strip('/')
        return f"{base_url}/root:/{clean_path}:/content"
    else:
        # Asumir que es un ID de item
        return f"{base_url}/items/{item_path_or_id}/content"

def _get_me_drive_item_workbook_url_base(item_id: str) -> str:
    """Devuelve la URL base para operaciones de Workbook en /me/drive."""
    return f"{settings.GRAPH_API_BASE_URL}/me/drive/items/{item_id}/workbook"

# Helper para manejo de errores de Office/Graph
def _handle_office_api_error(e: Exception, action_name: str, params_for_log: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    log_message = f"Error en Office Action '{action_name}'"
    if params_for_log:
        safe_params = {k: v for k, v in params_for_log.items() if k not in ['nuevo_contenido', 'valores', 'valores_filas']}
        log_message += f" con params: {safe_params}"
    logger.error(f"{log_message}: {type(e).__name__} - {str(e)}", exc_info=True)
    details_str = str(e); status_code_int = 500; graph_error_code = None
    if isinstance(e, requests.exceptions.HTTPError) and e.response is not None:
        status_code_int = e.response.status_code
        try:
            error_data = e.response.json(); error_info = error_data.get("error", {})
            details_str = error_info.get("message", e.response.text); graph_error_code = error_info.get("code")
        except Exception: details_str = e.response.text[:500] if e.response.text else "No response body"
    return {"status": "error", "action": action_name, "message": f"Error en {action_name}: {type(e).__name__}", "http_status": status_code_int, "details": details_str, "graph_error_code": graph_error_code}


# --- Acciones de Word (Operando sobre OneDrive /me/drive) ---

def crear_documento_word(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Dict[str, Any]:
    action_name = "office_crear_documento_word"
    nombre_archivo: Optional[str] = params.get("nombre_archivo")
    ruta_onedrive: str = params.get("ruta_onedrive", "/") # Relativa a la raíz de /me/drive
    conflict_behavior: str = params.get("conflict_behavior", "rename")

    if not nombre_archivo:
        return {"status": "error", "action": action_name, "message": "'nombre_archivo' es requerido.", "http_status": 400}
    if not nombre_archivo.lower().endswith(".docx"):
        nombre_archivo += ".docx"

    clean_folder_path = ruta_onedrive.strip('/')
    target_file_path_in_drive = f"{nombre_archivo}" if not clean_folder_path else f"{clean_folder_path}/{nombre_archivo}"
    
    # URL para crear archivo por path: /me/drive/root:/folder/file.docx:/content
    url = f"{settings.GRAPH_API_BASE_URL}/me/drive/root:/{target_file_path_in_drive}:/content"
    # Parámetros de query para comportamiento en conflicto
    query_api_params = {"@microsoft.graph.conflictBehavior": conflict_behavior}
    
    headers_upload = {'Content-Type': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'}
    
    logger.info(f"Creando documento Word vacío '{nombre_archivo}' en OneDrive /me/drive ruta '{target_file_path_in_drive}'")
    try:
        # PUT con cuerpo vacío (b'') para crear un archivo .docx vacío
        response = client.put(url, scope=settings.GRAPH_API_DEFAULT_SCOPE, params=query_api_params, data=b'', headers=headers_upload)
        return {"status": "success", "data": response.json(), "message": f"Documento Word '{nombre_archivo}' creado."}
    except Exception as e:
        return _handle_office_api_error(e, action_name, params)

def reemplazar_contenido_word(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Dict[str, Any]:
    action_name = "office_reemplazar_contenido_word"
    item_id_o_ruta: Optional[str] = params.get("item_id_o_ruta") # ID del archivo o ruta en /me/drive
    nuevo_contenido: Optional[Union[str, bytes]] = params.get("nuevo_contenido")
    content_type_param: Optional[str] = params.get("content_type")

    if not item_id_o_ruta or nuevo_contenido is None:
        return {"status": "error", "action": action_name, "message": "'item_id_o_ruta' y 'nuevo_contenido' son requeridos.", "http_status": 400}

    url = _get_me_drive_item_content_url(item_id_o_ruta)
    headers_upload = {}
    data_to_send: bytes

    if isinstance(nuevo_contenido, str):
        data_to_send = nuevo_contenido.encode('utf-8')
        headers_upload['Content-Type'] = content_type_param or 'text/plain'
        logger.warning(f"Reemplazando contenido Word '{item_id_o_ruta}' con texto plano. Se perderá el formato.")
    elif isinstance(nuevo_contenido, bytes):
        data_to_send = nuevo_contenido
        headers_upload['Content-Type'] = content_type_param or 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
    else:
        return {"status": "error", "action": action_name, "message": "'nuevo_contenido' debe ser string o bytes.", "http_status": 400}

    logger.info(f"Reemplazando contenido Word '{item_id_o_ruta}' en OneDrive /me/drive.")
    try:
        response = client.put(url, scope=settings.GRAPH_API_DEFAULT_SCOPE, data=data_to_send, headers=headers_upload)
        return {"status": "success", "data": response.json(), "message": "Contenido de Word reemplazado."}
    except Exception as e:
        return _handle_office_api_error(e, action_name, params)

def obtener_documento_word_binario(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Union[bytes, Dict[str, Any]]:
    action_name = "office_obtener_documento_word_binario"
    item_id_o_ruta: Optional[str] = params.get("item_id_o_ruta") # ID o ruta en /me/drive
    if not item_id_o_ruta:
        return {"status": "error", "action": action_name, "message": "'item_id_o_ruta' es requerido.", "http_status": 400}

    url = _get_me_drive_item_content_url(item_id_o_ruta)
    logger.info(f"Obteniendo binario de Word '{item_id_o_ruta}' desde OneDrive /me/drive.")
    try:
        response = client.get(url, scope=settings.GRAPH_API_DEFAULT_SCOPE, stream=True) # stream=True para contenido binario
        file_bytes = response.content
        logger.info(f"Documento Word '{item_id_o_ruta}' descargado ({len(file_bytes)} bytes).")
        return file_bytes # Devolver bytes directamente, el router FastAPI lo manejará.
    except Exception as e:
        return _handle_office_api_error(e, action_name, params)


# --- Acciones de Excel (Operando sobre OneDrive /me/drive) ---
# Para SharePoint, se necesitaría pasar site_id y drive_id, y construir URLs base diferentes.

def crear_libro_excel(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Dict[str, Any]:
    action_name = "office_crear_libro_excel"
    # Idéntico a crear_documento_word pero con diferente Content-Type y extensión.
    nombre_archivo: Optional[str] = params.get("nombre_archivo")
    ruta_onedrive: str = params.get("ruta_onedrive", "/")
    conflict_behavior: str = params.get("conflict_behavior", "rename")

    if not nombre_archivo:
        return {"status": "error", "action": action_name, "message": "'nombre_archivo' es requerido.", "http_status": 400}
    if not nombre_archivo.lower().endswith(".xlsx"):
        nombre_archivo += ".xlsx"
    
    clean_folder_path = ruta_onedrive.strip('/')
    target_file_path_in_drive = f"{nombre_archivo}" if not clean_folder_path else f"{clean_folder_path}/{nombre_archivo}"
    url = f"{settings.GRAPH_API_BASE_URL}/me/drive/root:/{target_file_path_in_drive}:/content"
    query_api_params = {"@microsoft.graph.conflictBehavior": conflict_behavior}
    headers_upload = {'Content-Type': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'}

    logger.info(f"Creando libro Excel '{nombre_archivo}' en OneDrive /me/drive ruta '{target_file_path_in_drive}'")
    try:
        response = client.put(url, scope=settings.GRAPH_API_DEFAULT_SCOPE, params=query_api_params, data=b'', headers=headers_upload)
        return {"status": "success", "data": response.json(), "message": f"Libro Excel '{nombre_archivo}' creado."}
    except Exception as e:
        return _handle_office_api_error(e, action_name, params)

def leer_celda_excel(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Dict[str, Any]:
    action_name = "office_leer_celda_excel"
    item_id: Optional[str] = params.get("item_id") # ID del archivo Excel en /me/drive
    hoja: Optional[str] = params.get("hoja") # Nombre o ID de la hoja
    celda_o_rango: Optional[str] = params.get("celda_o_rango") # Ej: "A1" o "Sheet1!A1:C5"

    if not all([item_id, hoja, celda_o_rango]):
        return {"status": "error", "action": action_name, "message": "'item_id', 'hoja', y 'celda_o_rango' son requeridos.", "http_status": 400}

    # Si la dirección ya incluye la hoja (ej. 'Sheet1!A1'), no se necesita /worksheets/{hoja}
    address_param = celda_o_rango
    if "!" not in celda_o_rango: # Si no se especifica la hoja en la dirección, usar el parámetro 'hoja'
        address_param = f"'{hoja}'!{celda_o_rango}"
    
    # El endpoint es /workbook/worksheet/{id|name}/range(address='...') o /workbook/range(address='Sheet1!A1')
    # Para mayor flexibilidad, usamos el que no requiere ID/nombre de hoja en el path si ya está en la dirección.
    # url = f"{_get_me_drive_item_workbook_url_base(item_id)}/worksheets/{hoja}/range(address='{celda_o_rango}')"
    # O más general si la dirección es completa:
    url = f"{_get_me_drive_item_workbook_url_base(item_id)}/range(address='{address_param}')"

    # $select para obtener valores, texto, fórmulas, etc.
    # La API de Graph para rangos es un poco diferente; no usa $select para estos detalles.
    # Se accede a las propiedades directamente del objeto range.
    # Para obtener múltiples propiedades, se pueden hacer llamadas separadas o construir un $select complejo si se quiere el objeto completo.
    # Para este ejemplo, obtenemos el objeto range y el llamador puede inspeccionar sus propiedades.
    # O, para ser específicos, se puede acceder a sub-propiedades como /range/text, /range/values, /range/formulas.
    # Por simplicidad, se devuelve el objeto range completo.
    
    logger.info(f"Leyendo Excel item '{item_id}', address='{address_param}'")
    try:
        response = client.get(url, scope=settings.GRAPH_API_DEFAULT_SCOPE) # Workbook.* scopes
        return {"status": "success", "data": response.json()}
    except Exception as e:
        return _handle_office_api_error(e, action_name, params)

def escribir_celda_excel(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Dict[str, Any]:
    action_name = "office_escribir_celda_excel"
    item_id: Optional[str] = params.get("item_id")
    hoja: Optional[str] = params.get("hoja")
    celda_o_rango: Optional[str] = params.get("celda_o_rango")
    valores: Optional[List[List[Any]]] = params.get("valores") # Debe ser una lista de listas (filas y columnas)

    if not all([item_id, hoja, celda_o_rango, valores]):
        return {"status": "error", "action": action_name, "message": "'item_id', 'hoja', 'celda_o_rango', y 'valores' (List[List[Any]]) son requeridos.", "http_status": 400}
    if not isinstance(valores, list) or not all(isinstance(row, list) for row in valores):
        return {"status": "error", "action": action_name, "message": "'valores' debe ser una lista de listas.", "http_status": 400}

    address_param = celda_o_rango
    if "!" not in celda_o_rango:
        address_param = f"'{hoja}'!{celda_o_rango}"
    
    url = f"{_get_me_drive_item_workbook_url_base(item_id)}/range(address='{address_param}')"
    # El payload para PATCH en un rango actualiza las propiedades del rango.
    # Para escribir valores, se actualiza la propiedad 'values'.
    payload = {"values": valores}
    # Opcionalmente, se pueden enviar 'formulas' si se quieren escribir fórmulas.

    logger.info(f"Escribiendo en Excel item '{item_id}', address='{address_param}'")
    try:
        response = client.patch(url, scope=settings.GRAPH_API_DEFAULT_SCOPE, json_data=payload) # Workbook.* scopes
        return {"status": "success", "data": response.json(), "message": "Celda/rango de Excel actualizado."}
    except Exception as e:
        return _handle_office_api_error(e, action_name, params)

def crear_tabla_excel(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Dict[str, Any]:
    action_name = "office_crear_tabla_excel"
    item_id: Optional[str] = params.get("item_id")
    hoja_nombre_o_id: Optional[str] = params.get("hoja")
    rango_direccion: Optional[str] = params.get("rango") # Ej: "A1:C10" (sin nombre de hoja)
    tiene_headers_tabla: bool = str(params.get("tiene_headers_tabla", "false")).lower() == "true"
    nombre_tabla: Optional[str] = params.get("nombre_tabla") # Opcional

    if not all([item_id, hoja_nombre_o_id, rango_direccion]):
        return {"status": "error", "action": action_name, "message": "'item_id', 'hoja', y 'rango' son requeridos.", "http_status": 400}

    url = f"{_get_me_drive_item_workbook_url_base(item_id)}/worksheets/{hoja_nombre_o_id}/tables/add"
    # La API /add espera la dirección del rango y si tiene encabezados.
    # El nombre de la tabla es opcional; si no se provee, Graph genera uno.
    payload: Dict[str, Any] = {
        "address": rango_direccion, # La API espera el rango sin el nombre de la hoja aquí.
        "hasHeaders": tiene_headers_tabla
    }
    # Si se proporciona un nombre de tabla, se puede intentar establecerlo, aunque la API /add lo devuelve.
    # Crear una tabla con nombre específico es más complejo, a menudo se crea y luego se actualiza.
    # Por ahora, nos enfocamos en crearla con /add. El nombre se puede pasar en el payload de 'add'.
    if nombre_tabla:
        # La documentación de Graph es un poco ambigua aquí, pero parece que 'name' no es un parámetro directo de /add.
        # El 'name' se asigna al crear, o se puede intentar un PATCH después a la tabla creada si se necesita un nombre específico.
        # Para este caso, si se pasa 'nombre_tabla', se ignora para /add y se usaría en un PATCH posterior si fuera necesario.
        logger.warning(f"Parámetro 'nombre_tabla' ({nombre_tabla}) proporcionado, pero la API /tables/add no lo toma directamente. La tabla se creará con un nombre autogenerado o sin nombre.")
        # O, si la librería o API soporta pasar 'name' en el cuerpo de 'add', se puede incluir.
        # Revisando la documentación más reciente: POST /tables espera un JSON con 'name' y 'address', 'hasHeaders'.
        # PERO /tables/add es para añadir desde un rango. El nombre es para /tables (POST directo).
        # El endpoint correcto para crear una tabla sobre un rango y potencialmente nombrarla es:
        # POST .../worksheets/{id|name}/tables
        # BODY: { "address": "A1:D8", "hasHeaders": true, "name": "MiTablaNueva" }
        # Ajustamos la URL y el payload
        url = f"{_get_me_drive_item_workbook_url_base(item_id)}/worksheets/{hoja_nombre_o_id}/tables"
        if nombre_tabla: payload["name"] = nombre_tabla


    logger.info(f"Creando tabla Excel en item '{item_id}', hoja '{hoja_nombre_o_id}', rango '{rango_direccion}'")
    try:
        response = client.post(url, scope=settings.GRAPH_API_DEFAULT_SCOPE, json_data=payload)
        return {"status": "success", "data": response.json(), "message": "Tabla de Excel creada."}
    except Exception as e:
        return _handle_office_api_error(e, action_name, params)

def agregar_filas_tabla_excel(client: AuthenticatedHttpClient, params: Dict[str, Any]) -> Dict[str, Any]:
    action_name = "office_agregar_filas_tabla_excel"
    item_id: Optional[str] = params.get("item_id")
    hoja_nombre_o_id: Optional[str] = params.get("hoja") # Necesario si se usa nombre de tabla
    tabla_nombre_o_id: Optional[str] = params.get("tabla_nombre_o_id") # Nombre o ID de la tabla
    valores_filas: Optional[List[List[Any]]] = params.get("valores_filas") # Lista de listas para las filas

    if not all([item_id, tabla_nombre_o_id, valores_filas]):
        return {"status": "error", "action": action_name, "message": "'item_id', 'tabla_nombre_o_id', y 'valores_filas' son requeridos.", "http_status": 400}
    if not isinstance(valores_filas, list) or not all(isinstance(row, list) for row in valores_filas):
        return {"status": "error", "action": action_name, "message": "'valores_filas' debe ser una lista de listas.", "http_status": 400}
    if not hoja_nombre_o_id: # El ID de la hoja es necesario para construir la URL a la tabla por nombre/ID.
         return {"status": "error", "action": action_name, "message": "'hoja_nombre_o_id' es requerido si se usa 'tabla_nombre_o_id'.", "http_status": 400}


    # Endpoint para añadir filas: .../workbook/worksheets/{sheet-id|name}/tables/{table-id|name}/rows
    url = f"{_get_me_drive_item_workbook_url_base(item_id)}/worksheets/{hoja_nombre_o_id}/tables/{tabla_nombre_o_id}/rows"
    
    # El payload para /rows es un objeto con una clave "values" que es una lista de listas.
    # También puede tener "index": null para añadir al final.
    payload = {"values": valores_filas, "index": None} 

    logger.info(f"Agregando {len(valores_filas)} filas a tabla Excel '{tabla_nombre_o_id}' en item '{item_id}'")
    try:
        response = client.post(url, scope=settings.GRAPH_API_DEFAULT_SCOPE, json_data=payload)
        # Devuelve el workbookTableRow creado (o un objeto que indica el rango añadido).
        return {"status": "success", "data": response.json(), "message": f"{len(valores_filas)} fila(s) agregada(s) a la tabla."}
    except Exception as e:
        return _handle_office_api_error(e, action_name, params)

# Las funciones placeholder originales como 'run_excel_script' o 'update_word_document' (si eran para Office Scripts)
# requerirían una lógica muy diferente, posiblemente interactuando con endpoints de scripts o formatos de archivo más complejos.
# Por ahora, las funciones se centran en operaciones a nivel de archivo y contenido básico (Word) o datos estructurados (Excel).