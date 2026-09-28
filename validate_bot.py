#!/usr/bin/env python3
"""
Bot Validation Script - Check if bot is ready for testing
Run this before starting test calls
"""

import sys
import subprocess
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from config import Config

def check_dependencies():
    """Check if all required packages are installed"""
    print("\n[INFO] Checking Dependencies...")
    print("-" * 50)
    
    required = {
        'pjsua2': 'SIP library (pjsua2-py)',
        'pyaudio': 'Audio input/output',
        'dotenv': 'Environment variables',
        'numpy': 'Numerical computing',
        'pydub': 'Audio processing'
    }
    
    if Config.VOICE_BOT_MODE == "modular":
        required['deepgram'] = 'Deepgram STT SDK'
        required['google.generativeai'] = 'Google Gemini SDK'
        required['sarvamai'] = 'Sarvam AI SDK'
    else:
        required['openai'] = 'OpenAI API'
    
    missing = []
    import importlib
    for pkg, desc in required.items():
        try:
            importlib.import_module(pkg)
            print(f"[OK] {desc:30} ({pkg})")
        except ImportError:
            print(f"[FAIL] {desc:30} ({pkg})")
            missing.append(pkg)
    
    if missing:
        print(f"\n[WARN] Missing packages: {', '.join(missing)}")
        # Map package names to pip install names
        install_map = {
            'pjsua2': 'pjsua2-py',
            'pyaudio': 'PyAudio',
            'pydub': 'pydub',
            'google.generativeai': 'google-generativeai',
            'deepgram': 'deepgram-sdk',
            'sarvamai': 'sarvamai'
        }
        install_names = [install_map.get(pkg, pkg) for pkg in missing]
        print(f"   Run: pip install {' '.join(install_names)}")
        return False
    
    print("\n[OK] All dependencies installed!")
    return True

def check_configuration():
    """Check if .env is properly configured"""
    print("\n[INFO] Checking Configuration...")
    print("-" * 50)
    
    checks = {
        'COMPANY_NAME': 'Company Name',
        'SALES_BOT_NAME': 'Bot Name',
        'SIP_PUBLIC_IP': 'SIP Public IP'
    }
    
    if Config.VOICE_BOT_MODE == "modular":
        checks['DEEPGRAM_API_KEY'] = 'Deepgram API Key'
        checks['GEMINI_API_KEY'] = 'Gemini API Key'
        checks['SARVAM_API_KEY'] = 'Sarvam API Key'
    else:
        checks['OPENAI_API_KEY'] = 'OpenAI API Key'
        
    missing = []
    for key, desc in checks.items():
        value = getattr(Config, key, None)
        if value:
            # Mask sensitive values
            if 'KEY' in key or 'TOKEN' in key:
                display = value[:10] + '...' if len(value) > 10 else value
            else:
                display = value
            print(f"[OK] {desc:30} = {display}")
        else:
            print(f"[FAIL] {desc:30} (NOT SET)")
            missing.append(key)
    
    if missing:
        print(f"\n[WARN] Missing configuration: {', '.join(missing)}")
        print(f"   Edit .env and set: {', '.join(missing)}")
        return False
    
    print("\n[OK] Configuration complete!")
    return True

def check_ai_connection():
    """Test AI API connection"""
    if Config.VOICE_BOT_MODE == "modular":
        print("\n[INFO] Checking Gemini Connection...")
        print("-" * 50)
        try:
            import google.generativeai as genai
            genai.configure(api_key=Config.GEMINI_API_KEY)
            # Fetch generative models as a connection check
            genai.list_models()
            print(f"[OK] Gemini API connected!")
            print(f"   Using model: {Config.GEMINI_MODEL}")
            return True
        except Exception as e:
            print(f"[FAIL] Gemini connection failed: {e}")
            print(f"   Check GEMINI_API_KEY in .env")
            return False
    else:
        print("\n[INFO] Checking OpenAI Connection...")
        print("-" * 50)
        try:
            from openai import OpenAI
            client = OpenAI(api_key=Config.OPENAI_API_KEY)
            # Try a simple API call
            response = client.models.list()
            print(f"[OK] OpenAI API connected!")
            print(f"   Available models: {len(response.data)}")
            print(f"   Using model: {Config.OPENAI_MODEL}")
            return True
        except Exception as e:
            print(f"[FAIL] OpenAI connection failed: {e}")
            print(f"   Check OPENAI_API_KEY in .env")
            return False

def check_sip_configuration():
    """Verify SIP configuration"""
    print("\n[INFO] Checking SIP Configuration...")
    print("-" * 50)
    
    checks = {
        'SIP_SERVER_HOST': ('SIP Host', '0.0.0.0'),
        'SIP_SERVER_PORT': ('SIP Port', 5060),
        'INBOUND_SIP_ENABLED': ('Inbound Enabled', True),
        'SIP_PUBLIC_IP': ('Public IP', 'Required')
    }
    
    all_ok = True
    for key, (desc, expected) in checks.items():
        value = getattr(Config, key, None)
        if value:
            if isinstance(expected, str) and expected == 'Required':
                print(f"[OK] {desc:30} = {value}")
            else:
                match = str(value) == str(expected)
                status = "[OK]" if match else "[WARN]"
                print(f"{status} {desc:30} = {value}")
        else:
            print(f"[FAIL] {desc:30} (NOT SET)")
            all_ok = False
    
    if all_ok:
        print("\n[OK] SIP configuration OK!")
    else:
        print("\n[WARN] Fix SIP configuration in .env")
    
    return all_ok

def main():
    """Run all checks"""
    print("\n" + "=" * 60)
    print(f"[BOT] BOT VALIDATION - Testing Readiness Check (MODE: {Config.VOICE_BOT_MODE.upper()})")
    print("=" * 60)
    
    results = {
        'Dependencies': check_dependencies(),
        'Configuration': check_configuration(),
        'AI Connection': check_ai_connection(),
        'SIP Configuration': check_sip_configuration()
    }
    
    print("\n" + "=" * 60)
    print("VALIDATION SUMMARY")
    print("=" * 60)
    
    for check, passed in results.items():
        status = "[PASS]" if passed else "[FAIL]"
        print(f"{status:10} {check}")
    
    all_passed = all(results.values())
    
    if all_passed:
        print("\n" + "***" * 15)
        print("[OK] BOT IS READY FOR TESTING!")
        print("***" * 15)
        print("\nNext steps:")
        print("1. Start the bot: python main.py")
        print("2. Wait for: 'Waiting for incoming SIP calls...'")
        print("3. Make test call to your Exotel virtual number")
        print("4. Check logs for 'Call state stored' message")
        return 0
    else:
        print("\n" + "!!!" * 15)
        print("[FAIL] BOT NOT READY - Fix issues above first")
        print("!!!" * 15)
        return 1

if __name__ == '__main__':
    sys.exit(main())
