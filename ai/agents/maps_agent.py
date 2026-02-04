"""
Maps agent - Routes API + Places API (New) for directions and place information.

Implements Plan-Act pattern for function call extraction.
Uses httpx for direct API calls to Google Routes and Places APIs.
"""

import os
import json
import logging
import httpx
from pathlib import Path
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

logger = logging.getLogger(__name__)

# API endpoints
ROUTES_API = "https://routes.googleapis.com/directions/v2:computeRoutes"
PLACES_TEXT_SEARCH = "https://places.googleapis.com/v1/places:searchText"
PLACES_DETAILS = "https://places.googleapis.com/v1/places"


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
# Routes API Functions
# ============================================================================

def get_directions(origin: str, destination: str, mode: str = "DRIVE") -> str:
    """
    Get directions between two locations using Routes API.

    Args:
        origin: Starting point (address or place name)
        destination: End point (address or place name)
        mode: Travel mode - DRIVE, WALK, or BICYCLE

    Returns:
        Formatted string with distance, duration, and turn-by-turn directions
    """
    try:
        logger.info(f"[MAPS] Getting directions: {origin} -> {destination} ({mode})")

        # Normalize mode
        mode = mode.upper() if mode else "DRIVE"
        if mode not in ["DRIVE", "WALK", "BICYCLE"]:
            mode = "DRIVE"

        headers = _get_headers(
            "routes.duration,routes.distanceMeters,routes.legs.steps.navigationInstruction,"
            "routes.legs.steps.distanceMeters,routes.legs.startLocation,routes.legs.endLocation"
        )

        body = {
            "origin": {"address": origin},
            "destination": {"address": destination},
            "travelMode": mode,
            "routingPreference": "TRAFFIC_AWARE" if mode == "DRIVE" else "ROUTING_PREFERENCE_UNSPECIFIED",
            "languageCode": "en-US",
            "units": "IMPERIAL"
        }

        response = httpx.post(ROUTES_API, headers=headers, json=body, timeout=20.0)
        response.raise_for_status()
        result = response.json()

        routes = result.get("routes", [])
        if not routes:
            return f"No route found from '{origin}' to '{destination}'."

        route = routes[0]
        dist_meters = route.get("distanceMeters", 0)
        duration_str = route.get("duration", "0s").replace("s", "")

        # Format distance
        miles = dist_meters / 1609.34
        if miles >= 1:
            dist_display = f"{miles:.1f} miles"
        else:
            feet = dist_meters * 3.28084
            dist_display = f"{int(feet)} feet"

        # Format duration
        try:
            secs = int(duration_str)
            if secs >= 3600:
                hrs, rem = divmod(secs, 3600)
                mins = rem // 60
                dur_display = f"{hrs}h {mins}m"
            else:
                mins = secs // 60
                dur_display = f"{mins} min" if mins > 0 else f"{secs} sec"
        except ValueError:
            dur_display = duration_str

        # Get turn-by-turn directions
        legs = route.get("legs", [{}])
        steps = legs[0].get("steps", []) if legs else []
        instructions = []
        for i, step in enumerate(steps[:8], 1):  # Limit to 8 steps
            nav = step.get("navigationInstruction", {})
            instr = nav.get("instructions", "Continue")
            step_dist = step.get("distanceMeters", 0)
            step_miles = step_dist / 1609.34
            if step_miles >= 0.1:
                instr += f" ({step_miles:.1f} mi)"
            elif step_dist > 0:
                instr += f" ({int(step_dist * 3.28084)} ft)"
            instructions.append(f"{i}. {instr}")

        mode_emoji = {"DRIVE": "🚗", "WALK": "🚶", "BICYCLE": "🚴"}.get(mode, "")

        output = f"""{mode_emoji} {mode.title()} directions: {origin} → {destination}

Distance: {dist_display}
Duration: {dur_display}

Directions:
{chr(10).join(instructions)}"""

        if len(steps) > 8:
            output += f"\n... and {len(steps) - 8} more steps"

        logger.info(f"[MAPS] Directions complete: {dist_display}, {dur_display}")
        return output

    except httpx.HTTPStatusError as e:
        logger.error(f"[MAPS] Routes API error: {e.response.status_code} - {e.response.text}")
        return f"Error fetching directions: {e.response.status_code}"
    except Exception as e:
        logger.error(f"[MAPS] Directions error: {e}", exc_info=True)
        return f"Error getting directions: {str(e)}"


def get_transit_info(origin: str, destination: str) -> str:
    """
    Get public transit directions between two locations using Routes API.

    Args:
        origin: Starting point (address or place name)
        destination: End point (address or place name)

    Returns:
        Formatted string with transit routes, departure times, and transfers
    """
    try:
        logger.info(f"[MAPS] Getting transit info: {origin} -> {destination}")

        headers = _get_headers(
            "routes.duration,routes.distanceMeters,"
            "routes.legs.steps.transitDetails,"
            "routes.legs.steps.travelMode,"
            "routes.legs.steps.staticDuration,"
            "routes.legs.steps.navigationInstruction"
        )

        body = {
            "origin": {"address": origin},
            "destination": {"address": destination},
            "travelMode": "TRANSIT",
            "computeAlternativeRoutes": True,
            "languageCode": "en-US",
            "units": "IMPERIAL"
        }

        response = httpx.post(ROUTES_API, headers=headers, json=body, timeout=20.0)
        response.raise_for_status()
        result = response.json()

        routes = result.get("routes", [])
        if not routes:
            return f"No transit routes found from '{origin}' to '{destination}'. Try checking if public transit is available in this area."

        # Process first route (best option)
        route = routes[0]
        duration_str = route.get("duration", "0s").replace("s", "")

        # Format duration
        try:
            secs = int(duration_str)
            if secs >= 3600:
                hrs, rem = divmod(secs, 3600)
                mins = rem // 60
                dur_display = f"{hrs}h {mins}m"
            else:
                mins = secs // 60
                dur_display = f"{mins} min"
        except ValueError:
            dur_display = duration_str

        # Extract transit steps
        legs = route.get("legs", [{}])
        steps = legs[0].get("steps", []) if legs else []

        transit_steps = []
        for step in steps:
            travel_mode = step.get("travelMode", "")
            transit_details = step.get("transitDetails", {})
            nav = step.get("navigationInstruction", {})

            if travel_mode == "TRANSIT" and transit_details:
                # Transit step (bus, subway, etc.)
                stop_details = transit_details.get("stopDetails", {})
                departure_stop = stop_details.get("departureStop", {}).get("name", "")
                arrival_stop = stop_details.get("arrivalStop", {}).get("name", "")

                transit_line = transit_details.get("transitLine", {})
                line_name = transit_line.get("name") or transit_line.get("nameShort", "")
                vehicle = transit_line.get("vehicle", {})
                vehicle_type = vehicle.get("type", "").replace("_", " ").title()

                # Vehicle emoji
                vehicle_emoji = {
                    "Bus": "🚌",
                    "Subway": "🚇",
                    "Rail": "🚆",
                    "Tram": "🚊",
                    "Ferry": "⛴️",
                }.get(vehicle_type, "🚏")

                num_stops = transit_details.get("stopCount", 0)
                stop_text = f"({num_stops} stops)" if num_stops else ""

                if line_name:
                    transit_steps.append(f"{vehicle_emoji} Take {line_name} ({vehicle_type}) {stop_text}")
                    if departure_stop:
                        transit_steps.append(f"   From: {departure_stop}")
                    if arrival_stop:
                        transit_steps.append(f"   To: {arrival_stop}")

            elif travel_mode == "WALK":
                # Walking step
                instr = nav.get("instructions", "Walk")
                step_duration = step.get("staticDuration", "0s").replace("s", "")
                try:
                    walk_mins = int(step_duration) // 60
                    if walk_mins > 0:
                        transit_steps.append(f"🚶 {instr} ({walk_mins} min walk)")
                    else:
                        transit_steps.append(f"🚶 {instr}")
                except ValueError:
                    transit_steps.append(f"🚶 {instr}")

        output = f"""🚇 Transit directions: {origin} → {destination}

Total time: {dur_display}

Route:
{chr(10).join(transit_steps) if transit_steps else "No detailed transit information available."}"""

        # Add alternative routes info if available
        if len(routes) > 1:
            output += f"\n\n({len(routes) - 1} alternative route(s) available)"

        logger.info(f"[MAPS] Transit info complete: {dur_display}")
        return output

    except httpx.HTTPStatusError as e:
        logger.error(f"[MAPS] Transit API error: {e.response.status_code} - {e.response.text}")
        if e.response.status_code == 400:
            return f"Transit directions not available for this route. Public transit may not be available in this area, or the locations couldn't be found."
        return f"Error fetching transit info: {e.response.status_code}"
    except Exception as e:
        logger.error(f"[MAPS] Transit error: {e}", exc_info=True)
        return f"Error getting transit info: {str(e)}"


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

        # Call Qwen via OpenRouter (minimal context!)
        client = OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=os.getenv("OPENROUTER_API_KEY")
        )

        response = client.chat.completions.create(
            model="openai/gpt-oss-20b:free",
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
