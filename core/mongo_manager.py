from motor.motor_asyncio import AsyncIOMotorClient
from config import Config
import logging

logger = logging.getLogger(__name__)

class MongoManager:
    def __init__(self):
        try:
            if not Config.DB_URL:
                logger.warning("⚠️ DB_URL not set in configurations. Call logs will not be saved.")
                self.client = None
                self.call_logs_collection = None
                return
            
            self.client = AsyncIOMotorClient(Config.DB_URL)
            self.db = self.client.get_default_database() # Uses the database name in the connection string
            self.call_logs_collection = self.db['Agent_Stream_CallsLogs']
            logger.info("✅ Connected to MongoDB Atlas successfully")
        except Exception as e:
            logger.error(f"❌ MongoDB connection failed: {e}")
            self.client = None
            self.call_logs_collection = None

    async def save_call_log(self, call_data: dict):
        if self.call_logs_collection is None:
            logger.warning("⚠️ Cannot save call log. MongoDB is not connected.")
            return
            
        try:
            await self.call_logs_collection.insert_one(call_data)
            logger.info(f"✅ Call log {call_data.get('call_id')} successfully saved to MongoDB")
            
            # Also update matching outbound_calls document from 'initiated' to completed/failed status
            raw_cid = str(call_data.get("call_id") or "").strip()
            clean_cid = raw_cid.split("@")[0].strip()
            if clean_cid:
                status_str = call_data.get("status", "completed")
                duration_val = call_data.get("duration_seconds") or call_data.get("duration") or 0
                summary_text = call_data.get("call_summary") or call_data.get("summary") or ""
                transcript_list = call_data.get("transcript") or call_data.get("messages") or []
                
                update_fields = {
                    "status": status_str,
                    "duration": duration_val,
                    "durationSeconds": duration_val,
                    "transcript": transcript_list,
                    "call_summary": summary_text,
                    "summary": summary_text
                }
                
                res = await self.db['outbound_calls'].update_many(
                    {"$or": [
                        {"call_sid": clean_cid},
                        {"call_id": clean_cid},
                        {"call_sid": raw_cid},
                        {"call_id": raw_cid}
                    ]},
                    {"$set": update_fields}
                )
                
                # Fallback: Exotel SIP trunks often omit the custom CallSid in headers. 
                # If no records were updated, match by the customer's phone number (to_phone from SIP).
                if res.modified_count == 0 and call_data.get("to_phone") and call_data.get("to_phone") != "default":
                    to_phone_raw = str(call_data.get("to_phone"))
                    phone_digits = "".join(filter(str.isdigit, to_phone_raw))
                    if len(phone_digits) >= 10:
                        last_10 = phone_digits[-10:]
                        fallback_res = await self.db['outbound_calls'].update_one(
                            {
                                "phone_number": {"$regex": last_10},
                            },
                            {"$set": update_fields},
                            sort=[("_id", -1)] # Most recent call for this customer
                        )
                        if fallback_res.modified_count > 0:
                            logger.info(f"✅ Fallback update successful! Linked transcript to outbound_calls via customer number '{last_10}'")
                        else:
                            logger.warning(f"⚠️ Fallback update failed: No outbound_calls record found for customer number '{last_10}'")
                elif res.modified_count > 0:
                    logger.info(f"✅ Updated {res.modified_count} outbound_calls records for Call SID {clean_cid} to status '{status_str}'")
        except Exception as e:
            logger.error(f"❌ Failed to save call log to MongoDB: {e}")

# Global instance
mongo_db = MongoManager()
