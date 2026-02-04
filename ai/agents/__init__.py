"""
Friday AI Agents - Self-contained agent implementations.

Each agent file contains both the agent logic (LLM-based tool extraction)
and the service operations (API calls), creating a fully self-contained module.

Architecture:
- root_agent: Main orchestrator
- calendar_agent: Google Calendar operations (tool-use LLM + Calendar API)
- sheets_agent: Google Sheets operations (tool-use LLM + Sheets API)
- drive_agent: Google Drive operations (tool-use LLM + Drive API)
- web_agent: Web search and URL operations (tool-use LLM + Gemini/httpx)

Shared utilities:
- _oauth: OAuth2 token management for Google Workspace agents
"""

from .root_agent import root_agent, ROOT_SYSTEM_PROMPT, ROOT_MODEL
from .calendar_agent import calendar_agent
from .sheets_agent import sheets_agent
from .drive_agent import drive_agent
from .web_agent import web_agent
from .maps_agent import maps_agent
from .docs_agent import docs_agent
from ._oauth import get_access_token

__all__ = [
    "root_agent",
    "calendar_agent",
    "sheets_agent",
    "drive_agent",
    "web_agent",
    "maps_agent",
    "docs_agent",
    "get_access_token",
    "ROOT_SYSTEM_PROMPT",
    "ROOT_MODEL",
]
