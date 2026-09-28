import logging
import re
from bson import ObjectId
from core.mongo_manager import mongo_db
from controllers.bot_controller import get_company_id_by_phone

logger = logging.getLogger(__name__)

def _sanitize_agent_doc(agent: dict | None) -> dict | None:
    if not agent:
        return None
    cleaned = dict(agent)
    for k, v in list(cleaned.items()):
        if isinstance(v, ObjectId):
            cleaned[k] = str(v)
    # Ensure per-agent voice_bot_mode is set (defaulting to 'modular' if omitted).
    # Also accept "voiceBotMode" (camelCase) — the admin portal's Node backend writes
    # the mode under that key, which this snake_case lookup was silently missing,
    # so every agent set to Realtime kept resolving to the "modular" default.
    resolved_mode = str(
        cleaned.get("voice_bot_mode") or cleaned.get("voiceBotMode") or cleaned.get("mode") or "modular"
    ).lower().strip()
    if resolved_mode not in ["modular", "realtime"]:
        resolved_mode = "modular"
    cleaned.pop("mode", None)
    cleaned.pop("voiceBotMode", None)
    cleaned["voice_bot_mode"] = resolved_mode
    return cleaned

async def resolve_agent_config(destination_id: str) -> dict | None:
    """
    Resolves the voice agent configuration based on the incoming SIP destination ID or phone number.
    """
    if not mongo_db.client or not destination_id:
        return None
        
    try:
        db = mongo_db.client.get_default_database()
        collections_to_search = ['exotel_agents', 'agents']
        
        for coll_name in collections_to_search:
            agents_collection = db[coll_name]
            
            # 1. Search directly by custom agentId
            agent = await agents_collection.find_one({"agentId": destination_id, "status": "active"})
            if agent:
                logger.info(f"🎯 Dynamic agent resolved by Agent ID in {coll_name}: {agent.get('name')} ({agent.get('agentId')})")
                return _sanitize_agent_doc(agent)
    
            # 2. Search by MongoDB _id (string or ObjectId)
            agent = await agents_collection.find_one({"_id": destination_id, "status": "active"})
            if not agent and ObjectId.is_valid(destination_id):
                agent = await agents_collection.find_one({"_id": ObjectId(destination_id), "status": "active"})
            if agent:
                logger.info(f"🎯 Dynamic agent resolved by MongoDB _id in {coll_name}: {agent.get('name')} ({agent.get('agentId')})")
                return _sanitize_agent_doc(agent)
    
            # 3. Search by assigned virtualNumber in MongoDB (exact match)
            agent = await agents_collection.find_one({"virtualNumber": destination_id})
            if agent:
                logger.info(f"🎯 Dynamic agent resolved by Virtual Number in {coll_name}: {agent.get('name')} ({agent.get('agentId')})")
                return _sanitize_agent_doc(agent)
    
            # 4. Search by digits of virtualNumber (last 10 digits regex match)
            clean_10 = re.sub(r'\D', '', str(destination_id))[-10:] if destination_id else ""
            if clean_10 and len(clean_10) >= 7:
                agent = await agents_collection.find_one({"virtualNumber": {"$regex": clean_10}})
                if agent:
                    logger.info(f"🎯 Dynamic agent resolved by Virtual Number Regex ({clean_10}) in {coll_name}: {agent.get('name')} ({agent.get('agentId')})")
                    return _sanitize_agent_doc(agent)
                
            # 5. Search by phone number mapping (using Company SQL lookup)
            company_id = await get_company_id_by_phone(destination_id)
            if company_id:
                # Load the active agent bound to this phone or the most recently updated active agent
                if clean_10:
                    agent = await agents_collection.find_one({
                        "$and": [
                            {"virtualNumber": {"$regex": clean_10}},
                            {"$or": [
                                {"enterprise": company_id},
                                {"company_id": company_id}
                            ]},
                            {"status": "active"}
                        ]
                    })
                if not agent:
                    agent = await agents_collection.find_one(
                        {"enterprise": company_id, "status": "active"},
                        sort=[("updatedAt", -1)]
                    )
                if not agent:
                    agent = await agents_collection.find_one(
                        {"company_id": company_id, "status": "active"},
                        sort=[("updatedAt", -1)]
                    )
                if agent:
                    logger.info(f"🎯 Dynamic agent resolved for company {company_id} via phone {destination_id} in {coll_name}: {agent.get('name')}")
                    return _sanitize_agent_doc(agent)

    except Exception as e:
        logger.error(f"❌ Failed to resolve agent config: {e}")
        
    return None

async def get_company_name(company_id: str) -> str | None:
    """Helper to query MongoDB database for company name by ID"""
    if not mongo_db.client:
        return None
    try:
        db = mongo_db.client.get_default_database()
        companies_collection = db['companies']
        company = await companies_collection.find_one({"company_id": company_id})
        if company:
            return company.get("name")
    except Exception as e:
        logger.error(f"Failed to lookup company name in MongoDB for ID {company_id}: {e}")
    return None

