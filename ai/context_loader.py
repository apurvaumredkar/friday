"""
Context loader for Friday AI assistant.

Loads context files from ai/context/:
- Prompts (.md) from ai/context/
- Skills (.md with frontmatter) from ai/context/skills/ — auto-discovered
- Tool definitions (.json) from ai/context/tools/
"""

import json
import logging
import re
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

CONTEXT_DIR = Path(__file__).parent / "context"
SKILLS_DIR = CONTEXT_DIR / "skills"
TOOLS_DIR = CONTEXT_DIR / "tools"


def load_prompt(name: str) -> str:
    """
    Load prompt from markdown file in ai/context/.

    Args:
        name: Prompt filename without extension (e.g., "SYSTEM_PROMPT")

    Returns:
        Prompt content as string

    Raises:
        FileNotFoundError: If prompt file doesn't exist
    """
    return (CONTEXT_DIR / f"{name}.md").read_text()


# Cache loaded skills to avoid repeated file I/O
_SKILL_CACHE: dict[str, str] = {}

# Cache loaded tool definitions (JSON) to avoid repeated file I/O
_TOOLS_CACHE: dict[str, list] = {}


def load_skill(skill_name: str) -> Optional[str]:
    """
    Load skill markdown content from ai/context/skills/.

    Args:
        skill_name: Name of skill file without .md extension

    Returns:
        Skill markdown content or None if not found
    """
    if skill_name in _SKILL_CACHE:
        logger.debug(f"Loaded skill '{skill_name}' from cache")
        return _SKILL_CACHE[skill_name]

    skill_file = SKILLS_DIR / f"{skill_name}.md"

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


def load_tools(tool_name: str) -> list:
    """
    Load tool definitions JSON from ai/context/tools/, with caching.

    Args:
        tool_name: Name of tools file without .json extension

    Returns:
        Parsed JSON list of tool definitions
    """
    if tool_name in _TOOLS_CACHE:
        return _TOOLS_CACHE[tool_name]

    tools_file = TOOLS_DIR / f"{tool_name}.json"
    tools = json.loads(tools_file.read_text())
    _TOOLS_CACHE[tool_name] = tools
    logger.info(f"Loaded tools: {tool_name} ({len(tools)} tools)")
    return tools


# ============================================================================
# Skill Registry — auto-discovery from ai/context/skills/*.md
# ============================================================================

# trigger_prefix -> {name, result_label, result_description, handler, routing}
_SKILL_REGISTRY: dict[str, dict] = {}


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """
    Parse --- delimited frontmatter from skill markdown.

    Returns:
        (metadata_dict, remaining_content after frontmatter)
    """
    # Must start with ---
    if not text.startswith("---"):
        return {}, text

    # Find the closing ---
    end_idx = text.index("---", 3)
    frontmatter_block = text[3:end_idx].strip()
    remaining = text[end_idx + 3:].strip()

    # Parse key: value lines
    metadata = {}
    for line in frontmatter_block.splitlines():
        line = line.strip()
        if ":" in line:
            key, value = line.split(":", 1)
            metadata[key.strip()] = value.strip()

    return metadata, remaining


def _extract_routing(content: str) -> str:
    """
    Extract ## Routing section content from skill markdown.

    Returns content between '## Routing' header and the next '---' delimiter
    or '##' header (whichever comes first).
    """
    match = re.search(r"^## Routing\s*\n", content, re.MULTILINE)
    if not match:
        return ""

    start = match.end()
    remaining = content[start:]

    # Find the end: next --- delimiter or next ## header
    end_match = re.search(r"^(---|## )", remaining, re.MULTILINE)
    if end_match:
        return remaining[:end_match.start()].strip()

    return remaining.strip()


def discover_skills():
    """
    Scan ai/context/skills/ for .md files with frontmatter, build registry.

    Only files starting with '---' (frontmatter) are treated as skills.
    Called at module load time.
    """
    _SKILL_REGISTRY.clear()

    if not SKILLS_DIR.exists():
        logger.warning(f"Skills directory not found: {SKILLS_DIR}")
        return

    for md_file in sorted(SKILLS_DIR.glob("*.md")):
        try:
            text = md_file.read_text()
            if not text.startswith("---"):
                continue

            metadata, content = _parse_frontmatter(text)
            trigger = metadata.get("trigger")
            if not trigger:
                logger.warning(f"Skill {md_file.name} has no 'trigger' in frontmatter, skipping")
                continue

            routing = _extract_routing(content)

            _SKILL_REGISTRY[trigger] = {
                "name": md_file.stem,
                "result_label": metadata.get("result_label", md_file.stem),
                "result_description": metadata.get("result_description", ""),
                "handler": metadata.get("handler"),
                "routing": routing,
            }
            logger.info(f"Registered skill: {md_file.stem} (trigger={trigger})")

        except Exception as e:
            logger.error(f"Failed to parse skill {md_file.name}: {e}")

    logger.info(f"Discovered {len(_SKILL_REGISTRY)} skill(s)")


def detect_skill(message: str, state: dict) -> Optional[str]:
    """
    Detect which skill should be activated based on message content.

    Checks the message for registered trigger prefixes from auto-discovered skills.

    Args:
        message: Last message content from root agent
        state: Current agent state (unused, kept for compatibility)

    Returns:
        Skill name (filename stem) or None
    """
    for trigger, info in _SKILL_REGISTRY.items():
        if f"{trigger}:" in message:
            return info["name"]
    return None


def get_skill_routing() -> str:
    """
    Build combined routing text from all discovered skills.

    Returns concatenated ## Routing sections for injection into the system prompt.
    """
    sections = []
    for info in _SKILL_REGISTRY.values():
        if info["routing"]:
            sections.append(info["routing"].strip())
    return "\n\n".join(sections)


def get_skill_tool_results() -> str:
    """
    Build TOOL RESULTS entries from all discovered skills.

    Returns formatted lines for the TOOL RESULTS section of the system prompt.
    """
    entries = []
    for info in _SKILL_REGISTRY.values():
        label = info["result_label"]
        desc = info["result_description"]
        if desc:
            entries.append(f'- "[TOOL RESULT - {label}]": {desc}')
        else:
            entries.append(f'- "[TOOL RESULT - {label}]"')
    return "\n".join(entries)


def get_skill_metadata(skill_name: str) -> Optional[dict]:
    """
    Get registry metadata for a skill by its name (filename stem).

    Returns:
        Dict with trigger, result_label, handler, etc. or None
    """
    for trigger, info in _SKILL_REGISTRY.items():
        if info["name"] == skill_name:
            return {"trigger": trigger, **info}
    return None


def clear_cache():
    """Clear skill and tool caches. Useful for testing or reloading."""
    _SKILL_CACHE.clear()
    _TOOLS_CACHE.clear()
    _SKILL_REGISTRY.clear()
    discover_skills()
    logger.info("Cleared caches and re-discovered skills")


# Auto-discover skills at module load
discover_skills()
