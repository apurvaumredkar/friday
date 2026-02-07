from ai.context_loader import load_prompt, load_tools

# Pre-load prompts at module initialization
ROOT_SYSTEM_PROMPT = load_prompt("SYSTEM_PROMPT")

from ai.orchestrator import Friday
from ai.agents import root_agent, web_agent, calendar_agent, sheets_agent, drive_agent, maps_agent, docs_agent

_friday_instance = None


def get_friday() -> Friday:
    """Get shared Friday singleton — avoids duplicate LangGraph compilations."""
    global _friday_instance
    if _friday_instance is None:
        _friday_instance = Friday()
    return _friday_instance


__all__ = [
    "Friday",
    "get_friday",
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
