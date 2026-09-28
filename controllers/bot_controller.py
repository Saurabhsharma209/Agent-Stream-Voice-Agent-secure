#!/usr/bin/env python3
"""
Controller: Bot Controller
Handles functional business logic for retrieving sales bot metrics, dynamic runtime configuration, and hot-reload.
"""

import logging
from typing import Dict, Any, List
from config import Config

logger = logging.getLogger(__name__)

# Track active bot instance to retrieve SIP server telemetry dynamically
active_bot_instance = None

# Track active streaming connections globally (deprecated in SIP-only mode)
_active_connections_counter = 0

# Initialise a RAG manager for company lookups
from core.rag_manager import RAGManager
from core.mongo_manager import mongo_db

rag_manager = RAGManager()

def increment_active_connections():
    """Increment global active stream counter"""
    global _active_connections_counter
    _active_connections_counter += 1
    logger.debug(f"📈 Active connection incremented: {_active_connections_counter}")

def decrement_active_connections():
    """Decrement global active stream counter"""
    global _active_connections_counter
    if _active_connections_counter > 0:
        _active_connections_counter -= 1
    logger.debug(f"📉 Active connection decremented: {_active_connections_counter}")

def extract_phone_number_from_uri(sip_uri: str) -> str:
    """Extract phone number user part from SIP URI.
    
    Handles formats like:
      sip:+12345@ip   -> 12345
      <sip:914040377112  -> 914040377112
      sip:914040377112@tsep.exotel.com -> 914040377112
    """
    raw = sip_uri.strip()
    # Strip leading angle bracket if present (e.g. '<sip:...')
    if raw.startswith("<"):
        raw = raw[1:]
    # Strip trailing angle bracket if present
    if raw.endswith(">"):
        raw = raw[:-1]
    raw = raw.lower()
    if raw.startswith("sips:"):
        raw = raw[5:]
    elif raw.startswith("sip:"):
        raw = raw[4:]
    user_part = raw.split('@')[0]
    # Remove leading + or whitespace
    user_part = user_part.replace("+", "").strip()
    return user_part

async def get_company_id_by_phone(phone_number: str) -> str | None:
    """Lookup company in MongoDB matching phone number.
    
    Tries multiple phone number variants to handle country code differences:
    - As-is (e.g. 914040377112)
    - Without leading 91 India country code (e.g. 04040377112)
    - Without leading 0 after stripping country code (e.g. 4040377112)
    """
    if not mongo_db.client:
        logger.warning("⚠️ MongoDB client offline, cannot resolve company by phone.")
        return None

    cleaned_phone = phone_number.replace("+", "").strip()
    try:
        db = mongo_db.client.get_default_database()
        companies_collection = db['companies']

        # Build list of variants to try
        variants = [cleaned_phone]
        # If starts with 91 (India country code), try local formats
        if cleaned_phone.startswith("91") and len(cleaned_phone) > 10:
            local = cleaned_phone[2:]  # strip 91 -> e.g. 4040377112
            variants.append(local)
            variants.append("0" + local)  # add leading 0 -> e.g. 04040377112
        # Also try with leading 91 added if it's a local number
        elif len(cleaned_phone) <= 11 and not cleaned_phone.startswith("91"):
            variants.append("91" + cleaned_phone)

        company = await companies_collection.find_one({"phone_number": {"$in": variants}})
        if company:
            logger.info(f"✅ Company matched: {company.get('name')} ({company.get('company_id')})")
            return company.get("company_id")
        
        logger.warning(f"Company not found for phone: {phone_number} (tried variants: {variants})")
        return None
    except Exception as e:
        logger.error(f"Failed to lookup company by phone: {e}")
        return None


async def query_knowledge_base(phone_number: str, query: str, top_k: int = 3, agent_config: dict = None) -> List[Dict[str, Any]]:
    """Resolve agentId / company / enterprise from agent config or phone, and perform RAG search with optional knowledgeBaseIds filtering."""
    document_ids = None
    candidates = []
    
    if agent_config:
        if agent_config.get("agentId"):
            candidates.append(agent_config.get("agentId"))
        if agent_config.get("enterprise"):
            candidates.append(agent_config.get("enterprise"))
        document_ids = agent_config.get("knowledgeBaseIds")
        
    phone_company = await get_company_id_by_phone(phone_number)
    if phone_company and phone_company not in candidates:
        candidates.append(phone_company)
        
    if not candidates:
        logger.warning(f"Company/Enterprise/Agent ID not found for phone: {phone_number}")
        return []
        
    for company_id in candidates:
        try:
            results = await rag_manager.search(company_id, query, top_k, document_ids=document_ids)
            if results:
                return [{"chunk": r["chunk_text"], "source": r["metadata"].get("source")} for r in results]
        except Exception as e:
            logger.error(f"Error querying knowledge base for {company_id}: {e}")
            
    return []

async def get_active_bot_telemetry() -> Dict[str, Any]:
    """
    Functional logic to retrieve current sales bot settings, environment state, and active call metrics.
    
    Returns:
        Dict listing active configs and system statistics.
    """
    logger.info("📊 [BotController] Harvesting bot runtime telemetry statistics")
    
    active_calls = 0
    if active_bot_instance and active_bot_instance.sip_server and active_bot_instance.sip_server.pjsua_initialized:
        active_calls = len(active_bot_instance.sip_server.sip_calls)
    
    return {
        "success": True,
        "bot_name": Config.SALES_BOT_NAME,
        "company_name": Config.COMPANY_NAME,
        "active_stream_calls": active_calls,
        "openai_settings": {
            "model": Config.OPENAI_MODEL,
            "voice": Config.OPENAI_VOICE,
            "temperature": Config.OPENAI_TEMPERATURE
        },
        "modular_settings": {
            "voice_bot_mode": Config.VOICE_BOT_MODE,
            "deepgram_model": Config.DEEPGRAM_MODEL,
            "deepgram_endpointing": getattr(Config, "DEEPGRAM_ENDPOINTING", 180),
            "speculative_rag": getattr(Config, "SPECULATIVE_RAG", True),
            "gemini_model": Config.GEMINI_MODEL,
            "sarvam_model": Config.SARVAM_MODEL,
            "sarvam_speaker": Config.SARVAM_SPEAKER,
            "sarvam_language_code": Config.SARVAM_LANGUAGE_CODE,
            "sarvam_pace": Config.SARVAM_PACE,
            "sarvam_pitch": Config.SARVAM_PITCH,
            "audio_gain": Config.AUDIO_GAIN
        },
        "audio_settings": {
            "sample_rate": Config.SAMPLE_RATE,
            "chunk_size_ms": Config.AUDIO_CHUNK_SIZE,
            "buffer_size_ms": Config.BUFFER_SIZE_MS
        },
        "telephony_mode": {
            "use_sip_trunk": Config.USE_SIP_TRUNK,
            "sip_server_host": Config.SIP_SERVER_HOST,
            "sip_server_port": Config.SIP_SERVER_PORT,
            "sip_public_endpoint": Config.SIP_PUBLIC_IP or "localhost"
        },
        "system_auth": {
            "require_auth": Config.REQUIRE_AUTH,
            "rate_limiting": Config.RATE_LIMITING_ENABLED
        }
    }

async def update_bot_runtime_config(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Functional logic to dynamically update bot configurations at runtime without restarting the server container.
    
    Args:
        payload: Parameters to update (e.g. sales_bot_name, company_name, openai_voice, openai_temperature, gemini_model).
        
    Returns:
        Dict confirming successful updates and updated configuration state.
    """
    logger.info(f"⚙️ [BotController] Dynamically hot-reloading configurations: {payload}")
    
    updated_fields = []
    
    # Safely apply runtime modifications to Config class variables
    if "sales_bot_name" in payload:
        Config.SALES_BOT_NAME = str(payload["sales_bot_name"])
        Config.SALES_REP_NAME = str(payload["sales_bot_name"])  # Keep alias in sync
        updated_fields.append("sales_bot_name")
        
    if "company_name" in payload:
        Config.COMPANY_NAME = str(payload["company_name"])
        updated_fields.append("company_name")
        
    if "openai_model" in payload:
        Config.OPENAI_MODEL = str(payload["openai_model"])
        updated_fields.append("openai_model")
        
    if "openai_voice" in payload:
        Config.OPENAI_VOICE = str(payload["openai_voice"])
        updated_fields.append("openai_voice")
        
    if "openai_temperature" in payload:
        try:
            Config.OPENAI_TEMPERATURE = float(payload["openai_temperature"])
            updated_fields.append("openai_temperature")
        except ValueError:
            logger.warning(f"⚠️ [BotController] Invalid temperature ignored: {payload['openai_temperature']}")

    if "voice_bot_mode" in payload:
        Config.VOICE_BOT_MODE = str(payload["voice_bot_mode"]).lower()
        updated_fields.append("voice_bot_mode")

    if "deepgram_model" in payload:
        Config.DEEPGRAM_MODEL = str(payload["deepgram_model"])
        updated_fields.append("deepgram_model")
        
    if "deepgram_endpointing" in payload:
        Config.DEEPGRAM_ENDPOINTING = int(payload["deepgram_endpointing"])
        updated_fields.append("deepgram_endpointing")
        
    if "speculative_rag" in payload:
        Config.SPECULATIVE_RAG = bool(payload["speculative_rag"])
        updated_fields.append("speculative_rag")
        
    if "gemini_model" in payload:
        Config.GEMINI_MODEL = str(payload["gemini_model"])
        updated_fields.append("gemini_model")

    if "sarvam_model" in payload:
        Config.SARVAM_MODEL = str(payload["sarvam_model"])
        updated_fields.append("sarvam_model")

    if "sarvam_speaker" in payload:
        Config.SARVAM_SPEAKER = str(payload["sarvam_speaker"])
        updated_fields.append("sarvam_speaker")

    if "sarvam_language_code" in payload:
        Config.SARVAM_LANGUAGE_CODE = str(payload["sarvam_language_code"])
        updated_fields.append("sarvam_language_code")

    if "sarvam_pace" in payload:
        Config.SARVAM_PACE = float(payload["sarvam_pace"])
        updated_fields.append("sarvam_pace")

    if "sarvam_pitch" in payload:
        Config.SARVAM_PITCH = float(payload["sarvam_pitch"])
        updated_fields.append("sarvam_pitch")

    if "audio_gain" in payload:
        Config.AUDIO_GAIN = float(payload["audio_gain"])
        updated_fields.append("audio_gain")
            
    logger.info(f"✅ [BotController] Dynamic reload complete. Fields modified: {updated_fields}")
    
    return {
        "success": True,
        "updated_fields": updated_fields,
        "current_config": {
            "sales_bot_name": Config.SALES_BOT_NAME,
            "company_name": Config.COMPANY_NAME,
            "openai_model": Config.OPENAI_MODEL,
            "openai_voice": Config.OPENAI_VOICE,
            "openai_temperature": Config.OPENAI_TEMPERATURE,
            "voice_bot_mode": Config.VOICE_BOT_MODE,
            "deepgram_model": Config.DEEPGRAM_MODEL,
            "gemini_model": Config.GEMINI_MODEL,
            "sarvam_model": Config.SARVAM_MODEL,
            "sarvam_speaker": Config.SARVAM_SPEAKER,
            "sarvam_language_code": Config.SARVAM_LANGUAGE_CODE,
            "sarvam_pace": Config.SARVAM_PACE,
            "sarvam_pitch": Config.SARVAM_PITCH,
            "audio_gain": Config.AUDIO_GAIN
        }
    }
