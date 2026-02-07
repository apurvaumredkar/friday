# CLAUDE.md — Friday Technical Design Document

> **Last updated:** 2026-02-07
> **Author:** Apurva (appu)
> **Status:** Active Development

---

## 1. Project Overview

**Friday** is a personal AI assistant built as a multi-interface system — accessible via Discord (text + voice), a web UI, and REST API. It uses **LangGraph** for multi-agent orchestration, routing natural language requests through specialized agents for text chat, web search, Google Workspace operations (Calendar, Sheets, Drive), maps/navigation, and document parsing.

The system runs on **WSL2** (Ubuntu on Windows) with an NVIDIA RTX 4070 GPU for on-device speech processing (ASR + TTS).

### 1.1 Design Philosophy — Single-User Personal Project

This is a **personal project designed exclusively for one user**. All development decisions prioritize:

1. **Individual Optimization Over Scalability**: Hardcoded preferences, personalized behaviors, and user-specific optimizations are encouraged. No user management, permissions, multi-tenancy, or horizontal scaling.
2. **Rapid Experimentation Over Enterprise Robustness**: Favor quick iteration and direct solutions over enterprise-grade patterns. Breaking changes and experimental features are acceptable.
3. **Minimal Abstraction**: Three similar lines of code are preferred over a premature abstraction. Only add complexity when the current task demands it.

---

## 2. System Architecture

### 2.1 Two-Server Supervisor/Worker Model

Friday runs as two independent processes following the **supervisor/worker pattern**:

```
                         Internet (ngrok)
                              |
                    +-------------------+
                    |  Control Server   |  Port 2400 — Supervisor
                    |  (control/server) |  Always-on systemd service
                    +-------------------+
                         |        |
                  Discord Slash   Live Logs
                  Commands        (WebSocket)
                         |
                    +-------------------+
                    |     Friday        |  Port 2401 — Worker
                    |    (main.py)      |  Managed subprocess
                    +-------------------+
                    |    |    |    |
                  API  Discord  Voice  Docker
                       Bot     Pipeline Services
```

| Component | Port | Process | Purpose |
|-----------|------|---------|---------|
| **Control Server** | 2400 | `control/server.py` | Supervisor — manages Friday lifecycle, serves logs, handles Discord slash commands. Runs as `friday-control.service` (systemd user service). |
| **Friday (main.py)** | 2401 | `main.py` | Worker — the actual AI assistant. FastAPI server + Discord bot + voice pipeline. Managed as a subprocess by the control server. |
| **ngrok** | — | `friday-ngrok.service` | Tunnel — exposes port 2400 to the internet at `lovella-flamy-rigorously.ngrok-free.dev` for Discord Interactions API. |
| **Qdrant** | 6333/6334 | Docker container | Vector database for semantic search (future use). |
| **Neo4j** | 7474/7687 | Docker container | Graph database for knowledge graph (future use). |

**Why two servers?** The control server exists so Friday can be managed remotely even when Friday is crashed or stopped. If `main.py` were the only daemon, a crash would mean no Discord bot, no way to restart remotely — requiring SSH access. The control server is a tiny (~500 LOC), stable process unlikely to crash, supervising the complex worker.

### 2.2 Startup Flow

```
main.py
  ├── Logging setup (conditional FileHandler based on isatty())
  ├── ensure_docker_services() — start Qdrant + Neo4j if not running
  ├── Thread: FastAPI server (port 2401)
  ├── Thread: Discord bot (discord.py)
  ├── Thread: Open web UI in browser (WSL-aware)
  ├── Write friday.pid (for control server process recovery)
  └── Wait for shutdown signal (SIGINT/SIGTERM)
```

When launched by the control server, stdout is redirected to `friday.log` via `subprocess.Popen`. The `sys.stdout.isatty()` check prevents adding a duplicate `FileHandler` (which would cause double-logged lines).

### 2.3 Shutdown Flow

```
Signal received (SIGTERM from control server, or SIGINT from Ctrl+C)
  ├── Set shutdown_event
  ├── Delete friday.pid
  ├── Join API thread (timeout=5s)
  ├── Join Discord thread (timeout=5s)
  └── Exit
```

The control server's `stop_friday()` sends SIGTERM, waits 10s, then SIGKILL if needed.

---

## 3. Agent Orchestration

### 3.1 LangGraph State Machine (`ai/orchestrator.py`)

The `Friday` class builds a `StateGraph` with the following topology:

```
START → root → should_continue() → {web, calendar, maps, docs, skill, END}
                                        |       |        |      |      |
                                        v       v        |      v      v
                                       root   root   maps_should_continue()  root
                                                          |          |
                                                         root       END
```

**State schema** (`AgentState`):
```python
class AgentState(TypedDict):
    messages: Annotated[list[dict], operator.add]  # Conversation history (append-only)
    original_prompt: str | None                     # Original user request
    pdf_bytes: bytes | None                         # Uploaded document content
    pdf_filename: str | None                        # Document filename
    tool_result: str | None                         # Tool execution result (loop prevention)
```

**Routing logic** — `should_continue()` reads the last assistant message and routes based on prefixes:

| Prefix | Target Node | Return Path | Stage Pattern |
|--------|------------|-------------|---------------|
| `SEARCH:` / `WEB:` | `web` | `web → root → END` | 3-stage (PAR) |
| `CALENDAR:` | `calendar` | `calendar → root → END` | 3-stage (PAR) |
| `MAPS:` | `maps` | `maps → root → END` (info) or `maps → END` (browser) | 2 or 3-stage |
| `DOCS:` | `docs` | `docs → root → END` | 3-stage (PAR) |
| *skill triggers* | `skill` | `skill → root → END` | Auto-discovered from `ai/context/skills/` |
| *(no prefix)* | `END` | Direct response | 1-stage |

**Loop prevention**: The `tool_result` field is set after any tool execution. When `should_continue()` sees a non-null `tool_result`, it returns `END` — preventing the root agent from re-routing the same request.

### 3.2 PAR Loop Pattern (Plan-Act-Reflect)

All tool-use agents follow this three-stage pattern:

1. **Plan**: Root agent analyzes user intent and emits a routing prefix (e.g., `CALENDAR: create meeting tomorrow at 3pm`)
2. **Act**: Specialized agent receives the command, uses LLM to extract a structured JSON function call, executes the API operation, and returns raw results
3. **Reflect**: Root agent receives `[TOOL RESULT - ...]` as a system message and formats a natural language response for the user

This pattern decouples intent detection (root) from API execution (tool agents) from response formatting (root again).

### 3.3 Agent Inventory (`ai/agents/`)

Each agent module is **self-contained** — combining LLM-based tool extraction AND service operations (API calls) in a single file:

| Agent | File | LLM Role | Service Integration | Tool Definitions |
|-------|------|----------|---------------------|------------------|
| **Root** | `root_agent.py` | Intent detection, routing, response formatting | None (LLM only) | — |
| **Calendar** | `calendar_agent.py` | Extract calendar operations | Google Calendar API (OAuth2) | `calendar_tools.json` |
| **Sheets** | `sheets_agent.py` | Extract spreadsheet operations | Google Sheets API (OAuth2) | `sheets_tools.json` |
| **Drive** | `drive_agent.py` | Extract file operations | Google Drive API (OAuth2) | `drive_tools.json` |
| **Web** | `web_agent.py` | Extract search/URL operations | Gemini Search (grounded) + browser open | `web_tools.json` |
| **Maps** | `maps_agent.py` | Extract location/direction queries | Google Places API + browser for directions | `maps_tools.json` |
| **Docs** | `docs_agent.py` | Extract document operations | pymupdf4llm (PDF parsing) | `docs_tools.json` |

**Tool-use agent pattern** (all non-root agents):
- **Minimal context**: Only tool definitions JSON + current user message (no conversation history)
- LLM extracts structured JSON function calls from natural language
- Returns raw API results to root agent for natural language formatting
- Each agent creates its own `OpenAI` client instance per invocation

**Shared utilities**:
- `_oauth.py` — OAuth2 token management (refresh token → access token) for Google Workspace agents (Calendar, Sheets, Drive)

### 3.4 Model Configuration (`config.json` + `ai/config.py`)

Models are configured in `config.json`, not hardcoded. Switch providers by changing `inference_provider`:

```json
{
  "inference_provider": "openrouter",
  "providers": {
    "openrouter": {
      "base_url": "https://openrouter.ai/api/v1",
      "api_key_env": "OPENROUTER_API_KEY",
      "models": {
        "root": "nvidia/nemotron-3-nano-30b-a3b:free",
        "tool": "nvidia/nemotron-nano-12b-v2-vl:free"
      }
    },
    "ollama": {
      "base_url": "http://localhost:11434/v1",
      "api_key_env": "OLLAMA_API_KEY",
      "models": {
        "root": "llama3.2:latest",
        "tool": "llama3.2:latest"
      }
    }
  }
}
```

All agents resolve models via `ai.config.get_model("root")` or `get_model("tool")`. No code changes needed to switch providers.

---

## 4. Context Hub (`ai/context/`)

Context files are organized into subdirectories by type:

```
ai/context/
├── SYSTEM_PROMPT.md           # Root agent system prompt (with placeholders)
├── USER_PROFILE.md            # User information (name, addresses, etc.)
├── skills/                    # Auto-discovered skill definitions
│   ├── PROCESS_PAYCHECK.md
│   ├── DRIVE_FETCH.md
│   └── WORK_HOME_TRANSIT.md
└── tools/                     # Tool definition JSONs for agent function extraction
    ├── calendar_tools.json
    ├── sheets_tools.json
    ├── drive_tools.json
    ├── web_tools.json
    ├── maps_tools.json
    └── docs_tools.json
```

Files loaded via `ai/context_loader.py` with in-memory caching. Skills are auto-discovered at module load.

### 4.1 Skills Framework

Skills are **self-describing** markdown files with frontmatter metadata. Dropping a `.md` file into `ai/context/skills/` auto-registers it — no code changes needed for simple skills.

**Skill file format:**
```markdown
---
trigger: MY_TRIGGER
result_label: My Skill Name
result_description: Brief description for tool results
handler: my_handler          # optional — omit for LLM-only skills
---

## Routing
Instructions for the root agent on when/how to trigger this skill...

---

## Workflow
Skill guide content used by the handler...
```

**Frontmatter fields:**
- `trigger` (required): Prefix string detected in root agent output (e.g., `WORK_TRANSIT`)
- `result_label` (required): Label for `[TOOL RESULT - {label}]` messages
- `result_description` (optional): One-liner for the TOOL RESULTS section of system prompt
- `handler` (optional): Name of registered Python handler in `SKILL_HANDLERS`. If omitted, uses generic LLM-based execution.

**Auto-discovery (`ai/context_loader.py`):**
- `discover_skills()` scans `ai/context/skills/*.md` at import time
- Only files starting with `---` (frontmatter) are treated as skills
- Builds `_SKILL_REGISTRY` mapping trigger prefixes to skill metadata
- `detect_skill()` checks messages against the registry (no hardcoded if/elif)
- `get_skill_routing()` / `get_skill_tool_results()` generate system prompt sections dynamically

**System prompt injection:**
- `SYSTEM_PROMPT.md` has `{SKILL_ROUTING}` and `{SKILL_TOOL_RESULTS}` placeholders
- At runtime, `root_agent.py` replaces them with content from all discovered skills' `## Routing` sections

**Current skills:**

| Skill | Trigger Prefix | Handler | Workflow |
|-------|---------------|---------|----------|
| `PROCESS_PAYCHECK` | `PAYCHECK_PROCESSING:` | `paycheck` | Extract PDF → parse CSV → Sheets → Drive |
| `DRIVE_FETCH` | `DRIVE_FETCH:` | `drive_fetch` | Search Drive → auto-open in browser |
| `WORK_HOME_TRANSIT` | `WORK_TRANSIT:` | `work_transit` | Google Directions API → transit routes |

**Adding a new skill:**
1. Create `ai/context/skills/MY_SKILL.md` with frontmatter + `## Routing` section — **done for simple skills**
2. (Complex skills only) Add entry to `SKILL_HANDLERS` dict in `ai/orchestrator.py` + write handler method

---

## 5. Services Layer

### 5.1 FastAPI Application Server (`services/api.py` — Port 2401)

REST API + web UI serving. Runs inside a thread started by `main.py`.

**Endpoints:**

| Endpoint | Method | Auth | Purpose |
|----------|--------|------|---------|
| `/` | GET | None | Serve web UI (`services/static/index.html`) |
| `/health` | GET | None | Health check |
| `/chat` | POST | None | Main conversational interface (JSON messages) |
| `/paycheck` | POST | None | Upload PDF → paycheck processing skill |
| `/asr` | POST | None | Transcribe audio via Parakeet TDT |
| `/tts` | POST | None | Synthesize speech via Kokoro |
| `/tts/voices` | GET | None | List available TTS voices |
| `/api/status` | GET | None | Service status + feature toggle states |
| `/api/features/toggle` | POST | None | Enable/disable features at runtime |

**Note:** Port 2401 is localhost-only (not exposed via ngrok). No authentication — acceptable for local access.

**Feature toggles** (in-memory, `_feature_states` dict):
- `web_search`, `image_analysis`, `calendar_sync`, `paycheck_processing`, `discord_integration` — default ON
- `speech` — default OFF (user enables via web UI)

### 5.2 Web UI (`services/static/`)

Single-page web interface served at `localhost:2401`.

| File | Purpose |
|------|---------|
| `index.html` | Layout — left sidebar (status, toggles) + center panel (chat) |
| `main.js` | App initialization, module coordination |
| `api.js` | API client (chat, status, feature toggle HTTP calls) |
| `chat.js` | Chat interface logic, message rendering (markdown support) |
| `toggles.js` | Feature toggle switches |
| `voice.js` | Browser-based voice interface (mic → ASR → chat → TTS → speaker) |
| `style.css` | Styling (dark theme) |

### 5.3 Discord Bot (`services/discord_bot.py`)

Full Discord.py bot with text and voice support. Creates its own `Friday()` instance.

**Text commands:**
- Any message in a channel → routed through Friday orchestrator
- PDF attachments → auto-detected, triggers paycheck processing skill
- Per-channel conversation history (in-memory `dict`)

**Voice commands (prefix `$`):**
- `$join` — Join user's voice channel, start listening
- `$leave` — Leave voice channel
- `$voice` — Toggle listening on/off

**Voice behavior:**
- Auto-joins when target user enters a voice channel
- Auto-leaves when target user disconnects
- Follows user across voice channel moves
- Pauses listening during TTS playback (prevents feedback loop)

### 5.4 Voice Pipeline (`services/voice/`)

Full-duplex voice interaction: listen to user speech → transcribe → process through Friday → synthesize response → play back in voice channel.

```
Discord Audio Packets (48kHz stereo)
  → FridayAudioSink (sink.py) — filters to target user only
  → AudioBuffer (audio_pipeline.py) — silence detection, speech segmentation
  → Resample to 16kHz mono (audio_pipeline.py)
  → Parakeet TDT ASR (ai/speech/asr.py) — NVIDIA ONNX, INT8 quantized
  → Friday Orchestrator (full LangGraph pipeline)
  → Clean text for TTS (remove markdown, emojis, URLs)
  → Kokoro TTS (ai/speech/tts.py) — ONNX model
  → Resample to 48kHz stereo (audio_pipeline.py)
  → Discord playback (playback.py)
```

**Architecture:**

| Module | Role |
|--------|------|
| `manager.py` | `VoiceManager` — orchestrates sessions, coordinates pipeline, thread pool executors |
| `sink.py` | `FridayAudioSink` — receives Discord audio, filters to target user |
| `audio_pipeline.py` | `AudioBuffer` — silence/speech detection, resampling utilities |
| `playback.py` | `create_tts_source()` — converts TTS output to Discord-playable audio |

**Performance optimizations:**
- Dedicated `ThreadPoolExecutor` for ASR (2 workers), TTS (2 workers), LLM (3 workers) to avoid GIL contention
- Uses `soxr` for high-quality audio resampling
- Speech models lazy-loaded, preloaded in background on bot startup

**Monkey-patch:** `PacketRouter._do_run` from `discord-ext-voice-recv` is patched to survive corrupted opus packets. The upstream library crashes the entire router on a single bad packet; the patch wraps `decoder.pop_data()` in try/except to skip bad frames.

### 5.5 Speech Engines (`ai/speech/`)

| Engine | Model | Framework | Quantization | Purpose |
|--------|-------|-----------|-------------|---------|
| **ASR** | NVIDIA Parakeet TDT 0.6b-v2 | `onnx-asr` | INT8 | Speech-to-text (English) |
| **TTS** | Kokoro | ONNX (`ai/models/kokoro/`) | — | Text-to-speech (multiple voices) |

Both engines are lazy-loaded singletons (`get_asr_engine()`, `get_tts_engine()`). Speech feature is off by default.

### 5.6 Google Workspace OAuth (`services/google_workspace/`)

**Not deprecated** — contains the one-time OAuth setup utility needed to generate refresh tokens.

| File | Purpose |
|------|---------|
| `google_oauth_setup.py` | Run once to perform OAuth2 flow, outputs refresh token to `tmp/google_oauth_credentials.env` |
| `google_auth.json` | OAuth client credentials (gitignored) |

Runtime OAuth token refresh is handled by `ai/agents/_oauth.py`.

---

## 6. Control Server (`control/`)

### 6.1 Server (`control/server.py` — Port 2400)

Lightweight FastAPI server that acts as the **supervisor** process. Runs as a systemd user service, exposed via ngrok.

**Capabilities:**
- Manage Friday subprocess (start/stop/reboot with graceful shutdown)
- PID file recovery on supervisor restart (tracks previously-spawned Friday)
- Discord slash commands via Interactions API (Ed25519 signature verification)
- Live log streaming via WebSocket (password-protected)

**Discord slash commands:**

| Command | Type | Behavior |
|---------|------|----------|
| `/start` | Deferred | Start Friday subprocess, show "Booting Friday up..." |
| `/stop` | Deferred | SIGTERM → wait 10s → SIGKILL, show "Shutting Friday down..." |
| `/reboot` | Deferred | Stop + start, show "Rebooting Friday..." |
| `/status` | Immediate | Return PID + uptime |
| `/logs` | Immediate | Return ngrok logs URL |
| `/clear` | Deferred | Bulk-delete messages in channel via Bot API |

**Command execution model:**
- **Immediate commands** (`status`, `logs`): Respond within Discord's 3-second limit
- **Deferred commands** (`start`, `stop`, `reboot`, `clear`): Return type 5 (deferred), execute in background thread, then edit the original response with the result. Progress messages shown while executing.

**Authentication:**
- Discord endpoint (`POST /`): Ed25519 signature verification (Discord Interactions API standard)
- Logs page (`GET /friday/logs`): HTTP Basic Auth (username: `appu`, password from `CONTROL_PASSWORD` env var)
- Logs WebSocket (`WS /friday/logs/ws`): Basic Auth from WebSocket upgrade header (browser auto-sends credentials)
- All credential comparisons use `secrets.compare_digest()` (timing-safe)

### 6.2 Command Registration (`control/register_commands.py`)

Registers Discord slash commands via PUT to Discord API. Supports both global (up to 1 hour propagation) and guild-specific (instant) registration.

```bash
python control/register_commands.py                    # Global
python control/register_commands.py --guild 123456789  # Guild (instant)
```

### 6.3 Systemd Services

| Service | Unit File | Command |
|---------|-----------|---------|
| `friday-control.service` | `control/friday-control.service` | `systemctl --user restart friday-control.service` |
| `friday-ngrok.service` | `control/friday-ngrok.service` | `systemctl --user restart friday-ngrok.service` |

Both are `Type=simple` with `Restart=always` and `RestartSec=5`.

**Restart helper:** `control/restart.sh` restarts both services and shows status.

### 6.4 Live Log Viewer

Accessible at `https://lovella-flamy-rigorously.ngrok-free.dev/friday/logs` (password-protected).

- Serves an embedded HTML page with WebSocket client
- WebSocket tails `friday.log` in real-time (0.5s poll interval)
- Sends last 200 lines as backlog on connection
- Auto-reconnects on disconnect (3s delay)
- Color-coded log levels (red=error, orange=warning, gray=debug)
- Client-side buffer capped at 2000 lines

---

## 7. Directory Structure

```
friday/
├── main.py                          # Entry point — starts all services
├── config.json                      # Model/provider configuration
├── docker-compose.yml               # Qdrant + Neo4j containers
├── requirements.txt                 # Python dependencies
├── .env                             # Secrets (gitignored)
├── .env.example                     # Template for .env
├── .gitignore
├── CLAUDE.md                        # This document
│
├── ai/                              # AI layer — orchestration + agents
│   ├── __init__.py                  # Exports Friday, ROOT_SYSTEM_PROMPT, all agents
│   ├── orchestrator.py              # LangGraph state machine (Friday class)
│   ├── config.py                    # Model/provider config loader
│   ├── context_loader.py            # Context/skill/tool loader with auto-discovery
│   ├── agents/                      # Self-contained agent modules
│   │   ├── __init__.py              # Re-exports all agent functions
│   │   ├── root_agent.py            # Main orchestrator agent (routing + reflection)
│   │   ├── calendar_agent.py        # Google Calendar operations
│   │   ├── sheets_agent.py          # Google Sheets operations
│   │   ├── drive_agent.py           # Google Drive operations
│   │   ├── web_agent.py             # Web search (Gemini) + browser opening
│   │   ├── maps_agent.py            # Google Places API + directions
│   │   ├── docs_agent.py            # PDF parsing (pymupdf4llm)
│   │   └── _oauth.py                # OAuth2 token refresh for Google APIs
│   ├── context/                     # Context hub — prompts, skills, tool defs
│   │   ├── __init__.py              # Exports skill discovery functions
│   │   ├── SYSTEM_PROMPT.md         # Root agent system prompt (with placeholders)
│   │   ├── USER_PROFILE.md          # User information (name, addresses)
│   │   ├── skills/                  # Auto-discovered skill definitions
│   │   │   ├── PROCESS_PAYCHECK.md  # Paycheck processing skill
│   │   │   ├── DRIVE_FETCH.md       # Drive file fetch skill
│   │   │   └── WORK_HOME_TRANSIT.md # Work/home transit skill
│   │   └── tools/                   # Agent tool definition JSONs
│   │       ├── calendar_tools.json
│   │       ├── sheets_tools.json
│   │       ├── drive_tools.json
│   │       ├── web_tools.json
│   │       ├── maps_tools.json
│   │       └── docs_tools.json
│   ├── speech/                      # Speech engine wrappers
│   │   ├── asr.py                   # Parakeet TDT ASR (ONNX)
│   │   └── tts.py                   # Kokoro TTS (ONNX)
│   └── models/                      # Local model weights (gitignored)
│       └── kokoro/                  # Kokoro TTS model files
│
├── services/                        # Service layer — interfaces
│   ├── api.py                       # FastAPI server (port 2401)
│   ├── discord_bot.py               # Discord.py bot
│   ├── static/                      # Web UI
│   │   ├── index.html               # Main layout
│   │   ├── main.js                  # App initialization
│   │   ├── api.js                   # API client
│   │   ├── chat.js                  # Chat interface
│   │   ├── toggles.js               # Feature toggles
│   │   ├── voice.js                 # Browser voice interface
│   │   └── style.css                # Styles (dark theme)
│   ├── voice/                       # Discord voice pipeline
│   │   ├── __init__.py              # Exports VoiceManager
│   │   ├── manager.py               # VoiceManager — session + pipeline orchestration
│   │   ├── sink.py                  # FridayAudioSink — Discord audio capture
│   │   ├── audio_pipeline.py        # AudioBuffer, resampling utilities
│   │   └── playback.py              # TTS → Discord audio source
│   └── google_workspace/            # OAuth setup utility (run once)
│       ├── __init__.py
│       ├── google_oauth_setup.py    # OAuth2 flow → refresh token
│       └── google_auth.json         # Client credentials (gitignored)
│
├── control/                         # Control server (supervisor)
│   ├── server.py                    # FastAPI control server (port 2400)
│   ├── register_commands.py         # Discord slash command registration
│   ├── restart.sh                   # Service restart helper script
│   ├── friday-control.service       # systemd unit file
│   └── friday-ngrok.service         # systemd unit file for ngrok tunnel
│
└── data/                            # Docker volume mounts (gitignored)
    ├── qdrant/                      # Qdrant storage
    └── neo4j/                       # Neo4j data
```

---

## 8. Environment Variables

All secrets stored in `.env` (gitignored). Template in `.env.example`.

| Variable | Required By | Purpose |
|----------|------------|---------|
| `OPENROUTER_API_KEY` | All agents | LLM inference via OpenRouter |
| `OLLAMA_API_KEY` | — | Local Ollama (set to `ollama` if using) |
| `DISCORD_TOKEN` | Discord bot + control server | Bot authentication + channel management |
| `DISCORD_PUBLIC_KEY` | Control server | Ed25519 signature verification |
| `DISCORD_APP_ID` | Control server | Interaction response routing |
| `GEMINI_API_KEY` | Web agent | Gemini Search with Google grounding |
| `GOOGLE_CLIENT_ID` | Google Workspace agents | OAuth2 client |
| `GOOGLE_CLIENT_SECRET` | Google Workspace agents | OAuth2 client |
| `GOOGLE_REFRESH_TOKEN` | Google Workspace agents | Long-lived OAuth2 refresh token |
| `GOOGLE_MAPS_API_KEY` | Maps agent | Google Places API |
| `PAYCHECK_SHEET_ID` | Paycheck skill | Target Google Sheet ID |
| `PAYCHECK_FOLDER_ID` | Paycheck skill | Target Google Drive folder ID |
| `CONTROL_PASSWORD` | Control server | HTTP Basic Auth for log viewer |

---

## 9. Commands Reference

### Run Friday (development)
```bash
source .venv/bin/activate
python main.py
```
Starts Docker services, FastAPI (2401), Discord bot, opens web UI in browser — all concurrently.

### Control Server (production)
```bash
# Start/restart services
bash control/restart.sh
# Or individually:
systemctl --user restart friday-control.service
systemctl --user restart friday-ngrok.service

# Check status
systemctl --user status friday-control.service friday-ngrok.service --no-pager

# View control server logs
journalctl --user -u friday-control -f --no-pager

# Register Discord commands (after adding new commands)
python control/register_commands.py                    # Global (1hr propagation)
python control/register_commands.py --guild 123456789  # Guild (instant)
```

### Docker Services
```bash
docker-compose up -d    # Start Qdrant (6333/6334) and Neo4j (7474/7687)
docker-compose down     # Stop services
```
Docker services are auto-started by `main.py` if not already running.

### Install Dependencies
```bash
pip install -r requirements.txt
# For GPU support (CUDA 12.4):
pip install torch --index-url https://download.pytorch.org/whl/cu124
```

### Google OAuth Setup (one-time)
```bash
python services/google_workspace/google_oauth_setup.py
# Follow browser prompts, copy output to .env
```

---

## 10. Key Development Patterns

| Agent | Model (OpenRouter) | Purpose | Contains |
|-------|---------------------|---------|----------|
| `root_agent.py` | `nvidia/nemotron-3-nano-30b-a3b:free` | Main orchestrator, text chat, routing | Agent only |
| `calendar_agent.py` | `nvidia/nemotron-nano-12b-v2-vl:free` | Calendar management | Agent + Google Calendar API |
| `sheets_agent.py` | `nvidia/nemotron-nano-12b-v2-vl:free` | Spreadsheet operations | Agent + Google Sheets API |
| `drive_agent.py` | `nvidia/nemotron-nano-12b-v2-vl:free` | File storage | Agent + Google Drive API |
| `web_agent.py` | `nvidia/nemotron-nano-12b-v2-vl:free` | Web search and URL fetching | Agent + Gemini Search + httpx |
| `maps_agent.py` | `nvidia/nemotron-nano-12b-v2-vl:free` | Maps, directions, transit | Agent + Places API (browser for directions) |
| `docs_agent.py` | `nvidia/nemotron-nano-12b-v2-vl:free` | Document parsing (PDF) | Agent + pymupdf4llm |

### 10.1 Adding a New Agent

1. Create `ai/agents/my_agent.py` with:
   - Service API functions (HTTP calls, data processing)
   - Agent function using `get_model("tool")` for LLM tool extraction
   - Tool execution dispatcher mapping JSON function calls to service functions
2. Create `ai/context/tools/my_agent_tools.json` with structured tool definitions
3. In `ai/orchestrator.py`:
   - Add node: `self.builder.add_node("my_node", self.my_node)`
   - Add routing in `should_continue()`: `if "MY_PREFIX:" in last_msg: return "my_node"`
   - Add edge: `self.builder.add_edge("my_node", "root")` (for reflect pattern)
   - Implement `my_node()` method following existing patterns
4. Export from `ai/agents/__init__.py`

### 10.2 Adding a New Skill

**Simple skill (LLM-only — no API calls needed):**
1. Create `ai/context/skills/MY_SKILL.md` with frontmatter (`trigger`, `result_label`) and `## Routing` section
2. Done — skill is auto-discovered at startup

**Complex skill (needs API calls):**
1. Create `ai/context/skills/MY_SKILL.md` with frontmatter (include `handler: my_handler`) and `## Routing` section
2. Add `"my_handler": "_execute_my_skill"` to `SKILL_HANDLERS` dict in `ai/orchestrator.py`
3. Implement `_execute_my_skill(self, state)` method in the `Friday` class

### 10.3 Adding a New Discord Slash Command

1. Add command definition in `control/register_commands.py` → `COMMANDS` list
2. Add handler in `control/server.py`:
   - For fast commands: Add to `COMMAND_HANDLERS` dict + `IMMEDIATE_COMMANDS` set
   - For slow commands: Add to `COMMAND_HANDLERS` dict + `PROGRESS_MESSAGES` dict
   - For commands needing context (like channel_id): Handle separately before the generic dispatcher
3. Register: `python control/register_commands.py`
4. Restart control server: `bash control/restart.sh`

### 10.4 WSL-Aware Browser Opening

The codebase runs on WSL2. Browser opening (in `web_agent.py` and `main.py`) detects WSL via `platform.uname().release` and uses `powershell.exe Start-Process` for Windows browser, falling back to `webbrowser.open()` otherwise.

### 10.5 Logging Convention

All modules use:
```python
logger = logging.getLogger(__name__)
```
Exception: Control server uses `logging.getLogger("friday-control")` (separate process identity).

Log prefixes for agent context: `[WEB]`, `[MAPS]`, `[DOCS]`, `[SKILL]`, `[SKILL:PAYCHECK]`, `[ROUTING]`.

---

## 11. Known Issues and Technical Debt

### Active Issues

| # | Severity | Issue | Location |
|---|----------|-------|----------|
| 1 | **High** | File handle leak — `open(FRIDAY_LOG, "a")` never closed after start/stop cycles | `control/server.py:139` |
| 2 | **High** | Conversation history unbounded — grows forever per channel, never pruned | `services/discord_bot.py:34` |
| 3 | **Medium** | Two separate `Friday()` instances — API and Discord bot create independent orchestrators | `services/api.py:21` vs `services/discord_bot.py:32` |
| 4 | **Medium** | Voice processing race condition — `session.processing` flag checked without lock | `services/voice/manager.py:331-347` |
| 5 | **Medium** | URL not sanitized before passing to PowerShell `Start-Process` | `ai/agents/web_agent.py` |
| 6 | **Low** | Voice manager `cleanup_all()` never called on shutdown | `services/discord_bot.py` |

### Architecture Decisions to Revisit

- **Supervisor health checks**: Control server only checks PID liveness, not actual application health. Should ping `localhost:2401/health` periodically.
- **Auto-restart on crash**: If Friday subprocess dies, control server does nothing until manual `/start`. Should auto-restart with backoff.
- **Feature toggle persistence**: `_feature_states` resets on restart (in-memory only).
- **Skill dispatch**: ~~Currently hardcoded~~ Resolved — skills are now auto-discovered from `ai/context/skills/*.md` with frontmatter-based registry.

---

## 12. Security Model

### Network Exposure

| Port | Binding | Internet Accessible | Authentication |
|------|---------|-------------------|----------------|
| 2400 | `0.0.0.0` | Yes (via ngrok) | Ed25519 signatures (Discord) + HTTP Basic Auth (logs) |
| 2401 | `0.0.0.0` | No (localhost only) | None |
| 6333/6334 | Docker | No | None |
| 7474/7687 | Docker | No | `neo4j/friday_neo4j` |

### Secrets Management

- All secrets in `.env` (gitignored, never committed)
- `.env.example` has empty values as template
- `google_auth.json` (OAuth client creds) gitignored separately
- `CONTROL_PASSWORD` used for HTTP Basic Auth with timing-safe comparison
- Discord interactions verified via Ed25519 signature on every request

### Authentication Flows

- **Discord Interactions API**: Ed25519 signature verification using `nacl.signing.VerifyKey`
- **Log viewer**: HTTP Basic Auth → browser caches credentials → auto-sends on WebSocket upgrade
- **Google APIs**: OAuth2 with refresh token (token refresh handled in `_oauth.py`)

---

## 13. Infrastructure

### Runtime Environment

- **OS**: WSL2 (Ubuntu) on Windows 11
- **GPU**: NVIDIA RTX 4070 (8GB VRAM) — used for ASR/TTS inference
- **Python**: 3.12+ (virtual environment at `.venv/`)
- **Process manager**: systemd user services (control server + ngrok)

### External Dependencies

| Service | Purpose | Free Tier |
|---------|---------|-----------|
| OpenRouter | LLM inference (Nemotron models) | Free tier available |
| Google Cloud | Calendar, Sheets, Drive, Maps APIs | Personal usage quota |
| Gemini API | Web search with Google grounding | Free tier |
| Discord | Bot hosting + Interactions API | Free |
| ngrok | Tunnel for Discord webhook delivery | Free static domain |

### Docker Services

Defined in `docker-compose.yml`, auto-started by `main.py`:
- **Qdrant**: Vector database (data at `./data/qdrant/`)
- **Neo4j**: Graph database (data at `./data/neo4j/`), auth: `neo4j/friday_neo4j`

Both set to `restart: unless-stopped`.
