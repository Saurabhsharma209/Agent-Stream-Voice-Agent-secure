#!/usr/bin/env python3
"""
FastAPI Core Gateway for Voice AI Bot Backend
Integrates modular routes, implements the WebSocket telephony adapter,
and serves a polished interactive dashboard with Swagger / OpenAPI support.
"""

import os
import logging
import asyncio
from contextlib import asynccontextmanager
from typing import Optional, List, Dict, Any, Union
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.encoders import ENCODERS_BY_TYPE
from bson import ObjectId
import datetime
from config import Config

# GLOBAL BEST PRACTICE: Teach FastAPI how to serialize MongoDB BSON types automatically
ENCODERS_BY_TYPE[ObjectId] = str
ENCODERS_BY_TYPE[datetime.datetime] = lambda dt: dt.isoformat() + "Z" if dt.tzinfo is None else dt.isoformat()

# Import routes
from routes.call_routes import router as call_router
from routes.bot_routes import router as bot_router
from routes.agent_routes import router as agent_router

# Configure Logger
logging.basicConfig(level=logging.INFO, format=Config.LOG_FORMAT)
logger = logging.getLogger(__name__)

# Import controllers to track streaming connections
from controllers import bot_controller

# Initialize Core AI Voice Bot Engine based on configuration mode
if Config.VOICE_BOT_MODE == "modular":
    logger.info("🤖 Starting Voice Bot in MODULAR Mode (Deepgram + Gemini + Cartesia)")
    try:
        from core.modular_sales_bot import ModularSalesBot
        sales_bot_engine = ModularSalesBot()
    except Exception as e:
        logger.error(f"❌ Error loading ModularSalesBot, falling back to OpenAI Realtime: {e}")
        from core.openai_realtime_sales_bot import OpenAIRealtimeSalesBot
        sales_bot_engine = OpenAIRealtimeSalesBot()
else:
    logger.info("🤖 Starting Voice Bot in REALTIME Mode (OpenAI Realtime API)")
    from core.openai_realtime_sales_bot import OpenAIRealtimeSalesBot
    sales_bot_engine = OpenAIRealtimeSalesBot()

# Lifespan context manager to handle database initialization and background services
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup logic
    
    # MongoDB metadata initialization is handled dynamically via motor connection.
    logger.info("🗄️ Metadata storage fully migrated to MongoDB Atlas.")

    sip_task = None
    if Config.USE_SIP_TRUNK:
        logger.info("📡 [Lifespan] USE_SIP_TRUNK is True. Starting Direct SIP Trunking Server in background...")
        try:
            # Run start_server in the background asyncio event loop
            sip_task = asyncio.create_task(sales_bot_engine.start_server())
            logger.info("📡 [Lifespan] SIP Server task scheduled successfully.")
        except Exception as e:
            logger.error(f"❌ [Lifespan] Failed to start/schedule SIP Server: {e}")
            
    yield
    
    # Shutdown logic
    if Config.USE_SIP_TRUNK:
        logger.info("🛑 [Lifespan] Stopping SIP Server...")
        if sales_bot_engine.sip_server:
            try:
                await sales_bot_engine.sip_server.stop()
                logger.info("🛑 [Lifespan] SIP Server stopped successfully.")
            except Exception as e:
                logger.error(f"❌ [Lifespan] Error stopping SIP server: {e}")
        if sip_task and not sip_task.done():
            sip_task.cancel()

# Initialize FastAPI App with customizable description for Swagger Docs
app = FastAPI(
    title="🤖 Enterprise Voice AI Agent API Server",
    description="""
    ## Carrier-Grade Voice AI Backend Integration Gateway.
    
    This API suite provides orchestrating endpoints to manage the lifecycle of conversational voice interactions.
    It integrates **OpenAI Realtime API** low-latency speech synthesis with **Exotel SIP/WebSocket** interfaces.
    
    ### 🎙️ Real-Time WebSocket Streaming Endpoints:
    * **Browser Embed Widget Stream**: `wss://ai-agent-stream.chauwk.com/api/v1/stream/browser?agent_id=default`
      * **Protocol:** Bidirectional WebSocket (PCM16 @ 16kHz Mono)
      * **Query Params:** `agent_id` (e.g. `default`, `agent_sales_01`)
      * **Client -> Server Event:** `{"event": "media", "media": {"payload": "<base64_pcm16>"}}`
      * **Server -> Client Events:**
        * `{"event": "audio", "audio": "<base64_pcm16>"}` (AI speech output)
        * `{"event": "clear"}` (Customer interruption signal)
        * `{"event": "status", "status": "listening"}` (State change)

    ### 📞 Core Operations:
    * **Call Management:** Initiate outbound leads calling campaigns, fetch active telephony status, and register callback webhooks.
    * **AI Personality Configuration:** Hot-reload prompting setups, voice character variants, and models dynamically.
    * **Low-Latency Streaming:** Bridging carrier RTP/PCM16 packets directly with OpenAI endpoints.
    """,
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    contact={
        "name": "Tech Support Integration Gateway",
        "url": "https://your-enterprise-portal.com",
    },
    lifespan=lifespan
)

# Configure CORS Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount static files directory
import os as _os
_static_dir = _os.path.join(_os.path.dirname(__file__), "static")
_os.makedirs(_static_dir, exist_ok=True)
app.mount("/static", StaticFiles(directory=_static_dir), name="static")

# Register routers
app.include_router(call_router)
app.include_router(bot_router)
app.include_router(agent_router)
from routes.crm_routes import router as crm_router
app.include_router(crm_router)

from fastapi import APIRouter
calls_log_router = APIRouter(prefix="/api/v1/calls", tags=["Call Logs"])

@calls_log_router.get("/logs")
async def get_call_logs(limit: int = 100):
    """Retrieve call logs from MongoDB ordered descending by date and time"""
    from core.mongo_manager import mongo_db
    if mongo_db.call_logs_collection is None:
        return []
    try:
        # Fetch matching logs sorted descending
        cursor = mongo_db.call_logs_collection.find({}).sort("timestamp", -1).limit(limit)
        logs = []
        async for doc in cursor:
            # Format _id to string for JSON serialization
            doc["_id"] = str(doc["_id"])
            # Format datetime field
            if "timestamp" in doc and doc["timestamp"]:
                doc["timestamp"] = doc["timestamp"].isoformat()
            logs.append(doc)
        return logs
    except Exception as e:
        logger.error(f"❌ Failed to fetch call logs: {e}")
        return []

@calls_log_router.delete("/logs/{call_id}")
async def delete_call_log(call_id: str):
    """Delete a call log by its call ID"""
    from core.mongo_manager import mongo_db
    if mongo_db.call_logs_collection is None:
        return {"status": "error", "message": "MongoDB not initialized"}
    try:
        result = await mongo_db.call_logs_collection.delete_one({"call_id": call_id})
        if result.deleted_count > 0:
            return {"status": "success", "message": f"Deleted log for call {call_id}"}
        else:
            return {"status": "error", "message": "Call log not found"}
    except Exception as e:
        logger.error(f"❌ Failed to delete call log {call_id}: {e}")
        return {"status": "error", "message": str(e)}

app.include_router(calls_log_router)


# ==============================================================================
# FastAPI WebSocket Telephony Adapter
# ==============================================================================

class FastAPIWebSocketAdapter:
    """
    Adapter pattern to wrap FastAPI's native WebSocket class, 
    making it fully compatible with websockets.legacy.server.WebSocketServerProtocol
    API methods used by the existing OpenAI Realtime audio streaming engine.
    """
    def __init__(self, websocket: WebSocket):
        self.websocket = websocket
        self.remote_address = (websocket.client.host, websocket.client.port) if websocket.client else ("0.0.0.0", 0)
        
    async def send(self, message: str):
        """Send standard JSON strings or audio frames back to telephony stream"""
        await self.websocket.send_text(message)
        
    async def recv(self) -> str:
        """Receive standard JSON strings or audio frames from telephony stream"""
        return await self.websocket.receive_text()
        
    def __aiter__(self):
        return self
        
    async def __anext__(self) -> str:
        try:
            return await self.recv()
        except Exception:
            raise StopAsyncIteration
            
    @property
    def closed(self) -> bool:
        # FastAPI manages connections inside context scope; default to false inside loop
        return False
        
    async def close(self, code: int = 1000):
        """Close connection cleanly"""
        await self.websocket.close(code)

@app.websocket("/stream")
async def websocket_stream_endpoint(websocket: WebSocket):
    """
    WebSocket endpoint - DISABLED in SIP-only mode.
    """
    await websocket.accept()
    logger.warning(f"🔌 [Gateway] Rejected WebSocket stream connection from {websocket.client.host if websocket.client else 'telephony gateway'} (SIP-Only mode active)")
    try:
        await websocket.send_json({"error": "WebSocket streaming is disabled. This server is configured for SIP-Only mode."})
    except Exception:
        pass
    await websocket.close(code=1008) # Policy Violation


# ==============================================================================
# Browser Embeddable Widget WebSocket Streaming Endpoint
# ==============================================================================

import uuid
import base64
import json

@app.websocket("/api/v1/stream/browser")
async def browser_stream_endpoint(websocket: WebSocket):
    """
    WebSocket Endpoint for embeddable Web Component voice agent widget (<agent-stream-voice>).
    Streams 16kHz PCM audio bidirectionally between browser widget and Voice AI Bot engine.
    """
    await websocket.accept()
    agent_id = websocket.query_params.get("agent_id", "default")
    stream_id = f"browser_{uuid.uuid4().hex[:8]}"
    logger.info(f"🌐 [Browser Widget] New connection established for agent_id='{agent_id}', stream_id='{stream_id}'")

    # Fetch agent configuration
    agent_config = None
    try:
        from core.agent_resolver import resolve_agent_config
        agent_config = await resolve_agent_config(agent_id)
    except Exception as e:
        logger.warning(f"⚠️ [Browser Widget] Could not resolve agent config for '{agent_id}': {e}")

    try:
        # Check if running Modular Sales Bot (Deepgram + Gemini + Sarvam/Cartesia)
        if type(sales_bot_engine).__name__ == "ModularSalesBot":
            logger.info(f"🌐 [Browser Widget] Connecting to Modular Sales Bot pipeline for {stream_id}")
            
            # 1. Pre-attach WebSocket so greeting audio is delivered to browser without being dropped
            sales_bot_engine.connections[stream_id] = {
                "browser_websocket": websocket
            }
            
            # 2. Initialize modular pipeline
            await sales_bot_engine.connect_to_openai_enhanced(stream_id, agent_config)
            
            # Re-verify connection succeeded
            if stream_id in sales_bot_engine.connections:
                sales_bot_engine.connections[stream_id]["browser_websocket"] = websocket
            else:
                logger.error(f"❌ [Browser Widget] Modular pipeline failed to initialize for stream '{stream_id}'. closing connection.")
                await websocket.send_json({"error": "Failed to connect to Modular pipeline. Check server logs."})
                await websocket.close(code=1011)
                return

            # 3. Main Loop: Receive microphone audio from Browser / Mobile App (supports both Chauwk & ElevenLabs format)
            while True:
                data_str = await websocket.receive_text()
                try:
                    msg = json.loads(data_str)
                    evt = msg.get("event") or msg.get("type")
                    if evt == "media" or "user_audio_chunk" in msg or "payload" in msg:
                        media_data = msg.get("media", {}) if isinstance(msg.get("media"), dict) else {}
                        payload_b64 = media_data.get("payload") or msg.get("payload") or msg.get("user_audio_chunk") or ""
                        # Default to 16000Hz (standard mobile record rate) if omitted
                        mic_sample_rate = int(media_data.get("sample_rate") or msg.get("sample_rate") or 16000)
                        if payload_b64:
                            pcm_bytes = base64.b64decode(payload_b64)
                            await sales_bot_engine.send_audio_to_openai(stream_id, pcm_bytes, sample_rate=mic_sample_rate)
                except Exception as msg_err:
                    logger.error(f"❌ Error handling browser modular audio payload: {msg_err}")

        # Fallback: Check if running OpenAI Realtime Sales Bot
        elif hasattr(sales_bot_engine, "connect_to_openai_enhanced"):
            logger.info(f"🌐 [Browser Widget] Connecting to OpenAI Realtime pipeline for {stream_id}")
            
            # 1. Pre-set connection entry with browser_websocket so initial greeting audio is delivered
            sales_bot_engine.openai_connections[stream_id] = {
                "browser_websocket": websocket
            }
            sales_bot_engine.connection_sample_rates[stream_id] = 24000
            await sales_bot_engine.connect_to_openai_enhanced(stream_id, agent_config)
            
            # Verify connection succeeded
            if stream_id not in sales_bot_engine.openai_connections:
                logger.error(f"❌ [Browser Widget] OpenAI Realtime connection failed for stream '{stream_id}'. closing connection.")
                await websocket.send_json({"error": "Failed to connect to OpenAI Realtime API. Check server logs."})
                await websocket.close(code=1011)
                return

            # 2. Switch to PCM16 format for browser (override defaults which were set for SIP/telephony)
            conn = sales_bot_engine.openai_connections[stream_id]
            conn["browser_websocket"] = websocket
            conn["input_format"] = "pcm16"
            conn["output_format"] = "pcm16"
            conn["sample_rate"] = 24000
            # Override the session_config so configure_openai_session_enhanced sends pcm16 to OpenAI
            conn["session_config"]["input_audio_format"] = "pcm16"
            conn["session_config"]["output_audio_format"] = "pcm16"
            
            # 3. Re-send session.update with PCM16 format (first one was sent inside connect_to_openai_enhanced)
            clean_config = {k: v for k, v in conn["session_config"].items() if not k.startswith('_')}
            openai_ws = conn["websocket"]
            await openai_ws.send(json.dumps({"type": "session.update", "session": clean_config}))
            logger.info(f"✅ [Browser Widget] PCM16 session.update sent for {stream_id}")

            # 4. Trigger initial greeting
            await sales_bot_engine.send_initial_greeting_enhanced(stream_id, agent_config)

            # 5. Main Loop: Receive microphone audio from Browser / Mobile App
            while True:
                data_str = await websocket.receive_text()
                try:
                    msg = json.loads(data_str)
                    evt = msg.get("event") or msg.get("type")
                    if evt == "media" or "user_audio_chunk" in msg or "payload" in msg:
                        media_data = msg.get("media", {}) if isinstance(msg.get("media"), dict) else {}
                        payload_b64 = media_data.get("payload") or msg.get("payload") or msg.get("user_audio_chunk") or ""
                        # Default to 16000Hz (standard mobile record rate) if omitted
                        mic_sample_rate = int(media_data.get("sample_rate") or msg.get("sample_rate") or 16000)
                        if payload_b64:
                            pcm_bytes = base64.b64decode(payload_b64)
                            await sales_bot_engine.send_audio_to_openai(stream_id, pcm_bytes, sample_rate=mic_sample_rate)
                except Exception as msg_err:
                    logger.error(f"❌ Error handling browser audio payload: {msg_err}")

        else:
            logger.error("❌ [Browser Widget] No active telephony voice bot engine is loaded.")
            await websocket.send_json({"error": "No voice agent engine active on server."})
            await websocket.close(code=1011)

    except WebSocketDisconnect:
        logger.info(f"🔌 [Browser Widget] Connection closed for stream_id='{stream_id}'")
    except Exception as e:
        err_msg = f"Voice Bot connection error: {e}"
        logger.error(f"❌ [Browser Widget] Exception during WebSocket session '{stream_id}': {err_msg}", exc_info=True)
        try:
            await websocket.send_json({"event": "error", "error": err_msg})
            await websocket.close(code=1011, reason=str(e)[:120])
        except Exception:
            pass
    finally:
        if hasattr(sales_bot_engine, "cleanup_connections"):
            await sales_bot_engine.cleanup_connections(stream_id)



# ==============================================================================
# Standalone Widget Iframe Endpoint & Dashboard
# ==============================================================================

@app.get("/widget", response_class=HTMLResponse, include_in_schema=False)
async def standalone_widget_page(agentId: Optional[str] = None, agent_id: Optional[str] = None):
    """Renders a standalone view of the voice agent widget for iframe embeddings."""
    target_id = agentId or agent_id or "default"
    base_url = Config.SERVER_BASE_URL.rstrip("/")
    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>AI Voice Agent Widget</title>
    <style>
        html, body {{
            margin: 0;
            padding: 0;
            background: transparent;
            overflow: hidden;
            width: 100%;
            height: 100%;
        }}
    </style>
</head>
<body>
    <agent-stream-voice agent-id="{target_id}" server-url="{base_url}" auto-open="true"></agent-stream-voice>
    <script src="{base_url}/static/voice-agent-widget.js" async></script>
</body>
</html>"""
    return HTMLResponse(content=html_content)

@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def home_dashboard():
    """Renders a beautiful modern portal summarizing gateway status and Swagger interfaces."""
    html_content = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Voice AI Agent Gateway</title>
        <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;600;800&display=swap" rel="stylesheet">
        <style>
            :root {{
                --bg: #090b11;
                --card-bg: rgba(22, 28, 45, 0.45);
                --accent: #3b82f6;
                --accent-grad: linear-gradient(135deg, #3b82f6 0%, #8b5cf6 100%);
                --text: #f3f4f6;
                --text-muted: #9ca3af;
                --border: rgba(255, 255, 255, 0.08);
            }}
            * {{
                box-sizing: border-box;
                margin: 0;
                padding: 0;
            }}
            body {{
                font-family: 'Outfit', sans-serif;
                background-color: var(--bg);
                color: var(--text);
                min-height: 100vh;
                display: flex;
                flex-direction: column;
                justify-content: center;
                align-items: center;
                padding: 2rem;
                overflow-x: hidden;
                background-image: 
                    radial-gradient(circle at 10% 20%, rgba(59, 130, 246, 0.08) 0%, transparent 40%),
                    radial-gradient(circle at 90% 80%, rgba(139, 92, 246, 0.08) 0%, transparent 40%);
            }}
            .container {{
                max-width: 800px;
                width: 100%;
                background: var(--card-bg);
                backdrop-filter: blur(16px);
                -webkit-backdrop-filter: blur(16px);
                border: 1px solid var(--border);
                border-radius: 24px;
                padding: 3rem;
                box-shadow: 0 20px 40px rgba(0,0,0,0.5);
                text-align: center;
                position: relative;
            }}
            .header h1 {{
                font-size: 2.75rem;
                font-weight: 800;
                background: var(--accent-grad);
                -webkit-background-clip: text;
                -webkit-text-fill-color: transparent;
                margin-bottom: 0.5rem;
                letter-spacing: -0.03em;
            }}
            .header p {{
                font-size: 1.125rem;
                color: var(--text-muted);
                margin-bottom: 2.5rem;
                font-weight: 300;
            }}
            .stats-grid {{
                display: grid;
                grid-template-columns: repeat(2, 1fr);
                gap: 1.5rem;
                margin-bottom: 3rem;
            }}
            .stat-card {{
                background: rgba(255,255,255,0.02);
                border: 1px solid var(--border);
                padding: 1.5rem;
                border-radius: 16px;
                display: flex;
                flex-direction: column;
                align-items: center;
                transition: transform 0.2s ease, border-color 0.2s ease;
            }}
            .stat-card:hover {{
                transform: translateY(-2px);
                border-color: rgba(59, 130, 246, 0.3);
            }}
            .stat-card .label {{
                font-size: 0.875rem;
                text-transform: uppercase;
                color: var(--text-muted);
                letter-spacing: 0.05em;
                margin-bottom: 0.5rem;
            }}
            .stat-card .value {{
                font-size: 1.5rem;
                font-weight: 600;
                color: var(--text);
            }}
            .stat-card .value.active {{
                color: #10b981;
                display: flex;
                align-items: center;
                gap: 0.5rem;
            }}
            .pulse-dot {{
                width: 10px;
                height: 10px;
                background-color: #10b981;
                border-radius: 50%;
                box-shadow: 0 0 0 0 rgba(16, 185, 129, 0.7);
                animation: pulse 1.6s infinite;
            }}
            @keyframes pulse {{
                0% {{
                    transform: scale(0.95);
                    box-shadow: 0 0 0 0 rgba(16, 185, 129, 0.7);
                }}
                70% {{
                    transform: scale(1);
                    box-shadow: 0 0 0 10px rgba(16, 185, 129, 0);
                }}
                100% {{
                    transform: scale(0.95);
                    box-shadow: 0 0 0 0 rgba(16, 185, 129, 0);
                }}
            }}
            .btn-container {{
                display: flex;
                gap: 1.25rem;
                justify-content: center;
            }}
            .btn {{
                padding: 1rem 2rem;
                border-radius: 12px;
                font-weight: 600;
                font-size: 1rem;
                text-decoration: none;
                cursor: pointer;
                transition: all 0.2s ease;
                display: inline-flex;
                align-items: center;
                gap: 0.5rem;
            }}
            .btn-primary {{
                background: var(--accent-grad);
                color: white;
                border: none;
                box-shadow: 0 4px 15px rgba(59, 130, 246, 0.3);
            }}
            .btn-primary:hover {{
                transform: translateY(-2px);
                box-shadow: 0 6px 20px rgba(59, 130, 246, 0.45);
            }}
            .btn-secondary {{
                background: transparent;
                color: var(--text);
                border: 1px solid var(--border);
            }}
            .btn-secondary:hover {{
                background: rgba(255, 255, 255, 0.05);
                border-color: var(--text-muted);
                transform: translateY(-2px);
            }}
            .footer {{
                margin-top: 3.5rem;
                font-size: 0.8125rem;
                color: var(--text-muted);
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>Voice AI Agent Gateway</h1>
                <p>Enterprise Real-Time Speech Telephony API Node</p>
            </div>
            
            <div class="stats-grid">
                <div class="stat-card">
                    <div class="label">System Status</div>
                    <div class="value active">
                        <span class="pulse-dot"></span> Online
                    </div>
                </div>
                <div class="stat-card">
                    <div class="label">Voice Character</div>
                    <div class="value">{Config.SALES_BOT_NAME} ({Config.OPENAI_VOICE})</div>
                </div>
                <div class="stat-card">
                    <div class="label">Telephony Mode</div>
                    <div class="value">Direct SIP Trunking (SIP Only)</div>
                </div>
                <div class="stat-card">
                    <div class="label">Active SIP Calls</div>
                    <div class="value" style="color: var(--accent);">{len(sales_bot_engine.sip_server.sip_calls) if (sales_bot_engine.sip_server and sales_bot_engine.sip_server.pjsua_initialized) else 0}</div>
                </div>
            </div>
            
            <div class="btn-container">
                <a href="/docs" class="btn btn-primary">
                    ⚡ Open Swagger API Docs
                </a>
                <a href="/redoc" class="btn btn-secondary">
                    📋 Open ReDoc Summary
                </a>
            </div>
            
            <div class="footer">
                &copy; 2026 {Config.COMPANY_NAME}. Protected under secure TLS 1.3 channels.
            </div>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)

@app.get("/admin", response_class=HTMLResponse, include_in_schema=False)
async def admin_portal():
    """Renders the Enterprise Multi-Tenant RAG Admin Dashboard"""
    html_content = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Enterprise Voice AI - Admin Console</title>
        <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;600;700;800&display=swap" rel="stylesheet">
        <style>
            :root {{
                --bg: #0b0f19;
                --card-bg: rgba(22, 28, 45, 0.55);
                --accent: #2563eb;
                --accent-hover: #1d4ed8;
                --accent-grad: linear-gradient(135deg, #3b82f6 0%, #8b5cf6 100%);
                --text: #f3f4f6;
                --text-muted: #9ca3af;
                --border: rgba(255, 255, 255, 0.08);
                --success: #10b981;
                --error: #ef4444;
            }}
            * {{
                box-sizing: border-box;
                margin: 0;
                padding: 0;
            }}
            body {{
                font-family: 'Outfit', sans-serif;
                background-color: var(--bg);
                color: var(--text);
                min-height: 100vh;
                display: flex;
                flex-direction: column;
                background-image: 
                    radial-gradient(circle at 10% 20%, rgba(59, 130, 246, 0.08) 0%, transparent 40%),
                    radial-gradient(circle at 90% 80%, rgba(139, 92, 246, 0.08) 0%, transparent 40%);
            }}
            header {{
                padding: 1.5rem 2rem;
                background: rgba(15, 23, 42, 0.6);
                backdrop-filter: blur(12px);
                border-bottom: 1px solid var(--border);
                display: flex;
                justify-content: space-between;
                align-items: center;
                position: sticky;
                top: 0;
                z-index: 100;
            }}
            header h1 {{
                font-size: 1.5rem;
                font-weight: 700;
                background: var(--accent-grad);
                -webkit-background-clip: text;
                -webkit-text-fill-color: transparent;
            }}
            .header-links a {{
                color: var(--text-muted);
                text-decoration: none;
                margin-left: 1.5rem;
                font-size: 0.9rem;
                transition: color 0.2s;
            }}
            .header-links a:hover {{
                color: var(--text);
            }}
            .main-content {{
                flex: 1;
                max-width: 1200px;
                width: 100%;
                margin: 2rem auto;
                padding: 0 2rem;
                display: grid;
                grid-template-columns: 250px 1fr;
                gap: 2rem;
            }}
            .sidebar {{
                display: flex;
                flex-direction: column;
                gap: 0.5rem;
            }}
            .tab-btn {{
                padding: 0.85rem 1.25rem;
                background: transparent;
                border: 1px solid transparent;
                color: var(--text-muted);
                border-radius: 12px;
                text-align: left;
                font-size: 0.95rem;
                font-weight: 500;
                cursor: pointer;
                transition: all 0.2s;
                display: flex;
                align-items: center;
                gap: 0.75rem;
            }}
            .tab-btn:hover {{
                background: rgba(255,255,255,0.03);
                color: var(--text);
            }}
            .tab-btn.active {{
                background: var(--card-bg);
                border-color: var(--border);
                color: var(--text);
                box-shadow: 0 4px 12px rgba(0,0,0,0.15);
            }}
            .sub-tab-btn.active {{
                background: var(--accent) !important;
                color: white !important;
                border-color: var(--accent) !important;
                box-shadow: 0 4px 12px rgba(37, 99, 235, 0.2);
            }}
            .panel {{
                display: none;
                background: var(--card-bg);
                backdrop-filter: blur(16px);
                border: 1px solid var(--border);
                border-radius: 20px;
                padding: 2.5rem;
                box-shadow: 0 10px 30px rgba(0,0,0,0.3);
            }}
            .panel.active {{
                display: block;
            }}
            h2 {{
                font-size: 1.75rem;
                margin-bottom: 1.5rem;
                font-weight: 700;
                letter-spacing: -0.02em;
            }}
            p.panel-desc {{
                color: var(--text-muted);
                font-size: 0.95rem;
                margin-bottom: 2rem;
            }}
            .form-group {{
                margin-bottom: 1.5rem;
            }}
            label {{
                display: block;
                font-size: 0.875rem;
                font-weight: 500;
                color: var(--text-muted);
                margin-bottom: 0.5rem;
            }}
            input[type="text"], select, input[type="file"] {{
                width: 100%;
                padding: 0.75rem 1rem;
                background: rgba(255,255,255,0.03);
                border: 1px solid var(--border);
                border-radius: 10px;
                color: var(--text);
                font-family: inherit;
                font-size: 0.95rem;
                outline: none;
                transition: border-color 0.2s, background 0.2s;
            }}
            input[type="text"]:focus, select:focus {{
                border-color: rgba(59, 130, 246, 0.5);
                background: rgba(255,255,255,0.05);
            }}
            .btn {{
                padding: 0.75rem 1.5rem;
                border-radius: 10px;
                font-weight: 600;
                font-size: 0.95rem;
                cursor: pointer;
                transition: all 0.2s ease;
                display: inline-flex;
                align-items: center;
                gap: 0.5rem;
                border: none;
            }}
            .btn-primary {{
                background: var(--accent-grad);
                color: white;
                box-shadow: 0 4px 15px rgba(59, 130, 246, 0.2);
            }}
            .btn-primary:hover {{
                transform: translateY(-1px);
                box-shadow: 0 6px 20px rgba(59, 130, 246, 0.35);
            }}
            .btn-danger {{
                background: var(--error);
                color: white;
            }}
            .btn-danger:hover {{
                background: #dc2626;
            }}
            table {{
                width: 100%;
                border-collapse: collapse;
                margin-top: 1rem;
            }}
            th, td {{
                padding: 0.85rem 1rem;
                text-align: left;
                border-bottom: 1px solid var(--border);
                font-size: 0.9rem;
            }}
            th {{
                font-weight: 600;
                color: var(--text-muted);
                text-transform: uppercase;
                font-size: 0.75rem;
                letter-spacing: 0.05em;
            }}
            tr:hover td {{
                background: rgba(255,255,255,0.01);
            }}
            .status-badge {{
                display: inline-block;
                padding: 0.25rem 0.6rem;
                border-radius: 20px;
                font-size: 0.75rem;
                font-weight: 600;
                text-transform: uppercase;
            }}
            .status-processed {{
                background: rgba(16, 185, 129, 0.15);
                color: #10b981;
            }}
            .status-processing {{
                background: rgba(245, 158, 11, 0.15);
                color: #f59e0b;
            }}
            .status-failed {{
                background: rgba(239, 68, 68, 0.15);
                color: #ef4444;
            }}
            .search-results {{
                margin-top: 2rem;
                display: flex;
                flex-direction: column;
                gap: 1rem;
            }}
            .result-card {{
                background: rgba(255,255,255,0.02);
                border: 1px solid var(--border);
                border-radius: 12px;
                padding: 1.25rem;
            }}
            .result-meta {{
                font-size: 0.75rem;
                color: var(--text-muted);
                margin-bottom: 0.5rem;
                display: flex;
                justify-content: space-between;
            }}
            .result-text {{
                font-size: 0.925rem;
                line-height: 1.5;
            }}
            .alert {{
                padding: 1rem;
                border-radius: 10px;
                margin-bottom: 1.5rem;
                font-size: 0.9rem;
                display: none;
            }}
            .alert-success {{
                background: rgba(16, 185, 129, 0.15);
                color: #10b981;
                border: 1px solid rgba(16, 185, 129, 0.3);
            }}
            .alert-error {{
                background: rgba(239, 68, 68, 0.15);
                color: #ef4444;
                border: 1px solid rgba(239, 68, 68, 0.3);
            }}
            .stat-card-row {{
                display: grid;
                grid-template-columns: repeat(3, 1fr);
                gap: 1.5rem;
                margin-bottom: 2rem;
            }}
            .mini-card {{
                background: rgba(255,255,255,0.02);
                border: 1px solid var(--border);
                border-radius: 12px;
                padding: 1.25rem;
                text-align: center;
            }}
            .mini-card .label {{
                font-size: 0.75rem;
                color: var(--text-muted);
                text-transform: uppercase;
                margin-bottom: 0.25rem;
            }}
            .mini-card .value {{
                font-size: 1.5rem;
                font-weight: 600;
            }}
            footer {{
                text-align: center;
                padding: 2rem;
                font-size: 0.8rem;
                color: var(--text-muted);
                border-top: 1px solid var(--border);
                background: rgba(15, 23, 42, 0.2);
            }}
        </style>
    </head>
    <body>
        <header>
            <h1>Voice AI Bot - Admin Console</h1>
            <div class="header-links">
                <a href="/">Home Dashboard</a>
                <a href="/docs" target="_blank">Swagger API</a>
            </div>
        </header>

        <div class="main-content">
            <!-- Sidebar Navigation -->
            <div class="sidebar">
                <button class="tab-btn active" onclick="switchTab('status-panel', this)">
                    📊 Telephony Status
                </button>
                <button class="tab-btn" onclick="switchTab('companies-panel', this)">
                    🏢 Company Tenants
                </button>
                <button class="tab-btn" onclick="switchTab('docs-panel', this)">
                    📂 RAG Document Store
                </button>
                <button class="tab-btn" onclick="switchTab('sandbox-panel', this)">
                    🔍 Vector Search Sandbox
                </button>
                <button class="tab-btn" onclick="switchTab('outbound-panel', this)">
                    📞 Outbound Call Center
                </button>
                <button class="tab-btn" onclick="switchTab('calls-panel', this)">
                    📈 Call Analytics & Logs
                </button>
            </div>

            <!-- Content Panels -->
            <div class="panels-container">
                <!-- Tab 1: Status -->
                <div id="status-panel" class="panel active">
                    <h2>Telephony & Bot Status</h2>
                    <p class="panel-desc">Real-time status of current bot configurations and SIP calls.</p>
                    
                    <div class="stat-card-row">
                        <div class="mini-card">
                            <div class="label">System State</div>
                            <div class="value" style="color: #10b981;">Online</div>
                        </div>
                        <div class="mini-card">
                            <div class="label">Active Calls</div>
                            <div class="value" id="active-calls-val" style="color: var(--accent);">0</div>
                        </div>
                        <div class="mini-card">
                            <div class="label">Running Mode</div>
                            <div class="value" id="running-mode-val" style="text-transform: capitalize;">-</div>
                        </div>
                    </div>
                    
                    <div style="margin-top: 2rem;">
                        <h3>Active Engine Settings</h3>
                        <table style="margin-top: 1rem;">
                            <tbody>
                                <tr>
                                    <td>Voice Bot Name</td>
                                    <td id="cfg-bot-name">-</td>
                                </tr>
                                <tr>
                                    <td>Company Registry</td>
                                    <td id="cfg-company-name">-</td>
                                </tr>
                                <tr>
                                    <td>STT Model (Deepgram)</td>
                                    <td id="cfg-stt-model">-</td>
                                </tr>
                                <tr>
                                    <td>LLM Model (Gemini)</td>
                                    <td id="cfg-llm-model">-</td>
                                </tr>
                                <tr>
                                    <td>TTS Model (Sarvam)</td>
                                    <td id="cfg-tts-model">-</td>
                                </tr>
                            </tbody>
                        </table>
                    </div>

                    <div style="margin-top: 2.5rem; background: rgba(255,255,255,0.01); border: 1px solid var(--border); padding: 1.75rem; border-radius: 16px;">
                        <h3>Configure AI Voice Settings</h3>
                        <p style="color: var(--text-muted); font-size: 0.85rem; margin-top: 0.25rem;">Adjust the agent's vocal tone, conversational pace, and playback volume instantly.</p>
                        
                        <div id="config-voice-alert" class="alert" style="margin-top: 1rem;"></div>
                        
                        <form id="voice-settings-form" onsubmit="handleUpdateVoiceConfig(event)" style="margin-top: 1.5rem; display: grid; grid-template-columns: 1fr 1fr 1fr 1fr auto; gap: 1.25rem; align-items: end;">
                            <div class="form-group" style="margin-bottom: 0;">
                                <label for="cfg-speaker">Sarvam Voice (Speaker)</label>
                                <select id="cfg-speaker" required>
                                    <option value="neha">Neha (Female)</option>
                                    <option value="shubh">Shubh (Male)</option>
                                    <option value="ritu">Ritu (Female)</option>
                                    <option value="priya">Priya (Female)</option>
                                    <option value="pooja">Pooja (Female)</option>
                                    <option value="simran">Simran (Female)</option>
                                    <option value="kavya">Kavya (Female)</option>
                                    <option value="ishita">Ishita (Female)</option>
                                    <option value="shreya">Shreya (Female)</option>
                                    <option value="roopa">Roopa (Female)</option>
                                    <option value="amelia">Amelia (Female)</option>
                                    <option value="sophia">Sophia (Female)</option>
                                    <option value="aditya">Aditya (Male)</option>
                                    <option value="rahul">Rahul (Male)</option>
                                    <option value="rohan">Rohan (Male)</option>
                                    <option value="amit">Amit (Male)</option>
                                    <option value="dev">Dev (Male)</option>
                                    <option value="ratan">Ratan (Male)</option>
                                    <option value="varun">Varun (Male)</option>
                                    <option value="manan">Manan (Male)</option>
                                    <option value="sumit">Sumit (Male)</option>
                                    <option value="kabir">Kabir (Male)</option>
                                    <option value="aayan">Aayan (Male)</option>
                                    <option value="ashutosh">Ashutosh (Male)</option>
                                    <option value="advait">Advait (Male)</option>
                                </select>
                            </div>
                            <div class="form-group" style="margin-bottom: 0;">
                                <label for="cfg-lang">Language Code</label>
                                <select id="cfg-lang" required>
                                    <option value="en-IN">English (en-IN)</option>
                                    <option value="hi-IN">Hindi (hi-IN)</option>
                                    <option value="te-IN">Telugu (te-IN)</option>
                                    <option value="ta-IN">Tamil (ta-IN)</option>
                                    <option value="kn-IN">Kannada (kn-IN)</option>
                                    <option value="ml-IN">Malayalam (ml-IN)</option>
                                    <option value="mr-IN">Marathi (mr-IN)</option>
                                    <option value="gu-IN">Gujarati (gu-IN)</option>
                                    <option value="bn-IN">Bengali (bn-IN)</option>
                                    <option value="pa-IN">Punjabi (pa-IN)</option>
                                    <option value="od-IN">Odia (od-IN)</option>
                                </select>
                            </div>
                            <div class="form-group" style="margin-bottom: 0;">
                                <label for="cfg-pace">Speaking Pace (Speed)</label>
                                <input type="number" id="cfg-pace" step="0.05" min="0.8" max="1.5" required placeholder="e.g. 1.15">
                            </div>
                            <div class="form-group" style="margin-bottom: 0;">
                                <label for="cfg-gain">Audio Gain (Volume Boost)</label>
                                <input type="number" id="cfg-gain" step="0.1" min="1.0" max="3.0" required placeholder="e.g. 1.5">
                            </div>
                            <button type="submit" class="btn btn-primary">💾 Save Settings</button>
                        </form>
                    </div>
                </div>

                <!-- Tab 2: Companies -->
                <div id="companies-panel" class="panel">
                    <h2>Company Registry Settings</h2>
                    <p class="panel-desc">Manage multi-tenant company configurations. Each company maps to a unique Exotel virtual phone number.</p>
                    
                    <div id="company-alert" class="alert"></div>

                    <!-- Register Company Form -->
                    <div style="background: rgba(255,255,255,0.01); border: 1px solid var(--border); padding: 1.5rem; border-radius: 12px; margin-bottom: 2rem;">
                        <h3>Register New Company DID</h3>
                        <form id="create-company-form" onsubmit="handleCreateCompany(event)" style="margin-top: 1rem; display: grid; grid-template-columns: 1fr 1fr auto; gap: 1rem; align-items: end;">
                            <div class="form-group" style="margin-bottom: 0;">
                                <label for="comp-name">Company Name</label>
                                <input type="text" id="comp-name" required placeholder="e.g. Acme Corp">
                            </div>
                            <div class="form-group" style="margin-bottom: 0;">
                                <label for="comp-phone">Virtual DID Phone Number</label>
                                <input type="text" id="comp-phone" required placeholder="e.g. 91804709XXXX">
                            </div>
                            <button type="submit" class="btn btn-primary">Add Company</button>
                        </form>
                    </div>

                    <h3>Registered Businesses</h3>
                    <table id="companies-table" style="margin-top: 1rem;">
                        <thead>
                            <tr>
                                <th>Company Name</th>
                                <th>Virtual Phone Number</th>
                                <th>Actions</th>
                            </tr>
                        </thead>
                        <tbody id="companies-list">
                            <tr>
                                <td colspan="3" style="text-align: center; color: var(--text-muted);">Loading companies...</td>
                            </tr>
                        </tbody>
                    </table>
                </div>

                <!-- Tab 3: RAG Upload -->
                <div id="docs-panel" class="panel">
                    <h2>RAG Document Indexer</h2>
                    <p class="panel-desc">Ingest knowledge bases from multiple sources. We support file uploads (PDF, TXT, DOCX, PPTX), website crawling, and custom text messages/FAQ data.</p>
                    
                    <div id="docs-alert" class="alert"></div>

                    <!-- Source Sub-Tabs -->
                    <div style="display: flex; gap: 0.75rem; margin-bottom: 1.75rem;">
                        <button class="btn btn-secondary sub-tab-btn active" id="sub-tab-files" onclick="switchSubTab('files')">📂 Files Ingest</button>
                        <button class="btn btn-secondary sub-tab-btn" id="sub-tab-web" onclick="switchSubTab('web')">🌐 Web Link Ingest</button>
                        <button class="btn btn-secondary sub-tab-btn" id="sub-tab-text" onclick="switchSubTab('text')">📝 Text Message Ingest</button>
                    </div>

                    <!-- Ingest Section 1: Files -->
                    <div id="ingest-files-section" class="ingest-section" style="background: rgba(255,255,255,0.01); border: 1px solid var(--border); padding: 1.5rem; border-radius: 12px; margin-bottom: 2rem;">
                        <h3>Index Files for Tenant</h3>
                        <form id="upload-docs-form" onsubmit="handleUploadDocs(event)" style="margin-top: 1rem; display: flex; flex-direction: column; gap: 1.25rem;">
                            <div class="form-group" style="margin-bottom: 0;">
                                <label for="upload-company-select">Select Company Context</label>
                                <select id="upload-company-select" class="company-select-shared" required>
                                    <option value="">-- Select Company --</option>
                                </select>
                            </div>
                            <div class="form-group" style="margin-bottom: 0;">
                                <label for="upload-files">Upload Reference Documents (PDF, TXT, DOCX, PPTX)</label>
                                <input type="file" id="upload-files" multiple required accept=".txt,.pdf,.docx,.pptx,.ppt">
                            </div>
                            <div>
                                <button type="submit" id="upload-submit-btn" class="btn btn-primary">🚀 Upload & Index Document</button>
                            </div>
                        </form>
                    </div>

                    <!-- Ingest Section 2: Web Links -->
                    <div id="ingest-web-section" class="ingest-section" style="display: none; background: rgba(255,255,255,0.01); border: 1px solid var(--border); padding: 1.5rem; border-radius: 12px; margin-bottom: 2rem;">
                        <h3>Index Website Link for Tenant</h3>
                        <form id="upload-web-form" onsubmit="handleUploadWeb(event)" style="margin-top: 1rem; display: flex; flex-direction: column; gap: 1.25rem;">
                            <div class="form-group" style="margin-bottom: 0;">
                                <label for="web-company-select">Select Company Context</label>
                                <select id="web-company-select" class="company-select-shared" required>
                                    <option value="">-- Select Company --</option>
                                </select>
                            </div>
                            <div class="form-group" style="margin-bottom: 0;">
                                <label for="web-url">Website URL to Crawl & Extract Text</label>
                                <input type="text" id="web-url" required placeholder="e.g. https://yourcompany.com/pricing">
                            </div>
                            <div>
                                <button type="submit" id="web-submit-btn" class="btn btn-primary">🌐 Crawl & Index Webpage</button>
                            </div>
                        </form>
                    </div>

                    <!-- Ingest Section 3: Raw Text Messages -->
                    <div id="ingest-text-section" class="ingest-section" style="display: none; background: rgba(255,255,255,0.01); border: 1px solid var(--border); padding: 1.5rem; border-radius: 12px; margin-bottom: 2rem;">
                        <h3>Index Custom Text / Text Messages for Tenant</h3>
                        <form id="upload-text-form" onsubmit="handleUploadText(event)" style="margin-top: 1rem; display: flex; flex-direction: column; gap: 1.25rem;">
                            <div class="form-group" style="margin-bottom: 0;">
                                <label for="text-company-select">Select Company Context</label>
                                <select id="text-company-select" class="company-select-shared" required>
                                    <option value="">-- Select Company --</option>
                                </select>
                            </div>
                            <div class="form-group" style="margin-bottom: 0;">
                                <label for="text-source-name">Source Label (e.g. support_notes)</label>
                                <input type="text" id="text-source-name" required placeholder="e.g. sms_logs_or_faq_updates">
                            </div>
                            <div class="form-group" style="margin-bottom: 0;">
                                <label for="text-content">Raw Text Message Data / FAQ Details</label>
                                <textarea id="text-content" required placeholder="Paste text messages, logs, or custom notes here..." style="width: 100%; height: 150px; background: rgba(255,255,255,0.03); border: 1px solid var(--border); border-radius: 10px; color: var(--text); padding: 0.75rem 1rem; font-family: inherit; font-size: 0.95rem; resize: vertical; outline: none;"></textarea>
                            </div>
                            <div>
                                <button type="submit" id="text-submit-btn" class="btn btn-primary">📝 Index Custom Text</button>
                            </div>
                        </form>
                    </div>

                    <div id="company-documents-section" style="display: none;">
                        <h3 id="company-docs-title">Documents List</h3>
                        <table style="margin-top: 1rem;">
                            <thead>
                                <tr>
                                    <th>Filename</th>
                                    <th>Size</th>
                                    <th>Uptime</th>
                                    <th>RAG Status</th>
                                    <th>Actions</th>
                                </tr>
                            </thead>
                            <tbody id="company-docs-list"></tbody>
                        </table>
                    </div>
                </div>

                <!-- Tab 4: Vector Search sandbox -->
                <div id="sandbox-panel" class="panel">
                    <h2>Knowledge Base Search sandbox</h2>
                    <p class="panel-desc">Simulate and test vector database retrieval. Select a company and submit query prompts to inspect similarity hits.</p>
                    
                    <div style="background: rgba(255,255,255,0.01); border: 1px solid var(--border); padding: 1.5rem; border-radius: 12px;">
                        <h3>Query Test</h3>
                        <form id="sandbox-search-form" onsubmit="handleSandboxSearch(event)" style="margin-top: 1rem; display: grid; grid-template-columns: 1fr 2fr auto; gap: 1rem; align-items: end;">
                            <div class="form-group" style="margin-bottom: 0;">
                                <label for="search-company-select">Company context</label>
                                <select id="search-company-select" required>
                                    <option value="">-- Select Company --</option>
                                </select>
                            </div>
                            <div class="form-group" style="margin-bottom: 0;">
                                <label for="search-query">Search Query</label>
                                <input type="text" id="search-query" required placeholder="Ask details about company services, pricing, or support rules...">
                            </div>
                            <button type="submit" class="btn btn-primary">Search DB</button>
                        </form>
                    </div>

                    <div class="search-results" id="search-sandbox-results"></div>
                </div>

                <!-- Tab 5: Outbound Call Center -->
                <div id="outbound-panel" class="panel">
                    <h2>Outbound Call Center</h2>
                    <p class="panel-desc">Trigger outbound callback calls to customers. Personalize greetings and monitor call progress in real-time.</p>
                    
                    <div id="outbound-alert" class="alert"></div>

                    <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 2rem; margin-bottom: 2rem;">
                        <div style="background: rgba(255,255,255,0.01); border: 1px solid var(--border); padding: 1.5rem; border-radius: 12px;">
                            <h3>Trigger Outbound Call</h3>
                            <form id="outbound-call-form" onsubmit="handleTriggerOutboundCall(event)" style="margin-top: 1rem; display: flex; flex-direction: column; gap: 1rem;">
                                <div class="form-group" style="margin-bottom: 0;">
                                    <label for="outbound-name">Customer Name</label>
                                    <input type="text" id="outbound-name" required placeholder="e.g. John Doe">
                                </div>
                                <div class="form-group" style="margin-bottom: 0;">
                                    <label for="outbound-phone">Customer Phone Number(s)</label>
                                    <input type="text" id="outbound-phone" required placeholder="e.g. +919876543210, +919876543211 (comma separated)">
                                </div>
                                <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 0.75rem;">
                                    <div class="form-group" style="margin-bottom: 0;">
                                        <label for="outbound-enterprise-id">Enterprise ID</label>
                                        <input type="text" id="outbound-enterprise-id" placeholder="e.g. ent_admin_101 (Default: ent_default)">
                                    </div>
                                    <div class="form-group" style="margin-bottom: 0;">
                                        <label for="outbound-agent-id">Agent ID</label>
                                        <input type="text" id="outbound-agent-id" placeholder="e.g. agent_sales_01 (Default: default)">
                                    </div>
                                </div>
                                <div class="form-group" style="margin-bottom: 0;">
                                    <label for="outbound-campaign-id">Campaign ID <span style="color: var(--text-muted); font-weight: normal; font-size: 0.8rem;">(Auto-generated if left blank)</span></label>
                                    <input type="text" id="outbound-campaign-id" placeholder="Leave blank to auto-generate campaign ID">
                                </div>
                                <button type="submit" id="outbound-submit-btn" class="btn btn-primary" style="margin-top: 0.5rem; align-self: flex-start;">📞 Start Call</button>
                            </form>
                        </div>

                        <div style="background: rgba(255,255,255,0.01); border: 1px solid var(--border); padding: 1.5rem; border-radius: 12px; display: flex; flex-direction: column;">
                            <h3>Bulk Batch Calling (Excel or CSV Upload)</h3>
                            <p style="color: var(--text-muted); font-size: 0.85rem; margin-top: 0.25rem; margin-bottom: 1.5rem;">Upload an Excel (.xlsx) or CSV file containing columns like <code>phone_number</code>, <code>customer_name</code>, <code>enterprise_id</code>, <code>agent_id</code>, and <code>campaign_id</code> to start batch calls.</p>
                            <form id="bulk-upload-form" onsubmit="handleBulkUpload(event)" style="display: flex; flex-direction: column; gap: 1.5rem; flex: 1; justify-content: space-between;">
                                <div class="form-group" style="margin-bottom: 0;">
                                    <label for="bulk-csv-file">Choose Excel or CSV File</label>
                                    <input type="file" id="bulk-csv-file" accept=".csv, .xlsx" required style="padding: 0.6rem; background: rgba(255,255,255,0.04); border: 1px solid var(--border); border-radius: 10px; color: var(--text); font-family: inherit; width: 100%;">
                                </div>
                                <div style="display: flex; gap: 1rem; align-items: center; justify-content: space-between; flex-wrap: wrap;">
                                    <button type="submit" id="bulk-submit-btn" class="btn btn-secondary">📤 Upload & Start Batch</button>
                                    <div style="display: flex; gap: 0.75rem;">
                                        <a href="/static/sample_contacts.xlsx" download style="color: var(--accent); text-decoration: none; font-size: 0.85rem; font-weight: 500; display: inline-flex; align-items: center; gap: 0.25rem;">📥 Excel Template</a>
                                        <span style="color: var(--text-muted); font-size: 0.85rem;">|</span>
                                        <a href="/static/sample_contacts.csv" download style="color: var(--accent); text-decoration: none; font-size: 0.85rem; font-weight: 500; display: inline-flex; align-items: center; gap: 0.25rem;">📥 CSV Template</a>
                                    </div>
                                </div>
                            </form>
                        </div>
                    </div>

                    <!-- Campaign Data Explorer -->
                    <div style="background: rgba(255,255,255,0.01); border: 1px solid var(--border); padding: 1.5rem; border-radius: 12px; margin-bottom: 2rem;">
                        <h3>📊 Campaign Data Explorer</h3>
                        <p style="color: var(--text-muted); font-size: 0.85rem; margin-top: 0.25rem; margin-bottom: 1rem;">Query all campaign analytics executed by an Enterprise ID under a specific Agent ID.</p>
                        <form onsubmit="handleFetchCampaigns(event)" style="display: flex; gap: 1rem; align-items: flex-end; flex-wrap: wrap;">
                            <div class="form-group" style="margin-bottom: 0; flex: 1; min-width: 180px;">
                                <label for="campaign-query-ent">Enterprise ID *</label>
                                <input type="text" id="campaign-query-ent" required placeholder="e.g. ent_admin_101">
                            </div>
                            <div class="form-group" style="margin-bottom: 0; flex: 1; min-width: 180px;">
                                <label for="campaign-query-agent">Agent ID (Optional)</label>
                                <input type="text" id="campaign-query-agent" placeholder="e.g. agent_sales_01">
                            </div>
                            <button type="submit" id="campaign-query-btn" class="btn btn-secondary" style="height: 42px;">🔍 Fetch Campaigns</button>
                        </form>
                        <div id="campaign-results-container" style="margin-top: 1.5rem; display: none;"></div>
                    </div>

                    <h3>Active & Recent Outbound Calls</h3>
                    <table style="margin-top: 1rem;">
                        <thead>
                            <tr>
                                <th>Call ID (SID)</th>
                                <th>Customer Name</th>
                                <th>Phone Number</th>
                                <th>Enterprise ID</th>
                                <th>Agent ID</th>
                                <th>Campaign ID</th>
                                <th>Call Status</th>
                            </tr>
                        </thead>
                        <tbody id="outbound-calls-list">
                            <tr>
                                <td colspan="7" style="text-align: center; color: var(--text-muted);">No outbound calls triggered yet. Start one above!</td>
                            </tr>
                        </tbody>
                    </table>
                </div>

                <!-- Tab 6: Call Analytics -->
                <div id="calls-panel" class="panel">
                    <h2>Call Analytics & Logs</h2>
                    <p class="panel-desc">Real-time repository of call transcripts, durations, and AI-extracted customer insights.</p>
                    
                    <div id="calls-alert" class="alert"></div>

                    <!-- Toolbar: search + refresh + export -->
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 1rem; gap: 1rem; flex-wrap: wrap;">
                        <input id="calls-search" type="text" placeholder="🔍  Search by name, phone, email, interest…"
                            oninput="filterCallLogs()"
                            style="flex: 1; min-width: 220px; padding: 0.6rem 1rem; background: rgba(255,255,255,0.04); border: 1px solid var(--border); border-radius: 10px; color: var(--text); font-family: inherit; font-size: 0.9rem; outline: none;">
                        <div style="display:flex;gap:0.6rem;">
                            <button class="btn btn-secondary" onclick="exportCallLogsCSV()" title="Export to CSV">📥 Export CSV</button>
                            <button class="btn btn-secondary" onclick="loadCallLogs()">🔄 Refresh</button>
                        </div>
                    </div>

                    <div style="overflow-x: auto;">
                        <table id="calls-analytics-table" style="width: 100%; border-collapse: collapse; margin-top: 0.5rem; font-size: 0.82rem;">
                            <thead>
                                <tr style="text-align: left; border-bottom: 2px solid var(--border);">
                                    <th style="padding: 0.65rem 0.75rem; color: var(--text-muted); white-space: nowrap;">#</th>
                                    <th style="padding: 0.65rem 0.75rem; color: var(--text-muted); white-space: nowrap;">Call Date</th>
                                    <th style="padding: 0.65rem 0.75rem; color: var(--text-muted); white-space: nowrap;">Time</th>
                                    <th style="padding: 0.65rem 0.75rem; color: var(--text-muted); white-space: nowrap;">Duration</th>
                                    <th style="padding: 0.65rem 0.75rem; color: var(--text-muted); white-space: nowrap;">Agent Name</th>
                                    <th style="padding: 0.65rem 0.75rem; color: var(--text-muted); white-space: nowrap;">Company Name</th>
                                    <th style="padding: 0.65rem 0.75rem; color: var(--text-muted); white-space: nowrap;">Caller Phone No.</th>
                                    <th style="padding: 0.65rem 0.75rem; color: var(--text-muted); white-space: nowrap;">Lead Phone No.</th>
                                    <th style="padding: 0.65rem 0.75rem; color: var(--text-muted); white-space: nowrap;">Name</th>
                                    <th style="padding: 0.65rem 0.75rem; color: var(--text-muted); white-space: nowrap;">Address</th>
                                    <th style="padding: 0.65rem 0.75rem; color: var(--text-muted); white-space: nowrap;">Email ID</th>
                                    <th style="padding: 0.65rem 0.75rem; color: var(--text-muted); white-space: nowrap;">Meeting Consent</th>
                                    <th style="padding: 0.65rem 0.75rem; color: var(--text-muted); white-space: nowrap;">Field Visit</th>
                                    <th style="padding: 0.65rem 0.75rem; color: var(--text-muted); white-space: nowrap;">Business Interest</th>
                                    <th style="padding: 0.65rem 0.75rem; color: var(--text-muted); white-space: nowrap; max-width: 220px;">Call Summary</th>
                                    <th style="padding: 0.65rem 0.75rem; color: var(--text-muted); white-space: nowrap;">Transcript</th>
                                    <th style="padding: 0.65rem 0.75rem; color: var(--text-muted); white-space: nowrap;">Actions</th>
                                </tr>
                            </thead>
                            <tbody id="calls-list">
                                <tr>
                                    <td colspan="17" style="text-align: center; color: var(--text-muted); padding: 2.5rem;">Loading call logs from MongoDB…</td>
                                </tr>
                            </tbody>
                        </table>
                    </div>
                    <div id="calls-count" style="margin-top: 0.75rem; color: var(--text-muted); font-size: 0.8rem; text-align: right;"></div>
                </div>

                <!-- Call Transcript Modal -->
                <div id="transcript-modal" style="display: none; position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0,0,0,0.7); z-index: 1000; align-items: center; justify-content: center;">
                    <div style="background: #0f172a; border: 1px solid var(--border); border-radius: 16px; padding: 2rem; width: 90%; max-width: 700px; max-height: 80vh; display: flex; flex-direction: column; box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.5);">
                        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 1.5rem; border-bottom: 1px solid var(--border); padding-bottom: 1rem;">
                            <h3 id="modal-title" style="margin: 0; color: var(--text);">Call Transcript</h3>
                            <button onclick="closeTranscriptModal()" style="background: none; border: none; color: var(--text-muted); font-size: 1.5rem; cursor: pointer; transition: color 0.2s;" onmouseover="this.style.color='#html_content'" onmouseout="this.style.color='var(--text-muted)'">&times;</button>
                        </div>
                        <div id="modal-body" style="overflow-y: auto; flex-grow: 1; margin-bottom: 1.5rem; padding-right: 0.5rem; max-height: 50vh;">
                            <!-- Conversation turns loaded here -->
                        </div>
                        <div style="display: flex; justify-content: flex-end;">
                            <button onclick="closeTranscriptModal()" class="btn btn-primary">Close</button>
                        </div>
                    </div>
                </div>
            </div>
        </div>

        <footer>
            &copy; 2026 {Config.COMPANY_NAME}. All administrative actions are logged and encrypted.
        </footer>

        <script>
            // Tab Switch Logic
            function switchTab(panelId, btn) {{
                document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
                document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
                
                document.getElementById(panelId).classList.add('active');
                btn.classList.add('active');
                
                if (panelId === 'companies-panel' || panelId === 'docs-panel' || panelId === 'sandbox-panel') {{
                    loadCompanies();
                }}
                if (panelId === 'calls-panel') {{
                    loadCallLogs();
                }}
                if (panelId === 'outbound-panel') {{
                    loadOutboundCalls();
                }}
            }}


            // ── Call Analytics loaded from external static file (no f-string escaping issues) ──
            


            // Show alert box
            function showAlert(alertId, message, isError = false) {{
                const alertEl = document.getElementById(alertId);
                alertEl.innerText = message;
                alertEl.className = 'alert ' + (isError ? 'alert-error' : 'alert-success');
                alertEl.style.display = 'block';
                setTimeout(() => {{
                    alertEl.style.display = 'none';
                }}, 6000);
            }}

            // Show alert box
            function showAlert(alertId, message, isError = false) {{
                const alertEl = document.getElementById(alertId);
                alertEl.innerText = message;
                alertEl.className = 'alert ' + (isError ? 'alert-error' : 'alert-success');
                alertEl.style.display = 'block';
                setTimeout(() => {{
                    alertEl.style.display = 'none';
                }}, 6000);
            }}

            // Load companies from DB
            let cachedCompanies = [];
            async function loadCompanies() {{
                try {{
                    const response = await fetch('/companies/');
                    if (!response.ok) throw new Error('Failed to load companies');
                    const data = await response.json();
                    cachedCompanies = data;
                    
                    // Render List
                    const listEl = document.getElementById('companies-list');
                    if (data.length === 0) {{
                        listEl.innerHTML = `<tr><td colspan="3" style="text-align: center; color: var(--text-muted);">No companies registered yet. Add a new company above!</td></tr>`;
                    }} else {{
                        listEl.innerHTML = data.map(c => `
                            <tr>
                                <td><strong>${{c.name}}</strong></td>
                                <td><code>${{c.phone_number}}</code></td>
                                <td>
                                    <button class="btn btn-danger" style="padding: 0.4rem 0.8rem; font-size: 0.8rem;" onclick="handleDeleteCompany('${{c.company_id}}')">Delete</button>
                                </td>
                            </tr>
                        `).join('');
                    }}
                    
                    // Populate Select Dropdowns
                    const selectElList = document.querySelectorAll('.company-select-shared, #search-company-select');
                    selectElList.forEach(selectEl => {{
                        const currentVal = selectEl.value;
                        selectEl.innerHTML = '<option value="">-- Select Company --</option>' + 
                            data.map(c => `<option value="${{c.company_id}}">${{c.name}} (${{c.phone_number}})</option>`).join('');
                        selectEl.value = currentVal;
                    }});
                }} catch (err) {{
                    console.error(err);
                }}
            }}

            // Fetch telemetry data
            async function loadTelemetry() {{
                try {{
                    const res = await fetch('/api/v1/bot/status');
                    if (!res.ok) return;
                    const data = await res.json();
                    
                    document.getElementById('active-calls-val').innerText = data.active_stream_calls;
                    document.getElementById('running-mode-val').innerText = data.modular_settings.voice_bot_mode;
                    
                    document.getElementById('cfg-bot-name').innerText = data.bot_name;
                    document.getElementById('cfg-company-name').innerText = data.company_name;
                    document.getElementById('cfg-stt-model').innerText = data.modular_settings.deepgram_model;
                    document.getElementById('cfg-llm-model').innerText = data.modular_settings.gemini_model;
                    document.getElementById('cfg-tts-model').innerText = data.modular_settings.sarvam_model;

                    // Populate voice config inputs once
                    const speakerInput = document.getElementById('cfg-speaker');
                    const langInput = document.getElementById('cfg-lang');
                    const paceInput = document.getElementById('cfg-pace');
                    const gainInput = document.getElementById('cfg-gain');
                    
                    if (speakerInput && !speakerInput.dataset.initialized) {{
                        speakerInput.value = data.modular_settings.sarvam_speaker;
                        speakerInput.dataset.initialized = "true";
                    }}
                    if (langInput && !langInput.dataset.initialized) {{
                        langInput.value = data.modular_settings.sarvam_language_code;
                        langInput.dataset.initialized = "true";
                    }}
                    if (paceInput && !paceInput.dataset.initialized) {{
                        paceInput.value = data.modular_settings.sarvam_pace;
                        paceInput.dataset.initialized = "true";
                    }}
                    if (gainInput && !gainInput.dataset.initialized) {{
                        gainInput.value = data.modular_settings.audio_gain;
                        gainInput.dataset.initialized = "true";
                    }}
                }} catch (err) {{
                    console.error(err);
                }}
            }}

            // Create new Company
            async function handleCreateCompany(e) {{
                e.preventDefault();
                const name = document.getElementById('comp-name').value;
                const phone = document.getElementById('comp-phone').value;
                
                try {{
                    const response = await fetch(`/companies/?name=${{encodeURIComponent(name)}}&phone_number=${{encodeURIComponent(phone)}}`, {{
                        method: 'POST'
                    }});
                    const result = await response.json();
                    if (!response.ok) {{
                        throw new Error(result.detail || 'Failed to create company');
                    }}
                    showAlert('company-alert', `Successfully registered company: ${{name}}`);
                    document.getElementById('create-company-form').reset();
                    loadCompanies();
                }} catch (err) {{
                    showAlert('company-alert', err.message, true);
                }}
            }}

            // Delete Company
            async function handleDeleteCompany(companyId) {{
                if (!confirm("Are you sure you want to delete this company and all of its document vectors? This cannot be undone.")) return;
                
                try {{
                    const response = await fetch(`/companies/${{companyId}}`, {{
                        method: 'DELETE'
                    }});
                    const result = await response.json();
                    if (!response.ok) {{
                        throw new Error(result.detail || 'Failed to delete company');
                    }}
                    showAlert('company-alert', 'Company successfully deleted.');
                    loadCompanies();
                }} catch (err) {{
                    showAlert('company-alert', err.message, true);
                }}
            }}

            // Upload Document to S3 / Chroma DB
            async function handleUploadDocs(e) {{
                e.preventDefault();
                const companyId = document.getElementById('upload-company-select').value;
                const fileInput = document.getElementById('upload-files');
                const submitBtn = document.getElementById('upload-submit-btn');
                let progressBar = document.getElementById('upload-progress-container');

                if (!companyId) return alert("Please select a company first.");
                if (fileInput.files.length === 0) return alert("Select at least one document.");

                const formData = new FormData();
                for (let i = 0; i < fileInput.files.length; i++) {{
                    formData.append("files", fileInput.files[i]);
                }}

                submitBtn.disabled = true;
                submitBtn.innerText = "⏫ Uploading...";

                // Show progress container
                if (!progressBar) {{
                    progressBar = document.createElement('div');
                    progressBar.id = 'upload-progress-container';
                    progressBar.style.cssText = 'margin-top:12px;padding:12px;background:rgba(255,255,255,0.05);border-radius:8px;border:1px solid rgba(255,255,255,0.1);';
                    progressBar.innerHTML =
                        '<div id="upload-progress-label" style="font-size:13px;color:#a0aec0;margin-bottom:6px;">Starting upload...</div>' +
                        '<div style="background:rgba(255,255,255,0.1);border-radius:99px;height:8px;overflow:hidden;">' +
                            '<div id="upload-progress-fill" style="height:100%;width:0%;background:linear-gradient(90deg,#667eea,#764ba2);border-radius:99px;transition:width 0.4s ease;"></div>' +
                        '</div>' +
                        '<div id="upload-progress-pct" style="font-size:12px;color:#667eea;margin-top:4px;text-align:right;">0%</div>';
                    submitBtn.parentNode.insertBefore(progressBar, submitBtn.nextSibling);
                }}
                progressBar.style.display = 'block';
                document.getElementById('upload-progress-label').innerText = 'Uploading file...';
                document.getElementById('upload-progress-fill').style.width = '0%';
                document.getElementById('upload-progress-pct').innerText = '0%';

                try {{
                    // Step 1: Submit — returns immediately with job_id(s)
                    const response = await fetch(`/companies/${{companyId}}/documents`, {{
                        method: 'POST',
                        body: formData
                    }});
                    const result = await response.json();
                    if (!response.ok) throw new Error(result.detail || 'Upload failed');

                    // Step 2: Poll each job for progress
                    const jobs = result.jobs || [];
                    for (const job of jobs) {{
                        submitBtn.innerText = '⚙️ Indexing ' + job.filename + '...';
                        document.getElementById('upload-progress-label').innerText = 'Indexing: ' + job.filename;
                        await pollJobProgress(job.job_id);
                    }}

                    showAlert('docs-alert', `✅ Document(s) uploaded and indexed successfully!`);
                    fileInput.value = '';
                    fetchCompanyDocs(companyId);
                }} catch (err) {{
                    showAlert('docs-alert', err.message, true);
                }} finally {{
                    submitBtn.disabled = false;
                    submitBtn.innerText = "🚀 Upload & Index Document";
                    setTimeout(() => {{ if(progressBar) progressBar.style.display = 'none'; }}, 3000);
                }}
            }}

            // Poll a job until done, updating the progress bar
            async function pollJobProgress(jobId) {{
                return new Promise((resolve, reject) => {{
                    const interval = setInterval(async () => {{
                        try {{
                            const res = await fetch(`/companies/jobs/${{jobId}}`);
                            if (!res.ok) {{ clearInterval(interval); return reject(new Error('Job not found')); }}
                            const job = await res.json();

                            const pct = job.total > 0 ? Math.round((job.progress / job.total) * 100) : 5;
                            document.getElementById('upload-progress-fill').style.width = pct + '%';
                            document.getElementById('upload-progress-pct').innerText = pct + '%';
                            document.getElementById('upload-progress-label').innerText = job.message || 'Processing...';

                            if (job.status === 'done') {{
                                document.getElementById('upload-progress-fill').style.width = '100%';
                                document.getElementById('upload-progress-pct').innerText = '100%';
                                clearInterval(interval);
                                resolve();
                            }} else if (job.status === 'error') {{
                                clearInterval(interval);
                                reject(new Error(job.message || 'Indexing failed'));
                            }}
                        }} catch(e) {{ clearInterval(interval); reject(e); }}
                    }}, 1000);
                }});
            }}

            // Crawl & Index Webpage URL
            async function handleUploadWeb(e) {{
                e.preventDefault();
                const companyId = document.getElementById('web-company-select').value;
                const url = document.getElementById('web-url').value;
                const submitBtn = document.getElementById('web-submit-btn');
                
                if (!companyId) return alert("Please select a company first.");
                
                submitBtn.disabled = true;
                submitBtn.innerText = "Crawling & indexing URL... Please wait...";
                
                try {{
                    const response = await fetch(`/companies/${{companyId}}/webpages?url=${{encodeURIComponent(url)}}`, {{
                        method: 'POST'
                    }});
                    const result = await response.json();
                    if (!response.ok) {{
                        throw new Error(result.detail || 'Crawling failed');
                    }}
                    showAlert('docs-alert', `Webpage successfully crawled and indexed.`);
                    document.getElementById('web-url').value = '';
                    fetchCompanyDocs(companyId);
                }} catch (err) {{
                    showAlert('docs-alert', err.message, true);
                }} finally {{
                    submitBtn.disabled = false;
                    submitBtn.innerText = "🌐 Crawl & Index Webpage";
                }}
            }}

            // Index Raw Text Messages
            async function handleUploadText(e) {{
                e.preventDefault();
                const companyId = document.getElementById('text-company-select').value;
                const sourceName = document.getElementById('text-source-name').value;
                const textVal = document.getElementById('text-content').value;
                const submitBtn = document.getElementById('text-submit-btn');
                
                if (!companyId) return alert("Please select a company first.");
                
                submitBtn.disabled = true;
                submitBtn.innerText = "Indexing custom text... Please wait...";
                
                try {{
                    const response = await fetch(`/companies/${{companyId}}/text`, {{
                        method: 'POST',
                        headers: {{ 'Content-Type': 'application/json' }},
                        body: JSON.stringify({{ text: textVal, source_name: sourceName }})
                    }});
                    const result = await response.json();
                    if (!response.ok) {{
                        throw new Error(result.detail || 'Indexing failed');
                    }}
                    showAlert('docs-alert', `Custom text successfully indexed.`);
                    document.getElementById('text-source-name').value = '';
                    document.getElementById('text-content').value = '';
                    fetchCompanyDocs(companyId);
                }} catch (err) {{
                    showAlert('docs-alert', err.message, true);
                }} finally {{
                    submitBtn.disabled = false;
                    submitBtn.innerText = "📝 Index Custom Text";
                }}
            }}

            // Fetch company documents
            async function fetchCompanyDocs(companyId) {{
                if (!companyId) return;
                try {{
                    const response = await fetch(`/companies/${{companyId}}`);
                    if (!response.ok) return;
                    const data = await response.json();
                    
                    const docSection = document.getElementById('company-documents-section');
                    const listEl = document.getElementById('company-docs-list');
                    
                    if (data.documents && data.documents.length > 0) {{
                        docSection.style.display = 'block';
                        listEl.innerHTML = data.documents.map(d => `
                            <tr>
                                <td>${{d.filename}}</td>
                                <td>${{(d.size_bytes / 1024).toFixed(1)}} KB</td>
                                <td>${{new Date(d.uploaded_at).toLocaleString()}}</td>
                                <td><span class="status-badge status-${{d.status}}">${{d.status}}</span></td>
                                <td>
                                    <button class="btn btn-danger" style="padding: 0.3rem 0.6rem; font-size: 0.75rem;" onclick="handleDeleteDoc('${{companyId}}', ${{d.id}})">Delete</button>
                                </td>
                            </tr>
                        `).join('');
                    }} else {{
                        docSection.style.display = 'none';
                    }}
                }} catch (err) {{
                    console.error(err);
                }}
            }}

            // Delete individual document
            async function handleDeleteDoc(companyId, docId) {{
                if (!confirm("Are you sure you want to delete this document and all of its associated vector chunks?")) return;
                
                try {{
                    const response = await fetch(`/companies/${{companyId}}/documents/${{docId}}`, {{
                        method: 'DELETE'
                    }});
                    const result = await response.json();
                    if (!response.ok) {{
                        throw new Error(result.detail || 'Failed to delete document');
                    }}
                    showAlert('docs-alert', 'Document and vectors successfully deleted.');
                    fetchCompanyDocs(companyId);
                }} catch (err) {{
                    showAlert('docs-alert', err.message, true);
                }}
            }}

            // Sub-tab navigation logic
            function switchSubTab(subTabType) {{
                document.querySelectorAll('.sub-tab-btn').forEach(btn => btn.classList.remove('active'));
                document.querySelectorAll('.ingest-section').forEach(sec => sec.style.display = 'none');
                
                if (subTabType === 'files') {{
                    document.getElementById('sub-tab-files').classList.add('active');
                    document.getElementById('ingest-files-section').style.display = 'block';
                }} else if (subTabType === 'web') {{
                    document.getElementById('sub-tab-web').classList.add('active');
                    document.getElementById('ingest-web-section').style.display = 'block';
                }} else if (subTabType === 'text') {{
                    document.getElementById('sub-tab-text').classList.add('active');
                    document.getElementById('ingest-text-section').style.display = 'block';
                }}
            }}

            // Sync all company select dropdown values and fetch documents
            document.querySelectorAll('.company-select-shared').forEach(select => {{
                select.addEventListener('change', (e) => {{
                    const companyId = e.target.value;
                    document.querySelectorAll('.company-select-shared').forEach(s => s.value = companyId);
                    if (companyId) {{
                        fetchCompanyDocs(companyId);
                    }} else {{
                        document.getElementById('company-documents-section').style.display = 'none';
                    }}
                }});
            }});

            // RAG Search Sandbox Testing
            async function handleSandboxSearch(e) {{
                e.preventDefault();
                const companyId = document.getElementById('search-company-select').value;
                const query = document.getElementById('search-query').value;
                const resultsEl = document.getElementById('search-sandbox-results');
                
                if (!companyId) return alert("Select a company first.");
                
                resultsEl.innerHTML = '<div style="text-align: center; color: var(--text-muted);">Querying vector database...</div>';
                
                try {{
                    const response = await fetch(`/companies/${{companyId}}/search?q=${{encodeURIComponent(query)}}&top_k=3`);
                    const data = await response.json();
                    if (!response.ok) throw new Error(data.detail || 'Search failed');
                    
                    if (data.length === 0) {{
                        resultsEl.innerHTML = '<div style="text-align: center; color: var(--text-muted); border: 1px dashed var(--border); padding: 2rem; border-radius: 12px;">No matching documents found in the database for this query. Make sure files are indexed!</div>';
                        return;
                    }}
                    
                    resultsEl.innerHTML = data.map((r, idx) => `
                        <div class="result-card">
                            <div class="result-meta">
                                <span>Hit #${{idx + 1}} | Source: <strong>${{r.source || 'Unknown'}}</strong></span>
                                <span>Similarity Match</span>
                            </div>
                            <div class="result-text">"${{r.chunk}}"</div>
                        </div>
                    `).join('');
                }} catch (err) {{
                    resultsEl.innerHTML = `<div style="color: var(--error); border: 1px solid rgba(239,68,68,0.2); padding: 1.5rem; border-radius: 12px; background: rgba(239,68,68,0.05);">Search failed: ${{err.message}}</div>`;
                }}
            }}

            // Outbound Call Center logic
            let activeOutboundCalls = [];
            
            async function handleTriggerOutboundCall(e) {{
                e.preventDefault();
                const name = document.getElementById('outbound-name').value;
                const phone = document.getElementById('outbound-phone').value;
                const enterpriseId = document.getElementById('outbound-enterprise-id').value;
                const agentId = document.getElementById('outbound-agent-id').value;
                const campaignId = document.getElementById('outbound-campaign-id').value;
                const submitBtn = document.getElementById('outbound-submit-btn');
                
                submitBtn.disabled = true;
                submitBtn.innerText = "🔌 Initiating...";
                
                try {{
                    const payload = {{
                        customer_name: name,
                        phone_number: phone
                    }};
                    if (enterpriseId) payload.enterprise_id = enterpriseId;
                    if (agentId) payload.agent_id = agentId;
                    if (campaignId) payload.campaign_id = campaignId;

                    const response = await fetch('/api/v1/calls/outbound', {{
                        method: 'POST',
                        headers: {{ 'Content-Type': 'application/json' }},
                        body: JSON.stringify(payload)
                    }});
                    
                    const result = await response.json();
                    if (!response.ok) {{
                        throw new Error(result.detail || 'Call trigger failed');
                    }}
                    
                    showAlert('outbound-alert', `✅ Call initiated. Campaign: ${{result.campaign_id}}. Primary SID: ${{result.call_sid}}`);
                    document.getElementById('outbound-call-form').reset();
                    
                    const sids = result.call_sids || [result.call_sid];
                    sids.forEach(sid => {{
                        const newCall = {{
                            call_sid: sid,
                            customer_name: name,
                            phone_number: phone,
                            enterprise_id: result.enterprise_id || 'ent_default',
                            agent_id: result.agent_id || 'default',
                            campaign_id: result.campaign_id || '-',
                            status: result.status || 'initiated'
                        }};
                        activeOutboundCalls.unshift(newCall);
                        pollOutboundCallStatus(sid);
                    }});
                    
                    renderOutboundCalls();
                    
                }} catch (err) {{
                    showAlert('outbound-alert', err.message, true);
                }} finally {{
                    submitBtn.disabled = false;
                    submitBtn.innerText = "📞 Start Call";
                }}
            }}

            function renderOutboundCalls() {{
                const listEl = document.getElementById('outbound-calls-list');
                if (activeOutboundCalls.length === 0) {{
                    listEl.innerHTML = `<tr><td colspan="7" style="text-align: center; color: var(--text-muted);">No outbound calls triggered yet. Start one above!</td></tr>`;
                    return;
                }}
                
                listEl.innerHTML = activeOutboundCalls.map(c => `
                    <tr>
                        <td><code>${{c.call_sid}}</code></td>
                        <td><strong>${{c.customer_name}}</strong></td>
                        <td><code>${{c.phone_number}}</code></td>
                        <td><span style="font-size:0.85rem; color:var(--text-muted);">${{c.enterprise_id || 'ent_default'}}</span></td>
                        <td><span style="font-size:0.85rem; color:var(--text-muted);">${{c.agent_id || 'default'}}</span></td>
                        <td><code style="color:var(--accent); font-size:0.82rem;">${{c.campaign_id || '-'}}</code></td>
                        <td><span class="status-badge status-${{c.status === 'completed' || c.status === 'in-progress' ? 'processed' : (c.status === 'failed' ? 'failed' : 'processing')}}">${{c.status}}</span></td>
                    </tr>
                `).join('');
            }}

            function pollOutboundCallStatus(callSid) {{
                const interval = setInterval(async () => {{
                    try {{
                        const response = await fetch(`/api/v1/calls/status/${{callSid}}`);
                        if (!response.ok) return;
                        const data = await response.json();
                        
                        // Update status in list
                        const call = activeOutboundCalls.find(c => c.call_sid === callSid);
                        if (call) {{
                            call.status = data.status;
                            renderOutboundCalls();
                            
                            // Stop polling if call is finished
                            if (['completed', 'failed', 'busy', 'no-answer', 'canceled'].includes(data.status)) {{
                                clearInterval(interval);
                            }}
                        }}
                    }} catch (err) {{
                        console.error(err);
                    }}
                }}, 3000);
            }}

            async function handleFetchCampaigns(e) {{
                e.preventDefault();
                const entId = document.getElementById('campaign-query-ent').value;
                const agentId = document.getElementById('campaign-query-agent').value;
                const btn = document.getElementById('campaign-query-btn');
                const resultsEl = document.getElementById('campaign-results-container');
                
                btn.disabled = true;
                btn.innerText = "⏳ Querying...";
                
                try {{
                    let url = `/api/v1/calls/campaigns?enterprise_id=${{encodeURIComponent(entId)}}`;
                    if (agentId) url += `&agent_id=${{encodeURIComponent(agentId)}}`;
                    
                    const response = await fetch(url);
                    const data = await response.json();
                    
                    if (!response.ok || !data.success) {{
                        throw new Error(data.detail || data.error || 'Failed to fetch campaign data');
                    }}
                    
                    resultsEl.style.display = 'block';
                    if (!data.campaigns || data.campaigns.length === 0) {{
                        resultsEl.innerHTML = `<div style="padding:1rem; background:rgba(255,255,255,0.02); border-radius:8px; color:var(--text-muted);">No campaigns found for Enterprise '${{entId}}'${{agentId ? ` under Agent '${{agentId}}'` : ''}}.</div>`;
                        return;
                    }}
                    
                    resultsEl.innerHTML = `
                        <div style="display:flex; flex-direction:column; gap:1rem;">
                            <div style="font-size:0.9rem; color:var(--text-muted);">Found <strong>${{data.total_campaigns}}</strong> campaign(s) for Enterprise <code>${{entId}}</code>${{agentId ? ` (Agent: <code>${{agentId}}</code>)` : ''}}:</div>
                            ${{data.campaigns.map(c => `
                                <div style="background:rgba(255,255,255,0.03); border:1px solid var(--border); border-radius:10px; padding:1.25rem;">
                                    <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:0.5rem; margin-bottom:0.75rem;">
                                        <div>
                                            <span style="font-size:1.05rem; font-weight:600; color:var(--accent);">📋 ${{c.campaign_id}}</span>
                                            <span style="margin-left:0.75rem; font-size:0.8rem; color:var(--text-muted);">Agent: <code>${{c.agent_id}}</code></span>
                                        </div>
                                        <div style="display:flex; gap:0.5rem;">
                                            <span class="status-badge status-processed">Total: ${{c.total_calls}}</span>
                                            <span class="status-badge status-processed" style="background:rgba(46,204,113,0.15); color:#2ecc71;">Completed: ${{c.completed_calls}}</span>
                                            <span class="status-badge status-failed">Failed: ${{c.failed_calls}}</span>
                                        </div>
                                    </div>
                                    <div style="font-size:0.8rem; color:var(--text-muted); margin-bottom:0.75rem;">
                                        Calls: ${{c.calls.map(call => `<code>${{call.phone_number}} (${{call.status}})</code>`).join(', ')}}
                                    </div>
                                </div>
                            `).join('')}}
                        </div>
                    `;
                }} catch (err) {{
                    resultsEl.style.display = 'block';
                    resultsEl.innerHTML = `<div style="padding:1rem; background:rgba(231,76,60,0.1); border:1px solid rgba(231,76,60,0.3); border-radius:8px; color:#e74c3c;">❌ ${{err.message}}</div>`;
                }} finally {{
                    btn.disabled = false;
                    btn.innerText = "🔍 Fetch Campaigns";
                }}
            }}

            async function loadOutboundCalls() {{
                try {{
                    const response = await fetch('/api/v1/calls/outbound');
                    if (!response.ok) return;
                    const data = await response.json();
                    if (data.success && data.calls) {{
                        activeOutboundCalls = data.calls;
                        renderOutboundCalls();
                        // Resume polling for active calls
                        activeOutboundCalls.forEach(c => {{
                            if (['initiated', 'ringing'].includes(c.status)) {{
                                pollOutboundCallStatus(c.call_sid);
                            }}
                        }});
                    }}
                }} catch (err) {{
                    console.error("Error loading outbound calls:", err);
                }}
            }}

            async function handleBulkUpload(e) {{
                e.preventDefault();
                const fileInput = document.getElementById('bulk-csv-file');
                const file = fileInput.files[0];
                if (!file) return;

                const submitBtn = document.getElementById('bulk-submit-btn');
                submitBtn.disabled = true;
                submitBtn.innerText = "⏳ Uploading...";

                const formData = new FormData();
                formData.append('file', file);

                try {{
                    const response = await fetch('/api/v1/calls/outbound/bulk', {{
                        method: 'POST',
                        body: formData
                    }});
                    const result = await response.json();
                    if (!response.ok) {{
                        throw new Error(result.detail || 'Bulk upload failed');
                    }}

                    showAlert('outbound-alert', "✅ Bulk calls triggered! " + result.message);
                    fileInput.value = '';
                    await loadOutboundCalls();

                }} catch (err) {{
                    showAlert('outbound-alert', err.message, true);
                }} finally {{
                    submitBtn.disabled = false;
                    submitBtn.innerText = "📤 Upload & Start Batch";
                }}
            }}

            // Update dynamic AI voice config
            async function handleUpdateVoiceConfig(e) {{
                e.preventDefault();
                const speaker = document.getElementById('cfg-speaker').value;
                const lang = document.getElementById('cfg-lang').value;
                const pace = parseFloat(document.getElementById('cfg-pace').value);
                const gain = parseFloat(document.getElementById('cfg-gain').value);
                const alertEl = document.getElementById('config-voice-alert');
                
                try {{
                    const response = await fetch('/api/v1/bot/config', {{
                        method: 'POST',
                        headers: {{ 'Content-Type': 'application/json' }},
                        body: JSON.stringify({{
                            sarvam_speaker: speaker,
                            sarvam_language_code: lang,
                            sarvam_pace: pace,
                            audio_gain: gain
                        }})
                    }});
                    const result = await response.json();
                    if (!response.ok) {{
                        throw new Error(result.detail || 'Failed to update voice configuration');
                    }}
                    showAlert('config-voice-alert', '✅ Voice configurations saved and hot-reloaded successfully!');
                }} catch (err) {{
                    showAlert('config-voice-alert', err.message, true);
                }}
            }}

            // Run loops
            loadTelemetry();
            loadOutboundCalls();
            setInterval(loadTelemetry, 5000);
        </script>
        <script src="/static/call_analytics.js"></script>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)

@app.get("/health", include_in_schema=False)
async def health_check():
    """Health check for container orchestration and uptime analytics."""
    sip_calls_count = len(sales_bot_engine.sip_server.sip_calls) if (sales_bot_engine.sip_server and sales_bot_engine.sip_server.pjsua_initialized) else 0
    return {"status": "healthy", "service": "Voice AI Agent Gateway", "concurrency_load": sip_calls_count}

