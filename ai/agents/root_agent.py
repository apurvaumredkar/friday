"""Root agent - Main orchestrator using configurable model."""

import os
import logging
from datetime import datetime
from dotenv import load_dotenv
from openai import OpenAI
from ai.context_loader import load_prompt
from ai.config import get_model, get_base_url, get_api_key

load_dotenv()

logger = logging.getLogger(__name__)

# Pre-load prompt template at module level
ROOT_SYSTEM_PROMPT_TEMPLATE = load_prompt("SYSTEM_PROMPT")


def _get_system_prompt() -> str:
    """Get system prompt with current datetime injected."""
    current_dt = datetime.now().strftime("%B %d, %Y %I:%M %p")
    return ROOT_SYSTEM_PROMPT_TEMPLATE.replace("{CURRENT_DATETIME}", current_dt)


def root_agent(messages: list[dict]) -> str:
    """Main orchestrator agent using configurable model from config.json."""
    try:
        model = get_model("root")
        logger.info(f"Root agent invoked with {len(messages)} messages")
        client = OpenAI(base_url=get_base_url(), api_key=get_api_key())
        logger.info(f"OpenAI client initialized for model: {model}")

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
