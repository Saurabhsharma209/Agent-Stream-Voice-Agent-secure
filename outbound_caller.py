#!/usr/bin/env python3
"""
Outbound Call Maker - Trigger outbound calls via REST API
Use Exotel REST API to initiate customer callbacks
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from config import Config
from core.exotel_outbound_api import ExotelOutboundAPI

async def make_callback_call(phone_number: str, customer_name: str = "Customer"):
    """
    Make an outbound callback to a customer via REST API
    
    Args:
        phone_number: Customer phone number
        customer_name: Customer name for greeting
    """
    try:
        print(f"\n📱 Initiating outbound call via REST API...")
        print(f"📞 Target: {phone_number}")
        print(f"👤 Customer: {customer_name}")
        
        # Initialize REST API client
        api = ExotelOutboundAPI()
        
        # Make the outbound call
        call_sid = await api.make_outbound_call(
            phone_number=phone_number,
            context={"customer_name": customer_name}
        )
        
        if call_sid:
            print(f"\n✅ Call initiated successfully!")
            print(f"🆔 Call SID: {call_sid}")
            print(f"💡 Call will route back to your SIP server when answered")
        else:
            print(f"\n❌ Failed to initiate call")
        
    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Make outbound calls via Exotel REST API")
    parser.add_argument("phone", help="Phone number to call (e.g., +919876543210)")
    parser.add_argument("--name", default="Customer", help="Customer name")
    
    args = parser.parse_args()
    
    print("🚀 Outbound Call Initiator (REST API)")
    print("=" * 50)
    
    # Check required config
    if not Config.EXOTEL_API_TOKEN or not Config.EXOTEL_ACCOUNT_SID:
        print("\n⚠️  REST API credentials not configured")
        print("Set EXOTEL_API_TOKEN and EXOTEL_ACCOUNT_SID in .env")
        sys.exit(1)
    
    if not Config.EXOTEL_FROM_NUMBER:
        print("\n⚠️  Outbound number not configured")
        print("Set EXOTEL_FROM_NUMBER in .env")
        sys.exit(1)
    
    print(f"✅ REST API configured")
    print(f"📤 From: {Config.EXOTEL_FROM_NUMBER}")
    print(f"🎯 To: {args.phone}")
    print()
    
    asyncio.run(make_callback_call(args.phone, args.name))
