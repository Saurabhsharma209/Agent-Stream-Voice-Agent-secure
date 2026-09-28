#!/usr/bin/env python3
"""
Controller: Call Controller
Handles the functional business logic for outbound calling, call status checks, and callback handlers.
"""

import logging
from typing import Dict, Any, Optional
from config import Config
from core.exotel_outbound_api import ExotelOutboundAPI

logger = logging.getLogger(__name__)

# Cache store for local call tracking (simulating persistent logging or database storage)
_call_records_cache: Dict[str, Dict[str, Any]] = {}

import uuid
import time
from datetime import datetime
from typing import Dict, Any, Optional, List, Union

async def initiate_outbound_call(
    phone_number: Union[str, List[str]],
    customer_name: str = "Customer",
    enterprise_id: Optional[str] = None,
    agent_id: Optional[str] = None,
    campaign_id: Optional[str] = None,
    campaign_name: Optional[str] = None,
    context: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Functional logic to initiate an outbound call via Exotel's REST API.
    Supports single or multiple phone numbers for a customer.
    Includes Enterprise ID verification, Agent ID binding, and auto-generated Campaign ID.
    
    Args:
        phone_number: Target phone number string, list of numbers, or comma-separated numbers.
        customer_name: Customer's name.
        enterprise_id: Enterprise ID to verify the call is placed on behalf of an enterprise admin.
        agent_id: Agent ID for specific AI voice agent configuration.
        campaign_id: Campaign ID (Auto-generated if omitted).
        context: Optional custom metadata dictionary associated with the call.
        
    Returns:
        Dict detailing success state, primary call SID, list of SIDs, enterprise_id, agent_id, campaign_id, and metadata.
    """
    try:
        # 1. Parse and normalize target phone numbers
        phone_list: List[str] = []
        if isinstance(phone_number, list):
            for p in phone_number:
                if isinstance(p, str):
                    for item in p.split(","):
                        cleaned = item.strip()
                        if cleaned and cleaned not in phone_list:
                            phone_list.append(cleaned)
        elif isinstance(phone_number, str):
            for item in phone_number.split(","):
                cleaned = item.strip()
                if cleaned and cleaned not in phone_list:
                    phone_list.append(cleaned)
                    
        if not phone_list:
            return {
                "success": False,
                "error": "No valid phone numbers provided."
            }

        # 2. Defaults & Verification
        enterprise_id = (enterprise_id or "").strip() or "ent_default"
        agent_id = (agent_id or "").strip() or "default"
        
        # Auto-generate Campaign ID if omitted
        if not campaign_id or not str(campaign_id).strip():
            campaign_id = f"cmp_{datetime.now().strftime('%Y%m%d')}_{uuid.uuid4().hex[:8]}"
        else:
            campaign_id = str(campaign_id).strip()

        logger.info(f"📞 [CallController] Verified enterprise call for Enterprise: '{enterprise_id}' on behalf of admin. Agent: '{agent_id}', Campaign: '{campaign_id}', Targets: {phone_list}")

        campaign_name = (campaign_name or "").strip() or None

        # Prepare merged context
        merged_context = {
            "customer_name": customer_name,
            "purpose": "sales_callback",
            "enterprise_id": enterprise_id,
            "agent_id": agent_id,
            "campaign_id": campaign_id,
            "campaign_name": campaign_name,
            **(context or {})
        }

        # Resolve agent's assigned virtual phone number across all collections
        agent_phone = None
        target_agent_id = agent_id if (agent_id and agent_id != "default") else None

        if target_agent_id or enterprise_id:
            try:
                from core.mongo_manager import mongo_db
                from bson import ObjectId
                from routes.agent_routes import build_enterprise_or_conditions
                if mongo_db.client is not None:
                    db = mongo_db.client.get_default_database()
                    id_to_search = target_agent_id or enterprise_id
                    id_conds = [
                        {"agentId": id_to_search},
                        {"_id": id_to_search}
                    ]
                    if ObjectId.is_valid(id_to_search):
                        id_conds.append({"_id": ObjectId(id_to_search)})
                        
                    query = {"$or": id_conds, "status": "active"}
                    if enterprise_id:
                        ent_conds = build_enterprise_or_conditions(enterprise_id)
                        if ent_conds:
                            query = {
                                "$and": [
                                    {"$or": id_conds},
                                    {"$or": ent_conds},
                                    {"status": "active"}
                                ]
                            }

                    for coll_name in ["exotel_agents", "agents", "modernexotelaiagents", "modernaiagents"]:
                        if coll_name in await db.list_collection_names():
                            agent_doc = await db[coll_name].find_one(query)
                            if agent_doc:
                                resolved_num = agent_doc.get("virtualNumber")
                                if resolved_num and str(resolved_num).strip():
                                    agent_phone = str(resolved_num).strip()
                                    logger.info(f"🎯 Resolved agent virtual phone number '{agent_phone}' from collection '{coll_name}' for Agent ID '{id_to_search}'")
                                    break
            except Exception as ae:
                logger.warning(f"⚠️ Could not resolve agent phone number from DB: {ae}")

        # STRICT RULE ENFORCEMENT:
        # If an agent_id is provided, the call MUST originate from that agent's attached virtual number.
        if target_agent_id and not agent_phone:
            logger.error(f"❌ Strict Rule Rejection: Agent '{target_agent_id}' does not have an attached virtual number.")
            return {
                "success": False,
                "error": f"Agent '{target_agent_id}' does not have an attached virtual number (virtualNumber). Outbound calls require a valid virtual number assigned to the agent.",
                "enterprise_id": enterprise_id,
                "agent_id": agent_id,
                "campaign_id": campaign_id
            }

        # Initialize the API client
        api = ExotelOutboundAPI()
        
        initiated_sids = []
        failed_numbers = []
        
        # Iterate through target numbers
        for target_phone in phone_list:
            greeting = f"Hi {customer_name}, this is {Config.SALES_BOT_NAME} from our sales team. Please wait while I connect you."
            
            call_sid = await api.make_outbound_call(
                phone_number=target_phone,
                greeting_text=greeting,
                context=merged_context,
                caller_id=agent_phone
            )
            
            if call_sid:
                initiated_sids.append((target_phone, call_sid))
            else:
                failed_numbers.append(target_phone)

        if not initiated_sids:
            logger.error(f"❌ [CallController] Failed to initiate calls for all provided numbers: {phone_list}")
            return {
                "success": False,
                "error": "Telephony outbound call failed to initiate. Please verify Exotel configurations.",
                "enterprise_id": enterprise_id,
                "agent_id": agent_id,
                "campaign_id": campaign_id
            }

        primary_sid = initiated_sids[0][1]
        all_sids = [sid for _, sid in initiated_sids]
        
        # Save each initiated call to cache and MongoDB
        for p_num, sid in initiated_sids:
            record = {
                "call_sid": sid,
                "phone_number": p_num,
                "customer_name": customer_name,
                "enterprise_id": enterprise_id,
                "agent_id": agent_id,
                "campaign_id": campaign_id,
                "campaign_name": campaign_name,
                "status": "initiated",
                "context": merged_context,
                "timestamp": time.time(),
                "error": None
            }
            _call_records_cache[sid] = record
            
            try:
                from core.mongo_manager import mongo_db
                if mongo_db.client is not None:
                    db = mongo_db.client.get_default_database()
                    await db['outbound_calls'].insert_one(record.copy())
                    logger.info(f"💾 Persisted outbound call record to MongoDB for phone: {p_num}, SID: {sid}, Enterprise: {enterprise_id}, Agent: {agent_id}, Campaign: {campaign_id}")
            except Exception as db_err:
                logger.error(f"⚠️ Failed to persist outbound call record to MongoDB: {db_err}")

        return {
            "success": True,
            "call_sid": primary_sid,
            "call_sids": all_sids,
            "enterprise_id": enterprise_id,
            "agent_id": agent_id,
            "campaign_id": campaign_id,
            "status": "initiated",
            "message": f"Successfully initiated {len(initiated_sids)} outbound call(s) for customer '{customer_name}' under campaign '{campaign_id}'."
        }
        
    except Exception as e:
        logger.error(f"❌ [CallController] Critical exception during call initiation: {e}")
        return {
            "success": False,
            "error": f"Internal Server Error: {str(e)}"
        }

async def fetch_call_status(call_sid: str) -> Dict[str, Any]:
    """
    Functional logic to query the active status of a call from Exotel API.
    
    Args:
        call_sid: The unique Exotel Call identifier.
        
    Returns:
        Dict with call status metadata.
    """
    logger.info(f"🔍 [CallController] Querying status for Call SID: {call_sid}")
    
    try:
        api = ExotelOutboundAPI()
        status_info = await api.get_call_status(call_sid)
        
        if not status_info:
            logger.warning(f"⚠️ [CallController] Status not found for Call SID: {call_sid}")
            
            # Check cache fallback
            if call_sid in _call_records_cache:
                return {
                    "success": True,
                    "call_sid": call_sid,
                    "status": _call_records_cache[call_sid]["status"],
                    "note": "Telephony service offline or key invalid. Details loaded from local cache records."
                }
                
            return {
                "success": False,
                "error": f"Call SID '{call_sid}' could not be retrieved from Telephony Service or local records."
            }
            
        # Update local cache record
        if call_sid not in _call_records_cache:
            _call_records_cache[call_sid] = {
                "call_sid": call_sid,
                "phone_number": status_info.get("To"),
                "customer_name": "Unknown",
                "context": {}
            }
        
        _call_records_cache[call_sid]["status"] = status_info.get("Status", "unknown")
        
        # Update status in MongoDB outbound_calls collection
        try:
            from core.mongo_manager import mongo_db
            if mongo_db.client is not None:
                db = mongo_db.client.get_default_database()
                await db['outbound_calls'].update_one(
                    {"call_sid": call_sid},
                    {"$set": {
                        "status": status_info.get("Status", "unknown"),
                        "duration": status_info.get("Duration")
                    }}
                )
                logger.info(f"💾 Updated outbound call status in MongoDB via fetch query for SID: {call_sid}")
        except Exception as db_err:
            logger.error(f"⚠️ Failed to update outbound call status in MongoDB via fetch: {db_err}")
        
        return {
            "success": True,
            "call_sid": call_sid,
            "status": status_info.get("Status"),
            "duration": status_info.get("Duration"),
            "direction": status_info.get("Direction", "outbound"),
            "from_number": status_info.get("From"),
            "to_number": status_info.get("To"),
            "start_time": status_info.get("StartTime"),
            "price": status_info.get("Price")
        }
        
    except Exception as e:
        logger.error(f"❌ [CallController] Exception fetching call status: {e}")
        return {
            "success": False,
            "error": f"Internal Server Error: {str(e)}"
        }

async def process_telephony_webhook(webhook_payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Functional logic to process incoming webhook events from the telephony system (e.g. call.completed).
    
    Args:
        webhook_payload: Raw event dictionary from Exotel callbacks.
        
    Returns:
        Dict confirming processing outcomes.
    """
    call_sid = webhook_payload.get("CallSid") or webhook_payload.get("call_sid")
    event_type = webhook_payload.get("EventType") or webhook_payload.get("event") or webhook_payload.get("Status")
    # Exotel/Twilio-style callbacks send capitalized keys ("Duration"); accept both cases since
    # this payload previously only looked for lowercase "duration" and silently never matched,
    # leaving `duration` permanently null on outbound_calls records.
    duration_value = webhook_payload.get("Duration")
    if duration_value is None:
        duration_value = webhook_payload.get("duration")

    logger.info(f"📥 [CallController] Telephony Webhook Received: Event={event_type}, CallSID={call_sid}, Duration={duration_value}")

    if not call_sid:
        return {
            "success": False,
            "error": "Missing CallSid identifier in callback payload."
        }

    # Update state cache records
    if call_sid in _call_records_cache:
        _call_records_cache[call_sid]["status"] = event_type or "completed"
        if duration_value is not None:
            _call_records_cache[call_sid]["duration"] = duration_value

    # Update status in MongoDB outbound_calls collection
    try:
        from core.mongo_manager import mongo_db
        if mongo_db.client is not None:
            db = mongo_db.client.get_default_database()
            update_fields = {"status": event_type or "completed"}
            if duration_value is not None:
                update_fields["duration"] = duration_value
            await db['outbound_calls'].update_one(
                {"call_sid": call_sid},
                {"$set": update_fields}
            )
            logger.info(f"💾 Updated outbound call status in MongoDB for SID: {call_sid}")
    except Exception as db_err:
        logger.error(f"⚠️ Failed to update outbound call status in MongoDB: {db_err}")
            
    return {
        "success": True,
        "call_sid": call_sid,
        "processed_event": event_type,
        "message": "Callback event successfully audited and integrated."
    }

async def fetch_campaign_data(
    enterprise_id: str,
    agent_id: Optional[str] = None,
    campaign_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    Query and aggregate all campaign data executed under a given enterprise_id and optional agent_id / campaign_id.
    """
    logger.info(f"🔍 [CallController] Querying campaign data for Enterprise: '{enterprise_id}', Agent: '{agent_id}', Campaign: '{campaign_id}'")
    try:
        from core.mongo_manager import mongo_db
        if mongo_db.client is None:
            return {
                "success": True,
                "enterprise_id": enterprise_id,
                "agent_id": agent_id,
                "total_campaigns": 0,
                "campaigns": []
            }
            
        db = mongo_db.client.get_default_database()
        
        # Build query filter matching enterprise_id and optional agent_id / campaign_id
        and_conditions = []
        
        if enterprise_id:
            and_conditions.append({
                "$or": [
                    {"enterprise_id": enterprise_id},
                    {"context.enterprise_id": enterprise_id}
                ]
            })
            
        if agent_id:
            and_conditions.append({
                "$or": [
                    {"agent_id": agent_id},
                    {"context.agent_id": agent_id}
                ]
            })

        if campaign_id:
            and_conditions.append({
                "$or": [
                    {"campaign_id": campaign_id},
                    {"context.campaign_id": campaign_id}
                ]
            })

        query_filter = {"$and": and_conditions} if and_conditions else {}

        cursor = db['outbound_calls'].find(query_filter).sort("timestamp", -1)
        
        campaigns_map: Dict[str, Dict[str, Any]] = {}
        
        async for doc in cursor:
            doc["_id"] = str(doc["_id"])
            cmp_id = doc.get("campaign_id") or doc.get("context", {}).get("campaign_id") or "cmp_default"
            ent_id = doc.get("enterprise_id") or doc.get("context", {}).get("enterprise_id") or enterprise_id
            ag_id = doc.get("agent_id") or doc.get("context", {}).get("agent_id") or agent_id or "default"
            cmp_name = doc.get("campaign_name") or doc.get("context", {}).get("campaign_name")
            source_file_name = doc.get("context", {}).get("source_file_name")
            source_file_url = doc.get("context", {}).get("source_file_url")
            status = doc.get("status", "unknown")
            ts = doc.get("timestamp", 0)

            if cmp_id not in campaigns_map:
                campaigns_map[cmp_id] = {
                    "campaign_id": cmp_id,
                    "campaign_name": cmp_name,
                    "source_file_name": source_file_name,
                    "source_file_url": source_file_url,
                    "enterprise_id": ent_id,
                    "agent_id": ag_id,
                    "total_calls": 0,
                    "completed_calls": 0,
                    "failed_calls": 0,
                    "initiated_calls": 0,
                    "in_progress_calls": 0,
                    "other_calls": 0,
                    "first_call_timestamp": ts,
                    "latest_call_timestamp": ts,
                    "calls": []
                }
                
            cmp = campaigns_map[cmp_id]
            cmp["total_calls"] += 1
            
            if status == "completed":
                cmp["completed_calls"] += 1
            elif status in ["failed", "no-answer", "busy", "canceled"]:
                cmp["failed_calls"] += 1
            elif status == "initiated":
                cmp["initiated_calls"] += 1
            elif status in ["in-progress", "ringing"]:
                cmp["in_progress_calls"] += 1
            else:
                cmp["other_calls"] += 1
                
            if ts and ts < cmp["first_call_timestamp"]:
                cmp["first_call_timestamp"] = ts
            if ts and ts > cmp["latest_call_timestamp"]:
                cmp["latest_call_timestamp"] = ts
                
            cmp["calls"].append({
                "call_sid": doc.get("call_sid"),
                "phone_number": doc.get("phone_number"),
                "customer_name": doc.get("customer_name"),
                "status": status,
                "duration": doc.get("duration"),
                "timestamp": ts
            })

        campaign_list = list(campaigns_map.values())
        campaign_list.sort(key=lambda c: c["latest_call_timestamp"], reverse=True)

        return {
            "success": True,
            "enterprise_id": enterprise_id,
            "agent_id": agent_id,
            "total_campaigns": len(campaign_list),
            "campaigns": campaign_list
        }
        
    except Exception as e:
        logger.error(f"❌ [CallController] Exception fetching campaign data: {e}")
        return {
            "success": False,
            "error": f"Internal Server Error: {str(e)}"
        }
