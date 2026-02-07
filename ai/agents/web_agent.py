"""
Web agent - Specialized agent for web search and URL content extraction.

Implements Plan-Act pattern for function call extraction.
Merges web service operations directly into agent for unified web operations.
"""

import os
import json
import logging
import webbrowser
from ai.config import get_model, get_client
from ai.context_loader import load_tools

logger = logging.getLogger(__name__)


def search_web(query: str) -> str:
    """
    Search the web using Gemini 2.0 Flash with Google Search grounding.

    Args:
        query: Search query string

    Returns:
        Search results as formatted text

    Raises:
        Exception: If Gemini API call fails
    """
    try:
        logger.info(f"[WEB] Searching web: {query}")

        from google import genai
        from google.genai import types
        client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

        # Enable Google Search tool
        search_tool = types.Tool(google_search=types.GoogleSearch())

        # Call Gemini with search grounding (no system instruction needed)
        response = client.models.generate_content(
            model="gemini-2.0-flash",
            contents=query,
            config=types.GenerateContentConfig(tools=[search_tool])
        )

        result = response.text
        logger.info(f"[WEB] Search complete, result length: {len(result)} chars")

        return result

    except Exception as e:
        logger.error(f"[WEB] Search error: {e}", exc_info=True)
        raise


def open_in_browser(url: str) -> str:
    """
    Open a URL in the user's default web browser.

    Args:
        url: Complete HTTP/HTTPS URL to open

    Returns:
        Success message confirming the URL was opened
    """
    import subprocess
    import platform
    from pathlib import Path
    from urllib.parse import urlparse

    try:
        # Validate URL scheme to prevent command injection
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return f"Error: Invalid URL scheme '{parsed.scheme}' — only http/https allowed"

        logger.info(f"[WEB] Opening in browser: {url}")

        # Check if running in WSL
        is_wsl = "microsoft" in platform.uname().release.lower()

        if is_wsl:
            # Use full path — systemd services don't have WSL interop on PATH
            powershell = Path("/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe")
            if not powershell.exists():
                logger.error("[WEB] powershell.exe not found at expected path")
                return f"Error: powershell.exe not found — cannot open browser on WSL"
            result = subprocess.run(
                [str(powershell), "Start-Process", url],
                capture_output=True, timeout=10,
            )
            if result.returncode != 0:
                stderr = result.stderr.decode(errors="replace").strip()
                logger.error(f"[WEB] PowerShell failed (rc={result.returncode}): {stderr}")
                return f"Error: Failed to open browser: {stderr}"
        else:
            webbrowser.open(url)

        return f"Opened {url} in browser"
    except Exception as e:
        logger.error(f"[WEB] Browser open error: {e}", exc_info=True)
        return f"Error: Failed to open browser: {str(e)}"


def web_agent(user_message: str) -> str:
    """
    Specialized web agent using configurable tool model.

    Implements Plan-Act pattern (Reflect happens in root agent):
    1. Plan: Tool model extracts function call from user's natural language request
    2. Act: Execute the web service function and return raw result
    3. (Reflect: Root agent with full history formats the response)

    Uses minimal context (system prompt + tool definitions, no conversation history).

    Args:
        user_message: User's natural language web request
            Examples:
                "Search for latest AI news"
                "Open https://example.com and summarize"

    Returns:
        Raw tool execution result string to be passed back to root agent
    """
    try:
        logger.info(f"[WEB] Web agent invoked: {user_message[:50]}...")

        # Load tool definitions (cached)
        web_tools = load_tools("web_tools")

        # Build system prompt
        system_prompt = """You are a web operations specialist for Friday AI assistant.

Your ONLY task is to translate user's natural language requests into precise web function calls.

RULES:
1. Use search_web for queries about current events, facts, or general questions
2. Use open_url for specific URLs that user wants to read or analyze
3. Extract clean queries (remove "search for", "look up", etc.)
4. URLs must include http:// or https://
5. Call exactly ONE function per request

OUTPUT FORMAT:
You MUST respond with ONLY a JSON object in this exact format:
{
  "name": "function_name",
  "arguments": {
    "param": "value"
  }
}

Do not include any explanatory text, markdown, or additional content. Only output the JSON object."""

        # Format tools as plain text in system prompt
        tools_text = "\n\nAvailable functions:\n" + json.dumps(web_tools, indent=2)
        system_prompt += tools_text

        # Call tool model via OpenRouter (minimal context!)
        client = get_client()

        response = client.chat.completions.create(
            model=get_model("tool"),
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message}
            ],
            temperature=0.3,
            max_tokens=300,
            extra_body={"thinking": {"type": "disabled"}}
        )

        assistant_response = response.choices[0].message.content
        logger.info(f"[WEB] Tool model response: {assistant_response[:100]}...")

        # Parse function call from response
        try:
            if "{" in assistant_response and "}" in assistant_response:
                start_idx = assistant_response.find("{")
                end_idx = assistant_response.rfind("}") + 1
                function_call_json = assistant_response[start_idx:end_idx]
                function_call = json.loads(function_call_json)

                function_name = function_call.get("name")
                function_args = function_call.get("arguments", {})

                logger.info(f"[WEB] Extracted function call: {function_name}({function_args})")
            else:
                # No function call, return text response
                return assistant_response
        except json.JSONDecodeError:
            # If we can't parse JSON, return the text response
            return assistant_response

        # Execute the appropriate web function and get raw result
        tool_result = None

        if function_name == "search_web":
            query = function_args["query"]
            logger.info(f"[WEB] Searching: {query}")

            result = search_web(query)
            tool_result = f"Search results for '{query}':\n\n{result}"

        elif function_name == "open_in_browser":
            url = function_args["url"]
            logger.info(f"[WEB] Opening in browser: {url}")

            result = open_in_browser(url)
            tool_result = result

        else:
            return f"Unknown function: {function_name}"

        # Return raw tool result
        logger.info(f"[WEB] Tool executed. Result: {tool_result[:100]}...")
        return tool_result

    except Exception as e:
        logger.error(f"[WEB] Web agent error: {e}", exc_info=True)
        return f"I encountered an error with that web operation: {str(e)}"
