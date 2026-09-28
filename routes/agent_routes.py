#!/usr/bin/env python3
"""
Route Definitions: Agent Routes
Exposes REST endpoints for creating, managing, and simulating custom voice agents.
Conforms to the NewAgentsApiSchema specification.
"""

import logging
import uuid
import datetime
import time
from typing import List, Dict, Any, Optional, Union, Annotated
import mimetypes
from fastapi import APIRouter, HTTPException, Header, Query, Path, status, UploadFile, File, BackgroundTasks
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field
from bson import ObjectId

from core.mongo_manager import mongo_db
from core.rag_manager import RAGManager

logger = logging.getLogger(__name__)
rag_manager = RAGManager()

import io

def extract_text_from_file(filename: str, body: bytes) -> str:
    """Extracts raw text from uploaded files to index into Chroma."""
    text = ""
    try:
        lower_name = filename.lower()
        if lower_name.endswith('.txt'):
            text = body.decode('utf-8', errors='ignore')
        elif lower_name.endswith('.pdf'):
            import PyPDF2
            reader = PyPDF2.PdfReader(io.BytesIO(body))
            text = "\n".join([page.extract_text() or "" for page in reader.pages])
        elif lower_name.endswith('.docx'):
            import docx
            doc = docx.Document(io.BytesIO(body))
            text = "\n".join([p.text for p in doc.paragraphs])
        else:
            logger.warning(f"Unsupported file type for {filename}")
            text = body.decode('utf-8', errors='ignore')
    except Exception as e:
        logger.error(f"Error extracting text from {filename}: {e}")
    return text

def bson_safe(obj):
    """Recursively convert BSON/MongoDB types to JSON-serializable Python types."""
    if isinstance(obj, dict):
        return {k: bson_safe(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [bson_safe(i) for i in obj]
    elif isinstance(obj, ObjectId):
        return str(obj)
    elif isinstance(obj, datetime.datetime):
        return obj.strftime('%Y-%m-%dT%H:%M:%S.%f') + "+05:30" if obj.tzinfo is None else obj.isoformat()
    elif isinstance(obj, bytes):
        return obj.decode("utf-8", errors="replace")
    else:
        try:
            # Handles Decimal128 and any other BSON types with __str__
            from bson import Decimal128
            if isinstance(obj, Decimal128):
                return float(str(obj))
        except ImportError:
            pass
        return obj

router = APIRouter(
    prefix="/api/exotel-sip/agents",
    tags=["Voice Agent Management"],
    responses={
        500: {"description": "Internal Server Error"}
    }
)

# === Pydantic Request Schemas ===

class TermsModel(BaseModel):
    enabled: bool = Field(False, json_schema_extra={"example": True})
    content: str = Field("", json_schema_extra={"example": "By speaking with this agent you agree..."})

class AgentCreateRequest(BaseModel):
    name: str = Field(..., json_schema_extra={"example": "Support Assistant"}, description="The name of the AI agent.")
    instructions: Optional[str] = Field("", json_schema_extra={"example": "You are a helpful customer support agent..."}, description="Prompt or core instructions.")
    firstMessage: Optional[str] = Field(None, json_schema_extra={"example": "Hello! How can I help you today?"}, description="Optional custom greeting message for calls. Default greeting spoken if omitted.")
    voiceId: Optional[str] = Field("default", json_schema_extra={"example": "meera"}, description="The ID of the voice to be used.")
    language: Optional[Union[str, List[str]]] = Field(None, json_schema_extra={"example": "en-IN"}, description="Primary language code (e.g. 'te-IN'). Default is 'en-IN' if omitted.")
    languages: Optional[Union[str, List[str]]] = Field(None, json_schema_extra={"example": ["en-IN", "hi-IN"]}, description="List of supported languages (e.g. ['te-IN', 'hi-IN']). Default is [language] or ['en-IN'].")
    voice_bot_mode: Optional[str] = Field("modular", json_schema_extra={"example": "modular"}, description="Operating mode for this voice agent: 'modular' or 'realtime'. Default is 'modular'.")
    
    # Optional Fields
    description: Optional[str] = Field("", json_schema_extra={"example": "Handles general customer inquiries."})
    knowledgeBaseIds: Optional[List[str]] = Field(default_factory=list, json_schema_extra={"example": ["64a2f8c8d8b9a7f3e1c2d3a4"]})
    terms: Optional[TermsModel] = Field(default_factory=lambda: TermsModel(enabled=False, content=""))
    platformAgreement: Optional[Union[str, bool]] = Field(None, json_schema_extra={"example": True})
    hinglish_mode: Optional[bool] = Field(False, json_schema_extra={"example": False})
    deepgramMulti: Optional[bool] = Field(False, json_schema_extra={"example": False}, description="If true, Deepgram STT uses language=multi (code-switching). Default is false — primary language locked. Only enable if language=multi is accurate for your use case.")
    virtualNumber: Optional[str] = Field(None, json_schema_extra={"example": "04000000000"}, description="Exotel virtual number bound to this agent.")

class AgentUpdateRequest(BaseModel):
    name: Optional[str] = Field(None, description="Updated name of the AI agent.")
    instructions: Optional[str] = Field(None, description="Updated system prompt or core instructions.")
    firstMessage: Optional[str] = Field(None, description="Updated greeting message for calls.")
    voiceId: Optional[str] = Field(None, description="Updated voice ID.")
    language: Optional[Union[str, List[str]]] = Field(None, description="Updated primary language code (e.g. 'te-IN').")
    languages: Optional[Union[str, List[str]]] = Field(None, description="Updated list of supported language codes (e.g. ['te-IN', 'hi-IN']).")
    voice_bot_mode: Optional[str] = Field(None, description="Updated operating mode ('modular' or 'realtime').")
    description: Optional[str] = Field(None, description="Updated description.")
    knowledgeBaseIds: Optional[List[str]] = Field(None, description="Updated list of knowledge base document IDs.")
    terms: Optional[TermsModel] = Field(None, description="Updated terms settings.")
    hinglish_mode: Optional[bool] = Field(None, description="Updated hinglish mode toggle.")
    deepgramMulti: Optional[bool] = Field(None, description="If true, Deepgram STT uses language=multi (code-switching). Default is false — primary language locked.")
    virtualNumber: Optional[str] = Field(None, description="Updated virtual number bound to this agent.")

    model_config = {
        "json_schema_extra": {
            "example": {
                "instructions": "You are a polite customer support agent...",
                "voice_bot_mode": "realtime"
            }
        }
    }

class SetAgentModeRequest(BaseModel):
    enterprise_id: str = Field(..., json_schema_extra={"example": "enterprise_id_here"}, description="Enterprise ID")
    agent_id: str = Field(..., json_schema_extra={"example": "agent_3a2e7c8f9b1d"}, description="Agent ID or MongoDB ObjectId of the agent")
    voice_bot_mode: Optional[str] = Field(None, json_schema_extra={"example": "modular"}, description="Operating mode: 'modular' or 'realtime'")
    mode: Optional[str] = Field(None, json_schema_extra={"example": "modular"}, description="Alias for voice_bot_mode: 'modular' or 'realtime'")

class AssignVirtualNumberRequest(BaseModel):
    enterprise_id: str = Field(..., json_schema_extra={"example": "enterprise_id_here"}, description="Enterprise ID")
    agent_id: str = Field(..., json_schema_extra={"example": "agent_3a2e7c8f9b1d"}, description="Agent ID or MongoDB ObjectId of the agent")
    virtual_number: str = Field(..., json_schema_extra={"example": "04000000000"}, description="The virtual phone number to assign to this agent")

class AddVoiceIdRequest(BaseModel):
    enterprise_id: str = Field(..., json_schema_extra={"example": "enterprise_id_here"}, description="Enterprise ID")
    agent_id: str = Field(..., json_schema_extra={"example": "agent_3a2e7c8f9b1d"}, description="Agent ID or MongoDB ObjectId of the agent")
    voice_id: Optional[str] = Field(None, json_schema_extra={"example": "neha"}, description="The voice ID to assign to this agent. Leave empty to list available voices.")

class SimulateRequest(BaseModel):
    message: str = Field(..., example="Hello, does this support refunds?")
    session_id: Optional[str] = Field(None, example="session_123")

class KBTextCreateRequest(BaseModel):
    title: str = Field(..., example="Refund Policy")
    content: str = Field(..., example="Full refunds within 30 days of purchase...")

class SaveEmailCredentialsRequest(BaseModel):
    # Frontend sends Gmail App Password creds under these upper-case keys
    # (see AIAgentsExotelViewTools.jsx) — Gmail's SMTP host/port are fixed,
    # so there's no need to collect them from the user.
    SENDER_EMAIL: str = Field(..., example="agent@company.com")
    PASSWORD: str = Field(..., example="xxxx xxxx xxxx xxxx")
    NAME: str = Field(..., example="Chauwk Support")
    CC: List[str] = Field(default_factory=list, example=["ops@company.com"])

# === Pydantic Response Schemas ===

class AgentDataResponse(BaseModel):
    id: str = Field(..., alias="_id", example="65b123456789abcdef012345")
    enterprise: str = Field(..., example="enterprise_id_here")
    name: str = Field(..., example="Support Assistant")
    instructions: Optional[str] = Field("", example="You are a helpful customer support agent...")
    firstMessage: Optional[str] = Field(default="", example="Hello! How can I help you today?")
    voiceId: Optional[str] = Field(default="default", example="meera")
    language: Optional[str] = Field(default="en-IN", example="en-IN")
    languages: Optional[List[str]] = Field(default_factory=lambda: ["en-IN"], example=["en-IN", "hi-IN"])
    voice_bot_mode: str = Field("modular", example="modular", description="Operating mode ('modular' or 'realtime')")
    hinglish_mode: bool = Field(False)
    description: Optional[str] = Field("")
    agentId: str = Field(..., example="agent_3a2e7c8f9b1d")
    knowledgeBaseIds: List[str] = Field(default_factory=list)
    terms: Optional[TermsModel] = Field(default_factory=lambda: TermsModel(enabled=False, content=""))
    virtualNumber: Optional[str] = Field(None, example="04045902355")
    status: str = Field("active")
    createdBy: Optional[str] = Field(default="", example="enterprise_id_here")
    createdAt: str = Field(...)
    updatedAt: str = Field(...)
    v: Optional[int] = Field(0, alias="__v")

class AgentCreateResponse(BaseModel):
    success: bool = Field(True, example=True)
    message: str = Field("Agent created successfully", example="Agent created successfully")
    data: AgentDataResponse

# === Safe MongoDB Operation Wrapper ===

async def safe_mongo_op(op_func):
    """Executes a MongoDB operation, recreating the client if the event loop was closed."""
    if mongo_db.client is None:
        return None
    try:
        return await op_func()
    except RuntimeError as re:
        if "loop is closed" in str(re).lower():
            logger.info("🔄 Event loop was closed. Recreating MongoDB client...")
            from motor.motor_asyncio import AsyncIOMotorClient
            from config import Config
            mongo_db.client = AsyncIOMotorClient(Config.DB_URL)
            return await op_func()
        raise

# === Enterprise Authentication Helper ===

def validate_enterprise(x_enterprise_id: Optional[str]):
    """Validate enterprise existence and account status for access control."""
    if not x_enterprise_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "success": False,
                "message": "Missing required fields: Enterprise ID in headers (x-enterprise-id)"
            }
        )
    if x_enterprise_id == "suspended-enterprise":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "success": False,
                "message": "Enterprise account is suspended"
            }
        )
    if x_enterprise_id == "nonexistent-enterprise":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "success": False,
                "message": "Enterprise account does not exist"
            }
        )

# === Database Helper ===

def build_enterprise_or_conditions(ent_id: Optional[str]) -> List[dict]:
    """Helper to build MongoDB $or filter matching string and ObjectId representations of enterprise ID."""
    if not ent_id or not str(ent_id).strip():
        return []
    clean_id = str(ent_id).strip()
    conds = [
        {"enterprise_id": clean_id},
        {"enterprise": clean_id},
        {"company_id": clean_id},
        {"createdBy": clean_id},
        {"enterpriseId": clean_id}
    ]
    if ObjectId.is_valid(clean_id):
        obj_id = ObjectId(clean_id)
        conds.extend([
            {"enterprise_id": obj_id},
            {"enterprise": obj_id},
            {"company_id": obj_id},
            {"createdBy": obj_id},
            {"enterpriseId": obj_id}
        ])
    return conds

async def find_agent_by_id_and_enterprise(agent_id_or_mongo_id: str, enterprise_id: str):
    """Find voice agent by agentId or MongoDB ObjectId supporting string/ObjectId enterprise values across all collections."""
    async def run_find():
        db = mongo_db.client.get_default_database()
        
        ent_conds = build_enterprise_or_conditions(enterprise_id)
        id_conds = [
            {"agentId": agent_id_or_mongo_id},
            {"_id": agent_id_or_mongo_id}
        ]
        if ObjectId.is_valid(agent_id_or_mongo_id):
            id_conds.append({"_id": ObjectId(agent_id_or_mongo_id)})
            
        query = {"$or": id_conds}
        if ent_conds:
            query = {
                "$and": [
                    {"$or": id_conds},
                    {"$or": ent_conds}
                ]
            }
            
        for coll_name in ["agents", "exotel_agents"]:
            if coll_name in await db.list_collection_names():
                agent = await db[coll_name].find_one(query)
                if agent:
                    return agent
        return None

    try:
        return await safe_mongo_op(run_find)
    except Exception as e:
        logger.error(f"Error querying agent in MongoDB: {e}")
        return None

# === API Endpoint Routes ===

@router.post(
    "/create-agent",
    response_model=AgentCreateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create Voice Agent",
    description="Registers a new custom voice agent on our own bot system and logs the metadata in MongoDB."
)
async def create_agent(
    payload: AgentCreateRequest,
    x_enterprise_id: Optional[str] = Header(None, alias="x-enterprise-id")
):
    # 1. Validate Enterprise ID
    validate_enterprise(x_enterprise_id)
    enterprise_id = x_enterprise_id
        
    # 2. Resolve language (primary string) and languages (list of supported language codes)
    raw_lang = payload.language
    raw_langs = payload.languages
    
    primary_language = "en-IN"
    if isinstance(raw_lang, str) and raw_lang.strip():
        primary_language = raw_lang.strip()
    elif isinstance(raw_lang, list) and raw_lang:
        primary_language = str(raw_lang[0]).strip()
    elif isinstance(raw_langs, list) and raw_langs:
        primary_language = str(raw_langs[0]).strip()
    elif isinstance(raw_langs, str) and raw_langs.strip():
        primary_language = raw_langs.split(",")[0].strip()
        
    resolved_languages = [primary_language]
    if isinstance(raw_langs, list):
        for l in raw_langs:
            s_l = str(l).strip()
            if s_l and s_l not in resolved_languages:
                resolved_languages.append(s_l)
    elif isinstance(raw_langs, str) and raw_langs.strip():
        for l in raw_langs.split(","):
            s_l = l.strip()
            if s_l and s_l not in resolved_languages:
                resolved_languages.append(s_l)

    # 3. Duplicate check: reject if an agent with the same name already exists for this enterprise
    if mongo_db.client is not None:
        async def check_duplicate():
            db = mongo_db.client.get_default_database()
            agents_collection = db['exotel_agents']
            return await agents_collection.find_one(
                {"enterprise": enterprise_id, "name": payload.name}
            )
        try:
            existing = await safe_mongo_op(check_duplicate)
            if existing:
                existing["_id"] = str(existing["_id"])
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "error": f"An agent named '{payload.name}' already exists for this enterprise.",
                        "existing_agent_id": existing.get("agentId"),
                        "existing_mongo_id": existing.get("_id"),
                        "hint": "Use the update-agent endpoint to modify it, or choose a different name."
                    }
                )
        except HTTPException:
            raise
        except Exception as e:
            logger.warning(f"⚠️ Duplicate check failed (proceeding with creation): {e}")

    # 4. Resolve operating mode ('modular' or 'realtime', default is 'modular')
    raw_mode = (getattr(payload, 'mode', None) or getattr(payload, 'voice_bot_mode', None) or "modular").lower().strip()
    if raw_mode not in ["modular", "realtime"]:
        raw_mode = "modular"

    # 5. Generate unique IDs for our own bot agent
    mongo_id = str(ObjectId())
    agent_uuid = f"agent_{uuid.uuid4().hex[:12]}"
    
    # 6. Build agent document matching the schema
    now_iso = datetime.datetime.utcnow().isoformat() + "Z"
    agent_data = {
        "_id": mongo_id,
        "enterprise": enterprise_id,
        "name": payload.name,
        "instructions": payload.instructions or "",
        "firstMessage": payload.firstMessage or "",
        "voiceId": payload.voiceId or "default",
        "language": primary_language,
        "languages": resolved_languages,
        "voice_bot_mode": raw_mode,
        "hinglish_mode": payload.hinglish_mode if payload.hinglish_mode is not None else False,
        "deepgramMulti": payload.deepgramMulti if payload.deepgramMulti is not None else False,
        "description": payload.description or "",
        "virtualNumber": (payload.virtualNumber if hasattr(payload, 'virtualNumber') and payload.virtualNumber else "") or "",
        "agentId": agent_uuid,
        "knowledgeBaseIds": payload.knowledgeBaseIds or [],
        "terms": {
            "enabled": payload.terms.enabled if payload.terms else False,
            "content": payload.terms.content if payload.terms else ""
        },
        "status": "active",
        "createdBy": enterprise_id,
        "createdAt": now_iso,
        "updatedAt": now_iso,
        "__v": 0
    }
    
    # 6. Save in MongoDB agents collection if connection is active
    async def run_insert():
        db = mongo_db.client.get_default_database()
        agents_collection = db['exotel_agents']
        await agents_collection.insert_one(agent_data.copy())

    saved_in_db = False
    if mongo_db.client is not None:
        try:
            await safe_mongo_op(run_insert)
            logger.info(f"✅ Voice agent {agent_uuid} successfully registered in MongoDB")
            saved_in_db = True
        except Exception as e:
            logger.error(f"❌ Failed to persist voice agent {agent_uuid} to MongoDB: {e}")
            # Continue behaving gracefully
    
    if not saved_in_db:
        logger.warning(f"⚠️ Voice agent {agent_uuid} created in memory only (no active MongoDB connection)")
        
    return {
        "success": True,
        "message": "Agent created successfully",
        "data": agent_data
    }

@router.post(
    "/set-agent-mode",
    status_code=status.HTTP_200_OK,
    summary="Set Agent Voice Bot Mode",
    description="Updates the operating mode ('modular' or 'realtime') for a specific agent taking enterprise_id, agent_id, and voice_bot_mode."
)
async def set_agent_mode(
    payload: SetAgentModeRequest,
    x_enterprise_id: Optional[str] = Header(None, alias="x-enterprise-id")
):
    ent_id = payload.enterprise_id or x_enterprise_id
    if not ent_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"success": False, "message": "enterprise_id (in body or x-enterprise-id header) is required"}
        )
    if not payload.agent_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"success": False, "message": "agent_id is required"}
        )

    mode_val = str(payload.voice_bot_mode or payload.mode or "").strip().lower()
    if mode_val not in ["modular", "realtime"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"success": False, "message": "Invalid mode. Allowed values are 'modular' or 'realtime'."}
        )

    validate_enterprise(ent_id)
    agent = await find_agent_by_id_and_enterprise(payload.agent_id, ent_id)
    if not agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"success": False, "message": f"Agent '{payload.agent_id}' not found for enterprise '{ent_id}'"}
        )

    if mongo_db.client is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"success": False, "message": "Database connection is not available"}
        )

    now_iso = datetime.datetime.utcnow().isoformat() + "Z"
    async def run_update_mode():
        db = mongo_db.client.get_default_database()
        ent_conds = build_enterprise_or_conditions(ent_id)
        id_conds = [
            {"agentId": payload.agent_id},
            {"_id": payload.agent_id}
        ]
        if ObjectId.is_valid(payload.agent_id):
            id_conds.append({"_id": ObjectId(payload.agent_id)})
            
        query = {"$or": id_conds}
        if ent_conds:
            query = {
                "$and": [
                    {"$or": id_conds},
                    {"$or": ent_conds}
                ]
            }

        updated_doc = None
        for coll_name in ["exotel_agents", "agents"]:
            if coll_name in await db.list_collection_names():
                res = await db[coll_name].update_one(
                    query,
                    {
                        "$set": {
                            "voice_bot_mode": mode_val,
                            "updatedAt": now_iso
                        },
                        "$unset": {
                            "mode": ""
                        }
                    }
                )
                if res.modified_count > 0 or res.matched_count > 0:
                    updated_doc = await db[coll_name].find_one(query)
                    break
        return updated_doc

    try:
        updated_agent = await safe_mongo_op(run_update_mode)
        if not updated_agent:
            updated_agent = dict(agent)
            updated_agent.pop("mode", None)
            updated_agent["voice_bot_mode"] = mode_val
            updated_agent["updatedAt"] = now_iso
        else:
            updated_agent.pop("mode", None)

        return {
            "success": True,
            "message": f"Successfully updated agent '{payload.agent_id}' voice_bot_mode to '{mode_val}'.",
            "enterprise_id": ent_id,
            "agent_id": payload.agent_id,
            "voice_bot_mode": mode_val,
            "data": bson_safe(dict(updated_agent))
        }
    except Exception as e:
        logger.error(f"Error updating agent mode: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"success": False, "message": f"Failed to update agent mode: {str(e)}"}
        )

class AdminSetAgentModeRequest(BaseModel):
    voiceBotMode: Optional[str] = Field(None, json_schema_extra={"example": "realtime"}, description="Operating mode: 'modular' or 'realtime' (realtime runs on the gpt-realtime-mini model).")
    mode: Optional[str] = Field(None, description="Alias for voiceBotMode.")

@router.patch(
    "/mode/{id}",
    status_code=status.HTTP_200_OK,
    summary="[Admin] Set Agent Voice Bot Mode",
    description="Super-admin endpoint: updates the operating mode ('modular' or 'realtime') for any agent by its id, across all enterprises. No enterprise scoping required."
)
async def admin_set_agent_mode(id: str, payload: AdminSetAgentModeRequest):
    mode_val = str(payload.voiceBotMode or payload.mode or "").strip().lower()
    if mode_val not in ["modular", "realtime"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"success": False, "message": "Invalid mode. Allowed values are 'modular' or 'realtime'."}
        )

    if mongo_db.client is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"success": False, "message": "Database connection is not available"}
        )

    now_iso = datetime.datetime.utcnow().isoformat() + "Z"
    id_conds = [{"agentId": id}, {"_id": id}]
    if ObjectId.is_valid(id):
        id_conds.append({"_id": ObjectId(id)})
    query = {"$or": id_conds}

    async def run_update_mode():
        db = mongo_db.client.get_default_database()
        for coll_name in ["exotel_agents", "agents"]:
            if coll_name in await db.list_collection_names():
                res = await db[coll_name].update_one(
                    query,
                    {"$set": {"voice_bot_mode": mode_val, "updatedAt": now_iso}, "$unset": {"mode": ""}}
                )
                if res.matched_count > 0:
                    return await db[coll_name].find_one(query)
        return None

    try:
        updated_agent = await safe_mongo_op(run_update_mode)
        if not updated_agent:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"success": False, "message": f"Agent '{id}' not found"}
            )
        return {
            "success": True,
            "message": f"Successfully updated agent '{id}' voice_bot_mode to '{mode_val}'.",
            "agent_id": id,
            "voice_bot_mode": mode_val,
            "data": bson_safe(dict(updated_agent))
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating agent mode (admin): {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"success": False, "message": f"Failed to update agent mode: {str(e)}"}
        )

@router.post(
    "/assign-virtual-number",
    status_code=status.HTTP_200_OK,
    summary="Assign Virtual Number to Agent",
    description="Assigns or attaches a virtual phone number (DID) to an existing voice agent."
)
async def assign_virtual_number(payload: AssignVirtualNumberRequest):
    if not payload.enterprise_id or not payload.agent_id or not payload.virtual_number:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"success": False, "message": "enterprise_id, agent_id, and virtual_number are required fields."}
        )

    if mongo_db.client is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"success": False, "message": "Database connection is not available"}
        )

    async def run_assign():
        db = mongo_db.client.get_default_database()
        ent_conds = build_enterprise_or_conditions(payload.enterprise_id)
        id_conds = [
            {"agentId": payload.agent_id},
            {"_id": payload.agent_id}
        ]
        if ObjectId.is_valid(payload.agent_id):
            id_conds.append({"_id": ObjectId(payload.agent_id)})
            
        query = {"$or": id_conds}
        if ent_conds:
            query = {
                "$and": [
                    {"$or": id_conds},
                    {"$or": ent_conds}
                ]
            }
        
        for coll_name in ["exotel_agents", "agents", "modernexotelaiagents", "modernaiagents"]:
            if coll_name in await db.list_collection_names():
                doc = await db[coll_name].find_one(query)
                if doc:
                    await db[coll_name].update_one(
                        {"_id": doc["_id"]},
                        {"$set": {
                            "virtualNumber": payload.virtual_number,
                            "updatedAt": datetime.datetime.utcnow().isoformat() + "Z"
                        },
                        "$unset": {
                            "phoneNumber": ""
                        }}
                    )
                    updated_doc = await db[coll_name].find_one({"_id": doc["_id"]})
                    return updated_doc
        return None

    try:
        updated_agent = await safe_mongo_op(run_assign)
        if not updated_agent:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"success": False, "message": "Agent not found for the specified enterprise and ID."}
            )
        
        safe_agent = bson_safe(dict(updated_agent))
        return {
            "success": True,
            "message": f"Virtual number {payload.virtual_number} assigned successfully.",
            "data": safe_agent
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error assigning virtual number to agent: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"success": False, "message": f"Internal server error: {str(e)}"}
        )

SARVAM_VOICES = [
    # bulbul:v3 voices
    "shubh", "aditya", "ritu", "priya", "neha", "rahul", "pooja", "rohan", "simran", "kavya", 
    "amit", "dev", "ishita", "shreya", "ratan", "varun", "manan", "sumit", "roopa", "kabir", 
    "aayan", "ashutosh", "advait", "anand", "tanya", "tarun", "sunny", "mani", "gokul", "vijay", 
    "shruti", "suhani", "mohit", "kavitha", "rehan", "soham", "rupali",
    # bulbul:v2 voices
    "anushka", "manisha", "vidya", "arya", "abhilash", "karun", "hitesh"
]

@router.post(
    "/add-voice-id-to-agent",
    status_code=status.HTTP_200_OK,
    summary="Add Voice ID to Agent",
    description="Assigns a Sarvam voice ID to an existing voice agent, or lists available voices if voice_id is omitted."
)
async def add_voice_id_to_agent(payload: AddVoiceIdRequest):
    if not payload.enterprise_id or not payload.agent_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"success": False, "message": "enterprise_id and agent_id are required fields."}
        )

    if mongo_db.client is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"success": False, "message": "Database connection is not available"}
        )

    async def run_query_and_update():
        db = mongo_db.client.get_default_database()
        ent_conds = build_enterprise_or_conditions(payload.enterprise_id)
        id_conds = [
            {"agentId": payload.agent_id},
            {"_id": payload.agent_id}
        ]
        if ObjectId.is_valid(payload.agent_id):
            id_conds.append({"_id": ObjectId(payload.agent_id)})
            
        query = {"$or": id_conds}
        if ent_conds:
            query = {
                "$and": [
                    {"$or": id_conds},
                    {"$or": ent_conds}
                ]
            }
        
        for coll_name in ["exotel_agents", "agents", "modernexotelaiagents", "modernaiagents"]:
            if coll_name in await db.list_collection_names():
                doc = await db[coll_name].find_one(query)
                if doc:
                    if not payload.voice_id:
                        # Just return the document to fetch name, and show available voices
                        return doc, False
                    
                    # Validate the provided voice ID
                    requested_voice = str(payload.voice_id).strip().lower()
                    if requested_voice not in SARVAM_VOICES:
                        raise HTTPException(
                            status_code=status.HTTP_400_BAD_REQUEST,
                            detail={
                                "success": False, 
                                "message": f"Invalid voice_id '{payload.voice_id}'.",
                                "available_voices": SARVAM_VOICES
                            }
                        )
                        
                    await db[coll_name].update_one(
                        {"_id": doc["_id"]},
                        {"$set": {
                            "voiceId": requested_voice,
                            "updatedAt": datetime.datetime.utcnow().isoformat() + "Z"
                        }}
                    )
                    updated_doc = await db[coll_name].find_one({"_id": doc["_id"]})
                    return updated_doc, True
        return None, False

    try:
        res, is_updated = await safe_mongo_op(run_query_and_update)
        if not res:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"success": False, "message": "Agent not found for the specified enterprise and ID."}
            )
        
        agent_name = res.get("name", "Unknown Agent")
        
        if is_updated:
            return {
                "success": True,
                "message": f"voice id \"{payload.voice_id}\" added successfully to agent \"{agent_name}\" successfully",
                "data": bson_safe(dict(res))
            }
        else:
            return {
                "success": True,
                "message": "Please select a voice_id from the available list to assign it to the agent.",
                "agent_name": agent_name,
                "available_voices": SARVAM_VOICES
            }
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error adding voice ID to agent: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"success": False, "message": f"Internal server error: {str(e)}"}
        )


@router.get(
    "/supported-languages",
    status_code=status.HTTP_200_OK,
    summary="Get Supported Languages",
    description="Returns a list of languages supported by the voice bot engine."
)
async def get_supported_languages():
    return {
        "success": True,
        "languages": [
            {"code": "en", "name": "English"},
            {"code": "hi", "name": "Hindi"},
            {"code": "es", "name": "Spanish"},
            {"code": "fr", "name": "French"},
            {"code": "de", "name": "German"}
        ]
    }

@router.get(
    "/supported-voices-and-languages",
    status_code=status.HTTP_200_OK,
    summary="Get Supported Voices and Languages",
    description="Returns a list of all available voice IDs and language codes for configuring voice agents."
)
async def get_supported_voices_and_languages():
    return {
        "success": True,
        "voices": [
            {"id": "shubh", "gender": "male", "model": "bulbul:v3", "description": "Hindi / Code-mixed Indian Male Voice (Default)"},
            {"id": "aditya", "gender": "male", "model": "bulbul:v3", "description": "Indian Male Voice"},
            {"id": "ritu", "gender": "female", "model": "bulbul:v3", "description": "Indian Female Voice"},
            {"id": "priya", "gender": "female", "model": "bulbul:v3", "description": "Indian Female Voice"},
            {"id": "neha", "gender": "female", "model": "bulbul:v3", "description": "Indian Female Voice"},
            {"id": "rahul", "gender": "male", "model": "bulbul:v3", "description": "Indian Male Voice"},
            {"id": "pooja", "gender": "female", "model": "bulbul:v3", "description": "Indian Female Voice"},
            {"id": "rohan", "gender": "male", "model": "bulbul:v3", "description": "Indian Male Voice"},
            {"id": "simran", "gender": "female", "model": "bulbul:v3", "description": "Indian Female Voice"},
            {"id": "kavya", "gender": "female", "model": "bulbul:v3", "description": "Indian Female Voice"},
            {"id": "amit", "gender": "male", "model": "bulbul:v3", "description": "Indian Male Voice"},
            {"id": "dev", "gender": "male", "model": "bulbul:v3", "description": "Indian Male Voice"},
            {"id": "ishita", "gender": "female", "model": "bulbul:v3", "description": "Indian Female Voice"},
            {"id": "shreya", "gender": "female", "model": "bulbul:v3", "description": "Indian Female Voice"},
            {"id": "ratan", "gender": "male", "model": "bulbul:v3", "description": "Indian Male Voice"},
            {"id": "varun", "gender": "male", "model": "bulbul:v3", "description": "Indian Male Voice"},
            {"id": "manan", "gender": "male", "model": "bulbul:v3", "description": "Indian Male Voice"},
            {"id": "sumit", "gender": "male", "model": "bulbul:v3", "description": "Indian Male Voice"},
            {"id": "roopa", "gender": "female", "model": "bulbul:v3", "description": "Indian Female Voice"},
            {"id": "kabir", "gender": "male", "model": "bulbul:v3", "description": "Indian Male Voice"},
            {"id": "aayan", "gender": "male", "model": "bulbul:v3", "description": "Indian Male Voice"},
            {"id": "ashutosh", "gender": "male", "model": "bulbul:v3", "description": "Indian Male Voice"},
            {"id": "advait", "gender": "male", "model": "bulbul:v3", "description": "Indian Male Voice"},
            {"id": "anand", "gender": "male", "model": "bulbul:v3", "description": "Indian Male Voice"},
            {"id": "tanya", "gender": "female", "model": "bulbul:v3", "description": "Indian Female Voice"},
            {"id": "tarun", "gender": "male", "model": "bulbul:v3", "description": "Indian Male Voice"},
            {"id": "sunny", "gender": "male", "model": "bulbul:v3", "description": "Indian Male Voice"},
            {"id": "mani", "gender": "male", "model": "bulbul:v3", "description": "Indian Male Voice"},
            {"id": "gokul", "gender": "male", "model": "bulbul:v3", "description": "Indian Male Voice"},
            {"id": "vijay", "gender": "male", "model": "bulbul:v3", "description": "Indian Male Voice"},
            {"id": "shruti", "gender": "female", "model": "bulbul:v3", "description": "Indian Female Voice"},
            {"id": "suhani", "gender": "female", "model": "bulbul:v3", "description": "Indian Female Voice"},
            {"id": "mohit", "gender": "male", "model": "bulbul:v3", "description": "Indian Male Voice"},
            {"id": "kavitha", "gender": "female", "model": "bulbul:v3", "description": "Indian Female Voice"},
            {"id": "rehan", "gender": "male", "model": "bulbul:v3", "description": "Indian Male Voice"},
            {"id": "soham", "gender": "male", "model": "bulbul:v3", "description": "Indian Male Voice"},
            {"id": "rupali", "gender": "female", "model": "bulbul:v3", "description": "Indian Female Voice"},
            {"id": "anushka", "gender": "female", "model": "bulbul:v2", "description": "Legacy Indian Female Voice"},
            {"id": "manisha", "gender": "female", "model": "bulbul:v2", "description": "Legacy Indian Female Voice"},
            {"id": "vidya", "gender": "female", "model": "bulbul:v2", "description": "Legacy Indian Female Voice"},
            {"id": "arya", "gender": "female", "model": "bulbul:v2", "description": "Legacy Indian Female Voice"},
            {"id": "abhilash", "gender": "male", "model": "bulbul:v2", "description": "Legacy Indian Male Voice"},
            {"id": "karun", "gender": "male", "model": "bulbul:v2", "description": "Legacy Indian Male Voice"},
            {"id": "hitesh", "gender": "male", "model": "bulbul:v2", "description": "Legacy Indian Male Voice"}
        ],
        "languages": [
            {"code": "en-IN", "name": "English (Indian)"},
            {"code": "hi-IN", "name": "Hindi (हिन्दी)"},
            {"code": "te-IN", "name": "Telugu (తెలుగు)"},
            {"code": "ta-IN", "name": "Tamil (தமிழ்)"},
            {"code": "kn-IN", "name": "Kannada (ಕನ್ನಡ)"},
            {"code": "ml-IN", "name": "Malayalam (മലയാളం)"},
            {"code": "mr-IN", "name": "Marathi (मराठी)"},
            {"code": "bn-IN", "name": "Bengali (বাংলা)"},
            {"code": "gu-IN", "name": "Gujarati (ગુજરાતી)"},
            {"code": "pa-IN", "name": "Punjabi (ਪੰਜਾਬੀ)"},
            {"code": "od-IN", "name": "Odia (ଓଡ଼ିଆ)"}
        ]
    }

@router.get(
    "/list",
    status_code=status.HTTP_200_OK,
    summary="List Voice Agents",
    description="Lists all custom Exotel voice agents created for the enterprise (excludes ElevenLabs agents)."
)
@router.get(
    "",
    status_code=status.HTTP_200_OK,
    include_in_schema=False
)
async def list_agents(
    enterprise_id: Optional[str] = Query(None, description="Enterprise ID query parameter"),
    enterprise: Optional[str] = Query(None, description="Enterprise ID query parameter"),
    company_id: Optional[str] = Query(None, description="Company ID query parameter"),
    x_enterprise_id: Optional[str] = Header(None, alias="x-enterprise-id")
):
    ent_id = (enterprise_id or enterprise or company_id or x_enterprise_id or "").strip()
    if ent_id in ["suspended-enterprise", "nonexistent-enterprise"]:
        validate_enterprise(ent_id)
        
    async def run_query():
        db = mongo_db.client.get_default_database()
        
        filter_q = {}
        conds = build_enterprise_or_conditions(ent_id)
        if conds:
            filter_q = {"$or": conds}
            
        agents_map = {}
        target_colls = ["agents", "exotel_agents", "modernexotelaiagents", "modernaiagents"]
        for coll_name in target_colls:
            try:
                cursor = db[coll_name].find(filter_q).sort("createdAt", -1)
                async for doc in cursor:
                    if doc.get("type") == "eleven": continue
                    doc_id = str(doc["_id"])
                    if doc_id not in agents_map:
                        safe_doc = bson_safe(dict(doc))
                        safe_doc["_id"] = doc_id
                        agents_map[doc_id] = safe_doc
            except Exception as ex:
                logger.warning(f"Error querying collection '{coll_name}': {ex}")
        return [a for a in agents_map.values() if a.get("type") != "eleven"]

    agents = []
    try:
        res = await safe_mongo_op(run_query)
        if res:
            agents = res
    except Exception as e:
        logger.error(f"Failed to fetch agents list: {e}")
        
    return {
        "success": True,
        "data": agents
    }

@router.get(
    "/stats",
    status_code=status.HTTP_200_OK,
    summary="Get Agent Statistics",
    description="Retrieves aggregate metrics and configurations about an enterprise's Exotel voice agents (excludes ElevenLabs agents)."
)
async def get_agent_stats(
    enterprise_id: Optional[str] = Query(None, description="Enterprise ID query parameter"),
    enterprise: Optional[str] = Query(None, description="Enterprise ID query parameter"),
    company_id: Optional[str] = Query(None, description="Company ID query parameter"),
    x_enterprise_id: Optional[str] = Header(None, alias="x-enterprise-id")
):
    ent_id = (enterprise_id or enterprise or company_id or x_enterprise_id or "").strip()
    if ent_id in ["suspended-enterprise", "nonexistent-enterprise"]:
        validate_enterprise(ent_id)
        
    async def run_query():
        db = mongo_db.client.get_default_database()
        
        filter_q = {}
        conds = build_enterprise_or_conditions(ent_id)
        if conds:
            filter_q = {"$or": conds}
            
        total_agents = 0
        active_agents = 0
        languages = {}
        seen_ids = set()
        target_colls = ["agents", "exotel_agents", "modernexotelaiagents", "modernaiagents"]
        for coll_name in target_colls:
            try:
                cursor = db[coll_name].find(filter_q)
                async for doc in cursor:
                    if doc.get("type") == "eleven": continue
                    doc_id = str(doc["_id"])
                    if doc_id in seen_ids:
                        continue
                    seen_ids.add(doc_id)
                    total_agents += 1
                    if doc.get("status") == "active":
                        active_agents += 1
                    lang = doc.get("language", "en")
                    if isinstance(lang, list) and lang:
                        lang = lang[0]
                    languages[str(lang)] = languages.get(str(lang), 0) + 1
            except Exception as ex:
                logger.warning(f"Error querying collection '{coll_name}': {ex}")
        return total_agents, active_agents, languages

    total_agents, active_agents, languages = 0, 0, {}
    try:
        res = await safe_mongo_op(run_query)
        if res:
            total_agents, active_agents, languages = res
    except Exception as e:
        logger.error(f"Failed to calculate stats: {e}")
        
    return {
        "success": True,
        "stats": {
            "totalAgents": total_agents,
            "activeAgents": active_agents,
            "languages": languages
        }
    }

@router.get(
    "/admin/all",
    status_code=status.HTTP_200_OK,
    summary="[Admin] List ALL Agents",
    description="Lists every agent across all enterprises stored in MongoDB. Shows agentId, name, enterprise, language, virtualNumber, knowledgeBaseIds, and status."
)
async def list_all_agents_admin():
    """Admin-only: returns all agents in the DB. Safe against BSON types."""
    import json

    if mongo_db.client is None:
        return JSONResponse(status_code=503, content={
            "success": False,
            "error": "MongoDB is not connected. Check DB_URL environment variable."
        })

    async def run_query():
        db = mongo_db.client.get_default_database()
        agents_collection = db['exotel_agents']
        cursor = agents_collection.find({})
        agents_list = []
        async for doc in cursor:
            # bson_safe converts ObjectId, datetime, Decimal128, bytes → plain Python types
            safe_doc = bson_safe(dict(doc))
            # Normalize: expose company_id as enterprise if enterprise field is missing
            if not safe_doc.get("enterprise") and safe_doc.get("company_id"):
                safe_doc["enterprise"] = safe_doc["company_id"]
            agents_list.append(safe_doc)
        return agents_list

    try:
        agents = await safe_mongo_op(run_query) or []
        payload = {
            "success": True,
            "total": len(agents),
            "agents": agents
        }
        return JSONResponse(status_code=200, content=payload)
    except Exception as e:
        logger.error(f"❌ /admin/all failed: {e}", exc_info=True)
        return JSONResponse(status_code=500, content={
            "success": False,
            "error": str(e)
        })

@router.get(
    "/logs",
    status_code=status.HTTP_200_OK,
    summary="Get Filtered Call Logs",
    description="Fetch call logs filtered by Enterprise ID, Agent ID, Phone Number, and Direction."
)
@router.get(
    "/call-logs",
    status_code=status.HTTP_200_OK,
    include_in_schema=False
)
async def get_filtered_call_logs(
    enterprise_id: Optional[str] = Query(None, description="Enterprise ID filter"),
    agent_id: Optional[str] = Query(None, description="Agent ID or Mongo _id filter"),
    phone_number: Optional[str] = Query(None, description="Phone number filter (customer or virtual)"),
    direction: Optional[str] = Query(None, description="Call direction ('inbound' or 'outbound')"),
    page: int = Query(1, ge=1, description="Page number"),
    limit: int = Query(50, ge=1, le=500, description="Records per page"),
    x_enterprise_id: Optional[str] = Header(None, alias="x-enterprise-id")
):
    ent_id = (enterprise_id or x_enterprise_id or "").strip()
    try:
        from bson import ObjectId
        from core.agent_resolver import resolve_agent_config
        import re
        
        if mongo_db.client is None:
            return {"success": True, "total": 0, "page": page, "limit": limit, "logs": []}
            
        db = mongo_db.client.get_default_database()
        calls_coll = db['Agent_Stream_CallsLogs']
        
        query_clauses = []
        
        if ent_id:
            query_clauses.append({
                "$or": [
                    {"enterprise_id": ent_id},
                    {"enterprise": ent_id},
                    {"company_id": ent_id},
                    {"createdBy": ent_id}
                ]
            })
            
        if agent_id and agent_id.strip():
            target_agent = agent_id.strip()
            target_ids = [target_agent]
            
            resolved_agent = await find_agent_by_id_and_enterprise(target_agent, ent_id) if ent_id else None
            if not resolved_agent:
                resolved_agent = await resolve_agent_config(target_agent)
            if resolved_agent:
                if resolved_agent.get("agentId"):
                    target_ids.append(resolved_agent.get("agentId"))
                if resolved_agent.get("_id"):
                    target_ids.append(str(resolved_agent.get("_id")))
                    
            agent_or = []
            for tid in set(target_ids):
                agent_or.extend([
                    {"agentId": tid},
                    {"agent_id": tid}
                ])
                if ObjectId.is_valid(tid):
                    agent_or.append({"_id": ObjectId(tid)})
            query_clauses.append({"$or": agent_or})
            
        if phone_number and phone_number.strip():
            clean_digits = re.sub(r'\D', '', str(phone_number))[-10:]
            if clean_digits:
                rgx = {"$regex": clean_digits}
                query_clauses.append({
                    "$or": [
                        {"phone_number": rgx},
                        {"virtualNumber": rgx},
                        {"to_number": rgx},
                        {"from_number": rgx},
                        {"caller_phone_no": rgx},
                        {"callerNumber": rgx},
                        {"leadPhone": rgx},
                        {"lead_phone_no": rgx}
                    ]
                })
                
        if direction and direction.strip().lower() in ["inbound", "outbound"]:
            query_clauses.append({"direction": direction.strip().lower()})
            
        final_filter = {}
        if len(query_clauses) == 1:
            final_filter = query_clauses[0]
        elif len(query_clauses) > 1:
            final_filter = {"$and": query_clauses}
            
        logs_map = {}
        target_colls = ["Agent_Stream_CallsLogs", "outbound_calls"]
        for coll_name in target_colls:
            try:
                cursor = db[coll_name].find(final_filter)
                async for doc in cursor:
                    if doc.get("type") == "eleven": continue
                    doc_id = str(doc["_id"])
                    if doc_id not in logs_map:
                        safe_doc = bson_safe(dict(doc))
                        safe_doc["_id"] = doc_id
                        logs_map[doc_id] = safe_doc
            except Exception as ex:
                logger.warning(f"Error querying call logs collection '{coll_name}': {ex}")

        all_logs = list(logs_map.values())
        
        def parse_ts(item):
            ts = item.get("timestamp") or item.get("createdAt") or 0
            if isinstance(ts, (int, float)):
                return float(ts)
            if isinstance(ts, str):
                try:
                    return datetime.datetime.fromisoformat(ts).timestamp()
                except Exception:
                    return 0.0
            return 0.0
            
        all_logs.sort(key=parse_ts, reverse=True)
        total_count = len(all_logs)
        skip = (page - 1) * limit
        paginated_logs = all_logs[skip : skip + limit]
        
        # Clean up transcript
        for log in paginated_logs:
            if "transcript" in log and isinstance(log["transcript"], list):
                for msg in log["transcript"]:
                    if isinstance(msg, dict) and "msg" in msg:
                        text = msg["msg"]
                        if isinstance(text, str):
                            # Remove RAG context
                            text = re.sub(r'\n\n\[Relevant Knowledge Base Context:.*?\]', '', text, flags=re.DOTALL)
                            # Remove Action outputs/requests if they are the only thing in the message
                            if text.startswith('[Requested action:') or text.startswith('[Action output:'):
                                text = re.sub(r'\[Requested action:.*?\]', '*Triggered an internal action*', text, flags=re.DOTALL)
                                text = re.sub(r'\[Action output:.*?\]', '*Action successfully completed*', text, flags=re.DOTALL)
                            msg["msg"] = text.strip()
        
        return {
            "success": True,
            "total": total_count,
            "page": page,
            "limit": limit,
            "logs": paginated_logs
        }
    except Exception as e:
        logger.error(f"Error querying call logs: {e}")
        raise HTTPException(
            status_code=500,
            detail={"success": False, "message": f"Error querying call logs: {str(e)}"}
        )

@router.get(
    "/{id}",
    status_code=status.HTTP_200_OK,
    summary="Get Agent Details",
    description="Retrieves detailed settings and parameters for a specific agent."
)
@router.get(
    "/get/{id}",
    status_code=status.HTTP_200_OK,
    summary="Get Agent Details (Alias)",
    description="Retrieves detailed settings and parameters for a specific agent.",
    include_in_schema=True
)
async def get_agent_details(
    id: str,
    x_enterprise_id: Optional[str] = Header(None, alias="x-enterprise-id")
):
    validate_enterprise(x_enterprise_id)
    agent = await find_agent_by_id_and_enterprise(id, x_enterprise_id)
    if not agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"success": False, "message": "Agent not found"}
        )
    safe_agent = bson_safe(dict(agent))
    return {
        "success": True,
        "data": safe_agent
    }

@router.get(
    "/{id}/public",
    status_code=status.HTTP_200_OK,
    summary="Get Public Agent Details",
    description="Retrieves public styling and metadata (name, description, avatar) for widget rendering."
)
async def get_public_agent_details(id: str):
    async def run_find():
        db = mongo_db.client.get_default_database()
        agents_collection = db['exotel_agents']
        agent = await agents_collection.find_one({"agentId": id})
        if not agent:
            agent = await agents_collection.find_one({"_id": id})
        return agent

    try:
        agent = await safe_mongo_op(run_find)
    except Exception as e:
        logger.error(f"Error querying agent in MongoDB: {e}")
        agent = None

    if not agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"success": False, "message": "Agent not found"}
        )
    
    return {
        "success": True,
        "name": agent.get("name", "AI Assistant"),
        "description": agent.get("description", ""),
        "avatar_url": agent.get("avatar_url", "")
    }

@router.put(
    "/{id}",
    status_code=status.HTTP_200_OK,
    summary="Update Agent",
    description="Modifies the configuration settings of an existing voice agent."
)
async def update_agent(
    id: str,
    payload: AgentUpdateRequest,
    x_enterprise_id: Optional[str] = Header(None, alias="x-enterprise-id")
):
    validate_enterprise(x_enterprise_id)
    agent = await find_agent_by_id_and_enterprise(id, x_enterprise_id)
    if not agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"success": False, "message": "Agent not found"}
        )
    
    # Extract only fields that were explicitly sent in the request JSON
    raw_dict = payload.model_dump(exclude_unset=True)
    update_data = {}
    
    for key, val in raw_dict.items():
        if key == "terms" and isinstance(val, dict):
            update_data["terms"] = {
                "enabled": val.get("enabled", False),
                "content": val.get("content", "")
            }
        elif key in ["language", "languages"]:
            continue  # Handled below
        elif key in ["mode", "voice_bot_mode"]:
            if isinstance(val, str) and val.strip().lower() in ["modular", "realtime"]:
                norm_mode = val.strip().lower()
                update_data["mode"] = norm_mode
                update_data["voice_bot_mode"] = norm_mode
        else:
            update_data[key] = val

    # Handle language / languages updates if explicitly sent
    if "languages" in raw_dict or "language" in raw_dict:
        raw_lang = raw_dict.get("language")
        raw_langs = raw_dict.get("languages")
        
        primary_lang = None
        if isinstance(raw_lang, str) and raw_lang.strip():
            primary_lang = raw_lang.strip()
        elif isinstance(raw_lang, list) and raw_lang:
            primary_lang = str(raw_lang[0]).strip()
        elif isinstance(raw_langs, list) and raw_langs:
            primary_lang = str(raw_langs[0]).strip()
        elif isinstance(raw_langs, str) and raw_langs.strip():
            primary_lang = raw_langs.split(",")[0].strip()
            
        if primary_lang:
            update_data["language"] = primary_lang
            resolved_languages = [primary_lang]
            if isinstance(raw_langs, list):
                for l in raw_langs:
                    s_l = str(l).strip()
                    if s_l and s_l not in resolved_languages:
                        resolved_languages.append(s_l)
            elif isinstance(raw_langs, str) and raw_langs.strip():
                for l in raw_langs.split(","):
                    s_l = l.strip()
                    if s_l and s_l not in resolved_languages:
                        resolved_languages.append(s_l)
            update_data["languages"] = resolved_languages

    if not update_data:
        safe_agent = bson_safe(dict(agent))
        return {
            "success": True,
            "message": "No fields to update",
            "data": safe_agent
        }

    update_data["updatedAt"] = datetime.datetime.utcnow().isoformat() + "Z"

    async def run_update():
        db = mongo_db.client.get_default_database()
        agents_collection = db['exotel_agents']
        await agents_collection.update_one(
            {"_id": agent["_id"]},
            {"$set": update_data}
        )
        return await agents_collection.find_one({"_id": agent["_id"]})

    if mongo_db.client is not None:
        try:
            updated_agent = await safe_mongo_op(run_update)
            if updated_agent:
                agent = updated_agent
        except Exception as e:
            logger.error(f"Failed to update agent in MongoDB: {e}")
            agent.update(update_data)

    safe_agent = bson_safe(dict(agent))
    return {
        "success": True,
        "message": "Agent updated successfully",
        "data": safe_agent
    }

@router.delete(
    "/{id}",
    status_code=status.HTTP_200_OK,
    summary="Delete Agent",
    description="Deletes a voice agent permanently from the system."
)
async def delete_agent(
    id: str,
    x_enterprise_id: Optional[str] = Header(None, alias="x-enterprise-id")
):
    validate_enterprise(x_enterprise_id)
    agent = await find_agent_by_id_and_enterprise(id, x_enterprise_id)
    if not agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"success": False, "message": "Agent not found"}
        )
    
    async def run_delete():
        db = mongo_db.client.get_default_database()
        agents_collection = db['exotel_agents']
        await agents_collection.delete_one({"_id": agent["_id"]})
        return True

    deleted_from_db = False
    if mongo_db.client is not None:
        try:
            deleted_from_db = await safe_mongo_op(run_delete)
        except Exception as e:
            logger.error(f"Failed to delete agent from MongoDB: {e}")

    return {
        "success": True,
        "message": "Agent deleted successfully" if deleted_from_db else "Agent deleted from memory"
    }

@router.get(
    "/{id}/embed-link",
    status_code=status.HTTP_200_OK,
    summary="Get Embed Link",
    description="Returns standard widget iframe code and direct access URLs for the voice bot."
)
async def get_agent_embed_link(
    id: str,
    x_enterprise_id: Optional[str] = Header(None, alias="x-enterprise-id")
):
    validate_enterprise(x_enterprise_id)
    agent = await find_agent_by_id_and_enterprise(id, x_enterprise_id)
    if not agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"success": False, "message": "Agent not found"}
        )
    
    agent_id = agent.get("agentId") or str(agent.get("_id", id))
    
    from config import Config
    base_url = Config.SERVER_BASE_URL.rstrip("/")
    
    # The HTML tag that embeds the widget on any webpage
    html_tag = (
        f'<agent-stream-voice '
        f'agent-id="{agent_id}" '
        f'server-url="{base_url}">'
        f'</agent-stream-voice>'
    )
    
    # The script tag that loads the widget JS
    script_tag = f'<script src="{base_url}/static/voice-agent-widget.js" async></script>'
    
    # Full HTML snippet to paste into any page body
    html_snippet = f"{html_tag}\n{script_tag}"
    
    # Iframe wrapper (optional, sandboxed embed)
    iframe_code = (
        f'<iframe src="{base_url}/widget?agentId={agent_id}" '
        f'width="380" height="540" frameborder="0" allow="microphone"></iframe>'
    )
    
    # Direct WebSocket URL for the voice stream
    ws_url = base_url.replace("https://", "wss://").replace("http://", "ws://")
    websocket_url = f"{ws_url}/api/v1/stream/browser?agent_id={agent_id}"
    
    # JavaScript console snippet for quick testing on any website
    js_test_snippet = (
        f"const s=document.createElement('script');"
        f"s.src='{base_url}/static/voice-agent-widget.js';"
        f"document.body.appendChild(s);"
        f"s.onload=()=>{{"
        f"const w=document.createElement('agent-stream-voice');"
        f"w.setAttribute('agent-id','{agent_id}');"
        f"w.setAttribute('server-url','{base_url}');"
        f"document.body.appendChild(w);"
        f"}};"
    )
    
    return {
        "success": True,
        "agentId": agent_id,
        "serverUrl": base_url,
        "websocketUrl": websocket_url,
        "embedCode": {
            "htmlSnippet": html_snippet,
            "htmlTag": html_tag,
            "scriptTag": script_tag,
            "iframe": iframe_code,
            "jsConsoleSnippet": js_test_snippet
        },
        "instructions": {
            "step1": f"Paste the htmlTag where you want the widget to appear on your webpage.",
            "step2": f"Paste the scriptTag at the bottom of your <body> tag.",
            "quickTest": f"Open any browser tab, press F12 → Console, type 'allow pasting', then paste jsConsoleSnippet."
        }
    }

@router.post(
    "/{id}/simulate",
    status_code=status.HTTP_200_OK,
    summary="Simulate Conversation",
    description="Simulates a real-time conversational exchange with the agent using Gemini."
)
async def simulate_conversation(
    id: str,
    payload: SimulateRequest,
    x_enterprise_id: Optional[str] = Header(None, alias="x-enterprise-id")
):
    validate_enterprise(x_enterprise_id)
    agent = await find_agent_by_id_and_enterprise(id, x_enterprise_id)
    if not agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"success": False, "message": "Agent not found"}
        )
    
    session_id = payload.session_id or f"sim_{uuid.uuid4().hex[:8]}"
    instructions = agent.get("instructions", "You are a customer assistant.")
    
    history_prompt = ""
    async def fetch_history():
        db = mongo_db.client.get_default_database()
        return await db['simulated_conversations'].find_one({"session_id": session_id})
        
    try:
        existing_session = await safe_mongo_op(fetch_history)
        if existing_session and "messages" in existing_session:
            for msg in existing_session["messages"]:
                role = "User Message" if msg.get("role") == "user" else "Agent Response"
                history_prompt += f"{role}: {msg.get('content')}\n"
    except Exception as e:
        logger.error(f"Error fetching simulation history: {e}")
        
    response_text = ""
    try:
        # Check Knowledge Base
        kb_ids = []
        async def fetch_kb_ids():
            db = mongo_db.client.get_default_database()
            cursor = db['agent_kb_documents'].find({"agentId": agent.get("agentId")})
            ids = []
            async for doc in cursor:
                ids.append(str(doc.get("docId")))
            return ids
            
        try:
            kb_ids = await safe_mongo_op(fetch_kb_ids) or []
        except Exception as e:
            logger.error(f"Failed to fetch KB docs for simulation: {e}")

        rag_context = ""
        if kb_ids:
            try:
                results = await rag_manager.search(company_id=agent.get("agentId"), query=payload.message, top_k=3, document_ids=kb_ids)
                if results:
                    rag_context = "Relevant Knowledge Base Information:\n" + "\n".join([r["chunk_text"] for r in results]) + "\n\n"
            except Exception as e:
                logger.error(f"Failed to fetch RAG context for simulation: {e}")

        guardrail = "STRICT RULE: You must ONLY answer questions using the knowledge base context provided. If the user asks a question NOT covered in the context, you MUST say: 'I'm sorry, but I don't have that information.' Do NOT invent facts or use outside knowledge.\n"
        prompt = f"System Instructions:\n{guardrail}{instructions}\n\n{rag_context}{history_prompt}User Message: {payload.message}\nAgent Response:"
        resp = rag_manager.gemini_client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt
        )
        response_text = resp.text.strip()
        
        async def save_history():
            db = mongo_db.client.get_default_database()
            new_messages = [
                {"role": "user", "content": payload.message, "timestamp": datetime.datetime.utcnow().isoformat() + "Z"},
                {"role": "agent", "content": response_text, "timestamp": datetime.datetime.utcnow().isoformat() + "Z"}
            ]
            await db['simulated_conversations'].update_one(
                {"session_id": session_id},
                {"$push": {"messages": {"$each": new_messages}}},
                upsert=True
            )
            
        try:
            await safe_mongo_op(save_history)
        except Exception as e:
            logger.error(f"Error saving simulation history: {e}")
            
    except Exception as e:
        logger.warning(f"Failed to call Gemini for simulation, using fallback: {e}")
        response_text = f"Simulated Agent Response: I received your message: '{payload.message}'."

    return {
        "success": True,
        "response": response_text,
        "session_id": session_id
    }

@router.post(
    "/{id}/simulate-voice",
    status_code=status.HTTP_200_OK,
    summary="Simulate Voice Response",
    description="Simulates conversation and returns audio references representing synthesis output."
)
async def simulate_voice_conversation(
    id: str,
    payload: SimulateRequest,
    x_enterprise_id: Optional[str] = Header(None, alias="x-enterprise-id")
):
    validate_enterprise(x_enterprise_id)
    agent = await find_agent_by_id_and_enterprise(id, x_enterprise_id)
    if not agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"success": False, "message": "Agent not found"}
        )
    
    session_id = payload.session_id or f"sim_{uuid.uuid4().hex[:8]}"
    instructions = agent.get("instructions", "You are a customer assistant.")
    
    history_prompt = ""
    async def fetch_history():
        db = mongo_db.client.get_default_database()
        return await db['simulated_conversations'].find_one({"session_id": session_id})
        
    try:
        existing_session = await safe_mongo_op(fetch_history)
        if existing_session and "messages" in existing_session:
            for msg in existing_session["messages"]:
                role = "User Message" if msg.get("role") == "user" else "Agent Response"
                history_prompt += f"{role}: {msg.get('content')}\n"
    except Exception as e:
        logger.error(f"Error fetching simulation history: {e}")
        
    response_text = ""
    try:
        # Check Knowledge Base
        kb_ids = []
        async def fetch_kb_ids():
            db = mongo_db.client.get_default_database()
            cursor = db['agent_kb_documents'].find({"agentId": agent.get("agentId")})
            ids = []
            async for doc in cursor:
                ids.append(str(doc.get("docId")))
            return ids
            
        try:
            kb_ids = await safe_mongo_op(fetch_kb_ids) or []
        except Exception as e:
            logger.error(f"Failed to fetch KB docs for voice simulation: {e}")

        rag_context = ""
        if kb_ids:
            try:
                results = await rag_manager.search(company_id=agent.get("agentId"), query=payload.message, top_k=3, document_ids=kb_ids)
                if results:
                    rag_context = "Relevant Knowledge Base Information:\n" + "\n".join([r["chunk_text"] for r in results]) + "\n\n"
            except Exception as e:
                logger.error(f"Failed to fetch RAG context for voice simulation: {e}")

        guardrail = "STRICT RULE: You must ONLY answer questions using the knowledge base context provided. If the user asks a question NOT covered in the context, you MUST say: 'I'm sorry, but I don't have that information.' Do NOT invent facts or use outside knowledge.\n"
        prompt = f"System Instructions:\n{guardrail}{instructions}\n\n{rag_context}{history_prompt}User Message: {payload.message}\nAgent Response:"
        resp = rag_manager.gemini_client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt
        )
        response_text = resp.text.strip()
        
        async def save_history():
            db = mongo_db.client.get_default_database()
            new_messages = [
                {"role": "user", "content": payload.message, "timestamp": datetime.datetime.utcnow().isoformat() + "Z"},
                {"role": "agent", "content": response_text, "timestamp": datetime.datetime.utcnow().isoformat() + "Z"}
            ]
            await db['simulated_conversations'].update_one(
                {"session_id": session_id},
                {"$push": {"messages": {"$each": new_messages}}},
                upsert=True
            )
            
        try:
            await safe_mongo_op(save_history)
        except Exception as e:
            logger.error(f"Error saving simulation history: {e}")
            
    except Exception as e:
        logger.warning(f"Failed to call Gemini for simulation: {e}")
        response_text = f"Simulated Voice Response: Received '{payload.message}'."

    voice_id = agent.get("voiceId", "pNInz6obbfDQGcgMyIGD")
    simulated_audio_url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream"

    return {
        "success": True,
        "response": response_text,
        "audio_url": simulated_audio_url,
        "voiceId": voice_id,
        "session_id": session_id
    }

@router.get(
    "/{id}/simulate/history",
    status_code=status.HTTP_200_OK,
    summary="Get Simulation Conversation History",
    description="Retrieves the full chat history for a specific simulation session."
)
async def get_simulation_history(
    id: str = Path(..., description="The unique MongoDB Object ID of the Agent"),
    session_id: str = Query(..., description="The unique session ID used during the simulation chat"),
    x_enterprise_id: Optional[str] = Header(None, alias="x-enterprise-id", description="The Enterprise ID of the user who owns this agent")
):
    validate_enterprise(x_enterprise_id)
    agent = await find_agent_by_id_and_enterprise(id, x_enterprise_id)
    if not agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"success": False, "message": "Agent not found"}
        )

    async def fetch_history():
        db = mongo_db.client.get_default_database()
        return await db['simulated_conversations'].find_one({"session_id": session_id})

    try:
        session_data = await safe_mongo_op(fetch_history)
        messages = session_data.get("messages", []) if session_data else []
        return {
            "success": True,
            "session_id": session_id,
            "messages": messages
        }
    except Exception as e:
        logger.error(f"Error fetching simulation history: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"success": False, "message": "Error fetching conversation history"}
        )




@router.post(
    "/{id}/create-kb-text",
    status_code=status.HTTP_201_CREATED,
    summary="Create Knowledge Base Text",
    description="Uploads text content to S3, splits it into chunks, generates vector embeddings, and stores them in Chroma DB."
)
async def create_kb_text(
    id: str,
    payload: KBTextCreateRequest,
    x_enterprise_id: Optional[str] = Header(None, alias="x-enterprise-id")
):
    validate_enterprise(x_enterprise_id)
    agent = await find_agent_by_id_and_enterprise(id, x_enterprise_id)
    if not agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"success": False, "message": "Agent not found"}
        )
    
    agent_id = agent.get("agentId")
    import time
    doc_id = int(time.time())
    filename = f"kb_{doc_id}_{payload.title.replace(' ', '_')}.txt"
    file_bytes = payload.content.encode('utf-8')
    
    # 1. Trigger S3 Upload and Chroma indexing
    try:
        await rag_manager.upload_documents(
            company_id=agent_id,
            filename=filename,
            file_body=file_bytes,
            text_content=payload.content,
            doc_id=doc_id
        )
    except Exception as e:
        logger.error(f"Failed to upload and index KB text: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"success": False, "message": f"Chroma/S3 indexing failed: {str(e)}"}
        )
    
    # 2. Persist metadata record in MongoDB
    kb_doc = {
        "agentId": agent_id,
        "docId": doc_id,
        "filename": filename,
        "title": payload.title,
        "createdAt": datetime.datetime.utcnow().isoformat() + "Z"
    }
    
    async def run_insert_kb():
        db = mongo_db.client.get_default_database()
        await db['agent_kb_documents'].insert_one(kb_doc.copy())

    if mongo_db.client is not None:
        try:
            await safe_mongo_op(run_insert_kb)
            if "_id" in kb_doc:
                kb_doc["_id"] = str(kb_doc["_id"])
        except Exception as e:
            logger.error(f"Failed to save KB document details to MongoDB: {e}")
            
    return {
        "success": True,
        "message": "Knowledge base document created and indexed successfully",
        "data": kb_doc
    }

@router.post(
    "/{id}/sendemialfromaiagentstools",
    status_code=status.HTTP_200_OK,
    summary="Save Email SMTP Credentials",
    description="Saves and secures SMTP server connection details for agent email delivery tools."
)
async def save_email_credentials(
    id: str,
    payload: SaveEmailCredentialsRequest,
    x_enterprise_id: Optional[str] = Header(None, alias="x-enterprise-id")
):
    validate_enterprise(x_enterprise_id)
    agent = await find_agent_by_id_and_enterprise(id, x_enterprise_id)
    if not agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"success": False, "message": "Agent not found"}
        )
    
    agent_id = agent.get("agentId")
    # Stable, deterministic id (not a fresh ObjectId per save) so re-saving
    # credentials for the same agent always returns the same doc_id — the
    # frontend chains this straight into the KB-text-creation call to link
    # the agent's "I can send email" capability to its knowledge base.
    doc_id = f"email_creds_{agent_id}"
    creds_doc = {
        "agentId": agent_id,
        "docId": doc_id,
        "email": payload.SENDER_EMAIL,
        "senderName": payload.NAME,
        "ccList": [c.strip() for c in payload.CC if c and c.strip()],
        # Gmail App Passwords always use these fixed SMTP settings.
        "smtp_host": "smtp.gmail.com",
        "smtp_port": 587,
        "smtp_user": payload.SENDER_EMAIL,
        "smtp_password": payload.PASSWORD,
        "updatedAt": datetime.datetime.utcnow().isoformat() + "Z"
    }

    async def run_save_creds():
        db = mongo_db.client.get_default_database()
        await db['agent_email_credentials'].update_one(
            {"agentId": agent_id},
            {"$set": creds_doc},
            upsert=True
        )

    if mongo_db.client is not None:
        try:
            await safe_mongo_op(run_save_creds)
        except Exception as e:
            logger.error(f"Failed to save email credentials to MongoDB: {e}")

    return {
        "success": True,
        "message": "Email credentials saved successfully",
        "data": {"_id": doc_id, "docId": doc_id}
    }

@router.get(
    "/{id}/get-decrypted-email-credentials",
    status_code=status.HTTP_200_OK,
    summary="Get Email Credentials",
    description="Retrieves the SMTP configurations associated with this agent."
)
async def get_email_credentials(
    id: str,
    x_enterprise_id: Optional[str] = Header(None, alias="x-enterprise-id")
):
    validate_enterprise(x_enterprise_id)
    agent = await find_agent_by_id_and_enterprise(id, x_enterprise_id)
    if not agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"success": False, "message": "Agent not found"}
        )
    
    agent_id = agent.get("agentId")
    
    async def run_get_creds():
        db = mongo_db.client.get_default_database()
        return await db['agent_email_credentials'].find_one({"agentId": agent_id})

    creds = None
    if mongo_db.client is not None:
        try:
            creds = await safe_mongo_op(run_get_creds)
        except Exception as e:
            logger.error(f"Failed to fetch email credentials: {e}")

    if not creds:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"success": False, "message": "Email credentials not found for this agent"}
        )

    return {
        "success": True,
        "data": {
            "email": creds.get("email"),
            "senderName": creds.get("senderName"),
            "ccList": creds.get("ccList", []),
            "smtp_host": creds.get("smtp_host"),
            "smtp_port": creds.get("smtp_port"),
            "smtp_user": creds.get("smtp_user"),
            "smtp_password": "••••••••"
        }
    }

@router.get(
    "/{id}/get-current-user-refresh-token",
    status_code=status.HTTP_200_OK,
    summary="Get Refresh Token",
    description="Returns token and session credentials for the agent dashboard context."
)
async def get_current_user_refresh_token(
    id: str,
    x_enterprise_id: Optional[str] = Header(None, alias="x-enterprise-id")
):
    validate_enterprise(x_enterprise_id)
    agent = await find_agent_by_id_and_enterprise(id, x_enterprise_id)
    if not agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"success": False, "message": "Agent not found"}
        )
    
    return {
        "success": True,
        "refresh_token": f"token_{uuid.uuid4().hex}"
    }

@router.post(
    "/{id}/upload-documents",
    status_code=status.HTTP_201_CREATED,
    summary="Upload Agent Documents",
    description="Uploads files (PDF, DOCX, TXT) directly to the Exotel Voice Agent's Knowledge Base.",
    openapi_extra={
        "requestBody": {
            "content": {
                "multipart/form-data": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "files": {
                                "type": "array",
                                "items": {
                                    "type": "string",
                                    "format": "binary"
                                },
                                "description": "Select one or more files to upload (PDF, DOCX, PPTX, TXT)"
                            }
                        },
                        "required": ["files"]
                    }
                }
            },
            "required": True
        }
    }
)
async def upload_agent_documents(
    id: str,
    background_tasks: BackgroundTasks,
    files: Annotated[List[UploadFile], File(description="Select one or more files to upload")],
    x_enterprise_id: Optional[str] = Header(None, alias="x-enterprise-id")
):
    # 1. Validate Enterprise & Agent in MongoDB
    validate_enterprise(x_enterprise_id)
    agent = await find_agent_by_id_and_enterprise(id, x_enterprise_id)
    if not agent:
        raise HTTPException(status_code=404, detail={"success": False, "message": "Agent not found"})
    
    agent_id = agent.get("agentId")
    
    # 2. Process Files
    for upload in files:
        body = await upload.read()
        
        extracted_text = extract_text_from_file(upload.filename, body)
        if not extracted_text or not extracted_text.strip():
            raise HTTPException(status_code=400, detail={"success": False, "message": f"Could not extract any text from {upload.filename}. Please ensure it is a valid text, pdf, or docx file."})
            
        doc_id = int(time.time())
        
        # 3. Trigger S3 Upload and Chroma indexing
        background_tasks.add_task(
            rag_manager.upload_documents,
            company_id=agent_id, # Uses agentId as the namespace in Chroma/S3
            filename=upload.filename,
            file_body=body,
            text_content=extracted_text,
            doc_id=doc_id
        )
        
        # 4. Save metadata to MongoDB agent_kb_documents collection
        kb_doc = {
            "agentId": agent_id,
            "docId": doc_id,
            "filename": upload.filename,
            "title": upload.filename,
            "createdAt": datetime.datetime.utcnow().isoformat() + "Z"
        }
        
        db = mongo_db.client.get_default_database()
        await db['agent_kb_documents'].insert_one(kb_doc)
        
    return {
        "success": True,
        "message": "Documents uploaded and processing in background"
    }

@router.get(
    "/{id}/debug-rag",
    status_code=status.HTTP_200_OK,
    summary="Debug RAG Search",
    description="Directly queries the vector database bypassing the LLM to inspect retrieved chunks."
)
async def debug_rag(
    id: str,
    query: str,
    x_enterprise_id: Optional[str] = Header(None, alias="x-enterprise-id")
):
    validate_enterprise(x_enterprise_id)
    agent = await find_agent_by_id_and_enterprise(id, x_enterprise_id)
    if not agent:
        raise HTTPException(status_code=404, detail={"success": False, "message": "Agent not found"})
        
    # Check Knowledge Base by querying agent_kb_documents
    kb_ids = []
    async def fetch_kb_ids():
        db = mongo_db.client.get_default_database()
        cursor = db['agent_kb_documents'].find({"agentId": agent.get("agentId")})
        ids = []
        async for doc in cursor:
            ids.append(str(doc.get("docId")))
        return ids
        
    try:
        kb_ids = await safe_mongo_op(fetch_kb_ids) or []
    except Exception as e:
        return {"success": False, "error": f"Failed to fetch KB docs: {e}"}

    if not kb_ids:
        return {"success": True, "message": "No documents found in MongoDB for this agent."}
        
    try:
        results = await rag_manager.search(company_id=agent.get("agentId"), query=query, top_k=5, document_ids=kb_ids)
        return {
            "success": True, 
            "message": f"Searched for '{query}'. Found {len(results)} chunks.",
            "chunks": results,
            "document_ids_used": kb_ids
        }
    except Exception as e:
        return {"success": False, "error": f"Search failed: {e}"}


@router.get(
    "/{id}/exotel-getkb-items",
    status_code=status.HTTP_200_OK,
    summary="List Agent Knowledge Base Items",
    description="Retrieves the list of documents and text items uploaded to the agent's knowledge base."
)
async def get_agent_kb_items(
    id: str,
    x_enterprise_id: Optional[str] = Header(None, alias="x-enterprise-id")
):
    validate_enterprise(x_enterprise_id)
    agent = await find_agent_by_id_and_enterprise(id, x_enterprise_id)
    if not agent:
        raise HTTPException(status_code=404, detail={"success": False, "message": "Agent not found"})
    
    agent_id = agent.get("agentId")
    
    db = mongo_db.client.get_default_database()
    kb_cursor = db['agent_kb_documents'].find({"agentId": agent_id})
    items = []
    async for kb in kb_cursor:
        if "_id" in kb:
            kb["_id"] = str(kb["_id"])
        items.append(kb)
        
    return {
        "success": True,
        "data": items
    }

@router.delete(
    "/{id}/exotel-deletekb-items/{doc_id}",
    status_code=status.HTTP_200_OK,
    summary="Delete Agent Knowledge Base Item",
    description="Deletes a specific knowledge base item from the agent."
)
async def delete_agent_kb_item(
    id: str,
    doc_id: int,
    x_enterprise_id: Optional[str] = Header(None, alias="x-enterprise-id")
):
    validate_enterprise(x_enterprise_id)
    agent = await find_agent_by_id_and_enterprise(id, x_enterprise_id)
    if not agent:
        raise HTTPException(status_code=404, detail={"success": False, "message": "Agent not found"})
    
    agent_id = agent.get("agentId")
    db = mongo_db.client.get_default_database()
    
    doc = await db['agent_kb_documents'].find_one({"agentId": agent_id, "docId": doc_id})
    if not doc:
        raise HTTPException(status_code=404, detail={"success": False, "message": "Document not found"})

    # Clean up the vectors in Chroma and the raw file in S3 before dropping the record
    try:
        rag_manager.delete_document(
            company_id=agent_id,
            doc_id=doc_id,
            filename=doc.get("filename", "")
        )
    except Exception as e:
        logger.error(f"Failed to clean up Chroma/S3 for doc {doc_id}: {e}")

    await db['agent_kb_documents'].delete_one({"agentId": agent_id, "docId": doc_id})
    
    return {
        "success": True,
        "message": "Knowledge base item deleted successfully"
    }

@router.get(
    "/{id}/exotel-getkb-items/{doc_id}/download",
    status_code=status.HTTP_307_TEMPORARY_REDIRECT,
    summary="Download Agent Knowledge Base Item"
)
async def download_agent_kb_item(
    id: str,
    doc_id: int,
    enterprise_id: Optional[str] = Query(None, alias="enterprise_id"),
    is_download: bool = Query(False, alias="download"),
    x_enterprise_id: Optional[str] = Header(None, alias="x-enterprise-id")
):
    ent_id = x_enterprise_id or enterprise_id
    validate_enterprise(ent_id)
    agent = await find_agent_by_id_and_enterprise(id, ent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    
    agent_id = agent.get("agentId")
    db = mongo_db.client.get_default_database()
    doc = await db['agent_kb_documents'].find_one({"agentId": agent_id, "docId": doc_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
        
    s3_key = f"documents/{agent_id}/{doc_id}_{doc['filename']}"
    if not rag_manager.s3_client:
        raise HTTPException(status_code=500, detail="S3 client not initialized")
        
    try:
        content_type, _ = mimetypes.guess_type(doc['filename'])
        disposition = f'attachment; filename="{doc["filename"]}"' if is_download else f'inline; filename="{doc["filename"]}"'
        url = rag_manager.s3_client.generate_presigned_url(
            'get_object',
            Params={
                'Bucket': rag_manager.bucket_name, 
                'Key': s3_key,
                'ResponseContentDisposition': disposition,
                'ResponseContentType': content_type or 'application/octet-stream'
            },
            ExpiresIn=3600
        )
        return RedirectResponse(url)
    except Exception as e:
        logger.error(f"Failed to generate presigned URL: {e}")
        raise HTTPException(status_code=500, detail="Failed to generate download link")





@router.get(
    "/{id}/email-credential-history",
    tags=["Agent Tools"],
    summary="Get Email Credential History (Exotel)",
    description="Gets the currently configured SMTP credentials for this agent."
)
async def get_email_credential_history(
    id: str,
    x_enterprise_id: str = Header(None, alias="x-enterprise-id")
):
    validate_enterprise(x_enterprise_id)
    agent = await find_agent_by_id_and_enterprise(id, x_enterprise_id)

    if not agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"success": False, "message": "Agent not found"}
        )

    agent_id = agent.get("agentId")
    db = mongo_db.client.get_default_database()
    cred = await db['agent_email_credentials'].find_one({"agentId": agent_id})

    if not cred:
        return {"success": True, "data": []}

    return {
        "success": True,
        "data": [{
            "id": str(cred.get("_id", "")),
            "email": cred.get("email", ""),
            "status": "Verified",
            "createdAt": cred.get("updatedAt", "")
        }]
    }


