"""Drive agent - Specialized tool-use agent with Google Drive API operations."""

import os
import json
import logging
import httpx
from pathlib import Path
from openai import OpenAI
from typing import Optional

from ._oauth import get_access_token
from ai.config import get_model, get_base_url, get_api_key

logger = logging.getLogger(__name__)


# ============================================================================
# Google Drive API Functions
# ============================================================================

def upload_file(
    file_bytes: bytes,
    filename: str,
    mime_type: str = "application/octet-stream",
    folder_id: str | None = None,
) -> dict:
    """
    Upload a file to Google Drive.

    Args:
        file_bytes: The file content as bytes.
        filename: The filename to use in Google Drive.
        mime_type: MIME type of the file (default: application/octet-stream).
        folder_id: Optional Google Drive folder ID to upload to.

    Returns:
        Dict containing file metadata from Google Drive API:
            - id: The file ID
            - name: The filename
            - mimeType: The MIME type

    Raises:
        httpx.HTTPStatusError: If upload fails.
    """
    access_token = get_access_token()

    metadata = {"name": filename}
    if folder_id:
        metadata["parents"] = [folder_id]

    # Build multipart upload body
    boundary = "----FridayUploadBoundary"
    metadata_json = json.dumps(metadata)

    body = (
        f"--{boundary}\r\n"
        f"Content-Type: application/json; charset=UTF-8\r\n\r\n"
        f"{metadata_json}\r\n"
        f"--{boundary}\r\n"
        f"Content-Type: {mime_type}\r\n\r\n"
    ).encode() + file_bytes + f"\r\n--{boundary}--".encode()

    response = httpx.post(
        "https://www.googleapis.com/upload/drive/v3/files",
        params={"uploadType": "multipart", "fields": "id,name,mimeType,webViewLink"},
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": f"multipart/related; boundary={boundary}",
        },
        content=body,
    )
    response.raise_for_status()

    result = response.json()
    logger.info(f"Uploaded to Google Drive: {filename} (ID: {result.get('id')})")

    return result


def upload_to_google_drive(pdf_bytes: bytes, filename: str) -> str:
    """
    Upload a PDF file to Google Drive (paycheck folder).

    This is a convenience wrapper for paycheck processing that uses
    the PAYCHECK_FOLDER_ID from environment variables.

    Args:
        pdf_bytes: The PDF file content as bytes
        filename: The filename to use in Google Drive

    Returns:
        Success message with file ID

    Raises:
        ValueError: If PAYCHECK_FOLDER_ID is not set
        httpx.HTTPStatusError: If upload fails
    """
    folder_id = os.getenv("PAYCHECK_FOLDER_ID")
    if not folder_id:
        raise ValueError("PAYCHECK_FOLDER_ID not set in environment")

    result = upload_file(
        file_bytes=pdf_bytes,
        filename=filename,
        mime_type="application/pdf",
        folder_id=folder_id,
    )

    return f"Uploaded {filename} to Google Drive"


def list_files(
    folder_id: str | None = None,
    mime_type: str | None = None,
    query: str | None = None,
    page_size: int = 100,
) -> list[dict]:
    """
    List files in Google Drive.

    Args:
        folder_id: Optional folder ID to list files from.
        mime_type: Optional MIME type filter.
        query: Optional custom query string (overrides folder_id and mime_type).
        page_size: Maximum number of files to return (default: 100).

    Returns:
        List of file metadata dicts with id, name, mimeType.
    """
    access_token = get_access_token()

    if query is None:
        query_parts = ["trashed = false"]
        if folder_id:
            query_parts.append(f"'{folder_id}' in parents")
        if mime_type:
            query_parts.append(f"mimeType = '{mime_type}'")
        query = " and ".join(query_parts)

    response = httpx.get(
        "https://www.googleapis.com/drive/v3/files",
        params={
            "q": query,
            "pageSize": page_size,
            "fields": "files(id,name,mimeType,createdTime,modifiedTime)",
        },
        headers={"Authorization": f"Bearer {access_token}"},
    )
    response.raise_for_status()

    return response.json().get("files", [])


def search_files(name: str, page_size: int = 10) -> list[dict]:
    """
    Search for files by name in Google Drive.

    Args:
        name: Search term to match against file names (partial match).
              If multiple words, searches for files matching ANY word.
        page_size: Maximum number of results (default: 10).

    Returns:
        List of file metadata dicts with id, name, mimeType, webViewLink.
    """
    access_token = get_access_token()

    # Split into words and filter out common words
    stop_words = {'from', 'my', 'the', 'a', 'an', 'in', 'on', 'drive', 'google', 'file', 'document', 'documents', 'folder'}
    words = [w.strip() for w in name.split() if w.strip().lower() not in stop_words and len(w.strip()) > 1]

    if not words:
        words = [name]  # Fallback to original if all words filtered

    # Build OR query for each word
    if len(words) == 1:
        query = f"name contains '{words[0]}' and trashed = false"
    else:
        # Search for any word match
        conditions = [f"name contains '{w}'" for w in words]
        query = f"({' or '.join(conditions)}) and trashed = false"

    logger.info(f"[DRIVE] Search query: {query}")

    response = httpx.get(
        "https://www.googleapis.com/drive/v3/files",
        params={
            "q": query,
            "pageSize": page_size,
            "fields": "files(id,name,mimeType,webViewLink,createdTime)",
        },
        headers={"Authorization": f"Bearer {access_token}"},
    )
    response.raise_for_status()

    return response.json().get("files", [])


def get_file_metadata(file_id: str) -> dict:
    """
    Get metadata for a specific file.

    Args:
        file_id: The Google Drive file ID.

    Returns:
        Dict with file metadata.
    """
    access_token = get_access_token()

    response = httpx.get(
        f"https://www.googleapis.com/drive/v3/files/{file_id}",
        params={"fields": "id,name,mimeType,size,createdTime,modifiedTime,webViewLink"},
        headers={"Authorization": f"Bearer {access_token}"},
    )
    response.raise_for_status()

    return response.json()


def download_file(file_id: str) -> bytes:
    """
    Download a file from Google Drive.

    Args:
        file_id: The Google Drive file ID.

    Returns:
        File content as bytes.
    """
    access_token = get_access_token()

    response = httpx.get(
        f"https://www.googleapis.com/drive/v3/files/{file_id}",
        params={"alt": "media"},
        headers={"Authorization": f"Bearer {access_token}"},
    )
    response.raise_for_status()

    return response.content


def delete_file(file_id: str) -> bool:
    """
    Delete a file from Google Drive.

    Args:
        file_id: The Google Drive file ID.

    Returns:
        True if deletion was successful.
    """
    access_token = get_access_token()

    response = httpx.delete(
        f"https://www.googleapis.com/drive/v3/files/{file_id}",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    response.raise_for_status()

    logger.info(f"Deleted file from Google Drive: {file_id}")
    return True


def create_folder(name: str, parent_folder_id: str | None = None) -> dict:
    """
    Create a folder in Google Drive.

    Args:
        name: The folder name.
        parent_folder_id: Optional parent folder ID.

    Returns:
        Dict with folder metadata including id.
    """
    access_token = get_access_token()

    metadata = {
        "name": name,
        "mimeType": "application/vnd.google-apps.folder",
    }
    if parent_folder_id:
        metadata["parents"] = [parent_folder_id]

    response = httpx.post(
        "https://www.googleapis.com/drive/v3/files",
        params={"fields": "id,name,mimeType"},
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
        json=metadata,
    )
    response.raise_for_status()

    result = response.json()
    logger.info(f"Created folder in Google Drive: {name} (ID: {result.get('id')})")

    return result


# ============================================================================
# Drive Agent
# ============================================================================

def drive_agent(user_message: str, file_bytes: bytes | None = None, folder_id_context: str | None = None) -> str:
    """
    Specialized Google Drive agent using Qwen 2.5 VL 7B Instruct.

    Implements Plan-Act pattern (Reflect happens in root agent):
    1. Plan: Qwen extracts function call from user's natural language request
    2. Act: Execute the google_drive function and return raw result
    3. (Reflect: Root agent with full history formats the response)

    Uses minimal context (system prompt + tool definitions, no conversation history).

    Args:
        user_message: User's natural language drive request
            Examples:
                "Upload this PDF to the paycheck folder"
                "List files in folder XYZ"
                "Create a new folder called Reports"
        file_bytes: Optional file content for upload operations
        folder_id_context: Optional folder ID to use (can be env var name like PAYCHECK_FOLDER_ID)

    Returns:
        Raw tool execution result string to be passed back to root agent
    """
    try:
        logger.info(f"[DRIVE] Drive agent invoked: {user_message[:50]}...")

        # Load tool definitions
        tools_file = Path(__file__).parent.parent / "skills" / "drive_tools.json"
        drive_tools = json.loads(tools_file.read_text())

        # Build system prompt
        system_prompt = """You are a Google Drive operations specialist for Friday AI assistant.

Your ONLY task is to translate user's natural language requests into precise Google Drive function calls.

RULES:
1. For folder_id, use environment variable names like PAYCHECK_FOLDER_ID if mentioned in context
2. File IDs are long alphanumeric strings from Google Drive
3. Common MIME types: application/pdf, image/png, image/jpeg, text/plain
4. Call exactly ONE function per request

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
        if folder_id_context:
            system_prompt += f"\n\nCONTEXT: Default folder ID to use: {folder_id_context}"

        # Format tools for Qwen
        tools_text = "\n\nAvailable functions:\n" + json.dumps(drive_tools, indent=2)
        system_prompt += tools_text

        # Call tool model via OpenRouter
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
        logger.info(f"[DRIVE] Qwen response: {assistant_response[:100]}...")

        # Parse function call
        try:
            if "{" in assistant_response and "}" in assistant_response:
                start_idx = assistant_response.find("{")
                end_idx = assistant_response.rfind("}") + 1
                function_call_json = assistant_response[start_idx:end_idx]
                function_call = json.loads(function_call_json)

                function_name = function_call.get("name")
                function_args = function_call.get("arguments", {})

                logger.info(f"[DRIVE] Extracted function call: {function_name}({function_args})")
            else:
                return assistant_response
        except json.JSONDecodeError:
            return assistant_response

        # Execute the appropriate google_drive function
        tool_result = None

        if function_name == "upload_file":
            filename = function_args["filename"]
            mime_type = function_args.get("mime_type", "application/octet-stream")
            folder_id = function_args.get("folder_id")

            # Resolve environment variable if needed
            if folder_id and folder_id.isupper() and "_" in folder_id:
                folder_id = os.getenv(folder_id, folder_id)

            if not file_bytes:
                return "Error: No file content provided for upload. File must be attached to the request."

            logger.info(f"[DRIVE] Uploading file: {filename} ({mime_type})")

            result = upload_file(
                file_bytes=file_bytes,
                filename=filename,
                mime_type=mime_type,
                folder_id=folder_id
            )

            file_id = result.get("id")
            web_link = result.get("webViewLink", "")
            tool_result = f"Successfully uploaded '{filename}' to Google Drive. File ID: {file_id}"
            if web_link:
                tool_result += f"\nView at: {web_link}"

        elif function_name == "search_files":
            name = function_args["name"]
            page_size = function_args.get("page_size", 10)

            logger.info(f"[DRIVE] Searching for files matching: {name}")

            files = search_files(name=name, page_size=page_size)

            if not files:
                tool_result = f"No files found matching '{name}'."
            else:
                file_list = []
                for f in files:
                    web_link = f.get("webViewLink", "")
                    file_list.append(f"- {f['name']}\n  View: {web_link}")
                tool_result = f"Found {len(files)} file(s) matching '{name}':\n" + "\n".join(file_list)

        elif function_name == "list_files":
            folder_id = function_args.get("folder_id")
            mime_type = function_args.get("mime_type")
            page_size = function_args.get("page_size", 100)

            # Resolve environment variable if needed
            if folder_id and folder_id.isupper() and "_" in folder_id:
                folder_id = os.getenv(folder_id, folder_id)

            logger.info(f"[DRIVE] Listing files (folder={folder_id}, type={mime_type})")

            files = list_files(
                folder_id=folder_id,
                mime_type=mime_type,
                page_size=page_size
            )

            if not files:
                tool_result = "No files found matching criteria."
            else:
                file_list = []
                entity_refs = []
                for f in files:
                    file_list.append(f"- {f['name']} (ID: {f['id']}, Type: {f.get('mimeType', 'unknown')})")
                    entity_refs.append({
                        "type": "drive_file",
                        "id": f["id"],
                        "description": f["name"],
                        "metadata": {"mime_type": f.get("mimeType", ""), "created": f.get("createdTime", "")}
                    })
                tool_result = f"Found {len(files)} file(s):\n" + "\n".join(file_list)

        elif function_name == "get_file_metadata":
            file_id = function_args["file_id"]

            logger.info(f"[DRIVE] Getting metadata for file {file_id}")

            metadata = get_file_metadata(file_id)

            tool_result = f"File metadata:\n" + json.dumps(metadata, indent=2)

        elif function_name == "delete_file":
            file_id = function_args["file_id"]

            logger.info(f"[DRIVE] Deleting file {file_id}")

            delete_file(file_id)

            tool_result = f"Successfully deleted file with ID: {file_id}"

        elif function_name == "create_folder":
            name = function_args["name"]
            parent_folder_id = function_args.get("parent_folder_id")

            # Resolve environment variable if needed
            if parent_folder_id and parent_folder_id.isupper() and "_" in parent_folder_id:
                parent_folder_id = os.getenv(parent_folder_id, parent_folder_id)

            logger.info(f"[DRIVE] Creating folder: {name}")

            result = create_folder(name, parent_folder_id)

            folder_id = result.get("id")
            tool_result = f"Successfully created folder '{name}'. Folder ID: {folder_id}"

        elif function_name == "download_file":
            file_id = function_args["file_id"]

            logger.info(f"[DRIVE] Downloading file {file_id}")

            # Note: This returns bytes, which won't be useful in text response
            # Better to return metadata about the download instead
            file_bytes_result = download_file(file_id)

            tool_result = f"Successfully downloaded file (ID: {file_id}). File size: {len(file_bytes_result)} bytes.\nNote: File content is binary and cannot be displayed as text."

        else:
            return f"Unknown function: {function_name}"

        # Return raw tool result
        logger.info(f"[DRIVE] Tool executed. Result: {tool_result[:100]}...")
        return tool_result

    except Exception as e:
        logger.error(f"[DRIVE] Drive agent error: {e}", exc_info=True)
        return f"I encountered an error with that drive operation: {str(e)}"
