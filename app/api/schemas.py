# app/api/schemas.py
from pydantic import BaseModel, Field
from typing import Dict, Any, Optional, List, Union # <--- 'Union' AÑADIDO AQUÍ

class ActionRequest(BaseModel):
    """
    Modelo para el cuerpo de la solicitud de acción.
    Valida que 'action' esté presente y que 'params' sea un diccionario.
    """
    action: str = Field(..., example="calendar_list_events", description="Nombre de la acción a ejecutar.")
    params: Dict[str, Any] = Field(default_factory=dict, example={"start_datetime": "2025-05-20T08:00:00Z", "end_datetime": "2025-05-20T17:00:00Z"}, description="Parámetros para la acción.")

class ErrorDetail(BaseModel):
    """
    Modelo para detalles de error específicos, si los hay.
    """
    code: Optional[str] = None
    message: Optional[str] = None
    target: Optional[str] = None
    details: Optional[List[Any]] = None # Podría ser una lista de otros ErrorDetail

class ErrorResponse(BaseModel):
    """
    Modelo para una respuesta de error estandarizada.
    """
    status: str = "error"
    action: Optional[str] = None
    message: str = Field(..., example="Descripción del error.")
    http_status: Optional[int] = Field(default=500, example=500)
    details: Optional[Union[str, Dict[str, Any], ErrorDetail]] = None # Detalles técnicos o adicionales
    graph_error_code: Optional[str] = None # Específico para errores de Microsoft Graph