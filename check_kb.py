import asyncio
import os

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

load_dotenv()


async def check():
    db_url = os.getenv("DB_URL") or os.getenv("DATABASE_URL")
    if not db_url:
        raise SystemExit("Set DB_URL (or DATABASE_URL) in your environment / .env — do not hardcode credentials.")

    client = AsyncIOMotorClient(db_url)
    db = client.get_default_database()
    docs = await db['agent_kb_documents'].find().to_list(length=10)
    for doc in docs:
        print(doc)
    print(f"Total docs: {len(docs)}")

asyncio.run(check())
