"""
Friday Context Hub

Directory structure:
- SYSTEM_PROMPT.md, USER_PROFILE.md — Agent prompts
- skills/*.md — Auto-discovered skill definitions (frontmatter + routing + workflow)
- tools/*.json — Tool definitions for agent function extraction

Adding a new skill:
1. Create context/skills/MY_SKILL.md with frontmatter and ## Routing section
2. (Optional) Register a Python handler in orchestrator.py for API-dependent skills
"""

from ai.context_loader import (
    load_skill,
    detect_skill,
    clear_cache,
    discover_skills,
    get_skill_routing,
    get_skill_tool_results,
    get_skill_metadata,
)

__all__ = [
    "load_skill",
    "detect_skill",
    "clear_cache",
    "discover_skills",
    "get_skill_routing",
    "get_skill_tool_results",
    "get_skill_metadata",
]
