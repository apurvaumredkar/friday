```
███████╗██████╗ ██╗██████╗  █████╗ ██╗   ██╗    
██╔════╝██╔══██╗██║██╔══██╗██╔══██╗╚██╗ ██╔╝    
█████╗  ██████╔╝██║██║  ██║███████║ ╚████╔╝     
██╔══╝  ██╔══██╗██║██║  ██║██╔══██║  ╚██╔╝      
██║     ██║  ██║██║██████╔╝██║  ██║   ██║       
╚═╝     ╚═╝  ╚═╝╚═╝╚═════╝ ╚═╝  ╚═╝   ╚═╝       
```

### My personal agentic voice AI assistant

---

## Pipeline

```
Discord voice → VAD (Silero) → ASR (Parakeet TDT 0.6B) → n8n webhook → TTS (Kokoro 82M) → Discord voice
```

The bot joins your voice channel when you do, listens for speech, transcribes it, sends it to an n8n workflow for LLM processing, and speaks the response back.

---

## Prerequisites

- Python 3.13
- CUDA-capable GPU
- A running [n8n](https://n8n.io) instance (self-hosted or cloud)
- A Discord bot token with the **Voice States** intent enabled

---

## Setup

**1. Create and activate a virtual environment**
```bash
uv venv --python 3.13
```

**2. Install dependencies**
```bash
uv pip install -r requirements.txt
```

**3. Configure environment variables**
```bash
cp .env.example .env
```

Fill in `.env`:

| Variable | Description |
|---|---|
| `DISCORD_TOKEN` | Bot token from [Discord Developer Portal](https://discord.com/developers/applications) |
| `DISCORD_USER_ID` | Your Discord user ID (see below) |
| `N8N_USER` | n8n webhook basic auth username |
| `N8N_SECRET` | n8n webhook basic auth password |
| `WEBHOOK_URL` | Your n8n webhook URL |

> **Getting your Discord User ID:** Settings → Advanced → enable Developer Mode, then right-click your username anywhere → Copy User ID.

**4. Set up the n8n workflow**

Import `friday.json` into n8n:
- Open your n8n instance
- Go to Workflows → Import from file → select `friday.json`
- Activate the workflow
- Copy the webhook URL and set it as `WEBHOOK_URL` in `.env`
- Set the webhook's basic auth credentials to match `N8N_USER` / `N8N_SECRET`

**5. Create the Discord bot**

- Go to [Discord Developer Portal](https://discord.com/developers/applications) → New Application
- Under Bot: enable **Server Members Intent** and **Voice States** (Voice States is the minimum required)
- Copy the token → set as `DISCORD_TOKEN` in `.env`
- Invite the bot to your server with `bot` scope and `Connect`, `Speak`, `Use Voice Activity` permissions

---

## Models

All models are downloaded automatically on first run — no manual download needed.

| Model | Purpose | Cached at |
|---|---|---|
| Parakeet TDT 0.6B (int8) | Speech-to-text | `./data/nemo-parakeet-tdt-0.6b-v2-int8/` |
| Kokoro 82M (int8) | Text-to-speech | `./data/kokoro-82m/` |
| Silero VAD | Voice activity detection | `./data/silero/` |

First boot will be slow (~1-2 min) due to model downloads and ONNX session initialization.

---

## Running

```bash
./run.sh
```

`run.sh` sets the required CUDA library paths from the venv before launching `main.py`.

---

## Behaviour

- Bot joins your voice channel when you join, leaves when you leave, and follows you if you switch channels
- Speech is detected via VAD; recording stops after 1.5s of silence
- Only your speech is sent to the webhook (filtered by `DISCORD_USER_ID`)
