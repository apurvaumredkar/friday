"""
Context loader for Friday AI assistant.

Loads all context files (skills, prompts, tool definitions) from ai/skills/
This is the central context hub for Friday containing:
- Skill guides (.md files with workflow instructions)
- System prompts (.md files loaded into agents)
- Tool definitions (.json files for Qwen tool-use agents)
"""

import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

SKILLS_DIR = Path(__file__).parent / "skills"


def load_prompt(name: str) -> str:
    """
    Load prompt from markdown file.

    Args:
        name: Prompt filename without extension (e.g., "SYSTEM_PROMPT")

    Returns:
        Prompt content as string

    Raises:
        FileNotFoundError: If prompt file doesn't exist
    """
    return (SKILLS_DIR / f"{name}.md").read_text()

# Cache loaded skills to avoid repeated file I/O
_SKILL_CACHE: dict[str, str] = {}


def load_skill(skill_name: str) -> Optional[str]:
    """
    Load skill markdown content.

    Args:
        skill_name: Name of skill file without .md extension
                   (e.g., "paycheck_processing")

    Returns:
        Skill markdown content or None if not found
    """
    # Check cache first
    if skill_name in _SKILL_CACHE:
        logger.debug(f"Loaded skill '{skill_name}' from cache")
        return _SKILL_CACHE[skill_name]

    # Load from file (now in skills/ subdirectory)
    skill_file = Path(__file__).parent / "skills" / f"{skill_name}.md"

    if not skill_file.exists():
        logger.warning(f"Skill file not found: {skill_file}")
        return None

    try:
        content = skill_file.read_text()
        _SKILL_CACHE[skill_name] = content
        logger.info(f"Loaded skill: {skill_name} ({len(content)} chars)")
        return content
    except Exception as e:
        logger.error(f"Failed to load skill '{skill_name}': {e}")
        return None


def detect_skill(message: str, state: dict) -> Optional[str]:
    """
    Detect which skill should be loaded based on message content and state.

    Args:
        message: Last message content from root agent
        state: Current agent state

    Returns:
        Skill name to load or None
    """
    # Paycheck processing detection
    if "PAYCHECK_PROCESSING:" in message:
        if state.get("pdf_bytes") and state.get("pdf_filename"):
            return "PROCESS_PAYCHECK"

    # Drive fetch detection
    if "DRIVE_FETCH:" in message:
        return "DRIVE_FETCH"

    return None


def clear_cache():
    """Clear the skill cache. Useful for testing or reloading skills."""
    global _SKILL_CACHE
    _SKILL_CACHE.clear()
    logger.info("Cleared skill cache")
