```
███████╗██████╗ ██╗██████╗  █████╗ ██╗   ██╗
██╔════╝██╔══██╗██║██╔══██╗██╔══██╗╚██╗ ██╔╝
█████╗  ██████╔╝██║██║  ██║███████║ ╚████╔╝
██╔══╝  ██╔══██╗██║██║  ██║██╔══██║  ╚██╔╝
██║     ██║  ██║██║██████╔╝██║  ██║   ██║
╚═╝     ╚═╝  ╚═╝╚═╝╚═════╝ ╚═╝  ╚═╝   ╚═╝
```

A privacy-focused, locally-run AI assistant built on a multi-agent architecture. Runs on Discord — supports both text and voice interaction. All inference is local via Ollama.

---

## Features

- **Text & voice interface** over Discord
- **Multi-agent orchestration** — the orchestrator delegates tasks to specialised sub-agents
- **Full voice pipeline** — speech-to-text (ASR) → agent response → text-to-speech (TTS), all running locally
- **Google Workspace integration** via official API services

---

## Agents

| Agent | Description |
|---|---|
| **Orchestrator** | Routes requests to sub-agents using Ollama (`granite4:3b-h`) |
| **WebAgent** | Web search via Google Gemini 2.5 Flash with grounding |
| **GoogleTasksAgent** | Full CRUD on Google Tasks (create, list, update, move, delete) |

---

## Voice Pipeline

```
User speaks → Discord → ASR → Orchestrator → TTS → Discord → User hears response
```

- **ASR**: Nvidia Parakeet TDT 0.6B v2 (ONNX, runs on CPU or CUDA)
- **TTS**: Kokoro 82M (ONNX, runs on CPU or CUDA)
- Silence detection triggers transcription after 1.5s of quiet

---

## Installation

### 1. Prerequisites

- [Ollama](https://ollama.com) running locally with `granite4:3b-h` pulled
- Python 3.12+
- `libopus0` for Discord voice: `sudo apt install libopus0`
- A Discord bot token with `message_content` and `voice_states` intents enabled

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Environment variables

Create a `.env` file in the project root:

```
DISCORD_BOT_TOKEN=
DISCORD_USER_ID=
GEMINI_API_KEY=
```

### 4. Google OAuth

On first run, a browser window will open for Google OAuth. Credentials are saved to `data/google_token.json` for subsequent runs.

### 5. Voice model files

Download and place in `data/parakeet/`:
- Parakeet ONNX model — [istupakov/parakeet-tdt-0.6b-v2-onnx](https://huggingface.co/istupakov/parakeet-tdt-0.6b-v2-onnx)

Download and place in `data/kokoro/`:
- `kokoro-v1.0.onnx` and `voices-v1.0.bin` — [thewh1teagle/kokoro-onnx releases](https://github.com/thewh1teagle/kokoro-onnx/releases/tag/model-files-v1.0)

### 6. Run

```bash
python main.py
```

---

## In Progress

| Agent | Description |
|---|---|
| **GoogleDriveAgent** | File search and retrieval via Google Drive API |
| **GoogleSheetsAgent** | Read/write access to Google Sheets |
| **Atlas** | Navigator agent for places, directions, and maps (Google Maps API) |
| **NotionAgent** | Notion workspace integration via MCP |

---

## Future Work

- **Long-term memory** — Qdrant (vector store) and Neo4j (knowledge graph) for persistent memory across conversations

---

## Infrastructure

A `docker-compose.yml` is included to spin up the memory backend services:

```bash
docker compose up -d
```

This starts:
- **Qdrant** — vector database (host network, default port 6333)
- **Neo4j** — graph database (host network, default ports 7474 / 7687)