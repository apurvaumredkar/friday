# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Setup
uv venv --python 3.13
uv pip install -r requirements.txt
cp .env.example .env   # then fill in credentials

# Run
./run.sh               # sets CUDA LD_LIBRARY_PATH then runs main.py
```

There are no tests. `data/test_speech.wav` exists as a manual test fixture.

## Architecture

Friday is a voice-to-voice Discord bot with a separate HTTP TTS endpoint.

**Voice pipeline (listen → transcribe → forward):**
```
Discord voice (48kHz stereo int16)
  → FridaySink (VAD every 0.5s via Silero ONNX)
  → silence timeout (1.5s) → stop_recording()
  → process(): ASR → webhook POST → restart recording
```

**TTS pipeline (HTTP-triggered):**
```
POST /speak {"text": "..."} → Kokoro TTS → Discord playback
```

The voice pipeline and TTS are decoupled — `process()` does not read the webhook response or trigger TTS. Speech output is initiated externally via the `/speak` FastAPI endpoint.

**`main.py`** is the only source file. It contains:
- `FridaySink(discord.sinks.PCMSink)` — accumulates PCM per user, runs Silero VAD, fires silence timer; filters by `DISCORD_USER_ID`
- `process(sink, vc)` — ASR + webhook POST via `run_in_executor`, then restarts recording
- `speak(text, vc)` — runs Kokoro TTS and plays audio to the voice channel
- `SpeakRequest` / `POST /speak` — FastAPI endpoint that calls `speak()` on the active voice client
- `_run_api()` — starts FastAPI/uvicorn on a daemon thread (port from `TTS_PORT` env, default 8765)
- Discord event handlers: auto-join on `on_ready`, follow/leave on `on_voice_state_update`

**External LLM via n8n:** No local LLM inference. `process()` POSTs `{"user_id": str, "text": str}` with basic auth to a webhook URL. The n8n workflow (`friday.json`) hosts a granite4:3b-h agent via Ollama.

## Model Details

| Model | Path | Provider | Notes |
|---|---|---|---|
| Parakeet TDT 0.6B int8 | `./data/nemo-parakeet-tdt-0.6b-v2-int8/` | CPU | 4 intra + 2 inter threads; RTF≈0.05 |
| Kokoro 82M **fp32** | `./data/kokoro-82m/` | CUDA | Must use fp32, not int8, on GPU (int8 = 550 memcpy nodes → slower) |
| Silero VAD | `./data/silero/` | CPU | Auto-downloaded by onnx-asr |

Models load at module import time — cold start takes ~1-2 min. Kokoro model files are auto-downloaded from GitHub if missing.

## Audio Format Notes

- Discord → PCM: 48kHz stereo int16
- VAD path: decimate 3× → 16kHz mono float32
- ASR input: 48kHz mono float32 (Parakeet resamples internally)
- TTS output: 24kHz mono float32 → `resample_poly(up=2)` → 48kHz stereo int16 → `discord.PCMAudio`

## CUDA Setup

`run.sh` sets `LD_LIBRARY_PATH` to bundled NVIDIA libs inside `.venv` (cublas, cudnn, etc.). Any script doing ONNX GPU inference must either be launched via `run.sh` or self-re-exec after setting `LD_LIBRARY_PATH`.

## Required Environment Variables

| Variable | Purpose |
|---|---|
| `DISCORD_TOKEN` | Bot token (Voice States intent required) |
| `DISCORD_USER_ID` | Numeric ID; only this user's audio is processed |
| `N8N_USER` / `N8N_SECRET` | Basic auth for n8n webhook |
| `WEBHOOK_URL` | n8n webhook endpoint |
| `TTS_PORT` | FastAPI listen port (optional, default 8765) |
