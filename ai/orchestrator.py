from typing import TypedDict, Annotated
import operator
import re
import logging
from langgraph.graph import StateGraph, START, END
from ai.agents import root_agent, web_agent, calendar_agent, sheets_agent, drive_agent, maps_agent, docs_agent
from ai.agents.drive_agent import search_files

logger = logging.getLogger(__name__)

# Pattern to strip chain-of-thought reasoning from model output
# Matches text like "Okay, so the user is asking..." or "<think>...</think>" before the actual response
_COT_PATTERNS = [
    re.compile(r"^<think>.*?</think>\s*", re.DOTALL),  # <think>...</think> blocks
    re.compile(r"^(?:Okay|Ok|Alright|So|Let me|Hmm),?\s+(?:so\s+)?(?:the user|Apurva|I need to|let me|I'll|looking at).*?\n", re.IGNORECASE),  # Reasoning preamble
]


def _strip_cot(text: str) -> str:
    """Strip chain-of-thought reasoning from model output."""
    cleaned = text
    for pattern in _COT_PATTERNS:
        cleaned = pattern.sub("", cleaned)
    return cleaned.strip()


class AgentState(TypedDict):
    messages: Annotated[list[dict], operator.add]
    original_prompt: str | None
    pdf_bytes: bytes | None
    pdf_filename: str | None
    tool_result: str | None  # For passing tool execution results back to root
    pending_tool_call: dict | None  # Structured tool call from root: {name, arguments}


# Routing map: tool_call function name -> graph node name
TOOL_ROUTING = {
    "web_search": "web",
    "open_url": "web",
    "calendar": "calendar",
    "maps": "maps",
    "docs": "docs",
}


class Friday:
    # Registry mapping handler names (from skill frontmatter) to method names
    SKILL_HANDLERS = {
        "paycheck": "_execute_paycheck_skill",
        "drive_fetch": "_execute_drive_fetch_skill",
    }

    def __init__(self):
        # Load context (triggers skill discovery)
        import ai.context

        self.builder = StateGraph(AgentState)
        self.builder.add_node("root", self.root_node)
        self.builder.add_node("web", self.web_node)  # Web operations (search, URL)
        self.builder.add_node("skill", self.skill_node)  # Generic skill execution node
        self.builder.add_node("calendar", self.calendar_node)
        self.builder.add_node("maps", self.maps_node)  # Maps operations (places, directions, transit)
        self.builder.add_node("docs", self.docs_node)  # Document operations (PDF read, search)

        self.builder.add_edge(START, "root")  # All input goes to root
        self.builder.add_conditional_edges("root", self.should_continue)
        self.builder.add_edge("web", "root")  # Web operations return to root for reflection
        self.builder.add_edge("skill", "root")  # Skills return to root for reflection
        self.builder.add_edge("calendar", "root")  # Calendar returns to root for reflection
        self.builder.add_conditional_edges("maps", self.maps_should_continue)  # Conditional: browser->END, place->root
        self.builder.add_edge("docs", "root")  # Docs operations return to root for reflection
        self.app = self.builder.compile()

    def invoke_paycheck(self, pdf_bytes: bytes, pdf_filename: str) -> dict:
        """Directly invoke paycheck processing, bypassing root agent routing.

        This ensures the paycheck skill always executes regardless of which
        model the root agent uses for routing decisions.
        """
        state = {
            "messages": [{"role": "user", "content": "Process this paycheck"}],
            "original_prompt": "Process this paycheck",
            "pdf_bytes": pdf_bytes,
            "pdf_filename": pdf_filename,
            "tool_result": None,
            "pending_tool_call": {"name": "skill_process_paycheck", "arguments": {"request": "Process this paycheck"}},
        }

        # Execute paycheck skill directly
        skill_result = self._execute_paycheck_skill(state)

        # Run reflection pass to format the result
        messages = state["messages"] + skill_result["messages"]
        response = root_agent(messages, use_tools=False)
        content = _strip_cot(response["content"])
        messages.append({"role": "assistant", "content": content})

        return {"messages": messages}

    def root_node(self, state: AgentState):
        try:
            # Reflection pass: tool_result is set (even if empty string) → format results, don't offer tools
            is_reflection = state.get("tool_result") is not None
            response = root_agent(state["messages"], use_tools=not is_reflection)

            # Strip chain-of-thought leakage from model output
            content = _strip_cot(response["content"])
            new_message = {"role": "assistant", "content": content}

            if is_reflection:
                logger.info("Reflection pass: formatted tool result into response")
                return {"messages": [new_message], "tool_result": None, "pending_tool_call": None}

            # First pass: check for tool call routing
            tool_calls = response.get("tool_calls", [])
            tool_call = tool_calls[0] if tool_calls else None
            if tool_call:
                logger.info(f"[ROUTING] Tool call detected: {tool_call['name']}({tool_call['arguments']})")
                if len(tool_calls) > 1:
                    logger.info(f"[ROUTING] {len(tool_calls) - 1} additional tool call(s) ignored (single-tool-per-turn)")

            return {"messages": [new_message], "pending_tool_call": tool_call}
        except Exception as e:
            error_msg = {"role": "assistant", "content": f"Error: {str(e)}"}
            return {"messages": [error_msg], "pending_tool_call": None}

    def web_node(self, state: AgentState):
        """Handle web operations (search, URL) using structured tool call args."""
        try:
            tool_call = state.get("pending_tool_call", {})
            name = tool_call.get("name", "")
            args = tool_call.get("arguments", {})

            if name == "open_url":
                command = args.get("url", "")
                logger.info(f"[WEB] Opening URL: {command[:50]}...")
            else:
                command = args.get("query", "")
                logger.info(f"[WEB] Searching: {command[:50]}...")

            # Execute web operation
            tool_result = web_agent(command)

            # Add tool result as system message for root
            tool_message = {
                "role": "system",
                "content": f"[TOOL RESULT - Web Operation]\n{tool_result}"
            }

            return {"messages": [tool_message], "tool_result": tool_result}

        except Exception as e:
            logger.error(f"[WEB] Error: {e}", exc_info=True)
            error_msg = {"role": "system", "content": f"[TOOL ERROR - Web] {str(e)}"}
            return {"messages": [error_msg], "tool_result": f"Error: {str(e)}"}

    def skill_node(self, state: AgentState):
        """
        Generic skill execution node.

        Detects which skill to execute based on tool_call name (skill_*),
        dispatches to registered Python handler or generic LLM-based execution.
        """
        try:
            from ai.context_loader import load_skill, get_skill_metadata

            tool_call = state.get("pending_tool_call", {})
            tool_name = tool_call.get("name", "")
            args = tool_call.get("arguments", {})

            # Extract skill name from tool call (e.g., "skill_process_paycheck" -> "PROCESS_PAYCHECK")
            skill_name_lower = tool_name.replace("skill_", "", 1)

            # Find matching skill in registry
            from ai.context_loader import _SKILL_REGISTRY
            matched_skill = None
            for trigger, info in _SKILL_REGISTRY.items():
                if info["name"].lower() == skill_name_lower:
                    matched_skill = info
                    break

            if not matched_skill:
                error_msg = {"role": "system", "content": f"[SKILL ERROR] Unknown skill: {tool_name}"}
                return {"messages": [error_msg], "tool_result": f"Error: Unknown skill: {tool_name}"}

            skill_name = matched_skill["name"]
            logger.info(f"[SKILL] Executing: {skill_name}")

            # Check for registered Python handler
            handler_name = matched_skill.get("handler")
            if handler_name and handler_name in self.SKILL_HANDLERS:
                handler_method = getattr(self, self.SKILL_HANDLERS[handler_name])
                return handler_method(state)

            # Generic execution: inject skill content as LLM context
            metadata = get_skill_metadata(skill_name)
            return self._execute_generic_skill(state, skill_name, metadata)

        except Exception as e:
            logger.error(f"[SKILL] Execution error: {e}", exc_info=True)
            error_msg = {"role": "system", "content": f"[SKILL ERROR] {str(e)}"}
            return {"messages": [error_msg], "tool_result": f"Error: {str(e)}"}

    def _execute_paycheck_skill(self, state: AgentState):
        """
        Execute paycheck processing workflow using 3 agents:
        1. docs_agent - Extract PDF text
        2. sheets_agent - Append paycheck data to sheet
        3. drive_agent - Upload PDF to Drive
        """
        try:
            from ai.agents.docs_agent import read_pdf

            pdf_bytes = state.get("pdf_bytes")
            pdf_filename = state.get("pdf_filename")

            if not pdf_bytes:
                return {
                    "messages": [{"role": "system", "content": "[SKILL ERROR] No PDF file provided"}],
                    "tool_result": "Error: No PDF file provided"
                }

            # Step 1: Extract PDF text using docs_agent
            logger.info(f"[SKILL:PAYCHECK] Step 1/3: Extracting PDF text via docs_agent...")
            pdf_text = read_pdf(pdf_bytes)
            logger.info(f"[SKILL:PAYCHECK] PDF extracted: {len(pdf_text)} chars")

            # Step 2: Parse paycheck data from extracted text
            # Use root_agent to parse the paycheck into CSV format (text-only, no tools needed)
            logger.info(f"[SKILL:PAYCHECK] Step 2/3: Parsing paycheck data...")
            parse_prompt = f"""Parse this paycheck and extract as CSV with these columns:
Pay Period,Gross Pay,Social Security,Medicare,Federal Income Tax,NY Income Tax,NY PFL,NY Disability,Total Deductions,Net Pay,Hours

Rules:
- Format pay period as "Mon DD - Mon DD" (e.g., "Nov 16 - Nov 30")
- If Social Security/Medicare show "Exempt", use 0.00
- Total Deductions = sum of tax withholdings only

Respond with ONLY the CSV data row (no headers, no explanation).

Paycheck text:
{pdf_text[:8000]}"""  # Limit text length

            csv_result = root_agent([{"role": "user", "content": parse_prompt}])
            csv_response = csv_result["content"]

            # Extract CSV values from response
            csv_line = csv_response.strip().split("\n")[-1]  # Last line is the data
            values = [v.strip() for v in csv_line.split(",")]
            logger.info(f"[SKILL:PAYCHECK] Parsed {len(values)} CSV values: {values[:3]}...")

            # Step 3: Use sheets_agent to append row to paycheck sheet
            logger.info(f"[SKILL:PAYCHECK] Step 3/3: Updating Google Sheet via sheets_agent...")
            sheets_request = (
                f"Append this row to sheet PAYCHECK_SHEET_ID, "
                f"tab name 'vaspian', starting at column B: {', '.join(values)}"
            )
            sheet_response = sheets_agent(sheets_request, sheet_id_context="PAYCHECK_SHEET_ID")
            logger.info(f"[SKILL:PAYCHECK] Sheet updated: {sheet_response[:50]}...")

            # Step 4: Use drive_agent to upload PDF
            drive_response = ""
            if pdf_bytes and pdf_filename:
                logger.info(f"[SKILL:PAYCHECK] Uploading PDF to Drive via drive_agent...")
                drive_request = f"Upload this PDF file: {pdf_filename}"
                drive_response = "\n" + drive_agent(
                    drive_request,
                    file_bytes=pdf_bytes,
                    folder_id_context="PAYCHECK_FOLDER_ID"
                )
                logger.info(f"[SKILL:PAYCHECK] PDF uploaded: {drive_response[:50]}...")

            # Combine results
            tool_result = f"Paycheck processed successfully!\n\n"
            tool_result += f"1. PDF parsed: {len(pdf_text)} chars extracted\n"
            tool_result += f"2. Sheet updated: {sheet_response}\n"
            tool_result += f"3. PDF uploaded: {drive_response}" if drive_response else ""

            logger.info(f"[SKILL:PAYCHECK] Processing complete!")

            tool_message = {
                "role": "system",
                "content": f"[TOOL RESULT - Paycheck Processing]\n{tool_result}"
            }

            return {"messages": [tool_message], "tool_result": tool_result}

        except Exception as e:
            logger.error(f"[SKILL:PAYCHECK] Error: {e}", exc_info=True)
            error_msg = {"role": "system", "content": f"[TOOL ERROR - Paycheck] {str(e)}"}
            return {"messages": [error_msg], "tool_result": f"Error: {str(e)}"}

    def _execute_drive_fetch_skill(self, state: AgentState):
        """Execute drive fetch workflow as defined in drive_fetch.md"""
        try:
            tool_call = state.get("pending_tool_call", {})
            search_term = tool_call.get("arguments", {}).get("request", "")

            logger.info(f"[SKILL:DRIVE_FETCH] Searching for: {search_term}")

            # Search for files directly
            files = search_files(search_term)

            if not files:
                tool_result = f"No files found matching '{search_term}'."
            else:
                # Build result with file links
                file_list = []
                for f in files:
                    web_link = f.get("webViewLink", "")
                    file_list.append(f"- {f['name']}\n  View: {web_link}")
                tool_result = f"Found {len(files)} file(s) matching '{search_term}':\n" + "\n".join(file_list)

                # Auto-open first result in browser
                first_link = files[0].get("webViewLink")
                if first_link:
                    logger.info(f"[SKILL:DRIVE_FETCH] Opening first result in browser: {first_link}")
                    web_agent(f"Open this URL in browser: {first_link}")
                    tool_result += f"\n\nOpened '{files[0]['name']}' in browser."

            logger.info(f"[SKILL:DRIVE_FETCH] Search complete: {tool_result[:100]}...")

            # Add tool result as a system message for root agent to see
            tool_message = {
                "role": "system",
                "content": f"[TOOL RESULT - Drive Fetch]\n{tool_result}"
            }

            return {"messages": [tool_message], "tool_result": tool_result}

        except Exception as e:
            logger.error(f"[SKILL:DRIVE_FETCH] Error: {e}", exc_info=True)
            error_msg = {"role": "system", "content": f"[TOOL ERROR - Drive Fetch] {str(e)}"}
            return {"messages": [error_msg], "tool_result": f"Error: {str(e)}"}

    def _execute_generic_skill(self, state: AgentState, skill_name: str, metadata: dict | None):
        """
        Execute a skill without a dedicated Python handler.

        Passes the skill content and user message to root agent for LLM-based execution.
        Used for simple skills that don't need API calls.
        """
        try:
            from ai.context_loader import load_skill

            skill_content = load_skill(skill_name)
            if not skill_content:
                error_msg = {"role": "system", "content": f"[SKILL ERROR] Could not load skill: {skill_name}"}
                return {"messages": [error_msg], "tool_result": f"Error: Skill not found: {skill_name}"}

            tool_call = state.get("pending_tool_call", {})
            request = tool_call.get("arguments", {}).get("request", "")
            result_label = metadata.get("result_label", skill_name) if metadata else skill_name

            logger.info(f"[SKILL] Generic execution for {skill_name}: {request[:50]}...")

            result = root_agent([
                {"role": "system", "content": f"Follow these skill instructions:\n\n{skill_content}"},
                {"role": "user", "content": request}
            ])
            response = result["content"]

            tool_message = {
                "role": "system",
                "content": f"[TOOL RESULT - {result_label}]\n{response}"
            }
            return {"messages": [tool_message], "tool_result": response}

        except Exception as e:
            logger.error(f"[SKILL] Generic execution error: {e}", exc_info=True)
            error_msg = {"role": "system", "content": f"[SKILL ERROR] {str(e)}"}
            return {"messages": [error_msg], "tool_result": f"Error: {str(e)}"}

    def calendar_node(self, state: AgentState):
        """Handle calendar operations using structured tool call args."""
        try:
            tool_call = state.get("pending_tool_call", {})
            command = tool_call.get("arguments", {}).get("request", "")

            logger.info(f"[CALENDAR] Processing: {command[:50]}...")

            # Execute calendar operation and get raw result
            tool_result = calendar_agent(command)
            logger.info(f"Calendar tool result: {tool_result[:100]}...")

            # Add tool result as a system message for root agent to see
            tool_message = {
                "role": "system",
                "content": f"[TOOL RESULT - Calendar Operation]\n{tool_result}"
            }

            return {"messages": [tool_message], "tool_result": tool_result}
        except Exception as e:
            error_msg = {"role": "system", "content": f"[TOOL ERROR - Calendar] {str(e)}"}
            return {"messages": [error_msg], "tool_result": f"Error: {str(e)}"}

    def maps_node(self, state: AgentState):
        """Handle maps operations (places, directions, transit) using structured tool call args."""
        try:
            tool_call = state.get("pending_tool_call", {})
            command = tool_call.get("arguments", {}).get("request", "")

            logger.info(f"[MAPS] Processing: {command[:50]}...")

            # Execute maps operation
            tool_result = maps_agent(command)

            # Check if browser was opened (2-stage: skip reflection, return directly)
            if tool_result.startswith("[BROWSER]"):
                # Remove the marker and return as final assistant message
                final_message = tool_result.replace("[BROWSER] ", "")
                logger.info(f"[MAPS] Browser operation - returning directly to user")
                return {
                    "messages": [{"role": "assistant", "content": final_message}],
                    "tool_result": tool_result  # Set to trigger END in should_continue
                }

            # For place info, continue to root for reflection (3-stage)
            tool_message = {
                "role": "system",
                "content": f"[TOOL RESULT - Maps Operation]\n{tool_result}"
            }

            return {"messages": [tool_message], "tool_result": tool_result}

        except Exception as e:
            logger.error(f"[MAPS] Error: {e}", exc_info=True)
            error_msg = {"role": "system", "content": f"[TOOL ERROR - Maps] {str(e)}"}
            return {"messages": [error_msg], "tool_result": f"Error: {str(e)}"}

    def docs_node(self, state: AgentState):
        """Handle document operations (PDF read, search, info) using structured tool call args."""
        try:
            tool_call = state.get("pending_tool_call", {})
            command = tool_call.get("arguments", {}).get("request", "")

            logger.info(f"[DOCS] Processing: {command[:50]}...")

            # Get document bytes from state (reuse pdf_bytes for now)
            doc_bytes = state.get("pdf_bytes")
            doc_filename = state.get("pdf_filename")

            # Execute docs operation
            tool_result = docs_agent(
                command,
                doc_bytes=doc_bytes,
                doc_filename=doc_filename
            )

            # Add tool result as system message for root
            tool_message = {
                "role": "system",
                "content": f"[TOOL RESULT - Docs Operation]\n{tool_result}"
            }

            return {"messages": [tool_message], "tool_result": tool_result}

        except Exception as e:
            logger.error(f"[DOCS] Error: {e}", exc_info=True)
            error_msg = {"role": "system", "content": f"[TOOL ERROR - Docs] {str(e)}"}
            return {"messages": [error_msg], "tool_result": f"Error: {str(e)}"}

    def maps_should_continue(self, state: AgentState):
        """Route from maps node: browser operations go to END, place info goes to root."""
        tool_result = state.get("tool_result", "")

        # Browser operations (directions/transit) go directly to END
        if tool_result.startswith("[BROWSER]"):
            logger.info("[ROUTING] Maps browser operation - ending (2-stage)")
            return END

        # Place info operations go to root for reflection (3-stage)
        logger.info("[ROUTING] Maps place info - going to root for reflection")
        return "root"

    def should_continue(self, state: AgentState):
        """Route from root agent based on structured tool calls."""
        if not state.get("messages"):
            return END

        # Check if we just processed a tool result (prevent loops)
        # Use 'is not None' so even empty string results trigger END
        if state.get("tool_result") is not None:
            return END

        # Check for pending tool call from root agent
        tool_call = state.get("pending_tool_call")
        if not tool_call:
            return END  # Direct text response, no routing needed

        name = tool_call.get("name", "")

        # Route to agent nodes
        if name in TOOL_ROUTING:
            target = TOOL_ROUTING[name]
            logger.info(f"[ROUTING] Tool call '{name}' -> {target}")
            return target

        # Route to skill node for skill_* tool calls
        if name.startswith("skill_"):
            logger.info(f"[ROUTING] Tool call '{name}' -> skill")
            return "skill"

        logger.warning(f"[ROUTING] Unknown tool call: {name}, ending")
        return END
