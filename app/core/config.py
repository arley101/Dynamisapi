# app/core/config.py
import os
from typing import List, Optional, Union
from pydantic import HttpUrl, field_validator, Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from dotenv import load_dotenv

load_dotenv()

class GoogleAdsCredentials(BaseSettings):
    """Credenciales específicas para Google Ads API."""
    CLIENT_ID: Optional[str] = None
    CLIENT_SECRET: Optional[str] = None
    DEVELOPER_TOKEN: Optional[str] = None
    REFRESH_TOKEN: Optional[str] = None
    LOGIN_CUSTOMER_ID: Optional[str] = None # El ID de la cuenta MCC o la cuenta directa (sin guiones)

    model_config = SettingsConfigDict(
        env_prefix='GOOGLE_ADS_',
        env_file='.env',
        env_file_encoding='utf-8',
        extra='ignore'
    )

class MetaAdsCredentials(BaseSettings):
    """Credenciales específicas para Meta Ads (Facebook Marketing API)."""
    APP_ID: Optional[str] = None
    APP_SECRET: Optional[str] = None # ¡Secreto!
    ACCESS_TOKEN: Optional[str] = None # Token de acceso de larga duración del sistema o del usuario
    BUSINESS_ACCOUNT_ID: Optional[str] = None # Ad Account ID, ej. "act_xxxxxxxxxxx"

    model_config = SettingsConfigDict(
        env_prefix='META_ADS_',
        env_file='.env',
        env_file_encoding='utf-8',
        extra='ignore'
    )

class Settings(BaseSettings):
    """Configuraciones de la aplicación cargadas desde variables de entorno."""
    APP_NAME: str = "EliteDynamicsAPI"
    APP_VERSION: str = "1.0.0"
    API_PREFIX: str = "/api/v1"
    LOG_LEVEL: str = "INFO"

    GRAPH_API_BASE_URL: HttpUrl = "https://graph.microsoft.com/v1.0"
    AZURE_MGMT_API_BASE_URL: HttpUrl = "https://management.azure.com"

    GRAPH_API_DEFAULT_SCOPE: List[str] = ["https://graph.microsoft.com/.default"]
    AZURE_MGMT_DEFAULT_SCOPE: List[str] = ["https://management.azure.com/.default"]
    POWER_BI_DEFAULT_SCOPE: List[str] = ["https://analysis.windows.net/powerbi/api/.default"]

    AZURE_OPENAI_RESOURCE_ENDPOINT: Optional[str] = None
    OPENAI_API_DEFAULT_SCOPE: Optional[List[str]] = None

    MEMORIA_LIST_NAME: str = "AsistenteMemoria"
    AZURE_OPENAI_API_VERSION: str = "2024-02-15-preview"
    DEFAULT_API_TIMEOUT: int = 90
    MAILBOX_USER_ID: str = "me"

    GITHUB_PAT: Optional[str] = None

    PBI_TENANT_ID: Optional[str] = None
    PBI_CLIENT_ID: Optional[str] = None
    PBI_CLIENT_SECRET: Optional[str] = None

    AZURE_CLIENT_ID_MGMT: Optional[str] = None
    AZURE_CLIENT_SECRET_MGMT: Optional[str] = None
    AZURE_SUBSCRIPTION_ID: Optional[str] = None
    AZURE_RESOURCE_GROUP: Optional[str] = None

    GOOGLE_ADS: GoogleAdsCredentials = GoogleAdsCredentials()
    META_ADS: MetaAdsCredentials = MetaAdsCredentials()

    @field_validator("OPENAI_API_DEFAULT_SCOPE", mode='before')
    @classmethod
    def assemble_openai_scope(cls, v, values):
        current_values = values.data if hasattr(values, 'data') else values
        if current_values.get("AZURE_OPENAI_RESOURCE_ENDPOINT") and not v:
            endpoint = str(current_values["AZURE_OPENAI_RESOURCE_ENDPOINT"])
            if endpoint.startswith("https://"):
                return [f"{endpoint.rstrip('/')}/.default"]
        return v

    @field_validator("LOG_LEVEL")
    @classmethod
    def log_level_must_be_valid(cls, value: str) -> str:
        valid_levels = ["CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG", "NOTSET"]
        if value.upper() not in valid_levels:
            raise ValueError(f"Invalid LOG_LEVEL: {value}. Must be one of {valid_levels}.")
        return value.upper()

    model_config = SettingsConfigDict(
        env_file='.env',
        env_file_encoding='utf-8',
        extra='ignore'
    )

settings = Settings()

# Para pruebas directas
if __name__ == "__main__":
    print(f"App Name: {settings.APP_NAME}")
    print(f"Log Level: {settings.LOG_LEVEL}")
    print(f"--- Google Ads Config ---")
    print(f"Client ID: {settings.GOOGLE_ADS.CLIENT_ID}")
    print(f"Developer Token: {settings.GOOGLE_ADS.DEVELOPER_TOKEN}")
    print(f"Login Customer ID: {settings.GOOGLE_ADS.LOGIN_CUSTOMER_ID}")
    print(f"Refresh Token (presente): {bool(settings.GOOGLE_ADS.REFRESH_TOKEN)}")
    print(f"--- Meta Ads Config ---")
    print(f"App ID: {settings.META_ADS.APP_ID}")
    print(f"Access Token (presente): {bool(settings.META_ADS.ACCESS_TOKEN)}")
    print(f"Business Account ID: {settings.META_ADS.BUSINESS_ACCOUNT_ID}")
    if settings.AZURE_OPENAI_RESOURCE_ENDPOINT:
        print(f"--- Azure OpenAI Config ---")
        print(f"Azure OpenAI Endpoint: {settings.AZURE_OPENAI_RESOURCE_ENDPOINT}")
        print(f"OpenAI Default Scope: {settings.OPENAI_API_DEFAULT_SCOPE}")