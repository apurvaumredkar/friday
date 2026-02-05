"""
Calendar agent - Specialized tool-use agent with Google Calendar API operations.

This module combines the calendar agent logic (Plan-Act pattern)
and Google Calendar API operations (create, list, update, delete events).
"""

import os
import json
import logging
import httpx
import pytz
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from openai import OpenAI

from ._oauth import get_access_token
from ai.config import get_model, get_base_url, get_api_key

logger = logging.getLogger(__name__)

# API Base URL
CALENDAR_API_BASE = "https://www.googleapis.com/calendar/v3"


# ============================================================================
# Google Calendar API Operations
# ============================================================================

def create_event(
    title: str,
    start_time: str,  # ISO 8601: "2026-01-30T14:00:00"
    end_time: str,    # ISO 8601: "2026-01-30T15:00:00"
    description: str = "",
    attendees: Optional[list[str]] = None,
    location: str = "",
    timezone: str = "America/New_York",
    calendar_id: str = "primary"
) -> dict:
    """
    Create a calendar event.

    Args:
        title: Event title/summary
        start_time: ISO 8601 datetime string (e.g., "2026-01-30T14:00:00")
        end_time: ISO 8601 datetime string (e.g., "2026-01-30T15:00:00")
        description: Event description/notes
        attendees: List of email addresses to invite
        location: Event location
        timezone: IANA timezone (e.g., "America/New_York")
        calendar_id: Calendar ID (default: "primary")

    Returns:
        Created event data including event ID and htmlLink

    Raises:
        httpx.HTTPStatusError: If API request fails
    """
    logger.info(f"Creating calendar event: {title}")
    access_token = get_access_token()

    event_body = {
        "summary": title,
        "description": description,
        "location": location,
        "start": {
            "dateTime": start_time,
            "timeZone": timezone,
        },
        "end": {
            "dateTime": end_time,
            "timeZone": timezone,
        },
    }

    if attendees:
        event_body["attendees"] = [{"email": email} for email in attendees]

    response = httpx.post(
        f"{CALENDAR_API_BASE}/calendars/{calendar_id}/events",
        headers={"Authorization": f"Bearer {access_token}"},
        json=event_body,
        timeout=10.0,
    )
    response.raise_for_status()
    event_data = response.json()
    logger.info(f"Created event ID: {event_data.get('id')}")
    return event_data


def list_events(
    start_date: str,  # "2026-01-29"
    end_date: str,    # "2026-02-05"
    max_results: int = 50,
    calendar_id: str = "primary",
    timezone: str = "America/New_York"
) -> list[dict]:
    """
    List calendar events in date range.

    Args:
        start_date: Start date (YYYY-MM-DD)
        end_date: End date (YYYY-MM-DD)
        max_results: Maximum number of events to return (default: 50)
        calendar_id: Calendar ID (default: "primary")
        timezone: IANA timezone

    Returns:
        List of event dictionaries with event details

    Raises:
        httpx.HTTPStatusError: If API request fails
    """
    logger.info(f"Listing events from {start_date} to {end_date}")
    access_token = get_access_token()

    # Convert dates to RFC3339 timestamps with proper timezone
    tz = pytz.timezone(timezone)
    start_dt = datetime.strptime(start_date, "%Y-%m-%d")
    start_dt = tz.localize(start_dt)
    end_dt = datetime.strptime(end_date, "%Y-%m-%d").replace(hour=23, minute=59, second=59)
    end_dt = tz.localize(end_dt)
    time_min = start_dt.isoformat()
    time_max = end_dt.isoformat()

    response = httpx.get(
        f"{CALENDAR_API_BASE}/calendars/{calendar_id}/events",
        headers={"Authorization": f"Bearer {access_token}"},
        params={
            "timeMin": time_min,
            "timeMax": time_max,
            "maxResults": max_results,
            "singleEvents": True,  # Expand recurring events
            "orderBy": "startTime",
            "timeZone": timezone,
        },
        timeout=10.0,
    )
    response.raise_for_status()
    data = response.json()
    events = data.get("items", [])
    logger.info(f"Found {len(events)} events")
    return events


def get_event(event_id: str, calendar_id: str = "primary") -> dict:
    """
    Get details of specific event.

    Args:
        event_id: Event ID
        calendar_id: Calendar ID (default: "primary")

    Returns:
        Event data dictionary

    Raises:
        httpx.HTTPStatusError: If API request fails or event not found
    """
    logger.info(f"Getting event details: {event_id}")
    access_token = get_access_token()

    response = httpx.get(
        f"{CALENDAR_API_BASE}/calendars/{calendar_id}/events/{event_id}",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=10.0,
    )
    response.raise_for_status()
    return response.json()


def update_event(
    event_id: str,
    updates: dict,
    calendar_id: str = "primary"
) -> dict:
    """
    Update existing calendar event.

    Args:
        event_id: Event ID to update
        updates: Dictionary of fields to update (e.g., {"summary": "New Title"})
        calendar_id: Calendar ID (default: "primary")

    Returns:
        Updated event data

    Raises:
        httpx.HTTPStatusError: If API request fails
    """
    logger.info(f"Updating event: {event_id}")
    access_token = get_access_token()

    # Whitelist of allowed fields
    ALLOWED_FIELDS = {
        "summary", "description", "location",
        "start", "end", "attendees", "reminders"
    }

    # Validate updates - reject read-only fields
    invalid_fields = set(updates.keys()) - ALLOWED_FIELDS
    if invalid_fields:
        raise ValueError(f"Cannot update read-only fields: {invalid_fields}")

    # Get current event first
    current_event = get_event(event_id, calendar_id)

    # Merge updates with current event
    current_event.update(updates)

    response = httpx.put(
        f"{CALENDAR_API_BASE}/calendars/{calendar_id}/events/{event_id}",
        headers={"Authorization": f"Bearer {access_token}"},
        json=current_event,
        timeout=10.0,
    )
    response.raise_for_status()
    logger.info(f"Updated event: {event_id}")
    return response.json()


def delete_event(event_id: str, calendar_id: str = "primary") -> bool:
    """
    Delete calendar event.

    Args:
        event_id: Event ID to delete
        calendar_id: Calendar ID (default: "primary")

    Returns:
        True if successful

    Raises:
        httpx.HTTPStatusError: If API request fails
    """
    logger.info(f"Deleting event: {event_id}")
    access_token = get_access_token()

    response = httpx.delete(
        f"{CALENDAR_API_BASE}/calendars/{calendar_id}/events/{event_id}",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=10.0,
    )
    response.raise_for_status()
    logger.info(f"Deleted event: {event_id}")
    return True


def check_availability(
    start_time: str,  # ISO 8601
    end_time: str,
    calendar_id: str = "primary",
    timezone: str = "America/New_York"
) -> dict:
    """
    Check if time slot is available (no conflicts).

    Uses Google Calendar's freeBusy query to check for scheduling conflicts.

    Args:
        start_time: ISO 8601 datetime string
        end_time: ISO 8601 datetime string
        calendar_id: Calendar ID (default: "primary")
        timezone: IANA timezone (default: "America/New_York")

    Returns:
        {
            "available": bool,  # True if no conflicts
            "conflicts": list[dict]  # List of conflicting time periods
        }

    Raises:
        httpx.HTTPStatusError: If API request fails
    """
    logger.info(f"Checking availability: {start_time} to {end_time}")
    access_token = get_access_token()

    # Ensure timestamps have timezone info (required by freeBusy API)
    tz = pytz.timezone(timezone)
    start_dt = datetime.fromisoformat(start_time)
    end_dt = datetime.fromisoformat(end_time)

    # Add timezone if naive
    if start_dt.tzinfo is None:
        start_dt = tz.localize(start_dt)
    if end_dt.tzinfo is None:
        end_dt = tz.localize(end_dt)

    time_min = start_dt.isoformat()
    time_max = end_dt.isoformat()

    # Use freebusy query
    response = httpx.post(
        f"{CALENDAR_API_BASE}/freeBusy",
        headers={"Authorization": f"Bearer {access_token}"},
        json={
            "timeMin": time_min,
            "timeMax": time_max,
            "items": [{"id": calendar_id}]
        },
        timeout=10.0,
    )
    response.raise_for_status()
    data = response.json()

    busy_periods = data.get("calendars", {}).get(calendar_id, {}).get("busy", [])
    available = len(busy_periods) == 0

    logger.info(f"Availability check: {'Available' if available else f'{len(busy_periods)} conflict(s)'}")
    return {
        "available": available,
        "conflicts": busy_periods
    }


def find_free_slots(
    date: str,  # "2026-01-30"
    duration_minutes: int,
    start_hour: int = 9,  # 9 AM
    end_hour: int = 17,   # 5 PM
    calendar_id: str = "primary",
    timezone: str = "America/New_York"
) -> list[dict]:
    """
    Find available time slots on a given date.

    Args:
        date: Date to check (YYYY-MM-DD)
        duration_minutes: Required slot duration in minutes
        start_hour: Working hours start (24-hour format, default: 9)
        end_hour: Working hours end (24-hour format, default: 17)
        calendar_id: Calendar ID (default: "primary")
        timezone: IANA timezone

    Returns:
        List of available slots:
        [
            {"start": "2026-01-30T09:00:00", "end": "2026-01-30T10:00:00"},
            ...
        ]

    Raises:
        httpx.HTTPStatusError: If API request fails
    """
    logger.info(f"Finding free slots on {date}")

    # Get all events for the day
    events = list_events(date, date, calendar_id=calendar_id, timezone=timezone)

    # Build list of busy periods
    busy_periods = []
    for event in events:
        start = event.get("start", {}).get("dateTime")
        end = event.get("end", {}).get("dateTime")
        if start and end:
            busy_periods.append({"start": start, "end": end})

    # Sort busy periods by start time
    busy_periods.sort(key=lambda x: x["start"])

    # Find gaps between busy periods (use timezone-aware datetimes)
    tz = pytz.timezone(timezone)
    free_slots = []
    current_time_dt = datetime.strptime(f"{date}T{start_hour:02d}:00:00", "%Y-%m-%dT%H:%M:%S")
    current_time_dt = tz.localize(current_time_dt)
    current_time = current_time_dt.isoformat()

    end_time_dt = datetime.strptime(f"{date}T{end_hour:02d}:00:00", "%Y-%m-%dT%H:%M:%S")
    end_time_dt = tz.localize(end_time_dt)
    end_time = end_time_dt.isoformat()

    for busy in busy_periods:
        # Check if there's a gap before this busy period
        if current_time < busy["start"]:
            # Calculate gap duration
            gap_start = datetime.fromisoformat(current_time)
            gap_end = datetime.fromisoformat(busy["start"])
            gap_duration = (gap_end - gap_start).total_seconds() / 60

            # Only add if gap is long enough
            if gap_duration >= duration_minutes:
                free_slots.append({
                    "start": current_time,
                    "end": busy["start"],
                    "duration_minutes": int(gap_duration)
                })

        # Move current_time to end of this busy period
        current_time = max(current_time, busy["end"])

    # Check for gap at end of day
    if current_time < end_time:
        gap_start = datetime.fromisoformat(current_time)
        gap_end = datetime.fromisoformat(end_time)
        gap_duration = (gap_end - gap_start).total_seconds() / 60

        if gap_duration >= duration_minutes:
            free_slots.append({
                "start": current_time,
                "end": end_time,
                "duration_minutes": int(gap_duration)
            })

    logger.info(f"Found {len(free_slots)} free slots")
    return free_slots


# ============================================================================
# Calendar Agent - Tool-use specialist with PAR loop (Qwen 2.5 VL)
# ============================================================================

def calendar_agent(user_message: str) -> str:
    """
    Specialized calendar agent using Qwen 2.5 VL 7B Instruct.

    Implements Plan-Act pattern (Reflect happens in root agent):
    1. Plan: Qwen extracts function call from user's natural language request
    2. Act: Execute the calendar function and return raw result
    3. (Reflect: Root agent with full history formats the response)

    Uses minimal context (system prompt + tool definitions, no conversation history).

    Args:
        user_message: User's natural language calendar request
            Examples:
                "Schedule meeting tomorrow at 2pm"
                "What's on my calendar this week?"
                "Am I free at 3pm today?"

    Returns:
        Raw tool execution result string to be passed back to root agent
    """
    try:
        logger.info(f"Calendar agent invoked: {user_message[:50]}...")

        # Load tool definitions
        tools_file = Path(__file__).parent.parent / "skills" / "calendar_tools.json"
        calendar_tools = json.loads(tools_file.read_text())

        # Build system prompt with current date
        current_date = datetime.now().strftime("%Y-%m-%d")
        system_prompt = f"""You are a calendar operations specialist for Friday AI assistant.

Your ONLY task is to translate user's natural language requests into precise calendar function calls.

RULES:
1. Always use today's date as reference: {current_date}
2. Convert relative dates ("tomorrow", "next week") to YYYY-MM-DD format
3. Convert times to 24-hour HH:MM format (e.g., "2pm" → "14:00")
4. Default duration is 60 minutes if not specified
5. For update/delete operations, user must provide event_id (prompt them to list events first)
6. Call exactly ONE function per request

OUTPUT FORMAT:
You MUST respond with ONLY a JSON object in this exact format:
{{
  "name": "function_name",
  "arguments": {{
    "param1": "value1",
    "param2": "value2"
  }}
}}

Do not include any explanatory text, markdown, or additional content. Only output the JSON object."""

        # Format tools for Qwen (as plain text in system prompt)
        tools_text = "\n\nAvailable functions:\n" + json.dumps(calendar_tools, indent=2)
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
            max_tokens=500,
            extra_body={"thinking": {"type": "disabled"}}
        )

        assistant_response = response.choices[0].message.content
        logger.info(f"Qwen response: {assistant_response[:100]}...")

        # Parse function call from response (Qwen returns JSON in text)
        # Look for JSON function call in the response
        try:
            # Try to extract JSON from response
            if "{" in assistant_response and "}" in assistant_response:
                start_idx = assistant_response.find("{")
                end_idx = assistant_response.rfind("}") + 1
                function_call_json = assistant_response[start_idx:end_idx]
                function_call = json.loads(function_call_json)

                function_name = function_call.get("name")
                function_args = function_call.get("arguments", {})

                logger.info(f"Extracted function call: {function_name}({function_args})")
            else:
                # No function call, return Qwen's text response
                return assistant_response
        except json.JSONDecodeError:
            # If we can't parse JSON, return the text response
            return assistant_response

        # Execute the appropriate calendar function and get raw result
        tool_result = None

        if function_name == "create_event":
            title = function_args["title"]
            date = function_args["date"]
            time = function_args["time"]
            duration_min = function_args["duration_minutes"]
            description = function_args.get("description", "")

            logger.info(f"[CALENDAR] Creating event: '{title}' on {date} at {time} ({duration_min}min)")

            start_time = f"{date}T{time}:00"
            start_dt = datetime.fromisoformat(start_time)
            end_dt = start_dt + timedelta(minutes=duration_min)
            end_time = end_dt.isoformat()

            event = create_event(
                title=title,
                start_time=start_time,
                end_time=end_time,
                description=description
            )

            event_id = event.get('id')
            logger.info(f"[CALENDAR] Event created: ID={event_id}, Link={event.get('htmlLink')}")

            tool_result = f"Event created successfully: '{title}' on {date} at {time} for {duration_min} minutes."

        elif function_name == "list_events":
            start_date = function_args["start_date"]
            end_date = function_args["end_date"]

            logger.info(f"[CALENDAR] Listing events: {start_date} to {end_date}")

            events = list_events(start_date, end_date)

            if not events:
                logger.info(f"[CALENDAR] No events found in range")
                tool_result = f"No events found between {start_date} and {end_date}."
            else:
                logger.info(f"[CALENDAR] Found {len(events)} events")
                events_summary = []
                entity_refs = []
                for event in events:
                    title = event.get("summary", "Untitled")
                    start = event.get("start", {}).get("dateTime", "")
                    event_id = event.get("id", "")
                    # Log event ID but don't include in user-facing result
                    logger.debug(f"[CALENDAR]   - {title} at {start} (ID: {event_id})")
                    events_summary.append(f"- {title} at {start}")
                tool_result = f"Found {len(events)} events:\n" + "\n".join(events_summary)

        elif function_name == "check_availability":
            date = function_args["date"]
            time = function_args["time"]
            duration_min = function_args["duration_minutes"]

            logger.info(f"[CALENDAR] Checking availability: {date} at {time} ({duration_min}min)")

            start_time = f"{date}T{time}:00"
            start_dt = datetime.fromisoformat(start_time)
            end_dt = start_dt + timedelta(minutes=duration_min)
            end_time = end_dt.isoformat()

            result = check_availability(start_time, end_time)

            if result["available"]:
                logger.info(f"[CALENDAR] Time slot available")
                tool_result = f"Time slot is available: {date} at {time} for {duration_min} minutes."
            else:
                conflicts = len(result.get("conflicts", []))
                logger.info(f"[CALENDAR] Time slot unavailable: {conflicts} conflict(s)")
                tool_result = f"Time slot is NOT available. {conflicts} conflict(s) found."

        elif function_name == "update_event":
            event_id = function_args["event_id"]
            field = function_args["field"]
            new_value = function_args["new_value"]

            logger.info(f"[CALENDAR] Updating event {event_id}: {field} -> {new_value}")

            updates = {field: new_value}
            updated_event = update_event(event_id, updates)

            logger.info(f"[CALENDAR] Event updated successfully")
            tool_result = f"Event updated successfully. Field '{field}' changed to '{new_value}'."

        elif function_name == "delete_event":
            event_id = function_args["event_id"]

            # Google Calendar event IDs are typically 20+ chars alphanumeric
            # If event_id looks like a placeholder (short, simple, or common placeholder patterns),
            # try to find the real ID from memory using the user's description
            is_placeholder = (
                len(event_id) < 15 or
                event_id.isdigit() or
                event_id.lower() in ("event_id_value", "event_id", "id", "unknown", "none", "null")
            )

            if is_placeholder:
                logger.info(f"[CALENDAR] Event ID '{event_id}' looks like a placeholder")
                return "I need more information to identify the event. Please list your events first with 'what's on my calendar' to get the event details."

            logger.info(f"[CALENDAR] Deleting event: {event_id}")

            event = get_event(event_id)
            title = event.get("summary", "Untitled")

            delete_event(event_id)

            logger.info(f"[CALENDAR] Event deleted: '{title}'")
            tool_result = f"Event deleted successfully. Event name: {title}."

        else:
            return f"Unknown function: {function_name}"

        # Return raw tool result to be passed back to root agent for reflection
        logger.info(f"Tool executed. Result: {tool_result[:100]}...")
        return tool_result

    except Exception as e:
        logger.error(f"Calendar agent error: {e}", exc_info=True)
        return f"I encountered an error with that calendar operation: {str(e)}"
