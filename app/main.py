# app/main.py
import logging
from fastapi import FastAPI, Request, HTTPException, status as http_status
from fastapi.responses import JSONResponse, StreamingResponse, Response
from azure.identity import DefaultAzureCredential
import uvicorn

# Importar el router de acciones
from app.api.routes.dynamics_actions import router as dynamics_router # <--- ESTA LÍNEA DEBE ESTAR DESCOMENTADA

# Importar la configuración de la aplicación
from app.core.config import settings

# Importar el cliente HTTP autenticado y el módulo de constantes original (que adaptaremos)
from app.shared.helpers.http_client import AuthenticatedHttpClient
# from app.shared import constants as app_constants # Usaremos settings directamente

logging.basicConfig(level=settings.LOG_LEVEL)
logger = logging.getLogger(__name__)

app = FastAPI( # <--- AQUÍ SE DEFINE 'app'
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="Backend centralizado para la ejecución de acciones y automatizaciones.",
    openapi_url=f"{settings.API_PREFIX}/openapi.json",
    docs_url=f"{settings.API_PREFIX}/docs",
    redoc_url=f"{settings.API_PREFIX}/redoc"
)

@app.get("/health", tags=["General"], summary="Verifica el estado de la API.")
async def health_check():
    return {"status": "ok", "appName": settings.APP_NAME, "appVersion": settings.APP_VERSION}

# --- Integración del router principal de acciones ---
app.include_router(dynamics_router, prefix=settings.API_PREFIX) # <--- ESTA ES LA LÍNEA QUE PROBABLEMENTE CAUSA EL ERROR SI 'app' NO ESTÁ DEFINIDA ANTES O SI dynamics_router NO SE IMPORTÓ

logger.info(f"{settings.APP_NAME} versión {settings.APP_VERSION} iniciada. Nivel de log: {settings.LOG_LEVEL}.")
logger.info(f"API accesible en (local): http://127.0.0.1:8000{settings.API_PREFIX}")
logger.info(f"Documentación OpenAPI (Swagger UI) en (local): http://127.0.0.1:8000{settings.API_PREFIX}/docs")

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level=settings.LOG_LEVEL.lower())