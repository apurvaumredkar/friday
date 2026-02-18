from .agents import *
import json
from pyprojroot import here

ROOT = here()
SYSTEM_PROMPT_PATH = ROOT / "ai" / "SYSTEM.md"


class Orchestrator(BaseAgent):
    def __init__(self):
        super().__init__()
        with open(SYSTEM_PROMPT_PATH, "r", encoding="utf-8") as s:
            self.system_prompt = s.read()
        self.agents = {
            "WebAgent": WebAgent(),
            "GoogleDriveAgent": GoogleDriveAgent(),
            "GoogleTasksAgent": GoogleTasksAgent(),
        }
        self.messages = [{"role": "system", "content": self.system_prompt}]

    async def run(self, user_input):
        if user_input:
            self.messages.append({"role": "user", "content": user_input})
        for i in range(10):
            response = self.chat(
                messages=self.messages, tools=[self.delegate], temperature=0.5
            )
            tool_calls = response.message.get("tool_calls", None)
            self.messages.append(response.message)
            if not tool_calls:
                return response.message.content
            for call in tool_calls:
                result = self.execute_tool(call.function.name, call.function.arguments)
                self.messages.append({"role": "tool", "content": str(result)})

    def delegate(self, agent_name, instructions):
        """
        Use this tool to instruct an agent to execute an action.

        Available agents:
        1. **WebAgent**: Queries the web to feb to fetch real-time and latest information.
        2. **GoogleTasksAgent**: Handles the user's Google Tasks account to set reminders and to-do lists.
        3. **GoogleDriveAgent**: Manages files and folder in the user's Google Drive account.

        Args:
            agent_name (str): Name of the agent.
            instructions (str): Natural language instructions for the agent.

        Returns:
            result (str): Results from task execution.
        """
        agent = self.agents.get(agent_name, None)
        if not agent:
            return f"Error: no agent registered for '{agent_name}'"
        self._logger.info(f"{agent} invoked.")
        return agent.execute(instructions)
