#!/usr/bin/env python3
"""
Exotel Outbound Call CLI - Simple REST API based calling
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from config import Config
from core.exotel_outbound_api import ExotelOutboundAPI

async def make_callback(phone_number: str, customer_name: str = "Customer"):
    """Make an outbound callback via Exotel REST API"""
    
    print("\n" + "=" * 60)
    print("📱 Exotel Outbound Call (REST API)")
    print("=" * 60)
    
    # Validate config
    if not Config.EXOTEL_API_TOKEN:
        print("❌ EXOTEL_API_TOKEN not configured in .env")
        sys.exit(1)
    
    if not Config.EXOTEL_ACCOUNT_SID:
        print("❌ EXOTEL_ACCOUNT_SID not configured in .env")
        sys.exit(1)
    
    if not Config.EXOTEL_FROM_NUMBER:
        print("❌ EXOTEL_FROM_NUMBER not configured in .env")
        sys.exit(1)
    
    # Initialize API
    api = ExotelOutboundAPI()
    
    # Create greeting
    greeting = f"Hi {customer_name}, this is Sarah from our sales team. Please wait while I connect you."
    
    print(f"\n📞 Target: {phone_number}")
    print(f"👤 Name: {customer_name}")
    print(f"🎤 Greeting: {greeting}")
    print()
    
    # Make the call
    call_sid = await api.make_outbound_call(
        phone_number=phone_number,
        greeting_text=greeting,
        context={
            "customer_name": customer_name,
            "purpose": "sales_callback"
        }
    )
    
    if not call_sid:
        print("\n❌ Failed to initiate call")
        sys.exit(1)
    
    print(f"\n✅ Call initiated!")
    print(f"🆔 Call SID: {call_sid}")
    print(f"⏳ Waiting 3 seconds for call to connect...")
    
    await asyncio.sleep(3)
    
    # Check status
    print("\n📊 Checking call status...")
    status = await api.get_call_status(call_sid)
    
    if status:
        print(f"✅ Call Status: {status.get('Status')}")
        print(f"📋 Duration: {status.get('Duration', 'N/A')} seconds")
        print(f"📞 From: {status.get('From')}")
        print(f"📞 To: {status.get('To')}")
    else:
        print("⚠️  Could not fetch call status")
    
    print("\n💡 What happens next:")
    print("   1. Exotel dials the customer")
    print("   2. Customer hears TTS greeting")
    print("   3. When customer answers, call routes to your inbound vSIP")
    print("   4. Your bot answers and starts conversation")
    print("   5. Full audio bridge with OpenAI")
    
    print("\n👋 Monitoring call for 30 seconds...")
    for i in range(6):
        await asyncio.sleep(5)
        status = await api.get_call_status(call_sid)
        if status:
            print(f"   [{i*5}s] Status: {status.get('Status')}")

if __name__ == "__main__":
    import argparse
    import logging
    
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    parser = argparse.ArgumentParser(
        description="Make outbound calls via Exotel REST API"
    )
    parser.add_argument("phone", help="Phone number to call (e.g., +919876543210)")
    parser.add_argument("--name", default="Customer", help="Customer name")
    
    args = parser.parse_args()
    
    print("\n🔌 Configuration:")
    print(f"   API Token: {'✅ Set' if Config.EXOTEL_API_TOKEN else '❌ Missing'}")
    print(f"   Account SID: {'✅ Set' if Config.EXOTEL_ACCOUNT_SID else '❌ Missing'}")
    print(f"   From Number: {'✅ Set' if Config.EXOTEL_FROM_NUMBER else '❌ Missing'}")
    
    asyncio.run(make_callback(args.phone, args.name))
