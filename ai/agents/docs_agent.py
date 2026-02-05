"""
Docs agent - PDF document reading and analysis.

Phase 1: PDF support using pymupdf4llm
Future: DOCX (python-docx), Google Docs (Docs API)
"""

import os
import re
import json
import logging
import uuid
from pathlib import Path
from io import BytesIO
from dotenv import load_dotenv
from openai import OpenAI
import pymupdf4llm
from ai.config import get_model, get_base_url, get_api_key
import pymupdf

load_dotenv()

logger = logging.getLogger(__name__)

# Temp directory for PDF processing
TMP_DIR = Path(__file__).parent.parent.parent / "tmp"
TMP_DIR.mkdir(exist_ok=True)


# ============================================================================
# PDF Service Functions
# ============================================================================

def extract_pdf_text(pdf_path: str) -> str:
    """
    Extract text from PDF file path using pymupdf4llm.

    This is the core PDF extraction function used by other services.

    Args:
        pdf_path: Path to PDF file

    Returns:
        Extracted text in markdown format
    """
    return pymupdf4llm.to_markdown(pdf_path)


def read_pdf(pdf_bytes: bytes) -> str:
    """
    Extract text content from PDF using pymupdf4llm.

    Args:
        pdf_bytes: PDF file content as bytes

    Returns:
        Extracted text in markdown format
    """
    try:
        logger.info(f"[DOCS] Reading PDF, size: {len(pdf_bytes)} bytes")

        # Save to temp file for pymupdf4llm
        temp_path = TMP_DIR / f"{uuid.uuid4()}.pdf"
        temp_path.write_bytes(pdf_bytes)

        try:
            # Extract text as markdown
            text = pymupdf4llm.to_markdown(str(temp_path))
            logger.info(f"[DOCS] Extracted {len(text)} characters from PDF")
            return text
        finally:
            # Cleanup temp file
            if temp_path.exists():
                temp_path.unlink()

    except Exception as e:
        logger.error(f"[DOCS] PDF read error: {e}", exc_info=True)
        return f"Error reading PDF: {str(e)}"


def search_pdf(text: str, query: str, case_sensitive: bool = False) -> str:
    """
    Search for text within extracted PDF content.

    Args:
        text: Extracted PDF text
        query: Search query
        case_sensitive: Whether search is case sensitive

    Returns:
        Formatted search results with context
    """
    try:
        logger.info(f"[DOCS] Searching PDF for: '{query}' (case_sensitive={case_sensitive})")

        if not text:
            return "No document content to search."

        lines = text.split('\n')
        matches = []

        flags = 0 if case_sensitive else re.IGNORECASE
        pattern = re.compile(re.escape(query), flags)

        for i, line in enumerate(lines, 1):
            if pattern.search(line):
                # Get context (line before and after)
                context_start = max(0, i - 2)
                context_end = min(len(lines), i + 1)
                context = '\n'.join(lines[context_start:context_end])

                matches.append({
                    "line": i,
                    "match": line.strip(),
                    "context": context.strip()
                })

        if not matches:
            return f"No matches found for '{query}'."

        # Format results
        result = f"Found {len(matches)} match(es) for '{query}':\n\n"
        for i, m in enumerate(matches[:10], 1):  # Limit to 10 matches
            result += f"**Match {i}** (line {m['line']}):\n"
            result += f"```\n{m['context']}\n```\n\n"

        if len(matches) > 10:
            result += f"... and {len(matches) - 10} more matches."

        logger.info(f"[DOCS] Found {len(matches)} matches")
        return result

    except Exception as e:
        logger.error(f"[DOCS] Search error: {e}", exc_info=True)
        return f"Error searching document: {str(e)}"


def get_pdf_info(pdf_bytes: bytes) -> str:
    """
    Get PDF metadata and structure information.

    Args:
        pdf_bytes: PDF file content as bytes

    Returns:
        Formatted metadata string
    """
    try:
        logger.info(f"[DOCS] Getting PDF info, size: {len(pdf_bytes)} bytes")

        # Open PDF with pymupdf
        doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")

        # Basic metadata
        page_count = len(doc)
        metadata = doc.metadata

        # Extract headings from TOC if available
        toc = doc.get_toc()
        headings = [item[1] for item in toc[:20]] if toc else []

        # Build result
        result = f"""PDF Information:

**Pages**: {page_count}
**Title**: {metadata.get('title', 'Unknown')}
**Author**: {metadata.get('author', 'Unknown')}
**Subject**: {metadata.get('subject', 'N/A')}
**Creator**: {metadata.get('creator', 'Unknown')}
**Producer**: {metadata.get('producer', 'Unknown')}
**Creation Date**: {metadata.get('creationDate', 'Unknown')}
**Modification Date**: {metadata.get('modDate', 'Unknown')}"""

        if headings:
            result += f"\n\n**Table of Contents** ({len(toc)} items):\n"
            for h in headings:
                result += f"- {h}\n"
            if len(toc) > 20:
                result += f"... and {len(toc) - 20} more items"

        doc.close()
        logger.info(f"[DOCS] PDF info retrieved: {page_count} pages")
        return result

    except Exception as e:
        logger.error(f"[DOCS] PDF info error: {e}", exc_info=True)
        return f"Error getting PDF info: {str(e)}"


# ============================================================================
# Docs Agent - Tool-use specialist
# ============================================================================

def docs_agent(
    user_message: str,
    doc_bytes: bytes = None,
    doc_filename: str = None
) -> str:
    """
    Specialized document agent using GPT OSS 20B for tool extraction.

    Implements Plan-Act pattern (Reflect happens in root agent):
    1. Plan: GPT OSS 20B extracts function call from user's request
    2. Act: Execute the document service function and return raw result
    3. (Reflect: Root agent with full history formats the response)

    Args:
        user_message: User's natural language document request
        doc_bytes: Optional uploaded document content
        doc_filename: Optional document filename

    Returns:
        Raw tool execution result string to be passed back to root agent
    """
    try:
        logger.info(f"[DOCS] Agent invoked: {user_message[:50]}...")

        # Check if we have a document
        if not doc_bytes:
            return "No document provided. Please upload a PDF file."

        # Detect file type
        if doc_filename:
            file_ext = Path(doc_filename).suffix.lower()
        else:
            # Try to detect from magic bytes
            if pdf_bytes[:4] == b'%PDF':
                file_ext = '.pdf'
            else:
                file_ext = '.pdf'  # Default to PDF

        if file_ext != '.pdf':
            return f"Currently only PDF files are supported. Received: {file_ext}"

        # Extract PDF content upfront for search operations
        pdf_text = read_pdf(doc_bytes)

        # Load tool definitions
        tools_file = Path(__file__).parent.parent / "skills" / "docs_tools.json"
        docs_tools = json.loads(tools_file.read_text())

        # Build system prompt
        system_prompt = """You are a document analysis specialist for Friday AI assistant.

Your ONLY task is to translate user's natural language requests into precise function calls.

CONTEXT: A PDF document has been uploaded and is ready for analysis.

RULES:
1. Use read_pdf to extract and return the full text content
2. Use search_pdf to find specific text, keywords, or phrases in the document
3. Use get_pdf_info to get metadata like page count, title, author, table of contents
4. Call exactly ONE function per request

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
        tools_text = "\n\nAvailable functions:\n" + json.dumps(docs_tools, indent=2)
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
            max_tokens=300
        )

        assistant_response = response.choices[0].message.content
        logger.info(f"[DOCS] LLM response: {assistant_response[:100]}...")

        # Parse function call from response
        try:
            if "{" in assistant_response and "}" in assistant_response:
                start_idx = assistant_response.find("{")
                end_idx = assistant_response.rfind("}") + 1
                function_call_json = assistant_response[start_idx:end_idx]
                function_call = json.loads(function_call_json)

                function_name = function_call.get("name")
                function_args = function_call.get("arguments", {})

                logger.info(f"[DOCS] Extracted function call: {function_name}({function_args})")
            else:
                # No function call detected, default to read_pdf
                function_name = "read_pdf"
                function_args = {}
        except json.JSONDecodeError:
            # Fallback to read_pdf
            function_name = "read_pdf"
            function_args = {}

        # Execute the appropriate function
        tool_result = None

        if function_name == "read_pdf":
            tool_result = pdf_text  # Already extracted above

        elif function_name == "search_pdf":
            query = function_args.get("query", "")
            case_sensitive = function_args.get("case_sensitive", False)
            if not query:
                tool_result = "No search query provided."
            else:
                tool_result = search_pdf(pdf_text, query, case_sensitive)

        elif function_name == "get_pdf_info":
            tool_result = get_pdf_info(doc_bytes)

        else:
            tool_result = f"Unknown function: {function_name}"

        logger.info(f"[DOCS] Tool executed. Result length: {len(tool_result)} chars")
        return tool_result

    except Exception as e:
        logger.error(f"[DOCS] Agent error: {e}", exc_info=True)
        return f"Error processing document: {str(e)}"
