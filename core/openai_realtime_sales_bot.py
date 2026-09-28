#!/usr/bin/env python3
"""
OpenAI Realtime Sales Bot - SIP Trunking Only Version

Direct SIP Trunking integration with OpenAI Realtime API for natural conversations
Bridges Exotel SIP trunk communication with OpenAI for high-quality voice interactions.

Security Notice: This code uses environment variables for sensitive configuration.
Set OPENAI_API_KEY environment variable before running.
"""

import asyncio
import websockets
import json
from core.rag_manager import RAGManager
from core.mongo_manager import mongo_db

RAG_MANAGER = RAGManager()
import logging
import base64
import time
import struct
import ssl
import os
import re
from typing import Dict, Any, Optional, Tuple
from urllib.parse import urlparse, parse_qs
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent))
from config import Config

# Configure enhanced logging
logging.basicConfig(
    level=getattr(logging, Config.LOG_LEVEL.upper()),
    format=Config.LOG_FORMAT
)
logger = logging.getLogger(__name__)

class OpenAIRealtimeSalesBot:
    def __init__(self):
        # Validate configuration first
        Config.validate()
        # Mapping of stream_id to company_id for RAG queries
        self.connection_company_map = {}
        
        self.openai_connections: Dict[str, Any] = {}
        
        # Enhanced audio buffering with dynamic sample rate support
        self.audio_buffers: Dict[str, bytes] = {}
        self.connection_sample_rates: Dict[str, int] = {}  # Track sample rate per connection
        self.connection_chunk_sizes: Dict[str, int] = {}   # Track chunk size per connection
        
        # Default audio configuration (will be updated per connection)
        self.default_sample_rate = Config.DEFAULT_SAMPLE_RATE
        self.min_chunk_size_ms = Config.MIN_CHUNK_SIZE_MS
        self.buffer_size_ms = Config.BUFFER_SIZE_MS
        
        # OpenAI Configuration - SECURE: Load from environment variables
        self.openai_api_key = Config.OPENAI_API_KEY
        self.openai_model = Config.OPENAI_MODEL
        self.openai_voice = Config.OPENAI_VOICE
        
        # Enhanced features flags
        self.exotel_enhanced_events = Config.EXOTEL_MARK_CLEAR_ENHANCED
        self.variable_chunk_support = Config.EXOTEL_VARIABLE_CHUNK_SUPPORT
        self.dynamic_chunk_sizing = Config.DYNAMIC_CHUNK_SIZING
        
        self.sip_server = None
        
        # Register bot instance for telemetry queries
        from controllers import bot_controller
        bot_controller.active_bot_instance = self
        
        logger.info("🤖 Enhanced OpenAI Realtime Sales Bot initialized!")
        logger.info(f"🎵 Multi-sample rate support: {Config.SUPPORTED_SAMPLE_RATES} Hz")
        logger.info(f"📦 Variable chunk sizes: {self.min_chunk_size_ms}ms - {Config.MAX_CHUNK_SIZE_MS}ms")
        logger.info(f"✨ Enhanced Exotel events: {self.exotel_enhanced_events}")
        logger.info(f"🏢 Company: {Config.COMPANY_NAME}")
        logger.info(f"👤 Sales Rep: {Config.SALES_REP_NAME}")
        logger.info("📡 MODE: Direct SIP Trunking (cost-effective, no applet needed)")

    async def connect_to_openai_enhanced(self, stream_id: str, agent_config: dict = None):
        """Establish enhanced connection to OpenAI Realtime API with dynamic configuration"""
        if getattr(Config, 'DISABLE_AI_ENGINES', False):
            logger.warning(f"🚫 Skipping OpenAI connection for {stream_id} because DISABLE_AI_ENGINES is True")
            return
        try:
            sample_rate = self.connection_sample_rates.get(stream_id, self.default_sample_rate)
            logger.info(f"🔗 CONNECTING TO OPENAI (ENHANCED) for {stream_id} @ {sample_rate}Hz")
            
            # Resolve called virtual DID number and agent config first
            to_phone = "default"
            from_phone = "default"
            outbound_record = None
            if self.sip_server and stream_id in self.sip_server.sip_calls:
                sip_call = self.sip_server.sip_calls[stream_id]
                from controllers.bot_controller import extract_phone_number_from_uri
                to_phone = extract_phone_number_from_uri(sip_call.to_uri)
                from_phone = extract_phone_number_from_uri(sip_call.from_uri)
                logger.info(f"Resolved called DID number: {to_phone}, caller number: {from_phone}")
                
                # Check if this is an outbound call to a customer
                caller_phone = from_phone
                clean_caller = "".join(filter(str.isdigit, caller_phone))[-10:]
                logger.info(f"🔎 Outbound match check: raw_from={sip_call.from_uri}, caller_phone={caller_phone}, clean_caller={clean_caller}")
                
                # Check MongoDB outbound_calls collection
                from core.mongo_manager import mongo_db
                if mongo_db.client is not None:
                    try:
                        db = mongo_db.client.get_default_database()
                        outbound_calls_coll = db['outbound_calls']
                        
                        if clean_caller:
                            # Search for active/non-completed records for this phone number suffix created in the last 1 hour
                            cursor = outbound_calls_coll.find({
                                "status": {"$not": {"$in": ["completed", "failed", "no-answer", "busy"]}},
                                "timestamp": {"$gt": time.time() - 3600}
                            })
                            async for record in cursor:
                                record_phone = record.get("phone_number", "")
                                clean_record = "".join(filter(str.isdigit, record_phone))[-10:]
                                if clean_record == clean_caller:
                                    outbound_record = record
                                    logger.info(f"📞 MongoDB Matches! Detected OUTBOUND call to customer: {record.get('customer_name')}")
                                    break
                    except Exception as db_err:
                        logger.error(f"⚠️ Failed to query outbound_calls collection from MongoDB: {db_err}")
                
                # Fallback to in-memory _call_records_cache if MongoDB did not resolve
                if not outbound_record:
                    from controllers.call_controller import _call_records_cache
                    if clean_caller:
                        for call_sid, record in _call_records_cache.items():
                            if record.get("status") not in ["completed", "failed", "no-answer", "busy"]:
                                record_phone = record.get("phone_number", "")
                                clean_record = "".join(filter(str.isdigit, record_phone))[-10:]
                                if clean_record == clean_caller:
                                    outbound_record = record
                                    logger.info(f"📞 Cache Matches! Detected OUTBOUND call to customer: {record.get('customer_name')}")
                                    break
                            
            if agent_config is None:
                # Resolve target ID from matched outbound record (agent_id or enterprise_id) or default to to_phone
                target_agent = None
                if outbound_record:
                    target_agent = outbound_record.get("agent_id") or outbound_record.get("enterprise_id")
                
                target_id = target_agent if (target_agent and target_agent != "default" and target_agent != "ent_default") else to_phone
                
                if target_id != "default":
                    try:
                        from core.agent_resolver import resolve_agent_config
                        agent_config = await resolve_agent_config(target_id)
                        if not agent_config and from_phone:
                            agent_config = await resolve_agent_config(from_phone)
                    except Exception as e:
                        logger.error(f"⚠️ Failed to dynamically resolve agent for ID {target_id}: {e}")

            # Check per-agent mode ('modular' or 'realtime')
            target_mode = (agent_config.get("mode") or agent_config.get("voice_bot_mode") or Config.VOICE_BOT_MODE or "realtime").lower().strip() if agent_config else "realtime"
            if target_mode == "modular":
                logger.info(f"🔀 [Per-Agent Handoff] Agent '{agent_config.get('name') if agent_config else 'default'}' is configured in MODULAR mode. Handing off stream {stream_id} to ModularSalesBot...")
                if not hasattr(self, "_modular_bot") or self._modular_bot is None:
                    from core.modular_sales_bot import ModularSalesBot
                    self._modular_bot = ModularSalesBot()
                self._modular_bot.sip_server = self.sip_server

                # Transfer browser websocket reference if present
                if stream_id in self.openai_connections and "browser_websocket" in self.openai_connections[stream_id]:
                    ws = self.openai_connections[stream_id]["browser_websocket"]
                    self._modular_bot.connections[stream_id] = {"browser_websocket": ws}

                self.openai_connections[stream_id] = {
                    "delegated_to": "modular",
                    "modular_bot": self._modular_bot
                }
                await self._modular_bot.connect_to_openai_enhanced(stream_id, agent_config)
                return

            # Determine voice and instructions dynamically
            voice = self.openai_voice
            instructions = None
            agent_name = Config.SALES_BOT_NAME
            first_message = None
            
            if agent_config:
                agent_name = agent_config.get("name", Config.SALES_BOT_NAME)
                prompt_parts = []
                if agent_config.get("systemPrompt"):
                    prompt_parts.append(str(agent_config["systemPrompt"]).strip())
                if agent_config.get("instructions"):
                    instr_str = str(agent_config["instructions"]).strip()
                    if instr_str not in prompt_parts:
                        prompt_parts.append(instr_str)
                if agent_config.get("description"):
                    prompt_parts.append(f"Agent Goal & Description: {str(agent_config['description']).strip()}")
                agent_instructions = "\n\n".join(prompt_parts)
                
                # Check if voiceId is a valid OpenAI voice
                candidate_voice = str(agent_config.get("voiceId") or "").lower()
                if candidate_voice in ["alloy", "ash", "ballad", "coral", "echo", "sage", "shimmer", "verse"]:
                    voice = candidate_voice
            else:
                agent_instructions = ""

            # Resolve selected languages (defaulting to English 'en-IN' if omitted)
            agent_languages = (agent_config.get("languages") if agent_config else None) or []
            if isinstance(agent_languages, str):
                agent_languages = [l.strip() for l in agent_languages.split(",") if l.strip()]
            if not agent_languages:
                primary = (agent_config.get("language") if agent_config else None) or Config.SARVAM_LANGUAGE_CODE
                agent_languages = [primary]
            
            primary_lang = agent_languages[0]
            is_hindi = primary_lang.startswith("hi")
            
            def resolve_lang_name(code: str) -> str:
                LOOKUP = {
                    "en": "English", "hi": "Hindi", "ta": "Tamil", "te": "Telugu",
                    "kn": "Kannada", "ml": "Malayalam", "mr": "Marathi", "bn": "Bengali",
                    "gu": "Gujarati", "pa": "Punjabi", "or": "Odia", "ur": "Urdu",
                    "es": "Spanish", "fr": "French", "de": "German", "it": "Italian",
                    "pt": "Portuguese", "ru": "Russian", "zh": "Chinese", "ja": "Japanese",
                    "ko": "Korean", "ar": "Arabic"
                }
                base = str(code or "").split("-")[0].lower().strip()
                return LOOKUP.get(base, str(code))

            allowed_names = [resolve_lang_name(l) for l in agent_languages]
            # Sanitize custom agent instructions to remove conflicting or ambiguous language phrases
            sanitized_instructions = (agent_instructions or "").strip()
            if sanitized_instructions:
                sanitized_instructions = sanitized_instructions.replace(
                    "adjust your language accordingly",
                    "adjust your explanation complexity and technical depth accordingly (while remaining strictly within allowed languages)"
                ).replace(
                    "adjust your language",
                    "adjust your explanation style"
                )
                if len(allowed_names) == 1:
                    sanitized_instructions = sanitized_instructions.replace(
                        "You can switch to any configured language at any time, including back to a previously used one",
                        f"You must speak and respond EXCLUSIVELY in {allowed_names[0]}"
                    ).replace(
                        "You can switch to any configured language at any time",
                        f"You must speak and respond EXCLUSIVELY in {allowed_names[0]}"
                    )
            
            if len(allowed_names) == 1:
                final_language_mandate = (
                    f"🚨 LANGUAGE CAPABILITY:\n"
                    f"Your default starting language is: {allowed_names[0]}.\n"
                    f"You must speak and respond primarily in {allowed_names[0]}.\n"
                    f"HOWEVER, if the customer explicitly speaks to you in another language (like Telugu, Hindi, etc.) or asks you to switch to another language, you are FULLY ALLOWED and ENCOURAGED to switch to their preferred language and assist them fluently.\n"
                    f"Do NOT say you are only configured for {allowed_names[0]}; seamlessly transition to the requested language.\n\n"
                )
            else:
                langs_str = ", ".join(allowed_names)
                final_language_mandate = (
                    f"🚨 LANGUAGE CAPABILITY:\n"
                    f"Your configured preferred languages are: {langs_str}.\n"
                    f"Primary language is: {allowed_names[0]}. Speak in {allowed_names[0]} by default unless the customer addresses you in another allowed language.\n"
                    f"HOWEVER, if the customer explicitly speaks in an unconfigured language or asks to switch languages, you are ALLOWED to switch to their preferred language to assist them gracefully.\n"
                    f"Do NOT say you are only configured for {langs_str}; seamlessly transition to the requested language.\n\n"
                )

            # Check terms & guardrails in agent config
            terms_info = agent_config.get("terms", {}) if agent_config else {}
            terms_content = (terms_info.get("content") or "").strip() if (isinstance(terms_info, dict) and terms_info.get("enabled")) else ""
            guardrails_block = ""
            if terms_content:
                guardrails_block = f"\n\n🚨 STRICT BUSINESS GUARDRAILS & TERMS:\n{terms_content}\n"

            rag_mandate = (
                "\n\n🛡️ ABSOLUTE KNOWLEDGE BASE (RAG) & INSTANT HANGUP MANDATE:\n"
                "1. MANDATORY TOOL EXECUTION FOR KNOWLEDGE BASE (ZERO EXCEPTIONS):\n"
                "   - You have ZERO internal memory or pre-trained knowledge regarding this company, its products, services, pricing, terms, features, or policies.\n"
                "   - For ANY question asked by the customer regarding products, services, pricing, features, policies, warranties, or FAQs, YOU MUST ALWAYS CALL THE `query_knowledge_base` TOOL BEFORE SPEAKING A SINGLE WORD.\n"
                "   - Never answer business or product questions directly without calling `query_knowledge_base` first.\n"
                "2. HANDLING MISSING KNOWLEDGE:\n"
                "   - If `query_knowledge_base` returns no matching results, state politely: 'I apologize, but that specific detail is not available in our knowledge base. How else may I help you?'\n"
                "   - Never fabricate, guess, or hallucinate product details.\n"
                "3. INSTANT CALL HANGUP MANDATE:\n"
                "   - Whenever the customer says 'bye', 'goodbye', 'cut the call', 'hang up', 'end the call', or indicates they are done, YOU MUST IMMEDIATELY CALL THE `end_call` TOOL.\n"
                "   - Speak a brief goodbye out loud (e.g. 'Thank you, goodbye!') and call `end_call` immediately."
            )

            # Unify system instructions prioritizing custom agent instructions & persona at the top
            instructions = (
                f"=== AGENT ROLE & CUSTOM PERSONALITY (HIGHEST PRIORITY) ===\n"
                f"You are a representative named {agent_name}.\n"
                f"{sanitized_instructions}\n\n"
                f"{final_language_mandate}"
                f"{guardrails_block}"
                f"{rag_mandate}"
            )

            # Resolve greeting based on call type (inbound vs outbound) and language
            if is_hindi:
                outbound_fallback = f"नमस्ते! मैं {Config.COMPANY_NAME} से {agent_name} बोल रही हूँ। क्या मेरी बात {{customer_name}} से हो रही है?"
                inbound_fallback = f"नमस्ते! {Config.COMPANY_NAME} में कॉल करने के लिए धन्यवाद। मैं आज आपकी क्या सहायता कर सकती हूँ?"
            else:
                outbound_fallback = f"Hello! I'm {agent_name} calling back from the sales team at {Config.COMPANY_NAME}. How can I help you today?"
                inbound_fallback = f"Hello! Thank you for calling {Config.COMPANY_NAME}. How can I help you today?"

            is_active_outbound_leg = bool(outbound_record and outbound_record.get("status") in ["initiated", "ringing", "in_progress"])
            
            if is_active_outbound_leg:
                custom_outbound = (agent_config.get("firstMessage") or "").strip() if agent_config else ""
                first_message = custom_outbound or outbound_fallback
                customer_name = outbound_record.get("customer_name", "")
                if customer_name:
                    first_message = first_message.replace("{customer_name}", customer_name).replace("{name}", customer_name)
            else:
                custom_inbound = (agent_config.get("firstMessage") or "").strip() if agent_config else ""
                first_message = custom_inbound or inbound_fallback
                if outbound_record and outbound_record.get("customer_name"):
                    customer_name = outbound_record.get("customer_name", "")
                    first_message = first_message.replace("{customer_name}", customer_name).replace("{name}", customer_name)

            # Enhanced URL for latest OpenAI Realtime API
            url = f"wss://api.openai.com/v1/realtime?model={self.openai_model}"
            
            # Create SSL context that handles certificate verification
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE
            
            # Enhanced headers for latest API version
            headers = {"Authorization": f"Bearer {self.openai_api_key}"}
            
            try:
                openai_ws = await websockets.connect(
                    url,
                    ssl=ssl_context,
                    additional_headers=headers,
                    ping_interval=20,
                    ping_timeout=10
                )
            except Exception as header_err:
                logger.warning(f"⚠️ additional_headers fallback to extra_headers due to: {header_err}")
                openai_ws = await websockets.connect(
                    url,
                    ssl=ssl_context,
                    extra_headers=headers,
                    ping_interval=20,
                    ping_timeout=10
                )
            
            # Get enhanced session configuration
            session_config = Config.get_enhanced_session_config(sample_rate, voice)
            if instructions:
                session_config['instructions'] = instructions
            
            input_format = session_config.get('input_audio_format', 'g711_ulaw')
            output_format = session_config.get('output_audio_format', 'g711_ulaw')
            
            existing_browser_ws = self.openai_connections.get(stream_id, {}).get("browser_websocket")
            self.openai_connections[stream_id] = {
                "websocket": openai_ws,
                "browser_websocket": existing_browser_ws,
                "start_time": time.time(),
                "sample_rate": sample_rate,
                "input_format": input_format,
                "output_format": output_format,
                "session_config": session_config,
                "user_speaking": False,
                "transcript": [],
                "to_phone": to_phone,
                "agent_config": agent_config,
                "first_message": first_message,
                "primary_language": allowed_names[0] if allowed_names else "English",
                "allowed_languages": ", ".join(allowed_names) if allowed_names else "English",
                "last_activity_time": time.time(),
                "silence_prompts_count": 0,
                "full_instructions": instructions,
                "direction": "outbound" if outbound_record else "inbound"
            }
            
            logger.info(f"✅ ENHANCED OPENAI CONNECTED for {stream_id} @ {sample_rate}Hz")
            logger.info(f"🎵 Audio Format: {input_format} → {output_format}")
            
            # Start listening to OpenAI responses and silence monitor FIRST so initial greeting audio is captured
            asyncio.create_task(self.handle_openai_responses_enhanced(stream_id, openai_ws))
            asyncio.create_task(self._silence_monitor_loop(stream_id))

            # Configure enhanced OpenAI session and trigger initial greeting
            await self.configure_openai_session_enhanced(stream_id, agent_config)
            
        except Exception as e:
            logger.error(f"❌ Failed to connect to OpenAI (enhanced): {e}")
            logger.error(f"Error type: {type(e).__name__}")
            if "SSL" in str(e):
                logger.error("💡 SSL Error - trying with insecure SSL context")
            elif "authentication" in str(e).lower():
                logger.error("💡 Authentication Error - check OpenAI API key")
            elif "websocket" in str(e).lower():
                logger.error("💡 WebSocket Error - check connection and headers")

    async def configure_openai_session_enhanced(self, stream_id: str, agent_config: dict = None):
        """Configure enhanced OpenAI Realtime session"""
        try:
            openai_connection = self.openai_connections[stream_id]
            openai_ws = openai_connection["websocket"]
            session_config = openai_connection["session_config"]
            sample_rate = openai_connection["sample_rate"]
            
            # Strip internal metadata keys (prefixed with _) before sending to OpenAI
            clean_config = {k: v for k, v in session_config.items() if not k.startswith('_')}

            session_update = {
                "type": "session.update",
                "session": clean_config
            }
            
            await openai_ws.send(json.dumps(session_update))
            
            input_format = session_config.get('input_audio_format', 'g711_ulaw')
            output_format = session_config.get('output_audio_format', 'g711_ulaw')
            voice = session_config.get('voice', 'coral')
            
            logger.info(f"🔧 ENHANCED OPENAI SESSION CONFIGURED for {stream_id}")
            logger.info(f"   🎵 Sample Rate: {sample_rate}Hz")
            logger.info(f"   🎤 Input Format: {input_format}")
            logger.info(f"   🔊 Output Format: {output_format}")
            logger.info(f"   🎭 Voice: {voice}")
            
            # Send enhanced initial greeting
            await self.send_initial_greeting_enhanced(stream_id, agent_config)
            
        except Exception as e:
            logger.error(f"❌ Error configuring enhanced OpenAI session: {e}")

    async def send_initial_greeting_enhanced(self, stream_id: str, agent_config: dict = None):
        """Send enhanced initial sales greeting through OpenAI"""
        try:
            openai_ws = self.openai_connections[stream_id]["websocket"]
            sample_rate = self.connection_sample_rates.get(stream_id, self.default_sample_rate)
            
            first_message = None
            is_outbound = False
            openai_config = self.openai_connections.get(stream_id)
            if openai_config:
                first_message = openai_config.get("first_message")
                is_outbound = (openai_config.get("direction") == "outbound")

            if not first_message and agent_config:
                first_message = agent_config.get("firstMessage")

            if not first_message:
                first_message = "Hello! Thank you for calling Chauwk. How can I help you today?"

            # Clear any initial PSTN silence/static from input audio buffer
            await openai_ws.send(json.dumps({"type": "input_audio_buffer.clear"}))

            full_instructions = openai_config.get("full_instructions", "") if openai_config else ""
            greeting_instruction = f"{full_instructions}\n\nGreet the caller immediately by saying this exact opening message in your configured voice: '{first_message}'. Do not wait for the user to speak first."

            # Create enhanced response for instant initial greeting
            response_msg = {
                "type": "response.create",
                "response": {
                    "instructions": greeting_instruction
                }
            }
            await openai_ws.send(json.dumps(response_msg))
            
            logger.info(f"👋 ENHANCED INITIAL GREETING SENT for {stream_id} @ {sample_rate}Hz: '{first_message}'")
            
        except Exception as e:
            logger.error(f"❌ Error sending enhanced initial greeting: {e}")

    async def _silence_monitor_loop(self, stream_id: str):
        """Monitors caller silence for OpenAI Realtime sessions and injects follow-up prompts or hangs up if idle."""
        logger.info(f"⏱️ Starting OpenAI silence monitor loop for stream {stream_id}")
        
        try:
            while True:
                await asyncio.sleep(1.0)
                
                openai_config = self.openai_connections.get(stream_id)
                if not openai_config:
                    break
                    
                # Update last activity if user is speaking or bot is playing audio
                user_is_speaking = openai_config.get("user_speaking", False)
                bot_is_speaking = False
                if self.sip_server and stream_id in self.sip_server.sip_calls:
                    call_state = self.sip_server.sip_calls[stream_id]
                    if call_state.is_playing or len(call_state.playback_buffer) > 0:
                        bot_is_speaking = True
                        
                if user_is_speaking or bot_is_speaking:
                    openai_config["last_activity_time"] = time.time()
                    continue
                    
                # Check idle duration
                idle_time = time.time() - openai_config.get("last_activity_time", time.time())
                if idle_time >= 8.0:
                    # Reset timer to prevent rapid repeated follow-ups
                    openai_config["last_activity_time"] = time.time()
                    
                    silence_count = openai_config.get("silence_prompts_count", 0) + 1
                    openai_config["silence_prompts_count"] = silence_count
                    ws = openai_config.get("websocket")
                    
                    primary_lang = openai_config.get("primary_language", "English")
                    
                    if silence_count == 1:
                        logger.info(f"⏱️ Silence Intimation 1/3 detected for 8 seconds on stream {stream_id}. Injecting prompt.")
                        if ws:
                            prompt_msg = {
                                "type": "conversation.item.create",
                                "item": {
                                    "type": "message",
                                    "role": "user",
                                    "content": [{
                                        "type": "input_text",
                                        "text": f"The customer has been silent for 8 seconds (Intimation 1/3). Please check politely in {primary_lang} if they are still on the line."
                                    }]
                                }
                            }
                            await ws.send(json.dumps(prompt_msg))
                            await ws.send(json.dumps({"type": "response.create"}))
                    elif silence_count == 2:
                        logger.info(f"⏱️ Silence Intimation 2/3 detected for 8 seconds on stream {stream_id}. Injecting prompt.")
                        if ws:
                            prompt_msg = {
                                "type": "conversation.item.create",
                                "item": {
                                    "type": "message",
                                    "role": "user",
                                    "content": [{
                                        "type": "input_text",
                                        "text": f"The customer is still silent for 8 seconds (Intimation 2/3). Ask in {primary_lang} if they are still there or need assistance."
                                    }]
                                }
                            }
                            await ws.send(json.dumps(prompt_msg))
                            await ws.send(json.dumps({"type": "response.create"}))
                    elif silence_count >= 3:
                        logger.info(f"⏱️ Final Silence Intimation 3/3 reached for stream {stream_id}. Hanging up after warning.")
                        if ws:
                            goodbye_msg = {
                                "type": "conversation.item.create",
                                "item": {
                                    "type": "message",
                                    "role": "user",
                                    "content": [{
                                        "type": "input_text",
                                        "text": f"The customer has remained silent after 3 intimations. State politely in {primary_lang} that since there is no response, you are hanging up now. Goodbye!"
                                    }]
                                }
                            }
                            await ws.send(json.dumps(goodbye_msg))
                            await ws.send(json.dumps({"type": "response.create"}))
                            
                            asyncio.create_task(self.delayed_hangup(stream_id))
                            break
                        
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"❌ Error in OpenAI silence monitor loop for stream {stream_id}: {e}")

    async def handle_openai_responses_enhanced(self, stream_id: str, openai_ws):
        """Handle enhanced responses from OpenAI Realtime API"""
        try:
            async for message in openai_ws:
                if getattr(Config, 'DISABLE_AI_ENGINES', False):
                    logger.warning(f"🚫 AI Engines disabled at runtime. Closing OpenAI connection for {stream_id}")
                    await openai_ws.close()
                    break
                try:
                    data = json.loads(message)
                    event_type = data.get("type", "")
                    
                    # Update activity timer for session movement
                    openai_config = self.openai_connections.get(stream_id)
                    if openai_config:
                        if event_type in [
                            "input_audio_buffer.speech_started",
                            "conversation.item.input_audio_transcription.completed"
                        ]:
                            openai_config["last_activity_time"] = time.time()
                            openai_config["silence_prompts_count"] = 0
                        elif event_type in ["response.output_audio.delta", "input_audio_buffer.speech_stopped"]:
                            openai_config["last_activity_time"] = time.time()
                    
                    logger.debug(f"🤖 ENHANCED OPENAI EVENT: {event_type} for {stream_id}")
                    
                    if event_type == "response.output_audio.delta":
                        openai_config = self.openai_connections.get(stream_id)
                        if openai_config and not openai_config.get("user_speaking", False):
                            await self.handle_openai_audio_delta_enhanced(stream_id, data)
                    elif event_type == "response.function_call_arguments.done":
                        await self.handle_openai_function_call_enhanced(stream_id, data)
                    elif event_type == "response.output_audio_transcript.delta":
                        transcript_delta = data.get('delta', '')
                        if transcript_delta:
                            logger.info(f"🗣️ SARAH SPEAKING: {transcript_delta.strip()}")
                            openai_config = self.openai_connections.get(stream_id)
                            if openai_config:
                                if "current_bot_text" not in openai_config:
                                    openai_config["current_bot_text"] = ""
                                openai_config["current_bot_text"] += transcript_delta
                    elif event_type == "response.output_audio_transcript.done":
                        openai_config = self.openai_connections.get(stream_id)
                        if openai_config and "current_bot_text" in openai_config:
                            bot_text = openai_config["current_bot_text"].strip()
                            if bot_text:
                                openai_config["transcript"].append({"role": "bot", "msg": bot_text})
                            openai_config["current_bot_text"] = ""
                    elif event_type == "conversation.item.input_audio_transcription.completed":
                        user_text = data.get("transcript", "").strip()
                        if user_text:
                            logger.info(f"🎤 CUSTOMER SAID: {user_text}")
                            openai_config = self.openai_connections.get(stream_id)
                            if openai_config:
                                openai_config["transcript"].append({"role": "user", "msg": user_text})
                                # Trigger server-side Intercept RAG search & OpenAI context injection
                                asyncio.create_task(self._process_user_turn_with_rag(stream_id, user_text))
                    elif event_type == "input_audio_buffer.speech_started":
                        logger.info(f"🎤 CUSTOMER STARTED SPEAKING (realtime VAD interruption triggered) for {stream_id}")
                        openai_config = self.openai_connections.get(stream_id)
                        if openai_config:
                            openai_config["user_speaking"] = True
                        
                        # Immediately stop bot response and clear audio playback buffer for instant barge-in
                        await self._handle_customer_interruption(stream_id, openai_ws)
                    elif event_type == "input_audio_buffer.speech_stopped":
                        logger.info(f"🎤 CUSTOMER STOPPED SPEAKING (enhanced) for {stream_id}")
                        openai_config = self.openai_connections.get(stream_id)
                        if openai_config:
                            openai_config["user_speaking"] = False
                    elif event_type == "response.done":
                        logger.info(f"✅ SARAH FINISHED RESPONSE (enhanced) for {stream_id}")
                    elif event_type == "error":
                        logger.error(f"❌ ENHANCED OPENAI ERROR: {data}")
                    elif event_type == "session.updated":
                        logger.info(f"🔧 SESSION UPDATED for {stream_id}")
                        
                except json.JSONDecodeError as e:
                    logger.error(f"❌ JSON decode error from OpenAI (enhanced): {e}")
                except Exception as e:
                    logger.error(f"❌ Error processing enhanced OpenAI response: {e}")
                    
        except Exception as e:
            logger.error(f"❌ Error in enhanced OpenAI response handler: {e}")

    async def _process_user_turn_with_rag(self, stream_id: str, user_transcript: str):
        """Executes server-side RAG search on ChromaDB and injects context + custom instructions into OpenAI before triggering speech generation"""
        try:
            openai_config = self.openai_connections.get(stream_id)
            if not openai_config:
                return
                
            openai_ws = openai_config["websocket"]
            to_phone = openai_config.get("to_phone", "default")
            agent_config = openai_config.get("agent_config")
            primary_lang = openai_config.get("primary_language", "English")
            allowed_langs = openai_config.get("allowed_languages", "English")
            
            logger.info(f"🔎 Executing Server-Side Intercept RAG for user query: '{user_transcript}' on stream {stream_id}")
            
            # 1. Query ChromaDB directly via bot_controller
            from controllers.bot_controller import query_knowledge_base
            rag_results = await query_knowledge_base(to_phone, user_transcript, top_k=8, agent_config=agent_config)
            
            # 2. Inject retrieved RAG context into OpenAI conversation as a user message
            context_text = json.dumps(rag_results) if rag_results else "No relevant documents found in knowledge base."
            
            item_msg = {
                "type": "conversation.item.create",
                "item": {
                    "type": "message",
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": (
                                f"AUTHORITATIVE KNOWLEDGE BASE CONTEXT:\n{context_text}\n\n"
                                f"USER QUESTION: {user_transcript}"
                            )
                        }
                    ]
                }
            }
            await openai_ws.send(json.dumps(item_msg))
            
            # 3. Trigger OpenAI speech response generation strictly guided by persona & RAG context
            full_instructions = openai_config.get("full_instructions", "")
            response_msg = {
                "type": "response.create",
                "response": {
                    "instructions": (
                        f"{full_instructions}\n\n"
                        f"IMPORTANT TURN INSTRUCTION: Synthesize a clear, short 1-2 sentence spoken response strictly adhering to your custom persona instructions above and allowed languages ({allowed_langs}). "
                        "Base all product, service, pricing, and business facts EXCLUSIVELY on the AUTHORITATIVE KNOWLEDGE BASE CONTEXT provided in the conversation above. "
                        f"If the context states no relevant documents found or lacks the required details, state politely in {primary_lang} that the information is not in our records. "
                        "DO NOT use pre-trained general memory or fabricate facts."
                    )
                }
            }
            await openai_ws.send(json.dumps(response_msg))
            logger.info(f"✅ Intercept RAG context injected & response created for stream {stream_id}")
            
        except Exception as e:
            logger.error(f"❌ Error processing user turn with RAG for stream {stream_id}: {e}")

    async def _handle_customer_interruption(self, stream_id: str, openai_ws):
        """Handle customer interruption with enhanced response cancellation"""
        try:
            # Enhanced interruption handling
            cancel_response_msg = {
                "type": "response.cancel"
            }
            await openai_ws.send(json.dumps(cancel_response_msg))
            logger.info(f"🛑 ENHANCED BOT INTERRUPTED - Customer started speaking for {stream_id}")
            
            # IMMEDIATELY clear the playback buffer in SIP Server so the bot stops speaking instantly
            if self.sip_server and stream_id in self.sip_server.sip_calls:
                call_state = self.sip_server.sip_calls[stream_id]
                call_state.playback_buffer = b""
                call_state.is_playing = False
                logger.info(f"🔇 Cleared playback buffer for {stream_id} due to interruption")
            
        except Exception as e:
            logger.error(f"❌ Error handling enhanced customer interruption: {e}")

    async def trigger_openai_response_enhanced(self, stream_id: str, openai_ws):
        """Trigger enhanced OpenAI response generation with improved parameters"""
        try:
            # Enhanced response triggering with better configuration
            await asyncio.sleep(0.2)  # Optimized pause verification
            
            response_create = {
                "type": "response.create",
                "response": {
                    "instructions": "Respond naturally and conversationally. Use appropriate pauses and inflections."
                }
            }
            await openai_ws.send(json.dumps(response_create))
            logger.info(f"🎯 TRIGGERED ENHANCED OPENAI RESPONSE for {stream_id}")
            
        except Exception as e:
            logger.error(f"❌ Error triggering enhanced OpenAI response: {e}")

    async def handle_openai_function_call_enhanced(self, stream_id: str, data: dict):
        """Handle enhanced function calls from OpenAI with improved error handling"""
        try:
            function_name = data.get("name", "")
            arguments = json.loads(data.get("arguments", "{}"))
            call_id = data.get("call_id", "")
            
            logger.info(f"🔧 ENHANCED FUNCTION CALL: {function_name} with {arguments}")
            
            # Execute function with enhanced error handling
            if function_name == "schedule_demo":
                result = await self.schedule_demo_enhanced(arguments)
            elif function_name == "send_pricing_info":
                result = await self.send_pricing_info_enhanced(arguments)
            elif function_name == "transfer_to_human":
                result = await self.transfer_to_human_enhanced(stream_id, arguments)
            elif function_name == "end_call":
                result = await self.end_call_enhanced(stream_id, arguments)
            elif function_name == "query_knowledge_base":
                result = await self.query_knowledge_base_enhanced(stream_id, arguments)
            else:
                result = {"status": "unknown_function", "error": f"Function {function_name} not implemented"}
            
            # Send enhanced function result back to OpenAI
            openai_ws = self.openai_connections[stream_id]["websocket"]
            
            function_response = {
                "type": "conversation.item.create",
                "item": {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": json.dumps(result)
                }
            }
            
            await openai_ws.send(json.dumps(function_response))
            
            # Create enhanced response (only if NOT ending the call)
            if function_name != "end_call":
                if function_name == "query_knowledge_base":
                    custom_instructions = (
                        "Synthesize a clear, short 1-2 sentence response using ONLY the provided knowledge base search results. "
                        "If the search results are empty or contain no relevant facts, state politely that the information is not in our knowledge base. "
                        "DO NOT use pre-trained general memory or make up facts."
                    )
                else:
                    custom_instructions = f"Based on the function result, provide a natural response to the customer about {function_name}."

                response_msg = {
                    "type": "response.create",
                    "response": {
                        "instructions": custom_instructions
                    }
                }
                await openai_ws.send(json.dumps(response_msg))
            
            logger.info(f"✅ ENHANCED FUNCTION CALL COMPLETED: {function_name}")
            
        except Exception as e:
            logger.error(f"❌ Error handling enhanced function call: {e}")

    async def query_knowledge_base_enhanced(self, stream_id: str, args: dict) -> dict:
        """Query RAG system using search parameters for caller's company context"""
        query = args.get("query", "")
        top_k = args.get("top_k", 8)
        
        # Get target phone number and agent config of call
        to_phone = "default"
        agent_config = None
        openai_config = self.openai_connections.get(stream_id)
        if openai_config:
            to_phone = openai_config.get("to_phone", "default")
            agent_config = openai_config.get("agent_config")
            
        logger.info(f"🔎 OpenAI Realtime Bot RAG search query: '{query}' for phone: {to_phone}")
        
        try:
            from controllers.bot_controller import query_knowledge_base
            results = await query_knowledge_base(to_phone, query, top_k, agent_config=agent_config)
            return {
                "status": "success",
                "results": results
            }
        except Exception as e:
            logger.error(f"❌ OpenAI Realtime Bot RAG query failed: {e}")
            return {
                "status": "error",
                "error": str(e)
            }

    async def schedule_demo_enhanced(self, args: dict) -> dict:
        """Enhanced demo scheduling with better data capture"""
        logger.info(f"📅 SCHEDULING ENHANCED DEMO: {args}")
        
        # Extract enhanced information
        customer_name = args.get('customer_name', 'Customer')
        product_interest = args.get('product_interest', 'Our solutions')
        company = args.get('company', '')
        contact_info = {
            'email': args.get('contact_email', ''),
            'phone': args.get('contact_phone', '')
        }
        preferences = {
            'date': args.get('preferred_date', ''),
            'time': args.get('preferred_time', ''),
            'notes': args.get('additional_notes', '')
        }
        
        # In production, this would integrate with CRM/scheduling system
        return {
            "status": "success",
            "message": f"Demo scheduled for {customer_name} interested in {product_interest}",
            "demo_id": f"DEMO_{int(time.time())}",
            "customer_name": customer_name,
            "product_interest": product_interest,
            "company": company,
            "contact_info": contact_info,
            "preferences": preferences,
            "scheduled_at": time.strftime('%Y-%m-%d %H:%M:%S')
        }

    async def send_pricing_info_enhanced(self, args: dict) -> dict:
        """Enhanced pricing information with detailed breakdown"""
        logger.info(f"💰 SENDING ENHANCED PRICING INFO: {args}")
        
        product = args.get('product', 'Our solution')
        company_size = args.get('company_size', 'standard')
        contact_email = args.get('contact_email', '')
        custom_requirements = args.get('custom_requirements', '')
        
        # In production, this would calculate custom pricing
        return {
            "status": "success", 
            "message": f"Detailed pricing information for {product} will be sent to {contact_email}",
            "product": product,
            "company_size": company_size,
            "contact_email": contact_email,
            "custom_requirements": custom_requirements,
            "quote_id": f"QUOTE_{int(time.time())}",
            "estimated_delivery": "within 24 hours"
        }

    async def transfer_to_human_enhanced(self, stream_id: str, args: dict) -> dict:
        """Enhanced human transfer with context preservation"""
        logger.info(f"👥 TRANSFERRING TO HUMAN AGENT: {args}")
        
        reason = args.get('reason', 'Customer request')
        context = args.get('customer_context', 'No additional context')
        urgency = args.get('urgency', 'medium')
        
        # In production, this would interface with call center system
        transfer_result = {
            "status": "transfer_initiated",
            "message": f"Transferring to human agent - {reason}",
            "transfer_id": f"TRANSFER_{int(time.time())}",
            "reason": reason,
            "context": context,
            "urgency": urgency,
            "stream_id": stream_id,
            "estimated_wait": "2-3 minutes"
        }
        
        # Log for human agent context
        logger.info(f"🚨 HUMAN TRANSFER INITIATED for {stream_id}:")
        logger.info(f"   Reason: {reason}")
        logger.info(f"   Context: {context}")
        logger.info(f"   Urgency: {urgency}")
        
        return transfer_result

    async def end_call_enhanced(self, stream_id: str, args: dict) -> dict:
        """Hang up the call when conversation is finished"""
        logger.info(f"📞 HANGING UP CALL via end_call tool for {stream_id}")
        if self.sip_server:
            # Clean up the call after a tiny delay so the function result response sends cleanly
            asyncio.create_task(self.delayed_hangup(stream_id))
            return {"status": "success", "message": "Call hangup initiated"}
        else:
            return {"status": "error", "message": "SIP Server not available"}

    async def delayed_hangup(self, stream_id: str, delay_seconds: float = 0.5):
        """Clean up the call immediately after allowing brief polite goodbye phrase to play"""
        try:
            logger.info(f"⏳ Executing fast hangup sequence for stream {stream_id}...")
            # 1. Brief grace period (1.0s) to allow final goodbye speech packet to transmit
            await asyncio.sleep(1.0)
            
            if self.sip_server:
                logger.info(f"✂️ Instant call hangup triggered for stream {stream_id}")
                await self.sip_server.hangup_call(stream_id)
        except Exception as e:
            logger.error(f"❌ Error during delayed hangup: {e}")
        except Exception as e:
            logger.error(f"❌ Error in OpenAI delayed hangup check: {e}")
            
        # 2. Final grace period for RTP transmission
        await asyncio.sleep(1.5)
        if self.sip_server:
            await self.sip_server.cleanup_call(stream_id)

    def _resample_audio(self, audio_data: bytes, from_rate: int, to_rate: int, stream_id: str = None, state_key: str = None) -> bytes:
        """Resample audio between different sample rates using audioop.ratecv (sample-exact & stateful)"""
        if from_rate == to_rate:
            return audio_data
            
        try:
            # Look up existing resampler state if session is active
            state = None
            connection = None
            if stream_id and state_key and hasattr(self, 'openai_connections'):
                connection = self.openai_connections.get(stream_id)
                if connection:
                    state = connection.get(f"{state_key}_resample_state")

            # Primary: audioop.ratecv — stdlib, sample-exact, no rounding drift
            import audioop
            resampled, new_state = audioop.ratecv(
                audio_data,
                2,          # sample width: 2 bytes = 16-bit
                1,          # mono
                from_rate,
                to_rate,
                state       # pass state to keep phase continuity
            )
            
            # Save new state for the next chunk
            if connection and state_key:
                connection[f"{state_key}_resample_state"] = new_state
                
            logger.debug(f"🔄 RESAMPLED AUDIO (audioop): {from_rate}Hz → {to_rate}Hz, {len(audio_data)} → {len(resampled)} bytes")
            return resampled
                
        except Exception as e:
            logger.warning(f"⚠️ audioop.ratecv failed ({e}), falling back to pydub")
            try:
                from pydub import AudioSegment
                seg = AudioSegment(data=audio_data, sample_width=2, frame_rate=from_rate, channels=1)
                resampled_bytes = seg.set_frame_rate(to_rate).raw_data
                logger.debug(f"🔄 RESAMPLED AUDIO (pydub fallback): {from_rate}Hz → {to_rate}Hz")
                return resampled_bytes
            except Exception as e2:
                logger.error(f"❌ All resampling failed: {e2}. Returning original audio (may cause distortion).")
                return audio_data

    def apply_noise_suppression(self, audio_data: bytes, sample_rate: int) -> bytes:
        """Enhanced noise suppression with sample rate awareness"""
        if not Config.AUDIO_ENHANCEMENT_ENABLED:
            return audio_data
            
        try:
            import numpy as np
            
            # Convert to 16-bit signed integers
            audio_samples = np.frombuffer(audio_data, dtype=np.int16)
            
            # Enhanced noise gate with sample rate adjustment
            noise_threshold = Config.NOISE_THRESHOLD * (sample_rate / 8000)  # Scale with sample rate
            audio_samples = np.where(np.abs(audio_samples) < noise_threshold, 0, audio_samples)
            
            # Sample rate specific filtering
            if len(audio_samples) > 10:
                # Adjust filter parameters based on sample rate
                if sample_rate >= 24000:
                    window_size = min(7, len(audio_samples) // 2)  # Larger window for higher sample rates
                elif sample_rate >= 16000:
                    window_size = min(5, len(audio_samples) // 2)
                else:
                    window_size = min(3, len(audio_samples) // 2)
                
                # Enhanced high-pass filter
                moving_avg = np.convolve(audio_samples.astype(np.float32), 
                                       np.ones(window_size)/window_size, mode='same')
                audio_samples = audio_samples - moving_avg.astype(np.int16) * 0.15
            
            # Enhanced dynamic range compression
            max_val = np.max(np.abs(audio_samples))
            if max_val > 0:
                # Adaptive compression based on sample rate
                compression_ratio = 0.85 if sample_rate >= 16000 else 0.8
                normalized = audio_samples.astype(np.float32) / max_val
                compressed = np.sign(normalized) * (np.abs(normalized) ** compression_ratio)
                audio_samples = (compressed * max_val * 0.9).astype(np.int16)
            
            return audio_samples.tobytes()
            
        except ImportError:
            logger.warning("📢 NumPy not available - skipping enhanced noise suppression")
            return audio_data
        except Exception as e:
            logger.error(f"❌ Error in enhanced noise suppression: {e}")
            return audio_data

    def generate_test_tone(self, duration_ms: int = 200, frequency: int = 800, sample_rate: int = None) -> bytes:
        """Generate enhanced test tone with configurable sample rate"""
        import math
        
        if sample_rate is None:
            sample_rate = self.default_sample_rate
            
        samples = int(sample_rate * duration_ms / 1000)
        amplitude = 5000  # Moderate volume
        
        audio_data = []
        for i in range(samples):
            # Generate sine wave
            t = i / sample_rate
            sample = int(amplitude * math.sin(2 * math.pi * frequency * t))
            sample = max(-32767, min(32767, sample))  # Clamp to 16-bit range
            audio_data.append(sample)
        
        # Convert to 16-bit PCM bytes (little-endian)
        return struct.pack(f'<{len(audio_data)}h', *audio_data)

    def convert_pcm_to_ulaw(self, pcm_data: bytes) -> bytes:
        """Convert 16-bit PCM to G.711 u-law (same sample rate)"""
        try:
            import audioop
            return audioop.lin2ulaw(pcm_data, 2)
        except ImportError:
            # G.711 u-law encoding table (simplified fallback)
            samples_pcm = struct.unpack(f'<{len(pcm_data)//2}h', pcm_data)
            ulaw_bytes = []
            
            for sample in samples_pcm:
                # Clamp to 14-bit range
                sample = max(-8159, min(8159, sample))
                
                # Sign and magnitude
                if sample < 0:
                    sample = -sample
                    sign = 0x80
                else:
                    sign = 0x00
                
                # Find the segment
                if sample < 32:
                    segment = 0
                    quantized = sample >> 1
                elif sample < 96:
                    segment = 1
                    quantized = (sample - 32) >> 2
                elif sample < 224:
                    segment = 2
                    quantized = (sample - 96) >> 3
                elif sample < 480:
                    segment = 3
                    quantized = (sample - 224) >> 4
                elif sample < 992:
                    segment = 4
                    quantized = (sample - 480) >> 5
                elif sample < 2016:
                    segment = 5
                    quantized = (sample - 992) >> 6
                elif sample < 4064:
                    segment = 6
                    quantized = (sample - 2016) >> 7
                else:
                    segment = 7
                    quantized = (sample - 4064) >> 8
                
                # Combine sign, segment, and quantized value
                ulaw_value = sign | (segment << 4) | quantized
                ulaw_bytes.append(ulaw_value ^ 0xFF)  # Complement for u-law
            
            return bytes(ulaw_bytes)

    def convert_ulaw_to_pcm(self, ulaw_data: bytes) -> bytes:
        """Convert G.711 u-law to 16-bit PCM (same sample rate)"""
        try:
            import audioop
            return audioop.ulaw2lin(ulaw_data, 2)
        except ImportError:
            # G.711 u-law decoding table (simplified fallback)
            pcm_samples = []
            
            for ulaw_byte in ulaw_data:
                ulaw_byte ^= 0xFF  # Un-complement
                
                sign = ulaw_byte & 0x80
                segment = (ulaw_byte >> 4) & 0x07
                quantized = ulaw_byte & 0x0F
                
                # Decode based on segment
                if segment == 0:
                    pcm_val = (quantized << 1) + 1
                elif segment == 1:
                    pcm_val = ((quantized << 2) + 33)
                elif segment == 2:
                    pcm_val = ((quantized << 3) + 97)
                elif segment == 3:
                    pcm_val = ((quantized << 4) + 225)
                elif segment == 4:
                    pcm_val = ((quantized << 5) + 481)
                elif segment == 5:
                    pcm_val = ((quantized << 6) + 993)
                elif segment == 6:
                    pcm_val = ((quantized << 7) + 2017)
                else:  # segment == 7
                    pcm_val = ((quantized << 8) + 4065)
                
                # Apply sign
                if sign:
                    pcm_val = -pcm_val
                
                # Scale up to 16-bit range from 14-bit range (multiply by 4)
                pcm_val = pcm_val << 2
                
                pcm_samples.append(pcm_val)
            
            return struct.pack(f'<{len(pcm_samples)}h', *pcm_samples)


    async def start_server(self):
        """Start SIP server for direct Exotel SIP trunking"""
        try:
            logger.info(f'🚀 Starting SIP Server on {Config.SIP_SERVER_HOST}:{Config.SIP_SERVER_PORT}')
            logger.info('📞 Ready for direct Exotel SIP trunk connections!')
            logger.info('💰 Cost-effective mode: No Voicebot Applet needed')
            logger.info('🎵 Multi-sample rate support: 8kHz, 16kHz, 24kHz')
            logger.info('🔐 Using SIP authentication from environment')
            
            # Import SIP server
            from core.sip_server import SIPServer
            
            # Create and start SIP server
            self.sip_server = SIPServer(openai_bot=self)
            
            # Initialize PJSUA (may take a moment)
            logger.info("⏳ Initializing PJSUA2 SIP stack...")
            self.sip_server.initialize_pjsua()
            
            # Start SIP server
            await self.sip_server.start()
            
            logger.info(f'✅ SIP Server running at sip://{Config.SIP_SERVER_HOST}:{Config.SIP_SERVER_PORT}')
            logger.info(f'📤 Outbound calls: Use ExotelOutboundAPI (REST API)')
            logger.info('📞 Waiting for incoming SIP calls...')
            
            # Keep running
            await asyncio.Future()  # Run forever
            
        except ImportError as e:
            logger.error(f'❌ SIP libraries not installed: {e}')
            logger.error('💡 Install with: pip install pjsua2-py PyAudio')
            raise
        except Exception as e:
            logger.error(f'❌ SIP Server Error: {e}')
            raise
        finally:
            if self.sip_server:
                await self.sip_server.stop()

    async def handle_exotel_dtmf(self, message: Dict[str, Any], stream_id: str):
        """Handle DTMF events from Exotel"""
        try:
            dtmf_data = message.get('dtmf', {})
            digit = dtmf_data.get('digit', '')
            duration = dtmf_data.get('duration', '')
            
            logger.info(f'📞 DTMF received: {digit} (duration: {duration}ms) for {stream_id}')
            
            # Handle DTMF logic here
            # For now, just acknowledge
            
        except Exception as e:
            logger.error(f'❌ Error handling DTMF: {e}')
    
    async def send_audio_to_openai(self, call_id: str, audio_chunk: bytes, sample_rate: int = 16000):
        """
        Public method for SIP server to send RTP audio to OpenAI
        This bridges incoming RTP audio packets with OpenAI Realtime API
        
        Args:
            call_id: SIP call identifier
            audio_chunk: PCM16 audio data
            sample_rate: Audio sample rate (8000, 16000, 24000)
        """
        if getattr(Config, 'DISABLE_AI_ENGINES', False):
            return
        try:
            # Map call_id to stream_id for compatibility with existing methods
            stream_id = call_id
            
            # Ensure OpenAI connection exists
            if stream_id not in self.openai_connections:
                logger.warning(f"⚠️ No OpenAI connection for SIP call {call_id}")
                return
            
            session = self.openai_connections.get(stream_id)
            if session and session.get("delegated_to") == "modular" and session.get("modular_bot"):
                await session["modular_bot"].send_audio_to_openai(call_id, audio_chunk, sample_rate=sample_rate)
                return
            
            # Initialize sample rate tracking if needed
            if stream_id not in self.connection_sample_rates:
                self.connection_sample_rates[stream_id] = sample_rate
            
            # Send audio to OpenAI via existing method
            await self._send_audio_to_openai(stream_id, audio_chunk, sample_rate)
            
        except Exception as e:
            logger.error(f"❌ Error sending RTP audio to OpenAI for {call_id}: {e}")

    async def _send_audio_to_openai(self, stream_id: str, chunk: bytes, sample_rate: int):
        """Send audio chunk to OpenAI with proper format and sample rate handling"""
        try:
            openai_config = self.openai_connections[stream_id]
            input_format = openai_config.get("input_format", "g711_ulaw")
            
            # Initialize buffer if not exists
            if stream_id not in self.audio_buffers:
                self.audio_buffers[stream_id] = b""
                
            # Append new chunk to buffer
            self.audio_buffers[stream_id] += chunk
            
            # Calculate buffer threshold (e.g. 160ms)
            buffer_ms = self.buffer_size_ms
            bytes_needed = int(sample_rate * 2 * buffer_ms / 1000)
            
            if len(self.audio_buffers[stream_id]) < bytes_needed:
                return  # Keep buffering
                
            # Extract buffered audio to process
            processed_audio = self.audio_buffers[stream_id]
            self.audio_buffers[stream_id] = b""  # Reset buffer
            
            # Apply noise suppression if enabled
            if Config.AUDIO_ENHANCEMENT_ENABLED:
                processed_audio = self.apply_noise_suppression(processed_audio, sample_rate)
            
            if input_format in ["g711_ulaw", "audio/pcmu"]:
                # Exotel/OpenAI expects 8kHz u-law.
                # If incoming is 16kHz PCM16, resample to 8kHz PCM16 first.
                if sample_rate != 8000:
                    processed_audio = self._resample_audio(processed_audio, sample_rate, 8000, stream_id=stream_id, state_key="input")
                # Convert 8kHz PCM16 to 8kHz u-law
                openai_audio = self.convert_pcm_to_ulaw(processed_audio)
            elif input_format == "pcm16":
                # OpenAI expects 24kHz PCM16.
                # If incoming is 16kHz, resample 16kHz PCM16 -> 24kHz PCM16.
                if sample_rate != 24000:
                    openai_audio = self._resample_audio(processed_audio, sample_rate, 24000, stream_id=stream_id, state_key="input")
                else:
                    openai_audio = processed_audio
            else:
                # Fallback
                openai_audio = processed_audio
                
            openai_audio_b64 = base64.b64encode(openai_audio).decode()
            
            # Send to OpenAI Realtime API
            openai_msg = {
                "type": "input_audio_buffer.append",
                "audio": openai_audio_b64
            }
            
            openai_ws = openai_config["websocket"]
            await openai_ws.send(json.dumps(openai_msg))
            
            logger.debug(f"📤 AUDIO SENT TO OPENAI: {len(openai_audio)} bytes {input_format} (from {len(processed_audio)} bytes PCM @ {sample_rate}Hz)")
            
        except Exception as e:
            logger.error(f"❌ Error sending audio to OpenAI: {e}")

    async def handle_openai_audio_delta_enhanced(self, stream_id: str, data: dict):
        """Handle audio response from OpenAI and send to SIP server or browser WebSocket for playback"""
        try:
            # Get audio from OpenAI (base64 encoded)
            audio_delta = data.get("delta", "")
            if not audio_delta:
                return
            
            # Get connection settings
            openai_config = self.openai_connections.get(stream_id)
            if not openai_config:
                return
                
            output_format = openai_config.get("output_format", "g711_ulaw")
            
            # Decode audio
            openai_audio = base64.b64decode(audio_delta)
            
            # Convert to 16kHz PCM16 Mono expected by browser/PJSUA2
            if output_format in ["g711_ulaw", "audio/pcmu"]:
                # Convert 8kHz u-law to 8kHz PCM16
                pcm_8k = self.convert_ulaw_to_pcm(openai_audio)
                # Resample 8kHz PCM16 -> 16kHz PCM16
                playback_audio = self._resample_audio(pcm_8k, 8000, 16000, stream_id=stream_id, state_key="output")
            elif output_format == "pcm16":
                # Resample 24kHz PCM16 -> 16kHz PCM16
                playback_audio = self._resample_audio(openai_audio, 24000, 16000, stream_id=stream_id, state_key="output")
            else:
                # Fallback: assume already 16kHz PCM
                playback_audio = openai_audio
                
            # Check if this is a browser widget connection — send raw audio without resampling
            browser_ws = openai_config.get("browser_websocket")
            if browser_ws:
                try:
                    # For browser: send PCM16 at its native rate (24kHz for OpenAI Realtime)
                    # The widget will create the AudioBuffer at the correct sample rate
                    if output_format in ["g711_ulaw", "audio/pcmu"]:
                        # ulaw -> pcm16 at 8kHz, then upsample to 24kHz for browser
                        pcm_8k = self.convert_ulaw_to_pcm(openai_audio)
                        browser_audio = self._resample_audio(pcm_8k, 8000, 24000, stream_id=stream_id, state_key="browser_output")
                        browser_sample_rate = 24000
                    else:
                        # Already PCM16 at 24kHz — send as-is
                        browser_audio = openai_audio
                        browser_sample_rate = 24000

                    browser_audio_b64 = base64.b64encode(browser_audio).decode('utf-8')
                    await browser_ws.send_json({
                        "event": "audio",
                        "type": "audio",
                        "audio": browser_audio_b64,
                        "audio_event": {
                            "audio_base_64": browser_audio_b64
                        },
                        "sample_rate": browser_sample_rate
                    })
                    logger.debug(f"🔊 OPENAI AUDIO → BROWSER: {len(browser_audio)} bytes PCM16 @ {browser_sample_rate}Hz")
                    return
                except Exception as ws_err:
                    logger.error(f"❌ Error sending audio delta to browser WebSocket: {ws_err}")
                    return

            if not self.sip_server:
                logger.warning(f"⚠️ SIP Server not initialized, cannot play OpenAI audio delta")
                return

            # Queue to PJSUA2 playback buffer
            await self.sip_server.send_audio_to_rtp(stream_id, playback_audio)
            logger.debug(f"🔊 OPENAI AUDIO DELTA ROUTED TO RTP: {len(openai_audio)} bytes {output_format} -> {len(playback_audio)} bytes PCM16 @ 16kHz")
            
        except Exception as e:
            logger.error(f"❌ Error handling OpenAI audio delta: {e}")

    async def cleanup_connections(self, stream_id: str):
        """Clean up OpenAI connections and buffers"""
        try:
            # Close OpenAI connection
            if stream_id in self.openai_connections:
                openai_config = self.openai_connections[stream_id]
                
                # --- MONGODB SAVE LOGIC (ENRICHED ANALYTICS) ---
                try:
                    duration = time.time() - openai_config["start_time"]
                    transcript = openai_config.get("transcript", [])
                    agent_name = None
                    company_name = None
                    agent_id = None
                    enterprise_id = None
                    agent_mongo_id = None
                    agent_config = openai_config.get("agent_config")
                    if agent_config:
                        agent_name = agent_config.get("name")
                        agent_id = agent_config.get("agentId")
                        enterprise_id = (
                            agent_config.get("enterprise")
                            or agent_config.get("createdBy")
                            or agent_config.get("enterprise_id")
                        )
                        agent_mongo_id = agent_config.get("_id")
                        try:
                            from core.agent_resolver import get_company_name
                            company_name = await get_company_name(enterprise_id)
                        except Exception:
                            pass

                    from core.analytics_manager import save_enriched_call_log
                    asyncio.create_task(
                        save_enriched_call_log(
                            call_id=stream_id,
                            duration=duration,
                            transcript=transcript,
                            to_phone=openai_config.get("to_phone", "default"),
                            direction=openai_config.get("direction", "inbound"),
                            agent_name=agent_name,
                            company_name=company_name,
                            agent_id=agent_id,
                            enterprise_id=enterprise_id,
                            agent_mongo_id=agent_mongo_id
                        )
                    )
                except Exception as db_err:
                    logger.error(f"Failed to save call log: {db_err}")
                
                openai_ws = openai_config["websocket"]
                try:
                    if hasattr(openai_ws, "closed"):
                        if not openai_ws.closed:
                            await openai_ws.close()
                    else:
                        from websockets.protocol import State
                        if openai_ws.state != State.CLOSED:
                            await openai_ws.close()
                except Exception:
                    try:
                        await openai_ws.close()
                    except Exception:
                        pass
                del self.openai_connections[stream_id]
                logger.info(f"🧹 OPENAI CONNECTION REMOVED: {stream_id}")
            
            # Clean up audio buffers and settings
            if stream_id in self.audio_buffers:
                del self.audio_buffers[stream_id]
                logger.info(f"🧹 AUDIO BUFFER CLEARED: {stream_id}")
            
            if stream_id in self.connection_sample_rates:
                del self.connection_sample_rates[stream_id]
            
            if stream_id in self.connection_chunk_sizes:
                del self.connection_chunk_sizes[stream_id]
                
        except Exception as e:
            logger.error(f"❌ Error during cleanup: {e}")




async def main():
    """Enhanced main function to start the OpenAI Realtime Sales Bot"""
    try:
        # Initialize the enhanced sales bot
        sales_bot = OpenAIRealtimeSalesBot()
        
        # Start the enhanced WebSocket server
        await sales_bot.start_server()
        
    except Exception as e:
        logger.error(f'❌ Enhanced Server Error: {e}')
        raise


if __name__ == "__main__":
    asyncio.run(main()) 