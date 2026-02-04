import logging
import os
import uuid
import io
import asyncio
import re
import base64
from pathlib import Path
from fastapi import FastAPI, HTTPException, File, UploadFile, Form, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from ai import Friday
from ai.speech import ASREngine, TTSEngine
from ai.speech.tts import clean_text_for_tts
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)

app = FastAPI(title="Friday AI API")
friday = Friday()

# Mount static files for web interface
STATIC_DIR = Path(__file__).parent / "static"
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.on_event("startup")
async def startup_event():
    """Preload models on startup."""
    import threading

    def preload_models():
        """Preload all models in background."""
        logger.info("Preloading models in background...")
        import time

        # Preload speech engines if enabled
        if _feature_states.get("speech", True):
            try:
                start = time.perf_counter()
                get_asr_engine()
                logger.info(f"ASR engine preloaded in {time.perf_counter() - start:.1f}s")
                get_tts_engine()
                logger.info(f"TTS engine preloaded in {time.perf_counter() - start:.1f}s")
            except Exception as e:
                logger.error(f"Failed to preload speech engines: {e}")

        logger.info("[OK] All models preloaded")

    threading.Thread(target=preload_models, daemon=True).start()

# Speech engines (lazy loaded)
_asr_engine: Optional[ASREngine] = None
_tts_engine: Optional[TTSEngine] = None

# Feature state management (in-memory, can be persisted to file if needed)
_feature_states = {
    "web_search": True,
    "image_analysis": True,
    "calendar_sync": True,
    "paycheck_processing": True,
    "speech": False,  # Combined ASR + TTS toggle - default OFF, user must enable
    "discord_integration": True
}


def get_asr_engine() -> ASREngine:
    global _asr_engine
    if _asr_engine is None:
        _asr_engine = ASREngine()
    return _asr_engine


def get_tts_engine() -> TTSEngine:
    global _tts_engine
    if _tts_engine is None:
        _tts_engine = TTSEngine()
    return _tts_engine


class Message(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    messages: List[Message]


class ChatResponse(BaseModel):
    messages: List[Dict[str, str]]


@app.get("/")
async def root():
    """Serve the web interface."""
    logger.info("Root endpoint accessed - serving web interface")
    index_path = STATIC_DIR / "index.html"
    if index_path.exists():
        return FileResponse(str(index_path))
    else:
        logger.warning("Web interface not found, returning API message")
        return {"message": "Friday AI Agent API is running"}


@app.get("/health")
async def health():
    logger.info("Health check endpoint accessed")
    return {"status": "healthy"}


@app.get("/logs")
async def get_logs(lines: int = 100):
    logger.info(f"Logs endpoint accessed, requesting {lines} lines")
    try:
        log_file = "friday.log"
        if not os.path.exists(log_file):
            logger.warning("Log file not found")
            raise HTTPException(status_code=404, detail="Log file not found")

        with open(log_file, 'r') as f:
            all_lines = f.readlines()
            last_lines = all_lines[-lines:] if len(all_lines) > lines else all_lines

        return PlainTextResponse(''.join(last_lines))

    except Exception as e:
        logger.error(f"Error reading logs: {e}")
        raise HTTPException(status_code=500, detail=f"Error reading logs: {str(e)}")


@app.get("/logs/download")
async def download_logs():
    logger.info("Log download endpoint accessed")
    try:
        log_file = "friday.log"
        if not os.path.exists(log_file):
            logger.warning("Log file not found for download")
            raise HTTPException(status_code=404, detail="Log file not found")

        return FileResponse(log_file, filename="friday.log", media_type="text/plain")

    except Exception as e:
        logger.error(f"Error downloading logs: {e}")
        raise HTTPException(status_code=500, detail=f"Error downloading logs: {str(e)}")


def clean_response(content: str, is_first_message: bool) -> str:
    """Remove redundant greetings from responses if not the first message."""
    if is_first_message:
        return content

    # List of greeting patterns to remove from subsequent messages
    greeting_patterns = [
        "Hey Apurva! Just hanging out, ready to help. What's on your mind?",
        "Hey Apurva!",
        "Hi Apurva!",
        "Hello Apurva!",
    ]

    cleaned = content.strip()
    for pattern in greeting_patterns:
        if cleaned.startswith(pattern):
            # Remove the greeting and any trailing whitespace/newlines
            cleaned = cleaned[len(pattern):].strip()

    return cleaned


@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    logger.info(f"Chat request received with {len(request.messages)} messages")
    try:
        messages_dict = [{"role": msg.role, "content": msg.content} for msg in request.messages]
        logger.info("Invoking Friday agent")
        result = friday.app.invoke({"messages": messages_dict})
        logger.info(f"Friday agent responded with {len(result['messages'])} messages")

        # Check if this is the first user message (conversation just started)
        user_message_count = sum(1 for msg in messages_dict if msg["role"] == "user")
        is_first_message = user_message_count == 1

        # Clean up responses to remove redundant greetings
        cleaned_messages = []
        for msg in result["messages"]:
            if msg["role"] == "assistant":
                cleaned_content = clean_response(msg["content"], is_first_message)
                cleaned_messages.append({"role": msg["role"], "content": cleaned_content})
            else:
                cleaned_messages.append(msg)

        return {"messages": cleaned_messages}

    except Exception as e:
        logger.error(f"Error processing chat request: {e}")
        raise HTTPException(status_code=500, detail=f"Error processing request: {str(e)}")


@app.post("/paycheck", response_model=ChatResponse)
async def process_paycheck(file: UploadFile = File(...)):
    """
    Process a paycheck PDF, add to Google Sheets, and upload to Google Drive.

    Upload a PDF paycheck file. The endpoint will:
    1. Pass PDF to orchestrator
    2. Paycheck skill extracts text via docs_agent
    3. Paycheck skill parses data and updates Google Sheet
    4. Paycheck skill uploads PDF to Google Drive
    """
    logger.info(f"Paycheck endpoint: received file {file.filename}")

    if not file.filename.lower().endswith('.pdf'):
        raise HTTPException(status_code=400, detail="Only PDF files are accepted")

    try:
        # Read PDF bytes
        pdf_bytes = await file.read()
        pdf_filename = file.filename
        logger.info(f"Received PDF: {pdf_filename}, size: {len(pdf_bytes)} bytes")

        # Pass to orchestrator - skill will handle extraction via docs_agent
        logger.info("Invoking Friday orchestrator with paycheck PDF")
        result = friday.app.invoke({
            "messages": [{"role": "user", "content": "Process this paycheck"}],
            "original_prompt": "Process this paycheck",
            "pdf_bytes": pdf_bytes,
            "pdf_filename": pdf_filename
        })

        logger.info(f"Paycheck processing complete, response messages: {len(result['messages'])}")
        return {"messages": result["messages"]}

    except Exception as e:
        logger.error(f"Error processing paycheck: {e}")
        raise HTTPException(status_code=500, detail=f"Error processing paycheck: {str(e)}")


# ============================================================================
# Web Interface API Endpoints
# ============================================================================


class ToggleRequest(BaseModel):
    feature: str
    enabled: bool


def check_service_status():
    """Check status of all services."""
    # Check Discord bot actual status
    discord_connected = False
    try:
        from services.discord_bot import get_bot_instance, _bot_responding
        bot = get_bot_instance()
        discord_connected = bot is not None and bot.is_ready() and _bot_responding
    except Exception as e:
        logger.debug(f"Could not check Discord bot status: {e}")

    # Check Google Workspace (check if credentials exist)
    google_authorized = all([
        os.getenv("GOOGLE_CLIENT_ID"),
        os.getenv("GOOGLE_CLIENT_SECRET"),
        os.getenv("GOOGLE_REFRESH_TOKEN")
    ])

    # Check if both ASR and TTS engines are loaded
    speech_loaded = (_asr_engine is not None) and (_tts_engine is not None)

    return {
        "services": {
            "discord": {
                "connected": discord_connected,
                "status": "online" if discord_connected else "offline"
            },
            "google_workspace": {
                "authorized": google_authorized,
                "drive": google_authorized,
                "sheets": google_authorized,
                "calendar": google_authorized
            },
            "speech": {
                "loaded": speech_loaded
            }
        },
        "features": _feature_states.copy()
    }


@app.get("/api/status")
async def get_status():
    """Get service status and feature states."""
    logger.info("Status endpoint accessed")
    return check_service_status()


@app.post("/api/features/toggle")
async def toggle_feature(request: ToggleRequest):
    """Enable/disable a feature."""
    logger.info(f"Toggle feature request: {request.feature} = {request.enabled}")

    if request.feature not in _feature_states:
        raise HTTPException(status_code=400, detail=f"Unknown feature: {request.feature}")

    _feature_states[request.feature] = request.enabled

    # Handle Discord bot status change
    if request.feature == "discord_integration":
        try:
            from services.discord_bot import get_bot_instance, set_bot_status
            bot = get_bot_instance()
            if bot and bot.is_ready():
                # Schedule the status change in the bot's event loop
                asyncio.run_coroutine_threadsafe(
                    set_bot_status(request.enabled),
                    bot.loop
                )
                logger.info(f"Discord bot status changed: {'online' if request.enabled else 'offline'}")
            else:
                logger.warning("Discord bot not ready, cannot change status")
        except Exception as e:
            logger.error(f"Failed to change Discord bot status: {e}")

    return {
        "feature": request.feature,
        "enabled": request.enabled
    }


@app.websocket("/ws/logs")
async def websocket_logs(websocket: WebSocket):
    """Stream logs via WebSocket."""
    await websocket.accept()
    logger.info("WebSocket log stream connected")

    try:
        log_file = "friday.log"

        # Send existing logs first
        if os.path.exists(log_file):
            with open(log_file, 'r') as f:
                # Send last 100 lines
                lines = f.readlines()
                for line in lines[-100:]:
                    await websocket.send_text(line.rstrip('\n'))

        # Follow log file for new entries
        with open(log_file, 'r') as f:
            # Go to end of file
            f.seek(0, 2)

            while True:
                line = f.readline()
                if line:
                    await websocket.send_text(line.rstrip('\n'))
                else:
                    # No new data, wait a bit
                    await asyncio.sleep(0.5)

    except WebSocketDisconnect:
        logger.info("WebSocket log stream disconnected")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        try:
            await websocket.close()
        except:
            pass



# ============================================================================
# Speech Endpoints (ASR & TTS)
# ============================================================================


class TTSRequest(BaseModel):
    text: str
    voice: str = "af_heart"
    speed: float = 1.0


class TranscriptionResponse(BaseModel):
    text: str


class VoicesResponse(BaseModel):
    voices: Dict[str, str]


@app.post("/asr", response_model=TranscriptionResponse)
async def transcribe_audio(file: UploadFile = File(...)):
    """
    Transcribe audio to text using Parakeet ASR.

    Upload an audio file (WAV, MP3, FLAC, etc.) and get the transcription.
    Audio should be in 16kHz sample rate for best results.
    """
    logger.info(f"ASR request received: {file.filename}")

    # Check if speech is enabled
    if not _feature_states.get("speech", True):
        raise HTTPException(status_code=403, detail="Speech feature is disabled")

    try:
        audio_bytes = await file.read()
        logger.info(f"Received audio file: {len(audio_bytes)} bytes")

        asr = get_asr_engine()
        text = asr.transcribe(audio_bytes)

        logger.info(f"Transcription complete: {len(text)} characters")
        return {"text": text}

    except Exception as e:
        logger.error(f"ASR transcription failed: {e}")
        raise HTTPException(status_code=500, detail=f"Transcription failed: {str(e)}")


@app.post("/tts")
async def synthesize_speech(request: TTSRequest):
    """
    Synthesize speech from text using Kokoro TTS.

    Returns audio as WAV file.

    Available voices:
    - af_heart: American Female (warm, friendly)
    - af_bella: American Female (bella)
    - am_adam: American Male (adam)
    - bf_emma: British Female (emma)
    - bm_george: British Male (george)
    """
    logger.info(f"TTS request: {len(request.text)} chars, voice={request.voice}")

    # Check if speech is enabled
    if not _feature_states.get("speech", True):
        raise HTTPException(status_code=403, detail="Speech feature is disabled")

    if len(request.text) > 5000:
        raise HTTPException(status_code=400, detail="Text too long (max 5000 characters)")

    try:
        import soundfile as sf
        import numpy as np

        # Clean text for TTS (remove markdown, emojis, etc.)
        cleaned_text = clean_text_for_tts(request.text)
        logger.info(f"TTS text cleaned: {len(request.text)} -> {len(cleaned_text)} chars")

        tts = get_tts_engine()
        samples, sample_rate = tts.synthesize(
            text=cleaned_text,
            voice=request.voice,
            speed=request.speed,
        )

        # Convert to WAV bytes
        buffer = io.BytesIO()
        sf.write(buffer, samples, sample_rate, format='WAV')
        buffer.seek(0)

        logger.info(f"TTS synthesis complete: {len(samples)} samples")
        return StreamingResponse(
            buffer,
            media_type="audio/wav",
            headers={"Content-Disposition": "attachment; filename=speech.wav"}
        )

    except Exception as e:
        logger.error(f"TTS synthesis failed: {e}")
        raise HTTPException(status_code=500, detail=f"Speech synthesis failed: {str(e)}")


@app.get("/tts/voices", response_model=VoicesResponse)
async def get_voices():
    """Get available TTS voices."""
    tts = get_tts_engine()
    return {"voices": tts.get_available_voices()}



if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=2401)
