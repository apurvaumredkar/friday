"""Root agent - Main orchestrator using Nemotron 3 Nano 30B."""

import os
import logging
from dotenv import load_dotenv
from openai import OpenAI
from ai.context_loader import load_prompt

load_dotenv()

logger = logging.getLogger(__name__)

# Pre-load skills at module level
ROOT_SYSTEM_PROMPT = load_prompt("SYSTEM_PROMPT")

# Model Configuration
ROOT_MODEL = "nvidia/nemotron-3-nano-30b-a3b:free"

## Root Agent - Main orchestrator, using Nemotron Nano 3 30B A3B, for it's agentic capabilities
def root_agent(messages: list[dict]) -> str:
    try:
        logger.info(f"Root agent invoked with {len(messages)} messages")
        client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=os.getenv("OPENROUTER_API_KEY"))
        logger.info(f"OpenAI client initialized for model: {ROOT_MODEL}")

        full_messages = [{
            "role": "system",
            "content": ROOT_SYSTEM_PROMPT
        }] + messages

        logger.info("Calling OpenRouter API")
        response = client.chat.completions.create(
            model=ROOT_MODEL,
            messages=full_messages
        )

        content = response.choices[0].message.content
        logger.info(f"Root agent response received, length: {len(content)} chars")
        return content

    except Exception as e:
        logger.error(f"Root agent error: {e}")
        raise
