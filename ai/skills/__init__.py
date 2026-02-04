"""
Friday Skills Framework - Central Context Hub

This directory contains all context files for Friday:
- **Skills** (.md): Workflow instruction guides (e.g., paycheck_processing.md)
- **Prompts** (.md): System prompts for agents (e.g., root_system.md)
- **Tools** (.json): Tool definitions for Qwen agents (e.g., calendar_tools.json)

Skills are markdown-based instruction guides loaded dynamically by the orchestrator.

Structure:
- Each skill is a .md file with step-by-step workflow instructions
- Tool definitions follow OpenAI function calling format
- No code execution - pure guidance and definitions

Adding a new skill:
1. Create skills/my_skill.md with detailed instructions
2. Add detection pattern to ai/context_loader.detect_skill()
3. Orchestrator automatically injects when pattern matches
"""

# Import from parent ai/ directory (context_loader.py moved up)
from ai.context_loader import load_skill, detect_skill, clear_cache

__all__ = [
    'load_skill',
    'detect_skill',
    'clear_cache'
]
