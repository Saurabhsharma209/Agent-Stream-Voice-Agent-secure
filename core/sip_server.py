#!/usr/bin/env python3
"""
SIP Server for Direct Exotel Trunking Integration using PJSUA2
Receives incoming SIP calls from Exotel and bridges with OpenAI Realtime API.
Fully supports TLS transport on port 5060.
"""

import asyncio
import logging
import json
import base64
import time
import threading
import os
import re
from typing import Dict, Any, Optional
from dataclasses import dataclass

try:
    import pjsua2 as pj
except ImportError:
    raise ImportError(
        "❌ pjsua2 is not installed! Since the bot is running in SIP-Only mode, "
        "pjsua2 is required. Install it using: pip install pjsua2-py"
    )

AudioMediaPortBase = pj.AudioMediaPort
CallBase = pj.Call
AccountBase = pj.Account
LogWriterBase = pj.LogWriter
INVALID_ID = pj.PJSUA_INVALID_ID
    
from config import Config

logger = logging.getLogger(__name__)

@dataclass
class SIPCallState:
    """Represents a single SIP call session"""
    call_id: str
    from_uri: str
    to_uri: str
    contact_uri: str
    start_time: float
    openai_connected: bool = False
    audio_buffer: bytes = b""
    playback_buffer: bytes = b""  # Buffer to store audio coming from OpenAI
    sample_rate: int = 16000
    rtp_port: int = 0
    is_playing: bool = False      # Track if currently playing (jitter pre-buffering active)

class OpenAIAudioPort(AudioMediaPortBase):
    """Custom AudioMediaPort subclass that captures raw audio from PJSIP and routes to OpenAI"""
    
    def __init__(self, call_id: str, sip_server):
        super().__init__()
        self.call_id = call_id
        self.sip_server = sip_server
        
    def onFrameReceived(self, frame):
        """Called by PJSUA2 when a recorded frame (RTP) is received from the caller"""
        try:
            if not hasattr(self, "_rx_count"):
                self._rx_count = 0
            self._rx_count += 1
            if self._rx_count % 100 == 0:
                logger.info(f"DEBUG: onFrameReceived called {self._rx_count} times for call {self.call_id}")

            call_state = self.sip_server.sip_calls.get(self.call_id)
            if call_state and call_state.openai_connected:
                # Get the raw PCM16 bytes from the SWIG ByteVector
                audio_data = bytes(frame.buf)
                
                # Dispatch audio processing to the main asyncio thread loop asynchronously
                asyncio.run_coroutine_threadsafe(
                    self.sip_server.openai_bot.send_audio_to_openai(
                        self.call_id, 
                        audio_data, 
                        sample_rate=call_state.sample_rate
                    ),
                    self.sip_server.loop
                )
        except Exception as e:
            logger.error(f"❌ Error in onFrameReceived: {e}")
            
    def onFrameRequested(self, frame):
        """Called by PJSUA2 when PJSIP needs a playback frame to send back to the caller"""
        try:
            call_state = self.sip_server.sip_calls.get(self.call_id)
            if call_state and call_state.openai_connected:
                req_size = frame.size
                
                # Check pre-buffering state (jitter buffer)
                # We wait to accumulate at least 150ms of audio (4800 bytes for 16kHz PCM16 Mono)
                # before we start playing to eliminate network/websocket jitter.
                threshold_bytes = 4800 
                
                if not call_state.is_playing:
                    if len(call_state.playback_buffer) >= threshold_bytes:
                        call_state.is_playing = True
                        logger.info(f"🔊 Jitter buffer filled ({len(call_state.playback_buffer)} bytes) -> Starting playback for {self.call_id}")
                    else:
                        # Provide comfort silence while buffering
                        frame.buf[:] = b"\x00" * req_size
                        frame.type = pj.PJMEDIA_FRAME_TYPE_AUDIO
                        return
                
                # Retrieve frame chunk from playout buffer
                chunk = call_state.playback_buffer[:req_size]
                call_state.playback_buffer = call_state.playback_buffer[req_size:]
                
                # Pad with silence if playback buffer does not have enough bytes
                if len(chunk) < req_size:
                    chunk += b"\x00" * (req_size - len(chunk))
                    call_state.is_playing = False # Starved of audio, reset play status
                    logger.debug(f"🔇 Playback buffer empty -> Pre-buffering reset for {self.call_id}")
                    
                frame.buf[:] = chunk
                frame.type = pj.PJMEDIA_FRAME_TYPE_AUDIO
            else:
                # Provide comfort silence
                silence = b"\x00" * frame.size
                frame.buf[:] = silence
                frame.type = pj.PJMEDIA_FRAME_TYPE_AUDIO
        except Exception as e:
            logger.error(f"❌ Error in onFrameRequested: {e}")

    def __del__(self):
        try:
            if hasattr(self, 'sip_server') and self.sip_server and hasattr(self.sip_server, 'ep'):
                try:
                    self.sip_server.ep.libRegisterThread("Python_GC_AudioPort")
                except Exception:
                    pass
        except Exception:
            pass

class MyCall(CallBase):
    """Subclass of pj.Call to manage individual call states and media connections"""
    
    def __init__(self, acc, sip_server, call_id=INVALID_ID):
        super().__init__(acc, call_id)
        self.sip_server = sip_server
        self.media_port = None
        self.sip_call_id = None
        
    def __del__(self):
        try:
            if hasattr(self, 'sip_server') and self.sip_server and hasattr(self.sip_server, 'ep'):
                try:
                    self.sip_server.ep.libRegisterThread("Python_GC_Call")
                except Exception:
                    pass
        except Exception:
            pass
        
    def onCallState(self, prm):
        try:
            ci = self.getInfo()
            self.sip_call_id = ci.callIdString
            logger.info(f"📞 Call state: {ci.stateText} - {ci.remoteUri} (SIP ID: {self.sip_call_id})")
            
            if ci.state == pj.PJSIP_INV_STATE_DISCONNECTED:
                logger.info(f"📞 Call disconnected: {ci.lastReason} (Status Code: {ci.lastStatusCode})")
                asyncio.run_coroutine_threadsafe(
                    self.sip_server.cleanup_call(self.sip_call_id),
                    self.sip_server.loop
                )
        except Exception as e:
            logger.error(f"❌ Error in onCallState: {e}")
            
    def onCallMediaState(self, prm):
        try:
            ci = self.getInfo()
            logger.info(f"🎵 Call media state changed for call {ci.callIdString}")
            
            # Prevent mid-call media renegotiations from resetting the session and history
            call_state = self.sip_server.sip_calls.get(self.sip_call_id)
            if call_state and call_state.openai_connected:
                logger.info(f"⏭️ Call {self.sip_call_id} is already bridged. Ignoring media state change to prevent reset.")
                return
            
            # Loop through call media streams to find active audio
            for i in range(len(ci.media)):
                mi = ci.media[i]
                if mi.type == pj.PJMEDIA_TYPE_AUDIO and mi.status == pj.PJSUA_CALL_MEDIA_ACTIVE:
                    logger.info(f"✅ Active audio media found at index {mi.index}")
                    
                    # Retrieve the Call's AudioMedia
                    call_med = self.getAudioMedia(mi.index)
                    
                    # Instantiate our custom AudioMediaPort
                    self.media_port = OpenAIAudioPort(self.sip_call_id, self.sip_server)
                    
                    # Configure audio format (16kHz PCM16 Mono 20ms frames)
                    fmt = pj.MediaFormatAudio()
                    fmt.type = pj.PJMEDIA_TYPE_AUDIO
                    fmt.clockRate = 16000
                    fmt.channelCount = 1
                    fmt.bitsPerSample = 16
                    fmt.frameTimeUsec = 20000
                    
                    self.media_port.createPort("OpenAI_Port", fmt)
                    
                    # Record call session details
                    vnm = getattr(self, 'virtual_number', None) or ci.localUri
                    call_state = SIPCallState(
                        call_id=self.sip_call_id,
                        from_uri=ci.remoteUri,
                        to_uri=vnm,
                        contact_uri=ci.remoteContact,
                        start_time=time.time(),
                        sample_rate=16000,
                        openai_connected=False
                    )
                    self.sip_server.sip_calls[self.sip_call_id] = call_state
                    
                    # Store call instance in server mapping
                    self.sip_server._call_objects[self.sip_call_id] = self
                    
                    # Bridge call media: call captures caller voice -> routes to our OpenAI port
                    call_med.startTransmit(self.media_port)
                    
                    # Bridge call media: our OpenAI port captures OpenAI voice -> routes to call playback
                    self.media_port.startTransmit(call_med)
                    
                    logger.info(f"🌉 Audio bridge established successfully for call {self.sip_call_id}")
                    
                    # Trigger OpenAI connection bridging
                    asyncio.run_coroutine_threadsafe(
                        self.sip_server._bridge_call_to_openai(self.sip_call_id, self),
                        self.sip_server.loop
                    )
                    break
        except Exception as e:
            logger.error(f"❌ Error in onCallMediaState: {e}")
            
    def onDtmfDigit(self, prm):
        logger.info(f"🔢 DTMF Digit received: {prm.digit}")

def is_valid_exotel_ip_or_domain(uri: str) -> bool:
    """Network-level firewall controls IP filtering. All incoming SIP trunk calls are accepted."""
    return True

class MyAccount(AccountBase):
    """Subclass of pj.Account to handle inbound SIP calls"""
    
    def __init__(self, sip_server):
        super().__init__()
        self.sip_server = sip_server
        
    def onIncomingCall(self, prm):
        try:
            logger.info(f"🔔 INCOMING SIP CALL (ID: {prm.callId})")
            
            # Create a MyCall object to handle incoming call
            call = MyCall(self, self.sip_server, prm.callId)
            
            # Retrieve call details
            try:
                ci = call.getInfo()
                remote_uri = ci.remoteUri
                local_uri = ci.localUri
                call_id_str = ci.callIdString
                
                # Extract Virtual Number from raw SIP packet headers if present
                if hasattr(prm, 'rdata') and hasattr(prm.rdata, 'wholeMsg'):
                    raw_sip = prm.rdata.wholeMsg or ""
                    matches = re.findall(r'(?:To:\s*<sip:|sip:|\+91|0)([0-9]{10,11})@', raw_sip, re.IGNORECASE)
                    if not matches:
                        matches = re.findall(r'([0-9]{10})', raw_sip)
                    if matches:
                        call.virtual_number = matches[0]
                        logger.info(f"📱 Extracted Virtual Number from SIP headers: {matches[0]}")
            except Exception as e:
                logger.error(f"❌ Error getting call info for callId={prm.callId}: {e}")
                # Fallback to reject to be safe
                call_prm = pj.CallOpParam()
                call_prm.statusCode = 403
                call.answer(call_prm)
                return

            # Reject all calls if AI engines are disabled
            if getattr(Config, 'DISABLE_AI_ENGINES', False):
                logger.info(f"🚫 REJECTED SIP CALL (AI Engines Disabled): remote={remote_uri}")
                call_prm = pj.CallOpParam()
                call_prm.statusCode = 503  # Service Unavailable
                call.answer(call_prm)
                return

            # Security check: verify that the call is from Exotel carrier trunk
            is_valid_exotel = is_valid_exotel_ip_or_domain(remote_uri) or is_valid_exotel_ip_or_domain(local_uri)
            
            if not is_valid_exotel:
                logger.warning(f"🛡️ REJECTED UNAUTHORIZED SIP CALL (Scanner Blocked): remote={remote_uri}, local={local_uri}")
                call_prm = pj.CallOpParam()
                call_prm.statusCode = 403  # Forbidden
                call.answer(call_prm)
                return

            # Keep the call object alive in Python to prevent immediate garbage collection & call drop
            self.sip_server._call_objects[call_id_str] = call
            logger.info(f"📌 Kept call reference alive for authorized call {call_id_str}")
            
            # Answer with 200 OK status
            call_prm = pj.CallOpParam()
            call_prm.statusCode = 200
            call.answer(call_prm)
            logger.info(f"✅ Answered incoming SIP call successfully: remote={remote_uri}")
            
        except Exception as e:
            logger.error(f"❌ Error answering call: {e}")


class MyLogWriter(LogWriterBase):
    """Bridges PJSUA2 internal native logs into Python's logging module"""
    
    def write(self, entry):
        msg = entry.msg.strip()
        if entry.level <= 2:
            logger.warning(f"[PJSIP] {msg}")
        elif entry.level <= 3:
            logger.info(f"[PJSIP] {msg}")
        else:
            logger.debug(f"[PJSIP] {msg}")

class SIPServer:
    """SIP Server for receiving calls from Exotel via direct SIP trunk using PJSUA2"""
    
    def __init__(self, openai_bot=None):
        self.openai_bot = openai_bot
        self.sip_account_inbound = None
        self.sip_calls: Dict[str, SIPCallState] = {}
        self.pjsua_initialized = False
        self._call_objects: Dict[str, MyCall] = {}
        self.loop = None
        
        # Configuration
        self.sip_host = Config.SIP_SERVER_HOST
        self.sip_port = Config.SIP_SERVER_PORT
        self.inbound_enabled = Config.INBOUND_SIP_ENABLED
        
        logger.info("🔌 SIP Server initialized (INBOUND ONLY via PJSUA2)")
        logger.info(f"📍 SIP Server Port: {self.sip_port}")
        logger.info(f"📥 Inbound vSIP: {'✅ ENABLED' if self.inbound_enabled else '❌ DISABLED'} (IP-based auth)")
        
    def initialize_pjsua(self):
        """Initialize PJSUA2 SIP stack with Endpoint, MediaConfig, and TransportConfig"""
        if not pj:
            raise ImportError("pjsua2-py not installed. Run: pip install pjsua2-py")
            
        try:
            # Create Endpoint instance
            self.ep = pj.Endpoint()
            self.ep.libCreate()
            
            # Configure EpConfig
            ep_cfg = pj.EpConfig()
            ep_cfg.logConfig.level = 3
            self.log_writer = MyLogWriter()
            ep_cfg.logConfig.writer = self.log_writer
            ep_cfg.uaConfig.maxCalls = Config.MAX_CONCURRENT_CALLS
            ep_cfg.uaConfig.userAgent = "Voice-AI-Bot/1.0 (SIP Inbound Only)"
            
            # Configure Media Settings
            ep_cfg.medConfig.clockRate = 16000
            ep_cfg.medConfig.sndClockRate = 16000
            ep_cfg.medConfig.channelCount = 1
            ep_cfg.medConfig.hasIoqueue = True
            
            # Initialize library
            self.ep.libInit(ep_cfg)
            
            # Set null sound device for headless environments
            try:
                self.ep.audDevManager().setNullDev()
                logger.info("🔇 Configured null audio device for headless container")
            except Exception as aud_err:
                logger.warning(f"⚠️ Could not set null audio device: {aud_err}")
            
            # Configure Transport Settings
            tp_cfg = pj.TransportConfig()
            tp_cfg.port = self.sip_port
            tp_cfg.boundAddress = "0.0.0.0"
            
            # Configure TLS Transport if set in environment (highly compliant with Exotel TLS trunk)
            transport_type = os.getenv('EXOTEL_SIP_TRANSPORT', 'TLS').upper()
            if transport_type == 'TLS':
                tls_cfg = pj.TlsConfig()
                cert_file = "certs/server.crt"
                key_file = "certs/server.key"
                
                tls_cfg.certFile = os.path.abspath(cert_file)
                tls_cfg.privKeyFile = os.path.abspath(key_file)
                tls_cfg.verifyClient = False
                tls_cfg.requireClientCert = False
                
                tp_cfg.tlsConfig = tls_cfg
                
                self.transport_id = self.ep.transportCreate(pj.PJSIP_TRANSPORT_TLS, tp_cfg)
                logger.info(f"✅ SIP Transport created: TLS/0.0.0.0:{self.sip_port}")
                logger.info(f"🌐 Public endpoint: tls://{Config.SIP_PUBLIC_IP or self.sip_host}:{self.sip_port}")
            else:
                # Create UDP Transport
                try:
                    self.udp_transport_id = self.ep.transportCreate(pj.PJSIP_TRANSPORT_UDP, tp_cfg)
                    logger.info(f"✅ SIP Transport created: UDP/0.0.0.0:{self.sip_port}")
                except Exception as e:
                    logger.error(f"❌ Failed to create UDP transport: {e}")
                
                # Create TCP Transport
                try:
                    self.tcp_transport_id = self.ep.transportCreate(pj.PJSIP_TRANSPORT_TCP, tp_cfg)
                    logger.info(f"✅ SIP Transport created: TCP/0.0.0.0:{self.sip_port}")
                except Exception as e:
                    logger.error(f"❌ Failed to create TCP transport: {e}")
                
                self.transport_id = getattr(self, "tcp_transport_id", getattr(self, "udp_transport_id", None))
                logger.info(f"🌐 Public endpoints listening on: sip://{Config.SIP_PUBLIC_IP or self.sip_host}:{self.sip_port} (UDP & TCP)")
                
            # Start Endpoint
            self.ep.libStart()
            
            # Create inbound account matching any incoming SIP calls
            if self.inbound_enabled:
                self.sip_account_inbound = MyAccount(self)
                acc_cfg = pj.AccountConfig()
                acc_cfg.idUri = "sip:vbot@0.0.0.0"
                self.sip_account_inbound.create(acc_cfg)
                logger.info("📥 Inbound PJSUA2 account created (IP-based auth, no credentials)")
                
            logger.info("✅ PJSUA2 stack initialized successfully!")
            self.pjsua_initialized = True
            
        except Exception as e:
            logger.error(f"❌ Failed to initialize PJSUA2: {e}")
            raise
            
    async def _bridge_call_to_openai(self, call_id: str, call):
        """Bridge a SIP call with OpenAI Realtime API using dynamically resolved agent configuration"""
        try:
            if not self.openai_bot:
                logger.info("ℹ️ OpenAI bot instance is None, instantiating on-the-fly...")
                try:
                    from core.openai_realtime_sales_bot import OpenAIRealtimeSalesBot
                    self.openai_bot = OpenAIRealtimeSalesBot()
                    self.openai_bot.sip_server = self
                except Exception as inst_err:
                    logger.error(f"❌ OpenAI bot reference not available and auto-instantiation failed: {inst_err}")
                    prm = pj.CallOpParam(True)
                    prm.statusCode = 500
                    call.hangup(prm)
                    return
                
            call_state = self.sip_calls[call_id]
            logger.info(f"🌉 BRIDGING SIP CALL {call_id} to OpenAI")
            
            # Dynamically resolve agent configuration from MongoDB by virtual number / destination URI
            agent_config = None
            try:
                from core.agent_resolver import resolve_agent_config
                destination_id = getattr(call_state, 'to_uri', '') or getattr(call_state, 'from_uri', '')
                agent_config = await resolve_agent_config(destination_id)
                if not agent_config and hasattr(call_state, 'from_uri') and call_state.from_uri:
                    agent_config = await resolve_agent_config(call_state.from_uri)
            except Exception as resolve_err:
                logger.error(f"⚠️ Error resolving agent config for SIP call {call_id}: {resolve_err}")

            # Per-recipient language override — set via the bulk-upload "language" file column
            # (routes/call_routes.py), stored on the matching outbound_calls record. Applied here,
            # before the modular/realtime dispatch below, so it reaches BOTH engines (previously
            # the Realtime dispatch path bypassed this entirely since it never goes through
            # modular_sales_bot.py).
            if agent_config:
                try:
                    from core.mongo_manager import mongo_db
                    if mongo_db.client is not None:
                        from_uri = getattr(call_state, 'from_uri', '') or ''
                        to_uri = getattr(call_state, 'to_uri', '') or ''
                        clean_from = "".join(filter(str.isdigit, from_uri))[-10:]
                        clean_to = "".join(filter(str.isdigit, to_uri))[-10:]
                        now_ts = time.time()
                        db = mongo_db.client.get_default_database()
                        cursor = db['outbound_calls'].find({"timestamp": {"$gt": now_ts - 600}}).sort("timestamp", -1)
                        outbound_record = None
                        candidates = []
                        async for record in cursor:
                            candidates.append(record)
                            clean_record = "".join(filter(str.isdigit, str(record.get("phone_number", ""))))[-10:]
                            if clean_record and (clean_record == clean_from or clean_record == clean_to):
                                outbound_record = record
                                break
                        if not outbound_record and candidates:
                            for cand in candidates:
                                if (now_ts - cand.get("timestamp", 0)) <= 180:
                                    outbound_record = cand
                                    break
                        call_language = (outbound_record.get("context") or {}).get("language") if outbound_record else None
                        if call_language:
                            agent_config = {**agent_config, "language": call_language, "languages": [call_language]}
                            logger.info(f"🌐 Per-recipient language override for this outbound call: '{call_language}'")
                except Exception as lang_err:
                    logger.error(f"⚠️ Failed to apply per-recipient language override for call {call_id}: {lang_err}")

            if agent_config:
                mode = agent_config.get("voice_bot_mode", "modular")
                logger.info(f"🎯 Resolved agent '{agent_config.get('name')}' (mode: {mode}, languages: {agent_config.get('languages')}) for call {call_id}")
                
                if mode == "modular":
                    logger.info(f"🔀 Agent mode is 'modular'. Routing call {call_id} to Modular Sales Bot Engine!")
                    try:
                        from core.modular_sales_bot import ModularSalesBot
                        if not hasattr(self, 'modular_bot') or not self.modular_bot:
                            self.modular_bot = ModularSalesBot(sip_server=self)
                        await self.modular_bot.connect_to_openai_enhanced(call_id, agent_config=agent_config)
                        call_state.openai_connected = True
                        return
                    except Exception as mod_err:
                        logger.error(f"❌ Error connecting call to Modular engine: {mod_err}. Falling back to Realtime.")
            else:
                logger.warning(f"⚠️ No dynamic agent found for SIP call {call_id}, using default agent config")
            
            # Set sample rate for SIP calls (16kHz)
            if hasattr(self.openai_bot, 'connection_sample_rates'):
                self.openai_bot.connection_sample_rates[call_id] = 16000
            
            # Connect to OpenAI Realtime API WITH the resolved agent_config
            await self.openai_bot.connect_to_openai_enhanced(call_id, agent_config=agent_config)
            call_state.openai_connected = True
            logger.info(f"✅ OpenAI connection established for {call_id}")
            
        except Exception as e:
            logger.error(f"❌ Error bridging call to OpenAI: {e}")
            try:
                prm = pj.CallOpParam(True)
                prm.statusCode = 500
                call.hangup(prm)
            except:
                pass
            finally:
                await self.cleanup_call(call_id)
                
    async def send_audio_to_rtp(self, call_id: str, audio: bytes):
        """Append outbound audio from OpenAI to the call's playback buffer"""
        if call_id in self.sip_calls:
            self.sip_calls[call_id].playback_buffer += audio
            
    async def cleanup_call(self, call_id: str):
        """Clean up SIP call resources and transmit SIP BYE to hang up the line"""
        try:
            call = self._call_objects.get(call_id)
            if not call:
                for k, v in list(self._call_objects.items()):
                    if v and getattr(v, 'sip_call_id', None) == call_id:
                        call = v
                        break

            # Send SIP BYE FIRST before removing object reference
            if call:
                try:
                    if hasattr(self, 'ep'):
                        try:
                            self.ep.libRegisterThread("Async_Cleanup")
                        except:
                            pass
                    prm = pj.CallOpParam(True)
                    prm.statusCode = 200
                    call.hangup(prm)
                    logger.info(f"📞 SIP BYE sent successfully for call {call_id}")
                except Exception as hangup_err:
                    logger.warning(f"⚠️ PJSIP hangup error for call {call_id}: {hangup_err}")

            # Now clean up call object reference
            if call_id in self._call_objects:
                del self._call_objects[call_id]
            for k, v in list(self._call_objects.items()):
                if v == call:
                    try:
                        del self._call_objects[k]
                    except KeyError:
                        pass

            # Clean up media buffers and OpenAI connections
            if call_id in self.sip_calls:
                call_state = self.sip_calls[call_id]
                
                # Disconnect from OpenAI
                if call_state.openai_connected and self.openai_bot:
                    await self.openai_bot.cleanup_connections(call_id)
                    
                # Remove call state
                del self.sip_calls[call_id]
                
                duration = time.time() - call_state.start_time
                logger.info(f"🧹 Cleaned up SIP call {call_id} (duration: {duration:.1f}s)")
            else:
                logger.info(f"🧹 Cleaned up SIP call reference {call_id} (no active media session)")
        except Exception as e:
            logger.error(f"❌ Error cleaning up call: {e}")
            
    async def start(self):
        """Start the SIP server and begin event loop thread"""
        try:
            self.loop = asyncio.get_running_loop()
            
            if not self.pjsua_initialized:
                self.initialize_pjsua()
                
            # Start PJSIP event processing daemon thread
            threading.Thread(target=self._pjsua_event_loop, daemon=True).start()
            logger.info("🚀 SIP Server started and listening for direct trunk connections!")
            
        except Exception as e:
            logger.error(f"❌ Failed to start SIP server: {e}")
            raise
            
    def _pjsua_event_loop(self):
        """PJSUA event processing loop"""
        try:
            # Register this worker thread with PJSIP so callbacks can run securely in Python
            self.ep.libRegisterThread("PJSIP_Event_Loop")
            
            while self.pjsua_initialized:
                self.ep.libHandleEvents(10)
                time.sleep(0.01)
        except Exception as e:
            logger.error(f"❌ PJSUA event loop error: {e}")
            
    async def stop(self):
        """Stop SIP server and release all resources"""
        try:
            self.pjsua_initialized = False
            
            # Hangup all active sessions
            for call_id in list(self.sip_calls.keys()):
                await self.cleanup_call(call_id)
                
            # Destroy PJSIP Endpoint
            if hasattr(self, 'ep'):
                self.ep.libDestroy()
                logger.info("✅ PJSUA2 Endpoint destroyed")
                
            logger.info("🛑 SIP Server stopped")
        except Exception as e:
            logger.error(f"❌ Error stopping SIP server: {e}")
