from typing import TypedDict, Annotated
import operator
import logging
from langgraph.graph import StateGraph, START, END
from ai.agents import root_agent, web_agent, calendar_agent, sheets_agent, drive_agent, maps_agent, docs_agent
from ai.agents.drive_agent import search_files

logger = logging.getLogger(__name__)


class AgentState(TypedDict):
    messages: Annotated[list[dict], operator.add]
    original_prompt: str | None
    pdf_bytes: bytes | None
    pdf_filename: str | None
    tool_result: str | None  # For passing tool execution results back to root


class Friday:
    def __init__(self):
        # Load skills (triggers registration)
        import ai.skills

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


    def root_node(self, state: AgentState):
        try:
            response = root_agent(state["messages"])
            new_message = {"role": "assistant", "content": response}

            # Check if we just processed a tool result
            # If so, clear the tool_result field to enable next operation
            if state.get("tool_result"):
                logger.info("Clearing tool_result after root processed tool output")
                return {"messages": [new_message], "tool_result": None}

            return {"messages": [new_message]}
        except Exception as e:
            error_msg = {"role": "assistant", "content": f"Error: {str(e)}"}
            return {"messages": [error_msg]}

    def web_node(self, state: AgentState):
        """Handle web operations (search, URL) and pass result back to root."""
        try:
            last_content = state["messages"][-1]["content"]

            # Extract command after prefix
            if last_content.startswith("SEARCH:"):
                command = last_content.replace("SEARCH:", "").strip()
            elif last_content.startswith("WEB:"):
                command = last_content.replace("WEB:", "").strip()
            else:
                return {"messages": [{"role": "system", "content": "[ERROR] No WEB/SEARCH prefix"}]}

            logger.info(f"[WEB] Processing: {command[:50]}...")

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

        Detects which skill to execute based on message content and state,
        then follows the workflow defined in the skill's markdown guide.
        """
        try:
            from ai.skills import detect_skill, load_skill

            last_content = state["messages"][-1]["content"]

            # Detect which skill to use
            skill_name = detect_skill(last_content, state)
            if not skill_name:
                error_msg = {"role": "system", "content": "[SKILL ERROR] No skill detected"}
                return {"messages": [error_msg], "tool_result": "Error: No skill detected"}

            logger.info(f"[SKILL] Executing: {skill_name}")

            # Load skill markdown (for logging/future use)
            skill_content = load_skill(skill_name)
            if skill_content:
                logger.debug(f"[SKILL] Loaded guide: {len(skill_content)} chars")

            # Execute skill workflow
            # For now, dispatch to specific handlers
            # TODO: Make this more dynamic by parsing markdown
            if skill_name == "PROCESS_PAYCHECK":
                return self._execute_paycheck_skill(state)
            elif skill_name == "DRIVE_FETCH":
                return self._execute_drive_fetch_skill(state)
            else:
                error_msg = {"role": "system", "content": f"[SKILL ERROR] Unknown skill: {skill_name}"}
                return {"messages": [error_msg], "tool_result": f"Error: Unknown skill {skill_name}"}

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
            # Use root_agent to parse the paycheck into CSV format
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

            csv_response = root_agent([{"role": "user", "content": parse_prompt}])

            # Extract CSV values from response
            csv_line = csv_response.strip().split("\n")[-1]  # Last line is the data
            values = [v.strip() for v in csv_line.split(",")]
            logger.info(f"[SKILL:PAYCHECK] Parsed {len(values)} CSV values: {values[:3]}...")

            # Step 3: Use sheets_agent to append row to paycheck sheet
            logger.info(f"[SKILL:PAYCHECK] Step 3/3: Updating Google Sheet via sheets_agent...")
            sheets_request = f"Append this paycheck data to the sheet: {', '.join(values)}"
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
            last_content = state["messages"][-1]["content"]

            # Extract search term from "DRIVE_FETCH: <search term>"
            search_term = last_content.split("DRIVE_FETCH:")[-1].strip()

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

    def calendar_node(self, state: AgentState):
        """Handle calendar operations and pass result back to root."""
        try:
            last_content = state["messages"][-1]["content"]
            # Extract command after "CALENDAR:" prefix
            # Check for multiple CALENDAR: prefixes
            if last_content.count("CALENDAR:") > 1:
                logger.warning(f"Multiple CALENDAR: prefixes detected, using first occurrence")

            # Use first occurrence after prefix (not last)
            parts = last_content.split("CALENDAR:", 1)
            if len(parts) < 2:
                return {"messages": [{"role": "assistant", "content": "❌ Missing CALENDAR: prefix"}]}
            command = parts[1].strip()

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
        """Handle maps operations (places, directions, transit)."""
        try:
            last_content = state["messages"][-1]["content"]

            # Extract command after "MAPS:" prefix
            parts = last_content.split("MAPS:", 1)
            if len(parts) < 2:
                return {"messages": [{"role": "system", "content": "[ERROR] Missing MAPS: prefix"}]}
            command = parts[1].strip()

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
        """Handle document operations (PDF read, search, info) and pass result back to root."""
        try:
            last_content = state["messages"][-1]["content"]

            # Extract command after "DOCS:" prefix
            parts = last_content.split("DOCS:", 1)
            if len(parts) < 2:
                return {"messages": [{"role": "system", "content": "[ERROR] Missing DOCS: prefix"}]}
            command = parts[1].strip()

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
        """Route from root agent to specialized nodes or skills."""
        if not state.get("messages"):
            return END

        last_msg = state["messages"][-1]["content"]

        # Check if we just processed a tool result (prevent loops)
        if state.get("tool_result"):
            return END

        # Check for skill handlers (orchestrator-driven detection)
        from ai.skills import detect_skill
        skill_name = detect_skill(last_msg, state)
        if skill_name:
            logger.info(f"[ROUTING] Detected skill: {skill_name}")
            return "skill"

        # Web operations (search, URL)
        if last_msg.startswith("SEARCH:") or last_msg.startswith("WEB:"):
            return "web"

        # Calendar operations
        if "CALENDAR:" in last_msg:
            return "calendar"

        # Maps operations (places, directions, transit)
        if "MAPS:" in last_msg:
            return "maps"

        # Document operations (PDF read, search, info)
        if "DOCS:" in last_msg:
            return "docs"

        return END

