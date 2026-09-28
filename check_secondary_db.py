import asyncio
import os

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

load_dotenv()


async def check():
    db_url = os.getenv("DB_URL_SECONDARY") or os.getenv("DB_URL") or os.getenv("DATABASE_URL")
    if not db_url:
        raise SystemExit("Set DB_URL_SECONDARY or DB_URL in your environment / .env — do not hardcode credentials.")

    agent_id = os.getenv("AGENT_ID", "")
    client = AsyncIOMotorClient(db_url)
    db = client.get_default_database()
    query = {"agentId": agent_id} if agent_id else {}
    docs = await db['agent_kb_documents'].find(query).to_list(length=100)
    print(f"Found {len(docs)} documents")

asyncio.run(check())
