"""Root agent - Main orchestrator using configurable model with native tool calling."""

import json
import re
import logging
from typing import Generator
from datetime import datetime
from ai.context_loader import load_prompt, load_tools, get_skill_tool_results, _SKILL_REGISTRY
from ai.config import get_model, get_client

logger = logging.getLogger(__name__)

# Pre-load prompt templates and routing tools at module level
ROOT_SYSTEM_PROMPT_TEMPLATE = load_prompt("SYSTEM_PROMPT")
USER_PROFILE = load_prompt("USER_PROFILE")
ROUTING_TOOLS = load_tools("routing_tools")

# Sentinel yielded by root_agent_stream when the model returns a tool call
TOOL_CALL_SENTINEL = "[TOOL_CALL]"


def _get_system_prompt() -> str:
    """Get system prompt with current datetime, user profile, and skill routing injected."""
    current_dt = datetime.now().strftime("%B %d, %Y %I:%M %p")
    return (ROOT_SYSTEM_PROMPT_TEMPLATE
            .replace("{CURRENT_DATETIME}", current_dt)
            .replace("{USER_PROFILE}", USER_PROFILE.strip())
            .replace("{SKILL_TOOL_RESULTS}", get_skill_tool_results()))


def _get_routing_tools() -> list[dict]:
    """Get routing tools + dynamically registered skill tools."""
    tools = list(ROUTING_TOOLS)
    for trigger, info in _SKILL_REGISTRY.items():
        tools.append({
            "type": "function",
            "function": {
                "name": f"skill_{info['name'].lower()}",
                "description": info.get("result_description") or f"Execute {info['name']} skill",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "request": {"type": "string", "description": "The user's request"}
                    },
                    "required": ["request"]
                }
            }
        })
    return tools


# Known routing tool names for validating text-based tool call extraction
_VALID_TOOL_NAMES = {"web_search", "open_url", "calendar", "maps", "docs"}


def _extract_json_object(text: str, start: int) -> str | None:
    """Extract a complete JSON object from text starting at a '{' character.

    Handles nested braces by counting depth. Returns the JSON substring or None.
    """
    if start >= len(text) or text[start] != '{':
        return None
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if escape:
            escape = False
            continue
        if ch == '\\' and in_string:
            escape = True
            continue
        if ch == '"' and not escape:
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None


def _extract_tool_call_from_content(content: str) -> tuple[list[dict], str] | None:
    """Extract tool calls embedded in text content when model doesn't use native tool_calls.

    Some models (e.g., Llama 3.3 on certain providers) emit tool calls as text
    instead of using the structured tool_calls field. This parses known patterns:
    1. <function(name){args}</function>  or  <function=name>{args}</function>
    2. {"type": "function", "name": "...", "parameters": {...}}  (with nested objects)
    3. {"name": "...", "parameters": {...}}  or  {"name": "...", "arguments": {...}}

    Returns (tool_calls_list, cleaned_content) or None if no tool call found.
    """
    # Collect all valid tool names including skill_ prefixes
    valid_names = set(_VALID_TOOL_NAMES)
    for trigger, info in _SKILL_REGISTRY.items():
        valid_names.add(f"skill_{info['name'].lower()}")

    # Pattern 1: <function(name){args}</function> or <function=name>{args}</function>
    fn_match = re.search(r'<function[=(](\w+)[)>]\s*(\{.*?\})\s*</function>', content, re.DOTALL)
    if fn_match:
        name = fn_match.group(1)
        if name in valid_names:
            try:
                args = json.loads(fn_match.group(2))
                cleaned = content[:fn_match.start()].strip()
                logger.info(f"[FALLBACK] Extracted tool call from <function> tag: {name}")
                return [{"name": name, "arguments": args}], cleaned
            except json.JSONDecodeError:
                pass

    # Pattern 2/3: JSON object with "name" field — use brace-counting for nested objects
    for match in re.finditer(r'\{', content):
        json_str = _extract_json_object(content, match.start())
        if not json_str:
            continue
        try:
            obj = json.loads(json_str)
            name = obj.get("name", "")
            if name in valid_names:
                args = obj.get("parameters") or obj.get("arguments") or {}
                if isinstance(args, str):
                    args = {"request": args}
                cleaned = content[:match.start()].strip()
                logger.info(f"[FALLBACK] Extracted tool call from JSON in content: {name}")
                return [{"name": name, "arguments": args}], cleaned
        except (json.JSONDecodeError, AttributeError):
            continue

    return None


def root_agent(messages: list[dict], use_tools: bool = True) -> dict:
    """Main orchestrator agent with native tool calling.

    Args:
        messages: Conversation history.
        use_tools: If True, include routing tools. Set False for reflection
                   (formatting tool results) to prevent re-triggering tools.

    Returns:
        Dict with 'role', 'content', and optionally 'tool_calls' list.
        Each tool_call is {'name': str, 'arguments': dict}.
    """
    try:
        model = get_model("root")
        logger.info(f"Root agent invoked with {len(messages)} messages (tools={'on' if use_tools else 'off'})")
        client = get_client()

        full_messages = [{
            "role": "system",
            "content": _get_system_prompt()
        }] + messages

        kwargs = {"model": model, "messages": full_messages}
        if use_tools:
            routing_tools = _get_routing_tools()
            kwargs["tools"] = routing_tools
            kwargs["tool_choice"] = "auto"
            logger.info(f"Calling OpenRouter API with {len(routing_tools)} tools")
        else:
            logger.info("Calling OpenRouter API (reflection, no tools)")

        response = client.chat.completions.create(**kwargs)

        msg = response.choices[0].message
        result = {"role": "assistant", "content": msg.content or ""}

        if msg.tool_calls:
            result["tool_calls"] = []
            for tc in msg.tool_calls:
                try:
                    args = json.loads(tc.function.arguments) if tc.function.arguments else {}
                except json.JSONDecodeError:
                    args = {"request": tc.function.arguments}
                result["tool_calls"].append({
                    "name": tc.function.name,
                    "arguments": args,
                })
            logger.info(f"Root agent returned tool call: {result['tool_calls'][0]['name']}")
        elif use_tools and result["content"]:
            # Fallback: some models embed tool calls in content instead of using native tool_calls
            extracted = _extract_tool_call_from_content(result["content"])
            if extracted:
                tool_calls, cleaned_content = extracted
                result["tool_calls"] = tool_calls
                result["content"] = cleaned_content
                logger.info(f"Root agent tool call extracted from content: {tool_calls[0]['name']}")
            else:
                logger.info(f"Root agent response (text): {len(result['content'])} chars")
        else:
            logger.info(f"Root agent response (text): {len(result['content'])} chars")

        return result

    except Exception as e:
        logger.error(f"Root agent error: {e}")
        raise


def root_agent_stream(messages: list[dict]) -> Generator[str, None, None]:
    """Streaming root agent. Yields token strings as they arrive.

    Used by the voice pipeline to stream LLM output for sentence-by-sentence
    TTS synthesis, reducing time-to-first-audio.

    If the model returns a tool call instead of content, yields TOOL_CALL_SENTINEL
    as the first (and only) token so the voice pipeline can fall back to batch mode.
    """
    try:
        model = get_model("root")
        client = get_client()
        full_messages = [{"role": "system", "content": _get_system_prompt()}] + messages

        logger.info("Starting streaming root agent call")
        response = client.chat.completions.create(
            model=model,
            messages=full_messages,
            tools=_get_routing_tools(),
            tool_choice="auto",
            stream=True,
        )

        tool_call_detected = False
        for chunk in response:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta

            # Check for tool call in streaming response
            if delta.tool_calls:
                if not tool_call_detected:
                    tool_call_detected = True
                    yield TOOL_CALL_SENTINEL
                continue

            if delta.content:
                yield delta.content

    except Exception as e:
        logger.error(f"Root agent stream error: {e}")
        raise
