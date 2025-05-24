# app/actions/googleads_actions.py
import logging
from typing import Dict, List, Optional, Any
from google.ads.googleads.client import GoogleAdsClient
from google.ads.googleads.errors import GoogleAdsException
from google.protobuf import json_format, field_mask_pb2

from app.core.config import settings # Para acceder a las configuraciones

logger = logging.getLogger(__name__)

_google_ads_client_instance: Optional[GoogleAdsClient] = None

def get_google_ads_client() -> GoogleAdsClient:
    """
    Inicializa y devuelve una instancia del cliente de Google Ads utilizando
    la configuración de variables de entorno.
    Reutiliza la instancia si ya ha sido creada.
    """
    global _google_ads_client_instance
    if _google_ads_client_instance:
        return _google_ads_client_instance

    required_env_vars = [
        settings.GOOGLE_ADS.DEVELOPER_TOKEN,
        settings.GOOGLE_ADS.CLIENT_ID,
        settings.GOOGLE_ADS.CLIENT_SECRET,
        settings.GOOGLE_ADS.REFRESH_TOKEN,
        settings.GOOGLE_ADS.LOGIN_CUSTOMER_ID, # Esencial para la mayoría de las operaciones
    ]
    if not all(required_env_vars):
        missing = []
        if not settings.GOOGLE_ADS.DEVELOPER_TOKEN: missing.append("GOOGLE_ADS_DEVELOPER_TOKEN")
        if not settings.GOOGLE_ADS.CLIENT_ID: missing.append("GOOGLE_ADS_CLIENT_ID")
        if not settings.GOOGLE_ADS.CLIENT_SECRET: missing.append("GOOGLE_ADS_CLIENT_SECRET")
        if not settings.GOOGLE_ADS.REFRESH_TOKEN: missing.append("GOOGLE_ADS_REFRESH_TOKEN")
        if not settings.GOOGLE_ADS.LOGIN_CUSTOMER_ID: missing.append("GOOGLE_ADS_LOGIN_CUSTOMER_ID")
        
        msg = f"Faltan credenciales/configuraciones de Google Ads: {', '.join(missing)}."
        logger.critical(msg)
        raise ValueError(msg)

    credentials_config = {
        "developer_token": settings.GOOGLE_ADS.DEVELOPER_TOKEN,
        "client_id": settings.GOOGLE_ADS.CLIENT_ID,
        "client_secret": settings.GOOGLE_ADS.CLIENT_SECRET,
        "refresh_token": settings.GOOGLE_ADS.REFRESH_TOKEN,
        "login_customer_id": settings.GOOGLE_ADS.LOGIN_CUSTOMER_ID.replace("-", ""),
        "use_proto_plus": True,
    }
    # La librería google-ads ya no usa 'linked_customer_id_is_manager' en la configuración del cliente.
    # El login_customer_id es el ID del MCC o de la cuenta directa que autoriza.
    # Las operaciones sobre cuentas cliente bajo un MCC se dirigen usando el 'customer_id' en cada solicitud de servicio.

    logger.info(f"Inicializando cliente de Google Ads con login_customer_id: {credentials_config['login_customer_id']}")
    try:
        _google_ads_client_instance = GoogleAdsClient.load_from_dict(credentials_config)
        logger.info("Cliente de Google Ads inicializado exitosamente.")
        return _google_ads_client_instance
    except Exception as e:
        logger.exception(f"Error crítico inicializando el cliente de Google Ads: {e}")
        raise ConnectionError(f"No se pudo inicializar el cliente de Google Ads: {e}")

def _format_google_ads_row_to_dict(google_ads_row) -> Dict[str, Any]:
    """Convierte un objeto GoogleAdsRow (protobuf) a un diccionario Python serializable."""
    try:
        return json_format.MessageToDict(
            google_ads_row._pb,
            preserving_proto_field_name=True,
            including_default_value_fields=False # Para no incluir campos con valores por defecto si no están seteados
        )
    except Exception as e:
        logger.warning(f"Fallo al convertir GoogleAdsRow a dict usando json_format: {e}. Intentando serialización manual limitada.")
        row_dict = {}
        try:
            for field_descriptor in google_ads_row._pb.DESCRIPTOR.fields:
                field_name = field_descriptor.name
                value = getattr(google_ads_row, field_name)
                if hasattr(value, "_pb"): # Mensaje anidado
                    row_dict[field_name] = json_format.MessageToDict(value._pb, preserving_proto_field_name=True, including_default_value_fields=False)
                elif isinstance(value, (list, tuple)) and value and hasattr(value[0], "_pb"):
                    row_dict[field_name] = [json_format.MessageToDict(item._pb, preserving_proto_field_name=True, including_default_value_fields=False) for item in value]
                else:
                    row_dict[field_name] = value
        except Exception as inner_e:
            logger.error(f"Error durante serialización manual de GoogleAdsRow: {inner_e}")
            return {"_raw_repr_": str(google_ads_row), "_serialization_error_": str(inner_e)}
        return row_dict


def _handle_google_ads_api_exception(
    ex: GoogleAdsException,
    action_name: str,
    customer_id_log: Optional[str] = None
) -> Dict[str, Any]:
    """Formatea una GoogleAdsException en una respuesta de error estándar."""
    logger.error(
        f"Google Ads API Exception en acción '{action_name}' para customer_id '{customer_id_log or 'N/A'}'. Request ID: {ex.request_id}. Failure: {ex.failure}",
        exc_info=True
    )
    error_list = []
    if ex.failure and hasattr(ex.failure, 'errors') and ex.failure.errors:
        for error_item in ex.failure.errors:
            err_detail = {"message": error_item.message}
            if hasattr(error_item, 'error_code') and hasattr(error_item.error_code, 'name'):
                err_detail["errorCode"] = error_item.error_code.name
            else:
                err_detail["errorCode"] = str(error_item.error_code)
            if hasattr(error_item, 'trigger') and error_item.trigger and hasattr(error_item.trigger, 'string_value'):
                err_detail["triggerValue"] = error_item.trigger.string_value
            if hasattr(error_item, 'location') and error_item.location and \
               hasattr(error_item.location, 'field_path_elements') and error_item.location.field_path_elements:
                field_path_details = []
                for path_element in error_item.location.field_path_elements:
                    element_info = {"fieldName": path_element.field_name}
                    if path_element.index is not None:
                        element_info["index"] = path_element.index
                    field_path_details.append(element_info)
                if field_path_details: # Solo añadir si hay elementos
                    err_detail["location"] = {"fieldPathElements": field_path_details}
            error_list.append(err_detail)
            
    primary_message = "Error en la API de Google Ads."
    if error_list and error_list[0].get("message"):
        primary_message = error_list[0]["message"]

    return {
        "status": "error",
        "action": action_name,
        "message": primary_message,
        "details": {
            "googleAdsFailure": {
                "errors": error_list,
                "requestId": ex.request_id
            }
        },
        "http_status": 400
    }

def _build_resource_from_dict(client: GoogleAdsClient, resource_type_name: str, data_dict: Dict[str, Any]):
    """
    Construye un objeto recurso de Google Ads (como Campaign, AdGroup) desde un diccionario.
    Esto es un helper simplificado y puede necesitar expansión para manejar todos los casos.
    """
    resource_obj = client.get_type(resource_type_name)
    
    # Usar MessageTo duże literyString (para convertir el diccionario a JSON)
    # y luego Parse (para convertir el JSON a un mensaje protobuf) puede ser una opción.
    # O asignar campos directamente, lo cual es más explícito.
    # client.copy_from(resource_obj, data_dict) # Esta utilidad puede funcionar si los nombres coinciden
    
    for key, value in data_dict.items():
        if hasattr(resource_obj, key):
            # Manejo de Enums (requiere que el valor del dict sea el string del Enum)
            field_descriptor = resource_obj._pb.DESCRIPTOR.fields_by_name.get(key)
            if field_descriptor and field_descriptor.enum_type is not None:
                try:
                    enum_type = client.enums[field_descriptor.enum_type.name]
                    setattr(resource_obj, key, enum_type[value.upper()])
                    continue
                except KeyError:
                    raise ValueError(f"Valor de enum inválido '{value}' para el campo '{key}' (Tipo Enum: {enum_type.DESCRIPTOR.name})")
                except Exception as e_enum:
                     raise ValueError(f"Error procesando enum para el campo '{key}' con valor '{value}': {e_enum}")


            # Manejo de mensajes anidados (simplificado, asume que 'value' es un dict compatible)
            if isinstance(value, dict):
                nested_obj_field = getattr(resource_obj, key)
                if hasattr(nested_obj_field, "CopyFrom"): # Es un mensaje protobuf
                    # Aquí se podría llamar recursivamente o usar un helper de proto-plus si existe
                    # client.copy_from(nested_obj_field, value)
                    # Para ser más explícito, podrías mapear campos del dict 'value' al 'nested_obj_field'
                    # Ejemplo:
                    # if key == "network_settings":
                    #    client.copy_from(resource_obj.network_settings, value) # Asume que 'value' es un dict para NetworkSettings
                    # Esto requeriría conocimiento de cada campo anidado.
                    # Por ahora, un copy_from si es un mensaje.
                    try:
                        client.copy_from(nested_obj_field, value)
                    except Exception as e_copy_nested:
                        logger.warning(f"No se pudo copiar directamente el dict al campo anidado '{key}': {e_copy_nested}. Se requiere mapeo manual o el dict no es compatible.")
                        # Podrías intentar asignar campos individuales del 'value' al 'nested_obj_field'
                else:
                    setattr(resource_obj, key, value) # Si no es un mensaje, asignar directamente
            else:
                setattr(resource_obj, key, value)
        else:
            logger.warning(f"Campo '{key}' del diccionario de entrada no encontrado en el recurso tipo '{resource_type_name}'. Se ignora.")
            
    return resource_obj

# --- ACCIONES ---

def googleads_search_stream(params: Dict[str, Any], gads_client_override: Optional[GoogleAdsClient] = None) -> Dict[str, Any]:
    action_name = "googleads_search_stream"
    customer_id: Optional[str] = params.get("customer_id")
    gaql_query: Optional[str] = params.get("query")

    if not customer_id:
        return {"status": "error", "action": action_name, "message": "'customer_id' es requerido.", "http_status": 400}
    if not gaql_query:
        return {"status": "error", "action": action_name, "message": "'query' (GAQL) es requerida.", "http_status": 400}
    
    customer_id_clean = str(customer_id).replace("-", "")

    try:
        gads_client = gads_client_override or get_google_ads_client()
        ga_service = gads_client.get_service("GoogleAdsService")
        logger.info(f"Ejecutando GAQL query en Customer ID '{customer_id_clean}': {gaql_query[:250]}...")
        search_request = gads_client.get_type("SearchGoogleAdsStreamRequest")
        search_request.customer_id = customer_id_clean
        search_request.query = gaql_query
        stream = ga_service.search_stream(request=search_request)
        results = [_format_google_ads_row_to_dict(row) for batch in stream for row in batch.results]
        logger.info(f"GAQL query para '{customer_id_clean}' completada. {len(results)} filas obtenidas.")
        return {"status": "success", "data": {"results": results}, "total_results": len(results)}
    except GoogleAdsException as ex:
        return _handle_google_ads_api_exception(ex, action_name, customer_id_clean)
    except (ValueError, ConnectionError) as conf_err:
        logger.error(f"Error de configuración/conexión en {action_name}: {conf_err}", exc_info=True)
        return {"status": "error", "action": action_name, "message": str(conf_err), "http_status": 503 if isinstance(conf_err, ConnectionError) else 400}
    except Exception as e:
        logger.exception(f"Error inesperado en {action_name} para customer_id '{customer_id_clean}': {e}")
        return {"status": "error", "action": action_name, "message": f"Error inesperado: {str(e)}", "http_status": 500}

def googleads_mutate_campaigns(params: Dict[str, Any], gads_client_override: Optional[GoogleAdsClient] = None) -> Dict[str, Any]:
    action_name = "googleads_mutate_campaigns"
    customer_id: Optional[str] = params.get("customer_id")
    operations_payload: Optional[List[Dict[str, Any]]] = params.get("operations")
    partial_failure: bool = params.get("partial_failure", False)
    validate_only: bool = params.get("validate_only", False)
    response_content_type_str: Optional[str] = params.get("response_content_type")


    if not customer_id: return {"status": "error", "action": action_name, "message": "'customer_id' es requerido.", "http_status": 400}
    if not operations_payload or not isinstance(operations_payload, list): return {"status": "error", "action": action_name, "message": "'operations' (lista) es requerida.", "http_status": 400}

    customer_id_clean = str(customer_id).replace("-", "")

    try:
        gads_client = gads_client_override or get_google_ads_client()
        campaign_service = gads_client.get_service("CampaignService")
        
        campaign_operations = []
        for op_dict in operations_payload:
            operation = gads_client.get_type("CampaignOperation")
            if "create" in op_dict:
                campaign_data = op_dict["create"]
                # operation.create.CopyFrom(_build_resource_from_dict(gads_client, "Campaign", campaign_data))
                # Simplificado: Usar copy_from con el diccionario directamente, asumiendo que los campos coinciden y son simples.
                # La librería proto-plus intenta hacer un buen trabajo con esto.
                # Se necesitaría lógica más compleja para enums y mensajes anidados si copy_from falla.
                created_campaign = operation.create
                gads_client.copy_from(created_campaign, campaign_data)

                # Ejemplo de manejo de Enum para status y advertising_channel_type si copy_from no lo maneja bien
                if "status" in campaign_data and isinstance(campaign_data["status"], str):
                    created_campaign.status = gads_client.enums.CampaignStatusEnum[campaign_data["status"].upper()]
                if "advertising_channel_type" in campaign_data and isinstance(campaign_data["advertising_channel_type"], str):
                    created_campaign.advertising_channel_type = gads_client.enums.AdvertisingChannelTypeEnum[campaign_data["advertising_channel_type"].upper()]
                # Los objetos anidados como network_settings necesitan su propio dict o ser construidos
                if "network_settings" in campaign_data and isinstance(campaign_data["network_settings"], dict):
                     gads_client.copy_from(created_campaign.network_settings, campaign_data["network_settings"])


            elif "update" in op_dict:
                campaign_data = op_dict["update"]
                updated_campaign = operation.update
                gads_client.copy_from(updated_campaign, campaign_data) # resource_name debe estar en campaign_data
                
                if "status" in campaign_data and isinstance(campaign_data["status"], str):
                    updated_campaign.status = gads_client.enums.CampaignStatusEnum[campaign_data["status"].upper()]
                # ... otros campos ...

                if "update_mask" in op_dict and isinstance(op_dict["update_mask"], str):
                    # update_mask es una lista de strings con los nombres de los campos.
                    paths = [path.strip() for path in op_dict["update_mask"].split(',')]
                    operation.update_mask.CopyFrom(field_mask_pb2.FieldMask(paths=paths))
                # Si no hay update_mask, la librería podría intentar inferirlo, o se actualizan todos los campos enviados.

            elif "remove" in op_dict and isinstance(op_dict["remove"], str):
                operation.remove = op_dict["remove"] # resource_name de la campaña a eliminar
            else:
                logger.warning(f"Operación de campaña no soportada o malformada: {op_dict}")
                continue
            campaign_operations.append(operation)

        if not campaign_operations: return {"status": "error", "action": action_name, "message": "No se proveyeron operaciones válidas.", "http_status": 400}

        logger.info(f"Ejecutando mutate Campaigns en Customer ID '{customer_id_clean}' con {len(campaign_operations)} operaciones.")
        
        mutate_request = gads_client.get_type("MutateCampaignsRequest")
        mutate_request.customer_id = customer_id_clean
        mutate_request.operations.extend(campaign_operations)
        mutate_request.partial_failure = partial_failure
        mutate_request.validate_only = validate_only
        if response_content_type_str and isinstance(response_content_type_str, str):
            try:
                mutate_request.response_content_type = gads_client.enums.ResponseContentTypeEnum[response_content_type_str.upper()]
            except KeyError:
                 logger.warning(f"ResponseContentType '{response_content_type_str}' inválido. Usando default.")


        response = campaign_service.mutate_campaigns(request=mutate_request)
        
        formatted_response = {"mutate_operation_responses": []}
        if response.partial_failure_error:
            google_ads_failure = gads_client.get_type("GoogleAdsFailure")
            for detail_any in response.partial_failure_error.details:
                if detail_any.Is(google_ads_failure.DESCRIPTOR):
                    detail_any.Unpack(google_ads_failure) # Desempaqueta en la variable google_ads_failure
                    break 
            # Construir una GoogleAdsException manualmente para usar el helper de formateo
            # Necesita el gRPC call original o None
            partial_failure_ex = GoogleAdsException(
                ex=None, # No hay excepción gRPC original aquí
                failure=google_ads_failure, 
                call=None, 
                trigger=None, 
                request_id= "N/A_PARTIAL_FAILURE" # El request_id del partial_failure_error no es el mismo que el de la ex principal.
            )
            formatted_response["partial_failure_error"] = _handle_google_ads_api_exception(
                partial_failure_ex, f"{action_name}_partial_failure", customer_id_clean
            ).get("details", {}).get("googleAdsFailure")


        for result in response.results:
            res_dict = {"resource_name": result.resource_name}
            if result.campaign.ByteSize() > 0 and (not response_content_type_str or response_content_type_str.upper() == "MUTABLE_RESOURCE"):
                 res_dict["campaign"] = _format_google_ads_row_to_dict(result.campaign)
            formatted_response["mutate_operation_responses"].append(res_dict)
            
        logger.info(f"Mutate Campaigns completado para '{customer_id_clean}'.")
        return {"status": "success", "data": formatted_response}

    except GoogleAdsException as ex:
        return _handle_google_ads_api_exception(ex, action_name, customer_id_clean)
    except (ValueError, ConnectionError) as conf_err:
        logger.error(f"Error de configuración/conexión en {action_name}: {conf_err}", exc_info=True)
        return {"status": "error", "action": action_name, "message": str(conf_err), "http_status": 503 if isinstance(conf_err, ConnectionError) else 400}
    except Exception as e:
        logger.exception(f"Error inesperado en {action_name} para customer_id '{customer_id_clean}': {e}")
        return {"status": "error", "action": action_name, "message": f"Error inesperado: {str(e)}", "http_status": 500}

# --- Placeholders para otras funciones de mutación (a implementar con lógica similar) ---

def googleads_mutate_adgroups(params: Dict[str, Any], gads_client_override: Optional[GoogleAdsClient] = None) -> Dict[str, Any]:
    action_name = "googleads_mutate_adgroups"
    logger.warning(f"Acción '{action_name}' llamada pero no completamente implementada con lógica robusta de mapeo de payload.")
    # Implementación similar a mutate_campaigns, usando AdGroupService y AdGroupOperation
    # Se necesita un mapeo detallado del payload de params["operations"] a objetos AdGroup.
    return {"status": "not_implemented", "message": f"La lógica detallada de {action_name} aún no está completamente implementada.", "http_status": 501}

def googleads_mutate_ads(params: Dict[str, Any], gads_client_override: Optional[GoogleAdsClient] = None) -> Dict[str, Any]:
    action_name = "googleads_mutate_ads" # Asumiendo que se mapeará como "googleads_mutate_adgroup_ads"
    logger.warning(f"Acción '{action_name}' (para AdGroupAds) llamada pero no completamente implementada.")
    # Implementación similar, usando AdGroupAdService y AdGroupAdOperation.
    return {"status": "not_implemented", "message": f"La lógica detallada de {action_name} aún no está completamente implementada.", "http_status": 501}

def googleads_mutate_keywords(params: Dict[str, Any], gads_client_override: Optional[GoogleAdsClient] = None) -> Dict[str, Any]:
    action_name = "googleads_mutate_keywords" # Asumiendo que se mapeará como "googleads_mutate_adgroup_criteria" para keywords
    logger.warning(f"Acción '{action_name}' (para AdGroupCriteria - Keywords) llamada pero no completamente implementada.")
    # Implementación similar, usando AdGroupCriterionService y AdGroupCriterionOperation (con KeywordInfo).
    return {"status": "not_implemented", "message": f"La lógica detallada de {action_name} aún no está completamente implementada.", "http_status": 501}

# Se podrían añadir más funciones para Assets, AssetGroups, etc. siguiendo el mismo patrón.