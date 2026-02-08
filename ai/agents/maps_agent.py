"""
Maps agent - Places API for place info, Directions API for transit routes.

Implements Plan-Act pattern for function call extraction.
Uses Places API for location details, Directions API for real-time transit,
and opens Google Maps in browser only for driving/walking/cycling.
"""

import os
import json
import logging
import httpx
from urllib.parse import quote_plus
from ai.agents.web_agent import open_in_browser
from ._oauth import get_http_client
from ai.config import get_model, get_client
from ai.context_loader import load_tools

logger = logging.getLogger(__name__)

# API endpoints
PLACES_TEXT_SEARCH = "https://places.googleapis.com/v1/places:searchText"
PLACES_DETAILS = "https://places.googleapis.com/v1/places"


def _build_maps_directions_url(origin: str, destination: str, mode: str = "transit") -> str:
    """Build a Google Maps directions URL."""
    origin_encoded = quote_plus(origin)
    destination_encoded = quote_plus(destination)

    mode_map = {
        "DRIVE": "driving", "WALK": "walking", "BICYCLE": "bicycling", "TRANSIT": "transit",
        "driving": "driving", "walking": "walking", "bicycling": "bicycling", "transit": "transit",
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


def _load_user_addresses() -> tuple[str | None, str | None]:
    """Parse Home Address and Work Address from USER_PROFILE.md."""
    from ai.context_loader import load_prompt
    try:
        profile = load_prompt("USER_PROFILE")
    except FileNotFoundError:
        return None, None

    home_address = None
    work_address = None
    for line in profile.strip().splitlines():
        if line.startswith("Home Address:"):
            home_address = line.split(":", 1)[1].strip()
        elif line.startswith("Work Address:"):
            work_address = line.split(":", 1)[1].strip()
    return home_address, work_address


# ============================================================================
# Places API
# ============================================================================

def get_place_info(query: str) -> str:
    """Get detailed information about a specific place using Places API."""
    try:
        logger.info(f"[MAPS] Getting place info: {query}")

        search_headers = _get_headers(
            "places.id,places.displayName,places.formattedAddress,"
            "places.rating,places.userRatingCount,places.types"
        )

        response = get_http_client().post(
            PLACES_TEXT_SEARCH,
            headers=search_headers,
            json={"textQuery": query, "pageSize": 1, "languageCode": "en"},
            timeout=15.0
        )
        response.raise_for_status()

        places = response.json().get("places", [])
        if not places:
            return f"No place found matching '{query}'."

        place_id = places[0].get("id")

        detail_headers = _get_headers(
            "id,displayName,formattedAddress,rating,userRatingCount,"
            "currentOpeningHours,nationalPhoneNumber,internationalPhoneNumber,"
            "websiteUri,googleMapsUri,priceLevel,types"
        )

        details = get_http_client().get(
            f"{PLACES_DETAILS}/{place_id}",
            headers=detail_headers,
            timeout=15.0
        ).json()

        name = details.get("displayName", {}).get("text", "Unknown")
        address = details.get("formattedAddress", "N/A")
        rating = details.get("rating")
        rating_count = details.get("userRatingCount", 0)
        phone = details.get("nationalPhoneNumber") or details.get("internationalPhoneNumber", "N/A")
        website = details.get("websiteUri", "N/A")
        maps_url = details.get("googleMapsUri", "N/A")
        price = details.get("priceLevel", "").replace("PRICE_LEVEL_", "").replace("_", " ").title()

        hours_info = details.get("currentOpeningHours", {})
        open_now = hours_info.get("openNow")
        open_status = "Open now" if open_now else "Closed" if open_now is False else "Hours unknown"

        weekday_hours = hours_info.get("weekdayDescriptions", [])
        hours_text = "\n  ".join(weekday_hours[:3]) if weekday_hours else "Not available"
        rating_str = f"{rating} ({rating_count} reviews)" if rating else "No ratings"

        result = f"Place: {name}\nAddress: {address}\nRating: {rating_str}\nStatus: {open_status}\nPhone: {phone}\nWebsite: {website}\nGoogle Maps: {maps_url}"
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
# Directions — real-time transit via API, browser for driving/walking/cycling
# ============================================================================

def get_directions(origin: str, destination: str, mode: str = "DRIVE") -> str:
    """Open Google Maps directions in browser (driving/walking/cycling only)."""
    try:
        logger.info(f"[MAPS] Opening directions in browser: {origin} -> {destination} ({mode})")
        url = _build_maps_directions_url(origin, destination, mode)
        open_in_browser(url)

        mode_display = {"DRIVE": "driving", "WALK": "walking", "BICYCLE": "cycling"}.get(
            mode.upper() if mode else "DRIVE", "driving"
        )
        logger.info(f"[MAPS] Browser opened: {url}")
        return f"[BROWSER] Opened Google Maps with {mode_display} directions from {origin} to {destination}."

    except Exception as e:
        logger.error(f"[MAPS] Directions error: {e}", exc_info=True)
        return f"Error opening directions: {str(e)}"


def get_transit_directions(origin: str, destination: str) -> str:
    """
    Get real-time public transit directions with exact departure/arrival times.

    Uses Google Directions API with mode=transit and departure_time=now.
    Returns formatted route text with step-by-step transit details.
    """
    api_key = os.environ.get("GOOGLE_MAPS_API_KEY")
    if not api_key:
        return "Error: GOOGLE_MAPS_API_KEY not set"

    try:
        logger.info(f"[MAPS] Fetching transit directions: {origin} -> {destination}")

        response = get_http_client().get(
            "https://maps.googleapis.com/maps/api/directions/json",
            params={
                "origin": origin,
                "destination": destination,
                "mode": "transit",
                "departure_time": "now",
                "alternatives": "true",
                "transit_mode": "bus|subway|rail",
                "key": api_key,
            },
            timeout=10.0,
        )
        response.raise_for_status()
        data = response.json()

        if data.get("status") != "OK":
            status = data.get("status", "UNKNOWN")
            logger.warning(f"[MAPS] Directions API status: {status}")
            return f"No transit routes found ({status}). There may be no public transit service for this route."

        routes = data.get("routes", [])
        if not routes:
            return "No transit routes found for this trip."

        result_parts = []
        for i, route in enumerate(routes, 1):
            leg = route["legs"][0]

            depart_time = leg.get("departure_time", {}).get("text", "N/A")
            arrive_time = leg.get("arrival_time", {}).get("text", "N/A")
            duration = leg.get("duration", {}).get("text", "N/A")

            route_header = f"Route {i}: {duration} (depart {depart_time}, arrive {arrive_time})"
            steps_text = []

            for step in leg.get("steps", []):
                travel_mode = step.get("travel_mode", "")

                if travel_mode == "TRANSIT":
                    td = step.get("transit_details", {})
                    line = td.get("line", {})
                    vehicle_type = line.get("vehicle", {}).get("type", "TRANSIT")
                    line_name = line.get("short_name") or line.get("name", "")
                    agency = line.get("agencies", [{}])[0].get("name", "")

                    dep_stop = td.get("departure_stop", {}).get("name", "")
                    arr_stop = td.get("arrival_stop", {}).get("name", "")
                    dep_time = td.get("departure_time", {}).get("text", "")
                    arr_time = td.get("arrival_time", {}).get("text", "")
                    num_stops = td.get("num_stops", 0)

                    label = f"{vehicle_type} {line_name}".strip()
                    if agency:
                        label += f" ({agency})"

                    steps_text.append(
                        f"  Board {label} at {dep_stop} at {dep_time} -> "
                        f"alight at {arr_stop} at {arr_time} ({num_stops} stops)"
                    )

                elif travel_mode == "WALKING":
                    walk_dur = step.get("duration", {}).get("text", "")
                    walk_dist = step.get("distance", {}).get("text", "")
                    steps_text.append(f"  Walk {walk_dist} ({walk_dur})")

            result_parts.append(route_header + "\n" + "\n".join(steps_text))

        result = "\n\n".join(result_parts)
        logger.info(f"[MAPS] Found {len(routes)} transit route(s)")
        return result

    except Exception as e:
        logger.error(f"[MAPS] Transit directions error: {e}", exc_info=True)
        return f"Error fetching transit directions: {str(e)}"


# ============================================================================
# Intent Classification Fallback
# ============================================================================

_PLACE_KEYWORDS = {
    "hours", "open", "closed", "rating", "phone", "address", "about", "info", "tell me about",
    "find", "near", "nearby", "restaurant", "store", "shop", "pizza", "coffee", "gas",
    "pharmacy", "hotel", "bar", "gym", "park", "library", "hospital", "bank",
    "where is", "where can i find", "closest", "nearest",
}
_PLACE_SEARCH_INDICATORS = {"find", "near", "nearby", "closest", "nearest"}
_DIRECTION_WORDS = {"to", "from", "directions", "route", "how do i get"}
_DRIVE_KEYWORDS = {"drive", "driving"}
_WALK_KEYWORDS = {"walk", "walking"}
_BIKE_KEYWORDS = {"bike", "biking", "cycling", "bicycle"}


def _classify_intent(message: str) -> tuple[str, dict]:
    """
    Classify intent from keywords when the tool model fails to produce JSON.

    Returns (function_name, arguments) based on keyword matching.
    Defaults to get_transit_directions since transit is the preferred mode.
    """
    lower = message.lower()
    home_addr, work_addr = _load_user_addresses()

    # Resolve "home" and "work" to addresses
    def resolve_location(text: str) -> str:
        t = text.lower()
        if home_addr and any(w in t for w in ["home", "my place", "my house"]):
            return home_addr
        if work_addr and any(w in t for w in ["work", "office", "my job"]):
            return work_addr
        return text

    # Place info queries — explicit place keywords without direction words
    has_direction_words = any(kw in lower for kw in _DIRECTION_WORDS)
    if any(kw in lower for kw in _PLACE_KEYWORDS) and not has_direction_words:
        return "get_place_info", {"query": message}

    # Place search indicators ("find", "near", "nearby", "closest", "nearest")
    # trigger place search even if other keywords are absent, as long as no direction words
    if any(kw in lower for kw in _PLACE_SEARCH_INDICATORS) and not has_direction_words:
        return "get_place_info", {"query": message}

    # Driving/walking/cycling — browser
    for keywords, mode in [(_DRIVE_KEYWORDS, "DRIVE"), (_WALK_KEYWORDS, "WALK"), (_BIKE_KEYWORDS, "BICYCLE")]:
        if any(kw in lower for kw in keywords):
            origin = home_addr or "here"
            destination = message
            return "get_directions", {"origin": origin, "destination": destination, "mode": mode}

    # Default: transit directions
    # Try to extract origin/destination from common patterns
    origin = home_addr or ""
    destination = work_addr or ""

    if "to work" in lower or "get to work" in lower:
        origin = home_addr or "home"
        destination = work_addr or "work"
    elif "to home" in lower or "get home" in lower or "back home" in lower:
        origin = work_addr or "work"
        destination = home_addr or "home"
    elif " to " in lower:
        parts = lower.split(" to ", 1)
        origin = resolve_location(parts[0].strip())
        destination = resolve_location(parts[1].strip())
    elif " from " in lower:
        parts = lower.split(" from ", 1)
        after_from = parts[1]
        if " to " in after_from:
            from_to = after_from.split(" to ", 1)
            origin = resolve_location(from_to[0].strip())
            destination = resolve_location(from_to[1].strip())
        else:
            origin = resolve_location(after_from.strip())
            destination = work_addr or home_addr or message

    if not origin or not destination:
        origin = home_addr or "home"
        destination = work_addr or "work"

    return "get_transit_directions", {"origin": origin, "destination": destination}


# ============================================================================
# Maps Agent — Tool-use specialist with fallback
# ============================================================================

TOOL_DISPATCH = {
    "get_place_info": lambda args: get_place_info(args.get("query", "")),
    "get_transit_directions": lambda args: get_transit_directions(args.get("origin", ""), args.get("destination", "")),
    "get_directions": lambda args: get_directions(args.get("origin", ""), args.get("destination", ""), args.get("mode", "DRIVE")),
}


def _extract_function_call(response_text: str) -> tuple[str, dict] | None:
    """Try to parse a JSON function call from LLM response. Returns (name, args) or None."""
    if "{" not in response_text or "}" not in response_text:
        return None
    try:
        start = response_text.find("{")
        end = response_text.rfind("}") + 1
        call = json.loads(response_text[start:end])
        name = call.get("name")
        args = call.get("arguments", {})
        if name and name in TOOL_DISPATCH:
            return name, args
    except (json.JSONDecodeError, AttributeError):
        pass
    return None


def maps_agent(user_message: str) -> str:
    """
    Specialized maps agent with LLM tool extraction + keyword fallback.

    1. Try LLM-based function call extraction (up to 2 attempts)
    2. If LLM fails, fall back to keyword-based intent classification
    3. Execute the function and return real API data — never raw LLM text
    """
    from datetime import datetime
    from ai.context_loader import load_prompt

    try:
        logger.info(f"[MAPS] Maps agent invoked: {user_message[:80]}...")

        maps_tools = load_tools("maps_tools")

        try:
            profile = load_prompt("USER_PROFILE")
        except FileNotFoundError:
            profile = ""

        current_dt = datetime.now().strftime("%A, %B %d, %Y %I:%M %p")

        system_prompt = f"""You are a maps function-call extractor. Output ONLY a JSON object, nothing else.

CURRENT DATE/TIME: {current_dt}

USER CONTEXT:
{profile}
"home"/"my place" = Home Address above. "work"/"office" = Work Address above.

RULES:
- get_transit_directions: DEFAULT for ALL route/direction/commute queries. Returns real-time bus/subway times.
- get_directions: ONLY if user explicitly says "drive"/"walk"/"bike".
- get_place_info: For place info (hours, rating, phone, etc.) — NOT for directions.
- Always use full street addresses from the user profile, not shorthand.

OUTPUT: Only a JSON object. No text before or after.
{{"name": "function_name", "arguments": {{"param": "value"}}}}

Available functions:
{json.dumps(maps_tools, indent=2)}"""

        client = get_client()

        # Attempt LLM extraction (up to 2 tries)
        function_call = None
        for attempt in range(2):
            response = client.chat.completions.create(
                model=get_model("tool"),
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message}
                ],
                temperature=0.0,
                max_tokens=200
            )

            llm_response = response.choices[0].message.content
            logger.info(f"[MAPS] Tool model response (attempt {attempt + 1}): {llm_response[:100]}...")

            function_call = _extract_function_call(llm_response)
            if function_call:
                break
            logger.warning(f"[MAPS] Tool model failed to produce valid JSON (attempt {attempt + 1})")

        # Fallback to keyword classification if LLM failed
        if not function_call:
            logger.warning(f"[MAPS] LLM extraction failed, using keyword fallback for: {user_message[:60]}")
            function_call = _classify_intent(user_message)

        func_name, func_args = function_call
        logger.info(f"[MAPS] Executing: {func_name}({func_args})")

        tool_result = TOOL_DISPATCH[func_name](func_args)

        logger.info(f"[MAPS] Tool executed. Result length: {len(tool_result)} chars")
        return tool_result

    except Exception as e:
        logger.error(f"[MAPS] Maps agent error: {e}", exc_info=True)
        return f"Error with maps operation: {str(e)}"
