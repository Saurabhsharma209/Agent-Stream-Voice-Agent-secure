#!/usr/bin/env python3
import asyncio, sys, os
from pathlib import Path
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv
load_dotenv()
from motor.motor_asyncio import AsyncIOMotorClient

DB_URL = os.getenv("DB_URL", "")
if not DB_URL:
    print("ERROR: DB_URL not found in .env")
    sys.exit(1)


async def migrate():
    print("[*] Connecting to MongoDB...")
    client = AsyncIOMotorClient(DB_URL)
    db = client.get_default_database()
    calls_coll = db["Agent_Stream_CallsLogs"]
    agents_coll = db["exotel_agents"]

    # Build lookup maps from exotel_agents
    print("[*] Loading agents...")
    agent_map_by_uuid = {}
    agent_map_by_mongoid = {}
    async for agent in agents_coll.find({}):
        uid = agent.get("agentId")
        mid = str(agent.get("_id", ""))
        if uid:
            agent_map_by_uuid[uid] = agent
        if mid:
            agent_map_by_mongoid[mid] = agent
    print(f"    Loaded: {len(agent_map_by_uuid)} by UUID, {len(agent_map_by_mongoid)} by Mongo _id")

    # Find all call logs missing enterprise_id
    missing_filter = {
        "$or": [
            {"enterprise_id": {"$exists": False}},
            {"enterprise_id": None},
            {"enterprise_id": ""},
        ]
    }
    total = await calls_coll.count_documents(missing_filter)
    print(f"\n[*] Call logs missing enterprise_id: {total}")

    if total == 0:
        print("[OK] Nothing to migrate.")
        client.close()
        return

    patched = skipped = not_found = 0

    async for doc in calls_coll.find(missing_filter):
        doc_id = doc["_id"]
        agent = None

        raw_aid = doc.get("agent_id") or doc.get("agentId") or doc.get("agent")
        raw_mid = doc.get("agent_mongo_id")

        if raw_aid and raw_aid in agent_map_by_uuid:
            agent = agent_map_by_uuid[raw_aid]
        elif raw_aid and raw_aid in agent_map_by_mongoid:
            agent = agent_map_by_mongoid[raw_aid]
        elif raw_mid and raw_mid in agent_map_by_mongoid:
            agent = agent_map_by_mongoid[raw_mid]

        if not agent:
            not_found += 1
            continue

        eid = (agent.get("enterprise") or agent.get("createdBy") or agent.get("enterprise_id") or "")
        auuid = agent.get("agentId") or ""
        amid = str(agent.get("_id", ""))

        if not eid:
            skipped += 1
            continue

        patch = {
            "enterprise_id":  eid,
            "enterprise":     eid,
            "company_id":     eid,
            "agentId":        auuid,
            "agent_id":       auuid or doc.get("agent_id") or "",
            "agent_mongo_id": amid,
        }
        if not doc.get("direction"):
            patch["direction"] = "inbound"

        await calls_coll.update_one({"_id": doc_id}, {"$set": patch})
        patched += 1
        if patched % 10 == 0:
            print(f"    ... {patched} patched so far")

    print(f"\n[DONE] Patched={patched}, Skipped={skipped}, NoMatch={not_found}, Total={total}")
    client.close()


asyncio.run(migrate())
