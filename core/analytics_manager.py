import logging
import json
import datetime
import asyncio
import time
from typing import Optional
from config import Config
from core.mongo_manager import mongo_db

logger = logging.getLogger(__name__)

async def extract_call_analytics(transcript_list: list) -> dict:
    """Uses Gemini to analyze the transcript and extract structured analytics fields."""
    # Convert transcript to text block
    formatted_transcript = ""
    for turn in transcript_list:
        role = turn.get("role", "user").upper()
        msg = turn.get("msg", "")
        # Skip internal system prompt messages if present
        if msg.startswith("System:"):
            continue
        formatted_transcript += f"{role}: {msg}\n"
        
    prompt = f"""
Analyze the following telephone conversation transcript between an AI sales agent (BOT) and a customer (USER) with high accuracy and strict adherence to the facts. Do not assume or extrapolate any information.

Extract the following structured fields in JSON format:
1. "name": The full name of the customer/caller. Only extract if explicitly stated by the user. Do not guess. If not mentioned, set to "Not provided".
2. "address": The physical address or location details of the customer. Do not guess. If not mentioned, set to "Not provided".
3. "email_id": The email address of the customer. Ensure it is a valid email format or spelled-out email (e.g. "name at gmail dot com" should be formatted as "name@gmail.com"). If not mentioned, set to "Not provided".
4. "provided_phone_no": The phone number provided or mentioned by the customer during the conversation. Format it as digits only. If not mentioned, set to "Not provided".
5. "caller_meeting_consent": Strictly set to "Yes" if the customer clearly agreed, consented, or said yes to scheduling a meeting, demo, or follow-up call. Otherwise, set to "No".
6. "customer_request_raised_field_visit": Strictly set to "Yes" if the customer explicitly requested a physical field visit, site visit, or physical meeting. Otherwise, set to "No".
7. "business_interest": The specific category, product, or service the customer showed interest in (e.g. Healthcare, Retail, AI Bot, Consulting). If not mentioned, set to "Not provided".
8. "call_summary": A concise, factual 2-3 sentence summary of the key discussion points and final outcome. Avoid vague language.

Guardrails:
- Strict Factuality: If any information is not explicitly present in the transcript, set it to "Not provided" or "No". Never invent data.
- User Intent: Do not count general conversation setup or system prompts as part of the customer's provided details.
- Output Format: Return ONLY a valid JSON object. Do not wrap the JSON in markdown code blocks or backticks.

Transcript:
{formatted_transcript}
"""
    try:
        import google.genai as genai
        from google.genai import types
        import os
        
        # Initialize client using Config.GEMINI_API_KEY or Vertex AI
        gcp_key_path = "project-gcp-key.json" if os.path.exists("project-gcp-key.json") else "/app/project-gcp-key.json"
        if os.path.exists(gcp_key_path):
            os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = gcp_key_path
            client = genai.Client(vertexai=True)
        else:
            client = genai.Client(api_key=Config.GEMINI_API_KEY)
            
        response = await client.aio.models.generate_content(
            model=Config.GEMINI_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.0
            )
        )
        content_text = response.text.strip()
        # Clean potential markdown output from Gemini if response_mime_type is not honored
        if content_text.startswith("```"):
            if "json" in content_text:
                content_text = content_text.split("json")[1]
            else:
                content_text = content_text.split("```")[1]
            content_text = content_text.split("```")[0].strip()
            
        data = json.loads(content_text)
        # Validate keys
        keys = ["name", "address", "email_id", "provided_phone_no", "caller_meeting_consent", 
                "customer_request_raised_field_visit", "business_interest", "call_summary"]
        for k in keys:
            if k not in data:
                data[k] = "Not provided" if k not in ["caller_meeting_consent", "customer_request_raised_field_visit"] else "No"
        return data
    except Exception as e:
        logger.error(f"Failed to extract call analytics via Gemini: {e}")
        return {
            "name": "Not provided",
            "address": "Not provided",
            "email_id": "Not provided",
            "provided_phone_no": "Not provided",
            "caller_meeting_consent": "No",
            "customer_request_raised_field_visit": "No",
            "business_interest": "Not provided",
            "call_summary": "Failed to analyze transcript"
        }

async def save_enriched_call_log(
    call_id: str,
    duration: float,
    transcript: list,
    to_phone: str,
    direction: str,
    agent_name: Optional[str] = None,
    company_name: Optional[str] = None,
    agent_id: Optional[str] = None,
    enterprise_id: Optional[str] = None,
    agent_mongo_id: Optional[str] = None,
    outbound_call_sid: Optional[str] = None,
    outbound_customer_name: Optional[str] = None,
    outbound_phone_number: Optional[str] = None
):
    """Enriches transcript using Gemini and saves the complete call analytics document to MongoDB."""
    try:
        logger.info(f"🧠 Generating Gemini analytics extraction for call {call_id}...")
        analytics = await extract_call_analytics(transcript)
        
        utc_now = datetime.datetime.utcnow()
        # Convert UTC to Indian Standard Time (IST: UTC + 5:30)
        ist_now = utc_now + datetime.timedelta(hours=5, minutes=30)
        
        # Format date and time fields in IST for the analytics tables
        call_date = ist_now.strftime("%Y-%m-%d")
        call_time = ist_now.strftime("%I:%M %p")
        
        call_log = {
            "call_id": call_id,
            "call_date": call_date,
            "time": call_time,
            "duration_seconds": round(duration, 2),
            "duration": f"{round(duration, 2)}s",
            "agent_name": agent_name or Config.SALES_BOT_NAME,
            "company_name": company_name or Config.COMPANY_NAME,
            # Agent identifiers — store both UUID agentId and Mongo _id for flexible querying
            "agent_id": agent_id,
            "agentId": agent_id,
            "agent_mongo_id": agent_mongo_id,
            # Enterprise identifier — stored under multiple keys to match CRM query patterns
            "enterprise_id": enterprise_id,
            "enterprise": enterprise_id,
            "company_id": enterprise_id,
            # Call metadata
            "direction": direction or "inbound",
            "caller_phone_no": to_phone,
            # Prefer the phone number the customer actually stated during the call; fall back to
            # the number we dialed (known upfront for outbound campaigns) if Gemini found none.
            "lead_phone_no": analytics.get("provided_phone_no") if analytics.get("provided_phone_no", "Not provided") != "Not provided" else (outbound_phone_number or "Not provided"),
            "timestamp": ist_now,
            # Links this transcript back to the outbound_calls campaign record that triggered it
            # (Exotel's outbound call_sid), so campaign results can be joined to a conversation.
            "outbound_call_sid": outbound_call_sid,

            # Transcript and summary
            "transcript": transcript,
            "messages_count": len(transcript),

            # Extracted Fields from Gemini
            "name": analytics.get("name") if analytics.get("name", "Not provided") != "Not provided" else (outbound_customer_name or "Not provided"),
            "address": analytics.get("address", "Not provided"),
            "email_id": analytics.get("email_id", "Not provided"),
            "caller_meeting_consent": analytics.get("caller_meeting_consent", "No"),
            "customer_request_raised_field_visit": analytics.get("customer_request_raised_field_visit", "No"),
            "business_interest": analytics.get("business_interest", "Not provided"),
            "call_summary": analytics.get("call_summary", "Not provided")
        }
        
        await mongo_db.save_call_log(call_log)
        logger.info(f"✅ Call log saved — call_id={call_id}, agent_id={agent_id}, enterprise_id={enterprise_id}, direction={direction}")
    except Exception as e:
        logger.error(f"❌ Error generating or saving enriched call log: {e}")
