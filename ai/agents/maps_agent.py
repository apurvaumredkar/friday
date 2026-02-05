"""
Maps agent - Places API for place info, browser-based directions display.

Implements Plan-Act pattern for function call extraction.
Uses Places API for location details, opens Google Maps in browser for directions.
"""

import os
import json
import logging
import httpx
from pathlib import Path
from urllib.parse import quote_plus
from dotenv import load_dotenv
from openai import OpenAI
from ai.agents.web_agent import open_in_browser
from ai.config import get_model, get_base_url, get_api_key

load_dotenv()

logger = logging.getLogger(__name__)

# API endpoints (Places API only - directions use browser)
PLACES_TEXT_SEARCH = "https://places.googleapis.com/v1/places:searchText"
PLACES_DETAILS = "https://places.googleapis.com/v1/places"


def _build_maps_directions_url(origin: str, destination: str, mode: str = "transit") -> str:
    """
    Build a Google Maps directions URL using the official Maps URLs API.
    Docs: https://developers.google.com/maps/documentation/urls/get-started
    """
    origin_encoded = quote_plus(origin)
    destination_encoded = quote_plus(destination)

    # Official travelmode values: driving, walking, bicycling, transit
    mode_map = {
        "DRIVE": "driving",
        "WALK": "walking",
        "BICYCLE": "bicycling",
        "TRANSIT": "transit",
        # Also accept lowercase
        "driving": "driving",
        "walking": "walking",
        "bicycling": "bicycling",
        "transit": "transit",
    }
    travel_mode = mode_map.get(mode.upper() if mode else "TRANSIT", "transit")

    return f"https://www.google.com/maps/dir/?api=1&origin={origin_encoded}&destination={destination_encoded}&travelmode={travel_mode}"


def _get_headers(field_mask: str) -> dict:
    """Get standard headers for Google Maps Platform APIs."""
    return {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": os.getenv("GOOGLE_MAPS_API_KEY"),
        "X-Goog-FieldMask": field_mask
    }


# ============================================================================
# Places API Functions
# ============================================================================

def get_place_info(query: str) -> str:
    """
    Get detailed information about a specific place using Places API (New).

    Args:
        query: Place name or description to search for

    Returns:
        Formatted string with place details (address, rating, hours, etc.)
    """
    try:
        logger.info(f"[MAPS] Getting place info: {query}")

        # First, search for the place
        search_headers = _get_headers(
            "places.id,places.displayName,places.formattedAddress,"
            "places.rating,places.userRatingCount,places.types"
        )

        search_body = {
            "textQuery": query,
            "pageSize": 1,
            "languageCode": "en"
        }

        response = httpx.post(
            PLACES_TEXT_SEARCH,
            headers=search_headers,
            json=search_body,
            timeout=15.0
        )
        response.raise_for_status()
        search_result = response.json()

        places = search_result.get("places", [])
        if not places:
            return f"No place found matching '{query}'."

        place = places[0]
        place_id = place.get("id")

        # Get detailed info for the place
        detail_headers = _get_headers(
            "id,displayName,formattedAddress,rating,userRatingCount,"
            "currentOpeningHours,nationalPhoneNumber,internationalPhoneNumber,"
            "websiteUri,googleMapsUri,priceLevel,types"
        )

        detail_response = httpx.get(
            f"{PLACES_DETAILS}/{place_id}",
            headers=detail_headers,
            timeout=15.0
        )
        detail_response.raise_for_status()
        details = detail_response.json()

        # Format the response
        name = details.get("displayName", {}).get("text", "Unknown")
        address = details.get("formattedAddress", "N/A")
        rating = details.get("rating")
        rating_count = details.get("userRatingCount", 0)
        phone = details.get("nationalPhoneNumber") or details.get("internationalPhoneNumber", "N/A")
        website = details.get("websiteUri", "N/A")
        maps_url = details.get("googleMapsUri", "N/A")
        price = details.get("priceLevel", "").replace("PRICE_LEVEL_", "").replace("_", " ").title()

        # Opening hours
        hours_info = details.get("currentOpeningHours", {})
        open_now = hours_info.get("openNow")
        open_status = "Open now" if open_now else "Closed" if open_now is False else "Hours unknown"

        weekday_hours = hours_info.get("weekdayDescriptions", [])
        hours_text = "\n  ".join(weekday_hours[:3]) if weekday_hours else "Not available"

        # Build result
        rating_str = f"{rating} ({rating_count} reviews)" if rating else "No ratings"

        result = f"""Place: {name}
Address: {address}
Rating: {rating_str}
Status: {open_status}
Phone: {phone}
Website: {website}
Google Maps: {maps_url}"""

        if price:
            result += f"\nPrice Level: {price}"

        if weekday_hours:
            result += f"\nHours:\n  {hours_text}"

        logger.info(f"[MAPS] Place info retrieved: {name}")
        return result

    except httpx.HTTPStatusError as e:
        logger.error(f"[MAPS] API error: {e.response.status_code} - {e.response.text}")
        return f"Error fetching place info: {e.response.status_code}"
    except Exception as e:
        logger.error(f"[MAPS] Place info error: {e}", exc_info=True)
        return f"Error getting place info: {str(e)}"


# ============================================================================
# Browser-Based Directions Functions
# ============================================================================

def get_directions(origin: str, destination: str, mode: str = "DRIVE") -> str:
    """
    Open Google Maps directions in browser.

    Args:
        origin: Starting point (address or place name)
        destination: End point (address or place name)
        mode: Travel mode - DRIVE, WALK, BICYCLE, or TRANSIT

    Returns:
        Message indicating browser was opened (prefixed with [BROWSER] for orchestrator)
    """
    try:
        logger.info(f"[MAPS] Opening directions in browser: {origin} -> {destination} ({mode})")

        # Build the Maps URL
        url = _build_maps_directions_url(origin, destination, mode)

        # Open in browser
        result = open_in_browser(url)

        # Mode display
        mode_display = {
            "DRIVE": "driving",
            "WALK": "walking",
            "BICYCLE": "cycling",
            "TRANSIT": "transit"
        }.get(mode.upper() if mode else "DRIVE", "driving")

        logger.info(f"[MAPS] Browser opened: {url}")

        # Return with [BROWSER] marker for orchestrator to detect (skip reflection)
        return f"[BROWSER] Opened Google Maps with {mode_display} directions from {origin} to {destination}."

    except Exception as e:
        logger.error(f"[MAPS] Directions error: {e}", exc_info=True)
        return f"Error opening directions: {str(e)}"


def get_transit_info(origin: str, destination: str) -> str:
    """
    Open Google Maps transit directions in browser.

    Args:
        origin: Starting point (address or place name)
        destination: End point (address or place name)

    Returns:
        Message indicating browser was opened (prefixed with [BROWSER] for orchestrator)
    """
    try:
        logger.info(f"[MAPS] Opening transit directions in browser: {origin} -> {destination}")

        # Build the Maps URL with transit mode
        url = _build_maps_directions_url(origin, destination, "transit")

        # Open in browser
        result = open_in_browser(url)

        logger.info(f"[MAPS] Browser opened: {url}")

        # Return with [BROWSER] marker for orchestrator to detect (skip reflection)
        return f"[BROWSER] Opened Google Maps with transit directions from {origin} to {destination}."

    except Exception as e:
        logger.error(f"[MAPS] Transit error: {e}", exc_info=True)
        return f"Error opening transit directions: {str(e)}"


# ============================================================================
# Maps Agent - Tool-use specialist
# ============================================================================

def maps_agent(user_message: str) -> str:
    """
    Specialized maps agent using Qwen for tool extraction.

    Implements Plan-Act pattern (Reflect happens in root agent):
    1. Plan: Qwen extracts function call from user's natural language request
    2. Act: Execute the maps service function and return raw result
    3. (Reflect: Root agent with full history formats the response)

    Uses minimal context (system prompt + tool definitions, no conversation history).

    Args:
        user_message: User's natural language maps/location request

    Returns:
        Raw tool execution result string to be passed back to root agent
    """
    try:
        logger.info(f"[MAPS] Maps agent invoked: {user_message[:50]}...")

        # Load tool definitions
        tools_file = Path(__file__).parent.parent / "skills" / "maps_tools.json"
        maps_tools = json.loads(tools_file.read_text())

        # Build system prompt
        system_prompt = """You are a maps and navigation specialist for Friday AI assistant.

Your ONLY task is to translate user's natural language requests into precise function calls.

RULES:
1. Use get_place_info for questions about a specific place (hours, rating, address, phone)
2. Use get_transit_info for public transit / subway / bus directions
3. Use get_directions for driving, walking, or cycling directions
4. For get_directions, mode defaults to DRIVE. Use WALK or BICYCLE if user specifies
5. Extract clean location names from the request
6. Call exactly ONE function per request

OUTPUT FORMAT:
You MUST respond with ONLY a JSON object in this exact format:
{
  "name": "function_name",
  "arguments": {
    "param": "value"
  }
}

Do not include any explanatory text, markdown, or additional content. Only output the JSON object."""

        # Add tools to system prompt
        tools_text = "\n\nAvailable functions:\n" + json.dumps(maps_tools, indent=2)
        system_prompt += tools_text

        # Call tool model via OpenRouter (minimal context!)
        client = OpenAI(
            base_url=get_base_url(),
            api_key=get_api_key()
        )

        response = client.chat.completions.create(
            model=get_model("tool"),
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message}
            ],
            temperature=0.3,
            max_tokens=300
        )

        assistant_response = response.choices[0].message.content
        logger.info(f"[MAPS] Qwen response: {assistant_response[:100]}...")

        # Parse function call from response
        try:
            if "{" in assistant_response and "}" in assistant_response:
                start_idx = assistant_response.find("{")
                end_idx = assistant_response.rfind("}") + 1
                function_call_json = assistant_response[start_idx:end_idx]
                function_call = json.loads(function_call_json)

                function_name = function_call.get("name")
                function_args = function_call.get("arguments", {})

                logger.info(f"[MAPS] Extracted function call: {function_name}({function_args})")
            else:
                return assistant_response
        except json.JSONDecodeError:
            return assistant_response

        # Execute the appropriate maps function
        tool_result = None

        if function_name == "get_place_info":
            query = function_args.get("query", "")
            tool_result = get_place_info(query)

        elif function_name == "get_directions":
            origin = function_args.get("origin", "")
            destination = function_args.get("destination", "")
            mode = function_args.get("mode", "DRIVE")
            tool_result = get_directions(origin, destination, mode)

        elif function_name == "get_transit_info":
            origin = function_args.get("origin", "")
            destination = function_args.get("destination", "")
            tool_result = get_transit_info(origin, destination)

        else:
            return f"Unknown function: {function_name}"

        logger.info(f"[MAPS] Tool executed. Result length: {len(tool_result)} chars")
        return tool_result

    except Exception as e:
        logger.error(f"[MAPS] Maps agent error: {e}", exc_info=True)
        return f"I encountered an error with that maps operation: {str(e)}"
