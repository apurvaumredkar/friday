"""Sheets agent - Specialized tool-use agent with Google Sheets API operations."""

import os
import json
import logging
import httpx
from pathlib import Path
from openai import OpenAI
from typing import Optional

from ._oauth import get_access_token

logger = logging.getLogger(__name__)

# API Base URL
SHEETS_API_BASE = "https://sheets.googleapis.com/v4"


# ============================================================================
# Google Sheets API Functions
# ============================================================================

def get_next_row(sheet_id: str, access_token: str, sheet_name: str = "vaspian", column: str = "B") -> int:
    """
    Get the next empty row number in a specific sheet and column.

    Args:
        sheet_id: The Google Sheet ID
        access_token: OAuth2 access token
        sheet_name: Name of the sheet tab (default: "vaspian")
        column: Column letter to check (default: "B")

    Returns:
        Next available row number (1-indexed)

    Raises:
        httpx.HTTPStatusError: If API request fails
    """
    logger.info(f"Getting next row for sheet {sheet_id}, column {column}")

    url = f"{SHEETS_API_BASE}/spreadsheets/{sheet_id}/values/{sheet_name}!{column}:{column}"
    response = httpx.get(
        url,
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=10.0,
    )
    response.raise_for_status()
    data = response.json()

    # values is a list of rows, each row is a list of cell values
    rows = data.get("values", [])
    next_row = len(rows) + 1

    logger.info(f"Next available row: {next_row}")
    return next_row


def append_to_sheet(
    sheet_id: str,
    values: list[str],
    sheet_name: str = "vaspian",
    start_column: str = "B",
    end_column: str = "L"
) -> dict:
    """
    Append a row to a Google Sheet starting at a specific column.

    Args:
        sheet_id: The Google Sheet ID (from the URL)
        values: List of values to append as a row
        sheet_name: Name of the sheet tab (default: "vaspian")
        start_column: Starting column letter (default: "B")
        end_column: Ending column letter (default: "L")

    Returns:
        API response dict with update information

    Raises:
        httpx.HTTPStatusError: If API request fails
    """
    logger.info(f"Appending row to sheet {sheet_id}")
    access_token = get_access_token()

    # Get the next empty row
    next_row = get_next_row(sheet_id, access_token, sheet_name, start_column)

    # Write to specific range starting at column B
    url = f"{SHEETS_API_BASE}/spreadsheets/{sheet_id}/values/{sheet_name}!{start_column}{next_row}:{end_column}{next_row}"

    response = httpx.put(
        url,
        params={"valueInputOption": "USER_ENTERED"},
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
        json={"values": [values]},
        timeout=10.0,
    )
    response.raise_for_status()

    result = response.json()
    logger.info(f"Sheet updated: {result.get('updates', {}).get('updatedRows', 0)} row(s)")

    return result


def read_range(
    sheet_id: str,
    range_name: str,
    value_render_option: str = "FORMATTED_VALUE"
) -> list[list[str]]:
    """
    Read values from a specific range in a Google Sheet.

    Args:
        sheet_id: The Google Sheet ID
        range_name: A1 notation range (e.g., "Sheet1!A1:C10")
        value_render_option: How to render values (FORMATTED_VALUE, UNFORMATTED_VALUE, FORMULA)

    Returns:
        2D list of cell values (rows and columns)

    Raises:
        httpx.HTTPStatusError: If API request fails
    """
    logger.info(f"Reading range {range_name} from sheet {sheet_id}")
    access_token = get_access_token()

    url = f"{SHEETS_API_BASE}/spreadsheets/{sheet_id}/values/{range_name}"
    response = httpx.get(
        url,
        params={"valueRenderOption": value_render_option},
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=10.0,
    )
    response.raise_for_status()

    data = response.json()
    values = data.get("values", [])

    logger.info(f"Read {len(values)} rows from sheet")
    return values


def update_range(
    sheet_id: str,
    range_name: str,
    values: list[list[str]],
    value_input_option: str = "USER_ENTERED"
) -> dict:
    """
    Update values in a specific range of a Google Sheet.

    Args:
        sheet_id: The Google Sheet ID
        range_name: A1 notation range (e.g., "Sheet1!A1:C10")
        values: 2D list of values to write
        value_input_option: How to interpret input (USER_ENTERED or RAW)

    Returns:
        API response dict with update information

    Raises:
        httpx.HTTPStatusError: If API request fails
    """
    logger.info(f"Updating range {range_name} in sheet {sheet_id}")
    access_token = get_access_token()

    url = f"{SHEETS_API_BASE}/spreadsheets/{sheet_id}/values/{range_name}"
    response = httpx.put(
        url,
        params={"valueInputOption": value_input_option},
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
        json={"values": values},
        timeout=10.0,
    )
    response.raise_for_status()

    result = response.json()
    logger.info(f"Updated {result.get('updatedCells', 0)} cells")

    return result


def clear_range(sheet_id: str, range_name: str) -> dict:
    """
    Clear values in a specific range of a Google Sheet.

    Args:
        sheet_id: The Google Sheet ID
        range_name: A1 notation range (e.g., "Sheet1!A1:C10")

    Returns:
        API response dict

    Raises:
        httpx.HTTPStatusError: If API request fails
    """
    logger.info(f"Clearing range {range_name} in sheet {sheet_id}")
    access_token = get_access_token()

    url = f"{SHEETS_API_BASE}/spreadsheets/{sheet_id}/values/{range_name}:clear"
    response = httpx.post(
        url,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
        json={},
        timeout=10.0,
    )
    response.raise_for_status()

    logger.info(f"Cleared range {range_name}")
    return response.json()


def populate_paycheck_sheet(csv_row: str) -> str:
    """
    Parse CSV row and append to the paycheck Google Sheet.

    This is a convenience function for paycheck processing that uses
    the PAYCHECK_SHEET_ID from environment variables.

    Args:
        csv_row: CSV string with paycheck data (Year excluded, starts from Pay Period)
            Format: Pay Period,Gross Pay,Social Security,Medicare,
                    Federal Income Tax,NY Income Tax,NY PFL,NY Disability,
                    Total Deductions,Net Pay,Hours

    Returns:
        Success message with pay period and net pay

    Raises:
        ValueError: If PAYCHECK_SHEET_ID is not set
        httpx.HTTPStatusError: If API request fails
    """
    logger.info(f"Populating paycheck sheet with: {csv_row[:50]}...")

    values = [v.strip() for v in csv_row.strip().split(",")]
    sheet_id = os.getenv("PAYCHECK_SHEET_ID")

    if not sheet_id:
        raise ValueError("PAYCHECK_SHEET_ID not set in environment")

    result = append_to_sheet(sheet_id, values)
    logger.info(f"Sheet updated: {result.get('updates', {}).get('updatedRows', 0)} row(s)")

    return f"Added paycheck entry: {values[0]} - Net Pay: ${values[9]}"


# ============================================================================
# Sheets Agent
# ============================================================================

def sheets_agent(user_message: str, sheet_id_context: str | None = None) -> str:
    """
    Specialized Google Sheets agent using Qwen 2.5 VL 7B Instruct.

    Implements Plan-Act pattern (Reflect happens in root agent):
    1. Plan: Qwen extracts function call from user's natural language request
    2. Act: Execute the google_sheets function and return raw result
    3. (Reflect: Root agent with full history formats the response)

    Uses minimal context (system prompt + tool definitions, no conversation history).

    Args:
        user_message: User's natural language sheets request
            Examples:
                "Add this paycheck data to the sheet: Nov 16-30, 1000.00, ..."
                "Read the data from cells A1 to C10"
                "Clear the range B2:D5"
        sheet_id_context: Optional sheet ID to use (can be env var name like PAYCHECK_SHEET_ID)

    Returns:
        Raw tool execution result string to be passed back to root agent
    """
    try:
        logger.info(f"[SHEETS] Sheets agent invoked: {user_message[:50]}...")

        # Load tool definitions
        tools_file = Path(__file__).parent.parent / "skills" / "sheets_tools.json"
        sheets_tools = json.loads(tools_file.read_text())

        # Build system prompt
        system_prompt = """You are a Google Sheets operations specialist for Friday AI assistant.

Your ONLY task is to translate user's natural language requests into precise Google Sheets function calls.

RULES:
1. For sheet_id, use environment variable names like PAYCHECK_SHEET_ID if mentioned in context
2. For ranges, use A1 notation (e.g., "Sheet1!A1:C10")
3. Column letters are case-insensitive (A, B, C, etc.)
4. Values should be arrays/lists of strings
5. Call exactly ONE function per request

OUTPUT FORMAT:
You MUST respond with ONLY a JSON object in this exact format:
{
  "name": "function_name",
  "arguments": {
    "param1": "value1",
    "param2": "value2"
  }
}

Do not include any explanatory text, markdown, or additional content. Only output the JSON object."""

        # Add context if provided
        if sheet_id_context:
            system_prompt += f"\n\nCONTEXT: Default sheet ID to use: {sheet_id_context}"

        # Format tools for Qwen
        tools_text = "\n\nAvailable functions:\n" + json.dumps(sheets_tools, indent=2)
        system_prompt += tools_text

        # Call Qwen via OpenRouter
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
            max_tokens=500,
            extra_body={"thinking": {"type": "disabled"}}
        )

        assistant_response = response.choices[0].message.content
        logger.info(f"[SHEETS] Qwen response: {assistant_response[:100]}...")

        # Parse function call
        try:
            if "{" in assistant_response and "}" in assistant_response:
                start_idx = assistant_response.find("{")
                end_idx = assistant_response.rfind("}") + 1
                function_call_json = assistant_response[start_idx:end_idx]
                function_call = json.loads(function_call_json)

                function_name = function_call.get("name")
                function_args = function_call.get("arguments", {})

                logger.info(f"[SHEETS] Extracted function call: {function_name}({function_args})")
            else:
                return assistant_response
        except json.JSONDecodeError:
            return assistant_response

        # Execute the appropriate google_sheets function
        tool_result = None

        if function_name == "append_row":
            sheet_id = function_args["sheet_id"]
            values = function_args["values"]
            sheet_name = function_args.get("sheet_name", "Sheet1")
            start_column = function_args.get("start_column", "A")

            # Resolve environment variable if needed
            if sheet_id.isupper() and "_" in sheet_id:
                sheet_id = os.getenv(sheet_id, sheet_id)

            logger.info(f"[SHEETS] Appending row to {sheet_id}, sheet '{sheet_name}'")

            # Calculate end column based on number of values
            end_col_index = ord(start_column.upper()) + len(values) - 1
            end_column = chr(end_col_index)

            result = append_to_sheet(
                sheet_id=sheet_id,
                values=values,
                sheet_name=sheet_name,
                start_column=start_column,
                end_column=end_column
            )

            updated_rows = result.get("updates", {}).get("updatedRows", 0)
            tool_result = f"Successfully appended row to sheet. Updated {updated_rows} row(s)."

        elif function_name == "read_range":
            sheet_id = function_args["sheet_id"]
            range_name = function_args["range_name"]

            # Resolve environment variable if needed
            if sheet_id.isupper() and "_" in sheet_id:
                sheet_id = os.getenv(sheet_id, sheet_id)

            logger.info(f"[SHEETS] Reading range {range_name} from {sheet_id}")

            values = read_range(sheet_id, range_name)

            if not values:
                tool_result = f"No data found in range {range_name}."
            else:
                tool_result = f"Read {len(values)} rows from {range_name}:\n" + json.dumps(values, indent=2)

        elif function_name == "update_range":
            sheet_id = function_args["sheet_id"]
            range_name = function_args["range_name"]
            values = function_args["values"]

            # Resolve environment variable if needed
            if sheet_id.isupper() and "_" in sheet_id:
                sheet_id = os.getenv(sheet_id, sheet_id)

            logger.info(f"[SHEETS] Updating range {range_name} in {sheet_id}")

            result = update_range(sheet_id, range_name, values)

            updated_cells = result.get("updatedCells", 0)
            tool_result = f"Successfully updated {updated_cells} cells in range {range_name}."

        elif function_name == "clear_range":
            sheet_id = function_args["sheet_id"]
            range_name = function_args["range_name"]

            # Resolve environment variable if needed
            if sheet_id.isupper() and "_" in sheet_id:
                sheet_id = os.getenv(sheet_id, sheet_id)

            logger.info(f"[SHEETS] Clearing range {range_name} in {sheet_id}")

            clear_range(sheet_id, range_name)

            tool_result = f"Successfully cleared range {range_name}."

        else:
            return f"Unknown function: {function_name}"

        # Return raw tool result
        logger.info(f"[SHEETS] Tool executed. Result: {tool_result[:100]}...")
        return tool_result

    except Exception as e:
        logger.error(f"[SHEETS] Sheets agent error: {e}", exc_info=True)
        return f"I encountered an error with that sheets operation: {str(e)}"
