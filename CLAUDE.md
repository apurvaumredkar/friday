# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Friday is a personal AI assistant that runs as both a Discord bot and a FastAPI server. It uses LangGraph for multi-agent orchestration, routing requests through specialized agents for text chat, web operations, paycheck processing, and calendar management.

### **Important: Single-User Personal Project**

This is a **personal project designed exclusively for one user**, not intended for production deployment, multi-user scenarios, or public-facing services. All development decisions should prioritize:

1. **Individual Optimization Over Scalability**: Features should be tailored to the specific workflows, preferences, and needs of a single user. There's no need to handle multiple users, authentication systems, rate limiting, or horizontal scaling. Hardcoded preferences, personalized behaviors, and user-specific optimizations are encouraged and appropriate.

2. **Rapid Experimentation Over Enterprise Robustness**: The codebase should favor quick iteration, direct solutions, and exploratory features over enterprise-grade patterns like comprehensive error recovery, extensive logging infrastructure, or defensive programming for edge cases. Breaking changes, refactors, and experimental features are acceptable since there's only one stakeholder who controls the entire system.

**Practical Implications:**
- No need for user management, permissions, or multi-tenancy
- Environment variables and configurations can be user-specific and opinionated
- Direct integrations (Google Workspace, Discord) can assume single-user context
- Performance optimizations can target specific hardware/usage patterns
- Features can be added based on single-user requests without generalization
- No requirement for backward compatibility, versioning, or migration strategies

## Commands

### Run the application
```bash
source .venv/bin/activate
python main.py
```
This starts both the FastAPI server (port 2401) and Discord bot concurrently in separate threads.

### Install dependencies
```bash
pip install -r requirements.txt
# For GPU support (CUDA 12.4):
pip install torch --index-url https://download.pytorch.org/whl/cu124
```

## Architecture

### Agent Orchestration (`ai/orchestrator.py`)
The `Friday` class uses LangGraph's `StateGraph` to route messages through nodes:
- **Route logic**: All input → `root` node first
- **Conditional edges**:
  - Root responses starting with `SEARCH:` or `WEB:` → `web` node (then back to `root` for reflection)
  - Root responses containing skill triggers → `skill` node (dynamic workflow execution)
  - Root responses containing `CALENDAR:` → `calendar` node (then back to `root` for reflection)
  - Root responses containing `MAPS:` → `maps` node (then back to `root` for reflection)
  - Root responses containing `DOCS:` → `docs` node (then back to `root` for reflection)
- **PAR Loop Pattern**: Web, calendar, and skills implement Plan-Act-Reflect:
  - Plan: Root agent detects intent; tool-use agents (Qwen) extract function calls
  - Act: Service layer executes operations (Google APIs, web fetch)
  - Reflect: Root agent formats result with full conversation context

### Self-Contained Agents (`ai/agents/`)
Each agent module is **fully self-contained**, combining both the agent logic (LLM-based tool extraction) AND service operations (API calls) in a single file:

| Agent | Model (OpenRouter) | Purpose | Contains |
|-------|---------------------|---------|----------|
| `root_agent.py` | `nvidia/nemotron-3-nano-30b-a3b:free` | Main orchestrator, text chat, routing | Agent only |
| `calendar_agent.py` | `nvidia/nemotron-nano-12b-v2-vl:free` | Calendar management | Agent + Google Calendar API |
| `sheets_agent.py` | `nvidia/nemotron-nano-12b-v2-vl:free` | Spreadsheet operations | Agent + Google Sheets API |
| `drive_agent.py` | `nvidia/nemotron-nano-12b-v2-vl:free` | File storage | Agent + Google Drive API |
| `web_agent.py` | `nvidia/nemotron-nano-12b-v2-vl:free` | Web search and URL fetching | Agent + Gemini Search + httpx |
| `maps_agent.py` | `nvidia/nemotron-nano-12b-v2-vl:free` | Maps, directions, transit | Agent + Places API (browser for directions) |
| `docs_agent.py` | `nvidia/nemotron-nano-12b-v2-vl:free` | Document parsing (PDF) | Agent + pymupdf4llm |

**Shared utilities**:
- `_oauth.py`: OAuth2 token management for Google Workspace agents

**Tool-Use Agent Pattern**:
- Minimal context: Only tool definitions + current user message (no conversation history)
- GPT OSS 20B extracts structured function calls from natural language
- Returns raw API results to root agent for natural language formatting
- Efficient: Fixed ~60 lines context vs. full conversation history

### Context Hub (`ai/skills/`)
All context files are consolidated in a single directory. **All `.md` files use UPPERCASE filenames.**

- **Prompts** (.md): System prompts for agents (e.g., `SYSTEM_PROMPT.md`)
- **Skills** (.md): Workflow instruction guides (e.g., `PROCESS_PAYCHECK.md`)
- **Tools** (.json): Tool definitions for LLM agents (e.g., `calendar_tools.json`, `web_tools.json`)

Files are loaded via `ai/context_loader.py` with caching.

### Skills Framework
Skills are markdown-based instruction guides that define multi-step workflows:

**Current Skills**:
- **`PROCESS_PAYCHECK.md`**: Process paycheck PDFs (docs_agent → sheets_agent → drive_agent)
- **`DRIVE_FETCH.md`**: Search Google Drive and auto-open first result in browser

**Naming Convention**: All `.md` files in `ai/skills/` must use UPPERCASE filenames

**Adding a New Skill**:
1. Create `ai/skills/MY_SKILL.md` with workflow instructions (UPPERCASE filename required)
2. Add detection pattern to `ai/context_loader.py`:
```python
def detect_skill(message: str, state: dict) -> Optional[str]:
    if "MY_SKILL_TRIGGER:" in message:
        return "MY_SKILL"
```
3. Add execution handler to orchestrator's `skill_node()`:
```python
if skill_name == "MY_SKILL":
    return self._execute_my_skill(state)
```

### Services
- **`services/api.py`**: FastAPI server (port 2401) with REST endpoints
- **`services/discord_bot.py`**: Discord.py bot with per-channel conversation history
- **`services/voice/`**: Discord voice channel integration (ASR → Friday → TTS)
- **`services/google_workspace/`**: **DEPRECATED** - Only contains OAuth setup utility; actual Google operations are in `ai/agents/`

### Voice Integration (`services/voice/`)
Enables voice conversations in Discord voice channels:
- **`manager.py`**: `VoiceManager` orchestrates sessions, handles ASR → Friday → TTS pipeline
- **`audio_pipeline.py`**: Audio buffering, silence detection, resampling (48kHz ↔ 16kHz)
- **`sink.py`**: `FridayAudioSink` receives Discord audio, filters by target user
- **`playback.py`**: `TTSAudioSource` plays TTS output to Discord

**Discord commands**: `$join` (join VC), `$leave` (disconnect), `$voice` (toggle listening)

### Speech Engines (`ai/speech/`)
- **ASR**: NVIDIA Parakeet TDT 0.6b-v2 via `onnx-asr` library with INT8 quantization
- **TTS**: Kokoro ONNX model from `ai/models/kokoro/`
- **Text Cleaning**: Centralized `clean_text_for_tts()` in `ai/speech/tts.py`

Models are lazy-loaded on first request to reduce startup time.

### API Endpoints
- **Health & Monitoring**: `/health`, `/logs`, `/logs/download`
- **Chat**: `/chat` - Main conversational interface
- **Paycheck**: `/paycheck` - Upload PDF, extract data, update Google Sheets, upload to Drive
- **Speech**: `/asr` (transcribe audio), `/tts` (synthesize speech), `/tts/voices` (list voices)

### Logging
Application logs to `friday.log` with rotation. Logs accessible via `/logs` endpoint (default: last 100 lines) or `/logs/download` for full file.

## Environment Variables

Required in `.env`:
- `DISCORD_TOKEN` - Discord bot token
- `OPENROUTER_API_KEY` - For root agent and tool-use agents (Nemotron, Qwen)
- `GEMINI_API_KEY` - For web services (search via Gemini with Google Search grounding)
- `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REFRESH_TOKEN` - For Google Workspace (Sheets, Drive, Calendar)
- `GOOGLE_MAPS_API_KEY` - For maps agent (Routes API, Places API)
- `PAYCHECK_SHEET_ID`, `PAYCHECK_FOLDER_ID` - For paycheck processing

## Key Patterns

### Adding a new agent
1. Create agent file in `ai/agents/my_agent.py` containing:
   - Service API functions (create, read, update, delete operations)
   - Agent function that uses Qwen for tool extraction + executes service functions
2. Add node in `Friday.__init__()` in `ai/orchestrator.py`
3. Add routing logic in `should_continue()`
4. Export from `ai/agents/__init__.py` and `ai/__init__.py`

### Agent communication protocol
Agents signal routing via response prefixes from root agent:
- `SEARCH:` triggers web agent (search_web tool) → returns to root for reflection
- `WEB:` triggers web agent (open_url tool) → returns to root for reflection
- `PAYCHECK_PROCESSING:` triggers paycheck skill (Sheets update + Drive upload)
- `DRIVE_FETCH:` triggers drive fetch skill (search Drive + auto-open result)
- `CALENDAR:` triggers calendar agent (tool extraction) → returns to root for reflection
- `MAPS:` triggers maps agent (place info, directions, transit) → returns to root for reflection
- `DOCS:` triggers docs agent (PDF read, search, info) → returns to root for reflection

**Tool Results Flow**:
- Web, calendar, and skill operations return raw results as system message: `[TOOL RESULT - Operation Type]`
- Root agent formats results naturally using full conversation context
- Enables multi-turn interactions and follow-up questions

## Directory Structure

```
friday/
├── main.py                 # Entry point - starts FastAPI + Discord threads
├── ai/
│   ├── orchestrator.py     # LangGraph StateGraph with nodes and routing
│   ├── context_loader.py   # Loads prompts, skills, tool definitions (central context hub)
│   ├── agents/             # Self-contained agent modules
│   │   ├── root_agent.py   # Main orchestrator (Nemotron)
│   │   ├── calendar_agent.py  # Calendar agent + Google Calendar API
│   │   ├── sheets_agent.py    # Sheets agent + Google Sheets API
│   │   ├── drive_agent.py     # Drive agent + Google Drive API
│   │   ├── web_agent.py       # Web agent + Gemini Search + httpx
│   │   ├── maps_agent.py      # Maps agent + Routes API + Places API
│   │   ├── docs_agent.py      # Docs agent + pymupdf4llm (PDF parsing)
│   │   └── _oauth.py          # Shared OAuth2 token management
│   ├── skills/             # Context hub (prompts, skills, tool definitions) - .md files use UPPERCASE
│   │   ├── SYSTEM_PROMPT.md      # Root agent system prompt
│   │   ├── PROCESS_PAYCHECK.md   # Paycheck skill guide
│   │   ├── DRIVE_FETCH.md        # Drive search skill guide
│   │   ├── calendar_tools.json    # Calendar tool definitions
│   │   ├── sheets_tools.json      # Sheets tool definitions
│   │   ├── drive_tools.json       # Drive tool definitions
│   │   ├── web_tools.json         # Web tool definitions
│   │   ├── maps_tools.json        # Maps tool definitions
│   │   └── docs_tools.json        # Docs tool definitions
│   └── speech/             # ASR and TTS engines
└── services/
    ├── api.py              # FastAPI server
    ├── discord_bot.py      # Discord bot
    ├── voice/              # Voice channel integration
    └── google_workspace/   # DEPRECATED - only OAuth setup remains
```
