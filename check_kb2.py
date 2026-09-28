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
    # Inspect collections / docs as needed using env-configured credentials only.
    print("Connected. Collections:", await db.list_collection_names())

asyncio.run(check())
