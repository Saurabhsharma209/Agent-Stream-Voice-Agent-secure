import asyncio
import os

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

load_dotenv()


async def check():
    db_url = os.getenv("DB_URL") or os.getenv("DATABASE_URL")
    if not db_url:
        raise SystemExit("Set DB_URL (or DATABASE_URL) in your environment / .env — do not hardcode credentials.")

    agent_id = os.getenv("AGENT_ID")
    if not agent_id:
        raise SystemExit("Set AGENT_ID in your environment / .env")

    client = AsyncIOMotorClient(db_url)
    db = client.get_default_database()
    collections_to_check = ["agents", "exotel_agents", "modernexotelaiagents", "modernaiagents"]
    for coll_name in collections_to_check:
        coll = db[coll_name]
        doc = await coll.find_one({"_id": agent_id}) or await coll.find_one({"id": agent_id})
        print(f"{coll_name}:", "found" if doc else "not found")

asyncio.run(check())
