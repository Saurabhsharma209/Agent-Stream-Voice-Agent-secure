#!/usr/bin/env python3
"""
Exotel Outbound Call REST API Integration
Makes outbound calls via Exotel's REST API (simpler than SIP outbound)

Flow:
1. Your code calls make_outbound_call()
2. We POST to Exotel REST API with customer number
3. Exotel dials the customer
4. Customer answers
5. Exotel routes call back to your inbound vSIP (port 5060)
6. Your bot answers and converses (existing inbound flow)
"""

import asyncio
import logging
import aiohttp
import json
from typing import Dict, Any, Optional
from config import Config

logger = logging.getLogger(__name__)

class ExotelOutboundAPI:
    """Handle outbound calls via Exotel REST API"""
    
    def __init__(self):
        """Initialize Exotel API client"""
        # Get from Exotel dashboard configuration
        self.api_key = Config.EXOTEL_API_KEY
        self.api_token = Config.EXOTEL_API_TOKEN
        self.account_sid = Config.EXOTEL_ACCOUNT_SID
        self.exotel_number = Config.EXOTEL_FROM_NUMBER  # Your virtual number
        subdomain = getattr(Config, "EXOTEL_SUBDOMAIN", "api.in.exotel.com") or "api.in.exotel.com"
        self.api_base_url = f"https://{subdomain}/v1"
        
        # Prepare Base64 Basic Auth headers
        import base64
        auth_str = f"{self.api_key}:{self.api_token}"
        self.auth_header = "Basic " + base64.b64encode(auth_str.encode('utf-8')).decode('utf-8')
        
        logger.info("🔌 Exotel Outbound API initialized")
        logger.info(f"📞 From Number: {self.exotel_number}")
        logger.info(f"🌐 Base URL: {self.api_base_url}")
    
    async def make_outbound_call(
        self,
        phone_number: str,
        greeting_text: str = None,
        context: Dict[str, Any] = None,
        caller_id: Optional[str] = None
    ) -> Optional[str]:
        """
        Make an outbound call to a customer using form-urlencoded POST
        """
        try:
            # Format phone number for Exotel Connect API
            # Standard formatting: prefix the 10 digits with a 0 as per documentation
            clean_number = phone_number.strip().replace(" ", "").replace("-", "")
            if clean_number.startswith("+91"):
                clean_number = "0" + clean_number[3:]
            elif clean_number.startswith("91") and len(clean_number) == 12:
                clean_number = "0" + clean_number[2:]
            elif len(clean_number) == 10 and clean_number.isdigit():
                clean_number = "0" + clean_number

            # Determine virtual number (CallerId & To) for Exotel Connect API
            effective_caller_id = None
            if caller_id and str(caller_id).strip():
                clean_cid = str(caller_id).strip().replace(" ", "").replace("-", "")
                if clean_cid.startswith("+91"):
                    clean_cid = "0" + clean_cid[3:]
                elif clean_cid.startswith("91") and len(clean_cid) == 12:
                    clean_cid = "0" + clean_cid[2:]
                elif len(clean_cid) == 10 and clean_cid.isdigit():
                    clean_cid = "0" + clean_cid
                effective_caller_id = clean_cid

            if not effective_caller_id:
                logger.error("❌ Outbound call aborted: No agent virtual number (caller_id) provided. Default virtual number fallback is disabled.")
                return None

            logger.info(f"📤 Making outbound call: Customer={clean_number}, Agent Virtual Number/CallerId={effective_caller_id}")
            
            # Connect Two Numbers endpoint
            url = f"{self.api_base_url}/Accounts/{self.account_sid}/Calls/connect.json"
            
            # Exotel API parameters for Connect leg
            payload = {
                "From": clean_number,
                "To": effective_caller_id,
                "CallerId": effective_caller_id,
                "CallType": "trans"
            }
            
            if context:
                # NOTE: previously hardcoded port 5000, but the FastAPI app actually listens on
                # Config.SERVER_PORT (5002 by default) — the mismatch meant Exotel's status
                # callback could never reach this server, so `status`/`duration` on outbound_calls
                # never updated automatically (only manual polling of GET /status/{sid} worked).
                payload["StatusCallback"] = f"http://{Config.SIP_PUBLIC_IP}:{Config.SERVER_PORT}/api/v1/calls/webhook"
            
            headers = {
                "Authorization": self.auth_header,
                "Content-Type": "application/x-www-form-urlencoded"
            }
            
            # Make API call using data=payload (x-www-form-urlencoded)
            async with aiohttp.ClientSession() as session:
                async with session.post(url, data=payload, headers=headers) as resp:
                    if resp.status == 200 or resp.status == 201:
                        data = await resp.json()
                        call_sid = data.get("Call", {}).get("Sid")
                        
                        logger.info(f"✅ Outbound call created")
                        logger.info(f"📞 Target: {phone_number}")
                        logger.info(f"🆔 Call SID: {call_sid}")
                        logger.info(f"📊 Status: {data.get('Call', {}).get('Status')}")
                        
                        return call_sid
                    else:
                        error_data = await resp.text()
                        logger.error(f"❌ Exotel API error: {resp.status}")
                        logger.error(f"Response: {error_data}")
                        return None
        
        except Exception as e:
            logger.error(f"❌ Error making outbound call: {e}")
            return None
    
    async def get_call_status(self, call_sid: str) -> Optional[Dict[str, Any]]:
        """
        Get the status of an outbound call
        """
        try:
            url = f"{self.api_base_url}/Accounts/{self.account_sid}/Calls/{call_sid}.json"
            
            headers = {
                "Authorization": self.auth_header,
                "Accept": "application/json"
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        call_info = data.get("Call", {})
                        
                        logger.info(f"📊 Call {call_sid} status: {call_info.get('Status')}")
                        return call_info
                    else:
                        logger.error(f"❌ Failed to get call status: {resp.status}")
                        return None
        
        except Exception as e:
            logger.error(f"❌ Error getting call status: {e}")
            return None
    
    async def hangup_call(self, call_sid: str) -> bool:
        """
        Hang up an active call
        """
        try:
            url = f"{self.api_base_url}/Accounts/{self.account_sid}/Calls/{call_sid}.json"
            
            payload = {
                "Action": "hangup"
            }
            
            headers = {
                "Authorization": self.auth_header,
                "Content-Type": "application/x-www-form-urlencoded"
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.post(url, data=payload, headers=headers) as resp:
                    if resp.status == 200:
                        logger.info(f"✅ Call {call_sid} hung up")
                        return True
                    else:
                        logger.error(f"❌ Failed to hangup: {resp.status}")
                        return False
        
        except Exception as e:
            logger.error(f"❌ Error hanging up call: {e}")
            return False


# Simple async client for testing
async def test_outbound_call():
    """Test making an outbound call"""
    api = ExotelOutboundAPI()
    
    call_sid = await api.make_outbound_call(
        "+919876543210",
        greeting_text="Hi John, this is Sarah from our sales team. Please wait..."
    )
    
    if call_sid:
        print(f"✅ Call initiated: {call_sid}")
        
        # Wait and check status
        await asyncio.sleep(5)
        status = await api.get_call_status(call_sid)
        print(f"📊 Status: {status}")
        
        # Hang up after 10 seconds
        await asyncio.sleep(5)
        await api.hangup_call(call_sid)
    else:
        print("❌ Failed to make call")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(test_outbound_call())
