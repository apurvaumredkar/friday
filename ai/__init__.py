from ai.context_loader import load_prompt

# Pre-load prompts at module initialization
ROOT_SYSTEM_PROMPT = load_prompt("SYSTEM_PROMPT")

from ai.orchestrator import Friday
from ai.agents import root_agent, web_agent, calendar_agent, sheets_agent, drive_agent, maps_agent, docs_agent

__all__ = [
    "Friday",
    "root_agent",
    "web_agent",
    "calendar_agent",
    "sheets_agent",
    "drive_agent",
    "maps_agent",
    "docs_agent",
    "load_prompt",
    "ROOT_SYSTEM_PROMPT",
]
