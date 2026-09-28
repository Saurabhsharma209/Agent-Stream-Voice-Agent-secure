#!/usr/bin/env python3
"""
Route Definitions: Bot Routes
Exposes REST endpoints for querying bot telemetry and hot-reloading personality configurations.
Generates comprehensive OpenAPI Swagger schemas.
"""

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from typing import Dict, Any, List, Optional
from controllers import bot_controller

router = APIRouter(
    prefix="/api/v1/bot",
    tags=["AI Voice Bot Configuration"],
    responses={
        500: {"description": "Internal Server Error"}
    }
)

# === Pydantic Input Schemas for Dynamic Configuration ===

class BotConfigUpdateRequest(BaseModel):
    sales_bot_name: Optional[str] = Field(
        None, 
        example="Sarah", 
        description="The voice agent character name used to represent the company."
    )
    company_name: Optional[str] = Field(
        None, 
        example="TechCorp Inc.", 
        description="Company details loaded dynamically in system instruction prompt templates."
    )
    openai_model: Optional[str] = Field(
        None, 
        example="gpt-4o-realtime-preview-2024-12-17", 
        description="Active LLM voice engine model variant."
    )
    openai_voice: Optional[str] = Field(
        None, 
        example="coral", 
        description="AI voice variant selection (coral, alloy, shimmer, echo, onyx, nova)."
    )
    openai_temperature: Optional[float] = Field(
        None, 
        example=0.7, 
        description="Creativity parameter for model responses (range 0.0 to 1.2)."
    )
    voice_bot_mode: Optional[str] = Field(
        None,
        example="modular",
        description="Active voice bot engine mode: 'modular' (Deepgram+Gemini+Sarvam) or 'realtime' (OpenAI Realtime API)."
    )
    deepgram_model: Optional[str] = Field(
        None,
        example="nova-2-phonecall",
        description="Active STT model for Deepgram (e.g. nova-2-phonecall, nova-2-general)."
    )
    gemini_model: Optional[str] = Field(
        None,
        example="gemini-2.5-flash",
        description="Active LLM model for Gemini (e.g. gemini-2.5-flash, gemini-3.5-flash)."
    )
    deepgram_endpointing: Optional[int] = Field(
        None,
        example=180,
        description="Silence delay (ms) for STT endpointing."
    )
    speculative_rag: Optional[bool] = Field(
        None,
        example=True,
        description="Toggle zero-latency speculative RAG lookups."
    )
    sarvam_model: Optional[str] = Field(
        None,
        example="bulbul:v3",
        description="Active TTS model for Sarvam AI (e.g. bulbul:v3)."
    )
    sarvam_speaker: Optional[str] = Field(
        None,
        example="shubh",
        description="Active speaker name for Sarvam AI TTS (e.g. shubh)."
    )
    sarvam_language_code: Optional[str] = Field(
        None,
        example="hi-IN",
        description="Language code for Sarvam AI TTS (e.g. hi-IN)."
    )
    sarvam_pace: Optional[float] = Field(
        None,
        example=1.15,
        description="Pace (speed) factor for Sarvam AI TTS (e.g. 1.15)."
    )
    sarvam_pitch: Optional[float] = Field(
        None,
        example=0.0,
        description="Pitch offset for Sarvam AI TTS (e.g. 0.0)."
    )
    audio_gain: Optional[float] = Field(
        None,
        example=1.5,
        description="Digital playback audio gain factor (e.g. 1.5)."
    )

# === Pydantic Output Schemas for Swagger Documentation ===

class BotOpenAISettings(BaseModel):
    model: str = Field(..., example="gpt-4o-realtime-preview-2024-12-17")
    voice: str = Field(..., example="coral")
    temperature: float = Field(..., example=0.7)

class BotModularSettings(BaseModel):
    voice_bot_mode: str = Field(..., example="modular")
    deepgram_model: str = Field(..., example="nova-2-phonecall")
    deepgram_endpointing: int = Field(..., example=180)
    speculative_rag: bool = Field(..., example=True)
    gemini_model: str = Field(..., example="gemini-2.5-flash")
    sarvam_model: str = Field(..., example="bulbul:v3")
    sarvam_speaker: str = Field(..., example="shubh")
    sarvam_language_code: str = Field(..., example="hi-IN")
    sarvam_pace: float = Field(..., example=1.15)
    sarvam_pitch: float = Field(..., example=0.0)
    audio_gain: float = Field(..., example=1.5)

class BotAudioSettings(BaseModel):
    sample_rate: int = Field(..., example=24000)
    chunk_size_ms: int = Field(..., example=10)
    buffer_size_ms: int = Field(..., example=160)

class BotTelephonyMode(BaseModel):
    use_sip_trunk: bool = Field(..., example=False)
    sip_server_host: str = Field(..., example="0.0.0.0")
    sip_server_port: int = Field(..., example=5060)
    sip_public_endpoint: str = Field(..., example="localhost")

class BotStatusResponse(BaseModel):
    success: bool = Field(..., example=True)
    bot_name: str = Field(..., example="Sarah")
    company_name: str = Field(..., example="TechSolutions Inc.")
    active_stream_calls: int = Field(..., example=0, description="Active concurrent streaming telephony calls.")
    openai_settings: BotOpenAISettings
    modular_settings: BotModularSettings
    audio_settings: BotAudioSettings
    telephony_mode: BotTelephonyMode

class BotConfigUpdateResponse(BaseModel):
    success: bool = Field(..., example=True)
    updated_fields: List[str] = Field(..., example=["sales_bot_name", "company_name"])
    current_config: Dict[str, Any]

# === API Endpoint Route Mappings ===

@router.get(
    "/status",
    response_model=BotStatusResponse,
    summary="Query Bot Runtime State",
    description="Inspect active connection statistics, telemetry parameters, underlying audio processing metrics, and telephony profiles."
)
async def get_bot_status():
    """Retrieve full active sales bot runtime telemetry details."""
    result = await bot_controller.get_active_bot_telemetry()
    return result

@router.post(
    "/config",
    response_model=BotConfigUpdateResponse,
    summary="Hot-Reload Configs Instantly",
    description="Dynamically updates active OpenAI properties and sales bot personality parameters at runtime without container interruption."
)
async def update_bot_config(payload: BotConfigUpdateRequest):
    """Hot-reload sales bot configurations dynamically."""
    result = await bot_controller.update_bot_runtime_config(payload.dict(exclude_none=True))
    return result
