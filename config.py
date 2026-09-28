#!/usr/bin/env python3
"""
Configuration settings for the Voice AI Bot System
This file contains all configurable parameters for the application.
"""

import os
from typing import List, Dict, Any
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

class Config:
    """Main configuration class for the Voice AI Bot System"""
    
    # ===== CORE API SETTINGS =====
    OPENAI_API_KEY = os.getenv('OPENAI_API_KEY', '')
    OPENAI_MODEL = os.getenv('OPENAI_MODEL', 'gpt-realtime-mini')
    OPENAI_VOICE = os.getenv('OPENAI_VOICE', 'coral')
    OPENAI_TEMPERATURE = float(os.getenv('OPENAI_TEMPERATURE', '0.7'))
    
    # ===== MODULAR PIPELINE SETTINGS =====
    DEEPGRAM_API_KEY = os.getenv('DEEPGRAM_API_KEY', '')
    GEMINI_API_KEY = os.getenv('GEMINI_API_KEY', '')
    SARVAM_API_KEY = os.getenv('SARVAM_API_KEY', '')
    VOICE_BOT_MODE = os.getenv('VOICE_BOT_MODE', 'modular').lower()
    
    # ===== RAG & DATABASE SETTINGS =====
    CHROMA_HOST = os.getenv('CHROMA_HOST', 'localhost')
    CHROMA_PORT = int(os.getenv('CHROMA_PORT', '8000'))
    AWS_S3_BUCKET_NAME = os.getenv('AWS_S3_BUCKET_NAME', '')
    DATABASE_URL = os.getenv('DATABASE_URL', '')
    DB_URL = os.getenv('DB_URL', '')

    
    DEEPGRAM_MODEL = os.getenv('DEEPGRAM_MODEL', 'nova-3')
    DEEPGRAM_LANGUAGE = os.getenv('DEEPGRAM_LANGUAGE', 'multi')
    DEEPGRAM_ENDPOINTING = int(os.getenv('DEEPGRAM_ENDPOINTING', '180'))
    DEEPGRAM_INTERIM_RESULTS = os.getenv('DEEPGRAM_INTERIM_RESULTS', 'true').lower() == 'true'
    SPECULATIVE_RAG = os.getenv('SPECULATIVE_RAG', 'true').lower() == 'true'
    EMBED_CACHE_SIZE = int(os.getenv('EMBED_CACHE_SIZE', '1000'))
    GEMINI_MODEL = os.getenv('GEMINI_MODEL', 'gemini-2.5-flash')
    SARVAM_MODEL = os.getenv('SARVAM_MODEL', 'bulbul:v3')
    SARVAM_SPEAKER = os.getenv('SARVAM_SPEAKER', 'neha')
    SARVAM_LANGUAGE_CODE = os.getenv('SARVAM_LANGUAGE_CODE', 'hi-IN')
    SARVAM_PACE = float(os.getenv('SARVAM_PACE', '1.15'))  # 1.15x speed for dynamic conversational pace
    SARVAM_PITCH = float(os.getenv('SARVAM_PITCH', '0.0'))
    AUDIO_GAIN = float(os.getenv('AUDIO_GAIN', '1.5'))  # 1.5x digital gain to boost quiet voice bot playback
    VAD_RMS_THRESHOLD = float(os.getenv('VAD_RMS_THRESHOLD', '4500.0'))  # High noise gate (4500 RMS) to ignore PSTN static
    
    
    # ===== SERVER SETTINGS =====
    SERVER_HOST = os.getenv('SERVER_HOST', '0.0.0.0')
    SERVER_PORT = int(os.getenv('SERVER_PORT', '5002'))
    WEB_DASHBOARD_PORT = int(os.getenv('WEB_DASHBOARD_PORT', '5001'))
    SERVER_BASE_URL = os.getenv('SERVER_BASE_URL', 'https://ai.chauwk.com')
    
    # ===== LOGGING =====
    LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO')
    LOG_FORMAT = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    
    # ===== AUDIO PROCESSING =====
    SAMPLE_RATE = int(os.getenv('SAMPLE_RATE', '24000'))
    DEFAULT_SAMPLE_RATE = int(os.getenv('DEFAULT_SAMPLE_RATE', '24000'))
    SUPPORTED_SAMPLE_RATES = [8000, 16000, 24000]
    AUDIO_CHUNK_SIZE = int(os.getenv('AUDIO_CHUNK_SIZE', '10'))
    MIN_CHUNK_SIZE_MS = int(os.getenv('MIN_CHUNK_SIZE_MS', '20'))
    MAX_CHUNK_SIZE_MS = int(os.getenv('MAX_CHUNK_SIZE_MS', '200'))
    BUFFER_SIZE_MS = int(os.getenv('BUFFER_SIZE_MS', '160'))
    SILENCE_THRESHOLD = float(os.getenv('SILENCE_THRESHOLD', '0.01'))
    NOISE_THRESHOLD = float(os.getenv('NOISE_THRESHOLD', '0.01'))
    VAD_RMS_THRESHOLD = float(os.getenv('VAD_RMS_THRESHOLD', '1500.0'))
    AUDIO_ENHANCEMENT_ENABLED = os.getenv('AUDIO_ENHANCEMENT_ENABLED', 'false').lower() == 'true'
    OPENAI_AUDIO_FORMAT = os.getenv('OPENAI_AUDIO_FORMAT', 'g711_ulaw')
    
    # ===== EXOTEL SPECIFIC =====
    EXOTEL_MARK_CLEAR_ENHANCED = os.getenv('EXOTEL_MARK_CLEAR_ENHANCED', 'true').lower() == 'true'
    EXOTEL_VARIABLE_CHUNK_SUPPORT = os.getenv('EXOTEL_VARIABLE_CHUNK_SUPPORT', 'true').lower() == 'true'
    DYNAMIC_CHUNK_SIZING = os.getenv('DYNAMIC_CHUNK_SIZING', 'true').lower() == 'true'
    
    # ===== EXOTEL OUTBOUND API (for REST-based outbound calls only) =====
    EXOTEL_API_KEY = os.getenv('EXOTEL_API_KEY', '')  # Your API Key (username)
    EXOTEL_API_TOKEN = os.getenv('EXOTEL_API_TOKEN', '')  # Your API Token (password)
    EXOTEL_ACCOUNT_SID = os.getenv('EXOTEL_ACCOUNT_SID', '')  # Your Account SID
    EXOTEL_FROM_NUMBER = ''  # Default virtual number fallback disabled per strict outbound policy
    EXOTEL_SUBDOMAIN = os.getenv('EXOTEL_SUBDOMAIN', 'api.in.exotel.com')  # e.g., api.in.exotel.com for Mumbai
    
    # ===== SMTP EMAIL CONFIGURATION =====
    SMTP_HOST = os.getenv('SMTP_HOST', '')
    SMTP_PORT = int(os.getenv('SMTP_PORT', '587'))
    SMTP_USER = os.getenv('SMTP_USER', '')
    SMTP_PASSWORD = os.getenv('SMTP_PASSWORD', '')
    SMTP_FROM_NAME = os.getenv('SMTP_FROM_NAME', 'Chauwk Sales Team')
    SMTP_FROM_EMAIL = os.getenv('SMTP_FROM_EMAIL', '')

    # ===== GMAIL OAUTH (per-enterprise "send as me" — replaces App Passwords) =====
    # Must match the OAuth client used by ai-webhooks.chauwk.com's existing
    # /auth/google/{id} Calendar-connect flow, which already writes refresh
    # tokens into the shared `oauthtokens` Mongo collection this reads from.
    GOOGLE_CLIENT_ID = os.getenv('GOOGLE_CLIENT_ID', '')
    GOOGLE_CLIENT_SECRET = os.getenv('GOOGLE_CLIENT_SECRET', '')
    GOOGLE_TOKEN_URI = os.getenv('GOOGLE_TOKEN_URI', 'https://oauth2.googleapis.com/token')

    # ===== DISABLE AI ENGINES (FOR TESTING / SAVING CREDITS) =====
    DISABLE_AI_ENGINES = os.getenv('DISABLE_AI_ENGINES', 'false').lower() == 'true'
    
    
    # ===== SIP SERVER CONFIGURATION =====
    SIP_SERVER_HOST = os.getenv('SIP_SERVER_HOST', '0.0.0.0')
    SIP_SERVER_PORT = int(os.getenv('SIP_SERVER_PORT', '5060'))
    SIP_PUBLIC_IP = os.getenv('SIP_PUBLIC_IP', '')  # Public IP - REQUIRED for incoming calls
    
    # ===== INBOUND ONLY (IP-based auth, no SIP credentials needed) =====
    INBOUND_SIP_ENABLED = os.getenv('INBOUND_SIP_ENABLED', 'true').lower() == 'true'
    USE_SIP_TRUNK = os.getenv('USE_SIP_TRUNK', 'true').lower() == 'true'
    
    # ===== BOT PERSONALITY =====
    SALES_BOT_NAME = 'Shaakti'
    SALES_REP_NAME = 'Shaakti'  # Alias for compatibility
    COMPANY_NAME = os.getenv('COMPANY_NAME', 'TechSolutions Inc.')
    TEMPERATURE = float(os.getenv('TEMPERATURE', '0.7'))
    
    # ===== AI ENGINE PREFERENCES =====
    PRIMARY_STT_PROVIDER = os.getenv('PRIMARY_STT_PROVIDER', 'whisper')
    PRIMARY_TTS_PROVIDER = os.getenv('PRIMARY_TTS_PROVIDER', 'gtts')
    PREFER_LLM_NLP = os.getenv('PREFER_LLM_NLP', 'true').lower() == 'true'
    RESAMPLER_BACKEND = os.getenv('RESAMPLER_BACKEND', 'pydub')
    
    # ===== PERFORMANCE =====
    # Default increased to 100 to support larger SIP trunk volumes.
    # Override with environment variable: MAX_CONCURRENT_CALLS
    MAX_CONCURRENT_CALLS = int(os.getenv('MAX_CONCURRENT_CALLS', '100'))
    CALL_TIMEOUT_SECONDS = int(os.getenv('CALL_TIMEOUT_SECONDS', '1800'))
    
    # ===== SECURITY =====
    REQUIRE_AUTH = os.getenv('REQUIRE_AUTH', 'false').lower() == 'true'
    RATE_LIMITING_ENABLED = os.getenv('RATE_LIMITING_ENABLED', 'true').lower() == 'true'
    
    # ===== MONITORING =====
    METRICS_ENABLED = os.getenv('METRICS_ENABLED', 'true').lower() == 'true'
    DETAILED_ANALYTICS = os.getenv('DETAILED_ANALYTICS', 'true').lower() == 'true'
    CONVERSATION_RECORDING = os.getenv('CONVERSATION_RECORDING', 'true').lower() == 'true'
    
    # ===== PRODUCTION MODE =====
    PRODUCTION_MODE = os.getenv('PRODUCTION_MODE', 'false').lower() == 'true'
    
    # ===== PRODUCTS/SERVICES CONFIGURATION =====
    # Purely reliant on system instructions and knowledge base RAG
    PRODUCTS = []
    
    # ===== VALIDATION =====
    @classmethod
    def validate(cls):
        """Validate required configuration"""
        if getattr(cls, 'DISABLE_AI_ENGINES', False):
            # Skip AI key validations when disabled
            return True
            
        errors = []
        
        if cls.VOICE_BOT_MODE == "modular":
            if not cls.DEEPGRAM_API_KEY:
                errors.append("DEEPGRAM_API_KEY is required in modular mode")
            if not cls.GEMINI_API_KEY and not (os.path.exists("project-gcp-key.json") or os.path.exists("/app/project-gcp-key.json")):
                errors.append("GEMINI_API_KEY is required in modular mode when project-gcp-key.json is missing")
            if not cls.SARVAM_API_KEY:
                errors.append("SARVAM_API_KEY is required in modular mode")
        else:
            if not cls.OPENAI_API_KEY:
                errors.append("OPENAI_API_KEY is required in realtime mode")
            
        if not cls.COMPANY_NAME:
            errors.append("COMPANY_NAME is required")
            
        if cls.SERVER_PORT < 1 or cls.SERVER_PORT > 65535:
            errors.append("SERVER_PORT must be between 1 and 65535")
            
        if errors:
            raise ValueError(f"Configuration errors: {', '.join(errors)}")
        
        return True
    
    # ===== HELPER METHODS =====
    @classmethod
    def get_openai_config(cls) -> Dict[str, Any]:
        """Get OpenAI-specific configuration"""
        return {
            'api_key': cls.OPENAI_API_KEY,
            'model': cls.OPENAI_MODEL,
            'voice': cls.OPENAI_VOICE,
            'temperature': cls.OPENAI_TEMPERATURE
        }
    
    @classmethod
    def get_server_config(cls) -> Dict[str, Any]:
        """Get server configuration"""
        return {
            'host': cls.SERVER_HOST,
            'port': cls.SERVER_PORT,
            'dashboard_port': cls.WEB_DASHBOARD_PORT
        }
    
    @classmethod
    def get_audio_config(cls) -> Dict[str, Any]:
        """Get audio processing configuration"""
        return {
            'sample_rate': cls.SAMPLE_RATE,
            'chunk_size': cls.AUDIO_CHUNK_SIZE,
            'min_chunk_size_ms': cls.MIN_CHUNK_SIZE_MS,
            'buffer_size_ms': cls.BUFFER_SIZE_MS,
            'silence_threshold': cls.SILENCE_THRESHOLD
        }
    
    @classmethod
    def get_adaptive_chunk_size(cls, sample_rate: int) -> int:
        """Get adaptive chunk size based on sample rate"""
        if sample_rate >= 24000:
            return 40  # 40ms for 24kHz
        elif sample_rate >= 16000:
            return 30  # 30ms for 16kHz
        else:
            return 20  # 20ms for 8kHz
    
    @classmethod
    def get_chunk_size_bytes(cls, sample_rate: int, chunk_size_ms: int) -> int:
        """Calculate chunk size in bytes"""
        return int(sample_rate * chunk_size_ms / 1000) * 2  # 2 bytes per sample (16-bit)
    
    @classmethod
    def get_enhanced_session_config(cls, sample_rate: int, voice: str) -> Dict[str, Any]:
        """Get enhanced session configuration — uses valid OpenAI Realtime API session.update schema"""
        instructions = (
            f"You are a professional sales representative named {cls.SALES_BOT_NAME} for {cls.COMPANY_NAME}. "
            "Keep responses very concise, short, and natural (1-2 sentences). "
            "When the conversation is finished or the user says goodbye, use the end_call tool to hang up."
        )
        return {
            'modalities': ['audio', 'text'],
            'instructions': instructions,
            'voice': voice,
            'input_audio_format': 'pcm16',
            'output_audio_format': 'pcm16',
            'input_audio_transcription': {
                'model': 'whisper-1'
            },
            'turn_detection': {
                'type': 'server_vad',
                'threshold': 0.65,
                'prefix_padding_ms': 400,
                'silence_duration_ms': 800,
                'create_response': False,
            },
            'tools': [
                {
                    'type': 'function',
                    'name': 'end_call',
                    'description': 'Call this to hang up the call when the conversation is finished, the user says goodbye, or they want to end the call.'
                },
                {
                    'type': 'function',
                    'name': 'query_knowledge_base',
                    'description': 'MANDATORY TOOL: Call this tool whenever the customer asks ANY question about company products, services, pricing, features, guarantees, FAQs, or policies to retrieve authoritative facts from the knowledge base.',
                    'parameters': {
                        'type': 'object',
                        'properties': {
                            'query': {'type': 'string', 'description': 'The exact search query to look up in the company knowledge base.'},
                        },
                        'required': ['query']
                    }
                }
            ],
            'tool_choice': 'auto',
            'temperature': cls.OPENAI_TEMPERATURE,
            # Internal metadata — NOT sent to OpenAI, used by gateway to track format
            '_input_sample_rate': 8000,
            '_output_sample_rate': 8000,
        }

    @classmethod
    def get_browser_session_config(cls, voice: str) -> Dict[str, Any]:
        """Browser-specific session config using pcm16 at 24kHz — the native OpenAI Realtime format"""
        base = cls.get_enhanced_session_config(24000, voice)
        base['input_audio_format'] = 'pcm16'
        base['output_audio_format'] = 'pcm16'
        base['_input_sample_rate'] = 24000
        base['_output_sample_rate'] = 24000
        return base