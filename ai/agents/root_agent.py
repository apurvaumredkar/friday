"""Root agent - Main orchestrator using configurable model."""

import logging
from typing import Generator
from datetime import datetime
from ai.context_loader import load_prompt, get_skill_routing, get_skill_tool_results
from ai.config import get_model, get_client

logger = logging.getLogger(__name__)

# Pre-load prompt templates at module level
ROOT_SYSTEM_PROMPT_TEMPLATE = load_prompt("SYSTEM_PROMPT")
USER_PROFILE = load_prompt("USER_PROFILE")


def _get_system_prompt() -> str:
    """Get system prompt with current datetime, user profile, and skill routing injected."""
    current_dt = datetime.now().strftime("%B %d, %Y %I:%M %p")
    return (ROOT_SYSTEM_PROMPT_TEMPLATE
            .replace("{CURRENT_DATETIME}", current_dt)
            .replace("{USER_PROFILE}", USER_PROFILE.strip())
            .replace("{SKILL_ROUTING}", get_skill_routing())
            .replace("{SKILL_TOOL_RESULTS}", get_skill_tool_results()))


def root_agent(messages: list[dict]) -> str:
    """Main orchestrator agent using configurable model from config.json."""
    try:
        model = get_model("root")
        logger.info(f"Root agent invoked with {len(messages)} messages")
        client = get_client()

        full_messages = [{
            "role": "system",
            "content": _get_system_prompt()
        }] + messages

        logger.info("Calling OpenRouter API")
        response = client.chat.completions.create(
            model=model,
            messages=full_messages
        )

        content = response.choices[0].message.content
        logger.info(f"Root agent response received, length: {len(content)} chars")
        return content

    except Exception as e:
        logger.error(f"Root agent error: {e}")
        raise


def root_agent_stream(messages: list[dict]) -> Generator[str, None, None]:
    """Streaming root agent. Yields token strings as they arrive.

    Used by the voice pipeline to stream LLM output for sentence-by-sentence
    TTS synthesis, reducing time-to-first-audio.
    """
    try:
        model = get_model("root")
        client = get_client()
        full_messages = [{"role": "system", "content": _get_system_prompt()}] + messages

        logger.info("Starting streaming root agent call")
        response = client.chat.completions.create(
            model=model,
            messages=full_messages,
            stream=True,
        )
        for chunk in response:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content

    except Exception as e:
        logger.error(f"Root agent stream error: {e}")
        raise
