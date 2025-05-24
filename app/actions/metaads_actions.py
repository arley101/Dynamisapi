# app/actions/metaads_actions.py
import logging
from typing import Dict, List, Optional, Any

# SDK de Facebook Business
from facebook_business.api import FacebookAdsApi
from facebook_business.adobjects.adaccount import AdAccount
from facebook_business.adobjects.campaign import Campaign
from facebook_business.adobjects.adset import AdSet
from facebook_business.adobjects.ad import Ad
from facebook_business.exceptions import FacebookRequestError

from app.core.config import settings # Para acceder a las credenciales de Meta Ads

logger = logging.getLogger(__name__)

_meta_ads_api_instance: Optional[FacebookAdsApi] = None

def get_meta_ads_api_client() -> FacebookAdsApi:
    """
    Inicializa y devuelve una instancia de la API de Facebook Ads.
    Reutiliza la instancia si ya ha sido creada.
    """
    global _meta_ads_api_instance
    if _meta_ads_api_instance:
        return _meta_ads_api_instance

    required_creds = [
        settings.META_ADS.APP_ID,
        settings.META_ADS.APP_SECRET,
        settings.META_ADS.ACCESS_TOKEN
    ]
    if not all(required_creds):
        msg = (
            "Faltan credenciales de Meta Ads en la configuración. Se requieren: "
            "META_ADS_APP_ID, META_ADS_APP_SECRET, META_ADS_ACCESS_TOKEN."
        )
        logger.critical(msg)
        raise ValueError(msg) # Será capturado por el manejador de errores de la acción

    logger.info("Inicializando cliente de Meta Ads (Facebook Marketing API)...")
    try:
        FacebookAdsApi.init(
            app_id=settings.META_ADS.APP_ID,
            app_secret=settings.META_ADS.APP_SECRET,
            access_token=settings.META_ADS.ACCESS_TOKEN,
            api_version="v19.0" # Especificar la versión de la API que se va a usar
        )
        _meta_ads_api_instance = FacebookAdsApi.get_default_api()
        if not _meta_ads_api_instance: # Doble chequeo por si get_default_api devuelve None
             raise ConnectionError("FacebookAdsApi.get_default_api() devolvió None después de la inicialización.")
        logger.info("Cliente de Meta Ads inicializado exitosamente.")
        return _meta_ads_api_instance
    except Exception as e:
        logger.exception(f"Error crítico inicializando el cliente de Meta Ads: {e}")
        # Este error es a nivel de conexión/configuración, no un error de API de una llamada específica
        raise ConnectionError(f"No se pudo inicializar el cliente de Meta Ads: {e}")


def _handle_meta_ads_api_error(
    e: Exception,
    action_name: str,
    params_for_log: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """Formatea una FacebookRequestError u otra excepción en una respuesta de error estándar."""
    log_message = f"Error en Meta Ads Action '{action_name}'"
    if params_for_log:
        log_message += f" con params: {params_for_log}"
    
    logger.error(f"{log_message}: {type(e).__name__} - {str(e)}", exc_info=True)
    
    details_str = str(e)
    status_code_int = 500
    api_error_code = None
    api_error_subcode = None
    api_error_message = str(e) # Mensaje genérico

    if isinstance(e, FacebookRequestError):
        status_code_int = e.http_status() or 500
        api_error_code = e.api_error_code()
        api_error_subcode = e.api_error_subcode()
        api_error_message = e.api_error_message() or str(e)
        # details_str puede contener más información formateada de la API
        details_str = f"API Error Code: {api_error_code}, Subcode: {api_error_subcode}, Message: {api_error_message}, Raw Response: {e.get_response()}"
    elif isinstance(e, (ValueError, ConnectionError)): # Errores de configuración o conexión
        status_code_int = 503 if isinstance(e, ConnectionError) else 400
        api_error_message = str(e)


    return {
        "status": "error",
        "action": action_name,
        "message": api_error_message,
        "http_status": status_code_int,
        "details": {
            "raw_exception": str(e),
            "api_error_code": api_error_code,
            "api_error_subcode": api_error_subcode,
            "full_details_if_available": details_str if isinstance(e, FacebookRequestError) else str(e)
        }
    }

def _get_ad_account(ad_account_id_from_params: Optional[str] = None) -> AdAccount:
    """Obtiene el objeto AdAccount, priorizando el ID de los parámetros."""
    # El ID de la cuenta publicitaria debe incluir el prefijo "act_".
    effective_ad_account_id = ad_account_id_from_params or settings.META_ADS.BUSINESS_ACCOUNT_ID
    
    if not effective_ad_account_id:
        raise ValueError("Se requiere 'ad_account_id' en los parámetros de la acción o META_ADS_BUSINESS_ACCOUNT_ID en la configuración.")
    
    if not effective_ad_account_id.startswith("act_"):
        effective_ad_account_id = f"act_{effective_ad_account_id.replace('act_', '')}"
        
    return AdAccount(effective_ad_account_id)

# --- Implementación de Acciones de Meta Ads ---

def metaads_list_campaigns(client_unused: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Lista las campañas de una cuenta publicitaria de Meta.
    Params requeridos: 'ad_account_id' (opcional si está en settings.META_ADS.BUSINESS_ACCOUNT_ID).
    Params opcionales: 'fields' (lista de campos a solicitar), 'filtering' (lista de filtros).
    """
    action_name = "metaads_list_campaigns"
    ad_account_id_param: Optional[str] = params.get("ad_account_id")
    fields_param: Optional[List[str]] = params.get("fields")
    filtering_param: Optional[List[Dict[str, Any]]] = params.get("filtering") # Ej: [{'field':'effective_status','operator':'IN','value':['ACTIVE','PAUSED']}]


    default_fields = [
        Campaign.Field.id,
        Campaign.Field.name,
        Campaign.Field.status,
        Campaign.Field.effective_status,
        Campaign.Field.objective,
        Campaign.Field.buying_type,
        Campaign.Field.start_time,
        Campaign.Field.stop_time,
        Campaign.Field.daily_budget,
        Campaign.Field.lifetime_budget,
        Campaign.Field.budget_remaining
    ]
    fields_to_request = fields_param if fields_param and isinstance(fields_param, list) else default_fields

    try:
        get_meta_ads_api_client() # Asegurar que la API esté inicializada
        ad_account = _get_ad_account(ad_account_id_param)
        
        logger.info(f"Listando campañas de Meta Ads para la cuenta '{ad_account['id']}' con campos: {fields_to_request}")
        
        api_params_sdk = {'fields': fields_to_request}
        if filtering_param and isinstance(filtering_param, list):
            api_params_sdk['filtering'] = filtering_param
            logger.info(f"Aplicando filtros: {filtering_param}")

        campaigns_cursor = ad_account.get_campaigns(params=api_params_sdk)
        
        campaigns_list = []
        # El cursor maneja la paginación automáticamente. Se puede iterar o cargar todo.
        for campaign in campaigns_cursor:
            campaigns_list.append(campaign.export_all_data()) # Convierte el objeto AdObject a dict

        logger.info(f"Se encontraron {len(campaigns_list)} campañas para la cuenta '{ad_account['id']}'.")
        return {"status": "success", "data": campaigns_list, "total_retrieved": len(campaigns_list)}

    except Exception as e:
        return _handle_meta_ads_api_error(e, action_name, {"ad_account_id": ad_account_id_param})


def metaads_create_campaign(client_unused: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Crea una nueva campaña en una cuenta publicitaria de Meta.
    Params requeridos: 'campaign_payload' (dict con los datos de la campaña), 'ad_account_id' (opcional).
    El payload debe incluir al menos 'name', 'objective', 'status', y 'special_ad_categories'.
    """
    action_name = "metaads_create_campaign"
    ad_account_id_param: Optional[str] = params.get("ad_account_id")
    campaign_payload: Optional[Dict[str, Any]] = params.get("campaign_payload")

    if not campaign_payload or not isinstance(campaign_payload, dict):
        return {"status": "error", "action": action_name, "message": "'campaign_payload' (dict) es requerido.", "http_status": 400}

    required_keys = [Campaign.Field.name, Campaign.Field.objective, Campaign.Field.status, Campaign.Field.special_ad_categories]
    if not all(key in campaign_payload for key in required_keys):
        missing = [key for key in required_keys if key not in campaign_payload]
        return {"status": "error", "action": action_name, "message": f"Faltan campos requeridos en 'campaign_payload': {missing}. Mínimo: name, objective, status, special_ad_categories.", "http_status": 400}

    try:
        get_meta_ads_api_client()
        ad_account = _get_ad_account(ad_account_id_param)
        
        logger.info(f"Creando campaña de Meta Ads en la cuenta '{ad_account['id']}' con nombre: '{campaign_payload.get('name')}'")
        
        # Crear el objeto Campaign y llamar a remote_create
        new_campaign = Campaign(parent_id=ad_account['id'])
        new_campaign.update(campaign_payload) # Llenar el objeto con los datos del payload
        
        new_campaign.remote_create() # Realizar la llamada API para crear
        
        logger.info(f"Campaña '{new_campaign[Campaign.Field.name]}' creada con ID: {new_campaign[Campaign.Field.id]}")
        return {"status": "success", "data": new_campaign.export_all_data()}
        
    except Exception as e:
        return _handle_meta_ads_api_error(e, action_name, {"ad_account_id": ad_account_id_param, "payload_keys": list(campaign_payload.keys()) if campaign_payload else None})


def metaads_update_campaign(client_unused: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Actualiza una campaña existente en Meta Ads.
    Params requeridos: 'campaign_id', 'update_payload' (dict con los campos a actualizar).
    """
    action_name = "metaads_update_campaign"
    campaign_id: Optional[str] = params.get("campaign_id")
    update_payload: Optional[Dict[str, Any]] = params.get("update_payload")

    if not campaign_id:
        return {"status": "error", "action": action_name, "message": "'campaign_id' es requerido.", "http_status": 400}
    if not update_payload or not isinstance(update_payload, dict) or not update_payload: # No puede ser vacío
        return {"status": "error", "action": action_name, "message": "'update_payload' (dict no vacío con campos a actualizar) es requerido.", "http_status": 400}

    try:
        get_meta_ads_api_client()
        
        logger.info(f"Actualizando campaña de Meta Ads ID: '{campaign_id}'")
        
        campaign_to_update = Campaign(campaign_id)
        campaign_to_update.update(update_payload)
        
        campaign_to_update.remote_update() # Realizar la llamada API para actualizar
        
        # Para obtener el objeto completo después de la actualización, se podría hacer un remote_read
        campaign_to_update.remote_read(fields=[Campaign.Field.name, Campaign.Field.status, Campaign.Field.objective]) # Ejemplo
        
        logger.info(f"Campaña ID '{campaign_id}' actualizada.")
        return {"status": "success", "data": campaign_to_update.export_all_data()}

    except Exception as e:
        return _handle_meta_ads_api_error(e, action_name, {"campaign_id": campaign_id, "update_keys": list(update_payload.keys()) if update_payload else None})


def metaads_delete_campaign(client_unused: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Elimina (archiva) una campaña en Meta Ads.
    Params requeridos: 'campaign_id'.
    Nota: En Meta Ads, las campañas generalmente se archivan (status=ARCHIVED) o se eliminan cambiando su estado.
    Una eliminación real es menos común o directa para campañas que ya han tenido actividad.
    Esta función cambiará el estado a DELETED si la API lo permite directamente, o ARCHIVED.
    """
    action_name = "metaads_delete_campaign"
    campaign_id: Optional[str] = params.get("campaign_id")

    if not campaign_id:
        return {"status": "error", "action": action_name, "message": "'campaign_id' es requerido.", "http_status": 400}

    try:
        get_meta_ads_api_client()
        
        logger.info(f"Intentando eliminar/archivar campaña de Meta Ads ID: '{campaign_id}'")
        
        campaign_to_delete = Campaign(campaign_id)
        
        # Opción 1: Eliminar realmente (si la API lo soporta para este objeto y estado)
        # campaign_to_delete.remote_delete()
        
        # Opción 2: Cambiar estado a ARCHIVED o DELETED (más común)
        # Para eliminar, se suele cambiar el estado a DELETED.
        # El estado ARCHIVED también la saca de la vista activa.
        # Verifiquemos la documentación de la API para la mejor forma de "eliminar".
        # Usualmente es un update de status.
        campaign_to_delete.update({Campaign.Field.status: Campaign.Status.deleted})
        campaign_to_delete.remote_update()

        logger.info(f"Campaña ID '{campaign_id}' marcada como eliminada/archivada.")
        return {"status": "success", "message": f"Campaña '{campaign_id}' marcada como eliminada/archivada."}

    except Exception as e:
        return _handle_meta_ads_api_error(e, action_name, {"campaign_id": campaign_id})

def metaads_get_insights(client_unused: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Obtiene insights (métricas) para un nivel específico (campaña, adset, ad, cuenta).
    Params requeridos: 'object_id' (ID de la campaña, adset, ad, o cuenta), 
                      'level' ('campaign', 'adset', 'ad', 'account').
    Params opcionales: 'fields', 'date_preset', 'time_range' (dict con 'since', 'until'), 'filtering', 'breakdowns', etc.
    """
    action_name = "metaads_get_insights"
    object_id_param: Optional[str] = params.get("object_id") # ID del objeto (campaña, adset, ad) o ID de cuenta para nivel 'account'
    level_param: Optional[str] = params.get("level", "campaign").lower() # campaign, adset, ad, account
    
    # Parámetros de la API de Insights
    fields_param: Optional[List[str]] = params.get("fields")
    date_preset_param: Optional[str] = params.get("date_preset") # ej: 'last_30d', 'this_month'
    time_range_param: Optional[Dict[str, str]] = params.get("time_range") # ej: {'since': 'YYYY-MM-DD', 'until': 'YYYY-MM-DD'}
    filtering_param: Optional[List[Dict[str, Any]]] = params.get("filtering")
    breakdowns_param: Optional[List[str]] = params.get("breakdowns")
    # action_breakdowns, time_increment, limit, etc.

    if not object_id_param and level_param != "account": # Para account level, object_id es el ad_account_id
        return {"status": "error", "action": action_name, "message": "'object_id' es requerido para niveles campaign, adset, ad.", "http_status": 400}
    if level_param not in ['campaign', 'adset', 'ad', 'account']:
        return {"status": "error", "action": action_name, "message": "'level' debe ser 'campaign', 'adset', 'ad', o 'account'.", "http_status": 400}

    default_insight_fields = [
        'campaign_name', 'adset_name', 'ad_name', 'impressions', 'spend', 
        'clicks', 'ctr', 'cpc', 'reach', 'frequency', 'objective',
        # 'actions', 'action_values' # Estos son campos complejos y pueden necesitar 'action_attribution_windows'
    ]
    fields_to_request = fields_param if fields_param and isinstance(fields_param, list) else default_insight_fields

    api_params_sdk: Dict[str, Any] = {'fields': fields_to_request}
    if date_preset_param: api_params_sdk['date_preset'] = date_preset_param
    if time_range_param and isinstance(time_range_param, dict): api_params_sdk['time_range'] = time_range_param
    if filtering_param and isinstance(filtering_param, list): api_params_sdk['filtering'] = filtering_param
    if breakdowns_param and isinstance(breakdowns_param, list): api_params_sdk['breakdowns'] = breakdowns_param
    if params.get("limit"): api_params_sdk['limit'] = int(params.get("limit"))
    if params.get("action_breakdowns"): api_params_sdk['action_breakdowns'] = params.get("action_breakdowns")
    if params.get("time_increment"): api_params_sdk['time_increment'] = params.get("time_increment") # 1, 7, 30, 'monthly'

    try:
        get_meta_ads_api_client()
        target_object: Any # Campaign, AdSet, Ad, o AdAccount
        
        if level_param == 'campaign':
            target_object = Campaign(object_id_param)
        elif level_param == 'adset':
            target_object = AdSet(object_id_param)
        elif level_param == 'ad':
            target_object = Ad(object_id_param)
        elif level_param == 'account':
            # Si object_id_param se provee para account, es el ad_account_id.
            # Sino, se usa el default de la configuración.
            target_object = _get_ad_account(object_id_param) 
        else: # No debería llegar aquí por la validación anterior
            raise ValueError(f"Nivel de insights desconocido: {level_param}")

        logger.info(f"Obteniendo insights de Meta Ads para ID '{target_object['id']}' (Nivel: {level_param}) con parámetros: {api_params_sdk}")
        
        # El método get_insights es asíncrono por defecto (devuelve AdReportRun)
        # Para obtener resultados síncronos, se debe pasar is_async=False o manejar el AdReportRun.
        # Por simplicidad aquí, intentaremos una llamada síncrona si es posible,
        # o iniciaremos el job y devolveremos el ID del job.
        # La llamada síncrona se hace usando .execute() en el cursor.
        
        # Alternativa: Iniciar un job asíncrono
        # report_run = target_object.get_insights(params=api_params_sdk, async=True)
        # logger.info(f"Job de insights iniciado con ID: {report_run[AdReportRun.Field.id]}. Esperando finalización...")
        # while True:
        #     report_run.remote_read()
        #     if report_run[AdReportRun.Field.async_status] == "Job Completed":
        #         break
        #     elif report_run[AdReportRun.Field.async_status] == "Job Failed":
        #         raise Exception(f"Job de insights falló. ID: {report_run[AdReportRun.Field.id]}")
        #     time.sleep(5) # Esperar antes de volver a consultar
        # insights_cursor = report_run.get_result()
        
        # Para llamada síncrona (puede ser lenta para grandes queries):
        insights_cursor = target_object.get_insights(params=api_params_sdk, is_async=False) # Forzar síncrono

        insights_list = []
        for insight in insights_cursor:
            insights_list.append(insight.export_all_data())

        logger.info(f"Se obtuvieron {len(insights_list)} registros de insights para ID '{target_object['id']}'.")
        return {"status": "success", "data": insights_list, "total_retrieved": len(insights_list)}

    except Exception as e:
        return _handle_meta_ads_api_error(e, action_name, {"object_id": object_id_param, "level": level_param})