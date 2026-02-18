import ollama
import logging

MODEL = "granite4:3b"


class BaseAgent:
    def __init__(self):
        self.model = MODEL
        self.system_prompt = "..."
        self._logger = logging.getLogger(__name__)

    def chat(self, messages, tools, temperature):
        messages.insert(0, {"role": "system", "content": self.system_prompt})
        return ollama.chat(
            model=MODEL,
            messages=messages,
            stream=False,
            tools=tools,
            options={"temperature": temperature},
        )

    def execute(self, instructions):
        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": instructions},
        ]
        for _ in range(100):
            response = self.chat(messages=messages, tools=self.tools, temperature=0.15)
            tool_calls = response.message.get("tool_calls", None)
            if not tool_calls:
                return response.message.content
            messages.append(response.message)
            for call in tool_calls:
                result = self.execute_tool(call.function.name, call.function.arguments)
                messages.append({"role": "tool", "content": str(result)})

    def execute_tool(self, tool_name: str, tool_args: dict):
        if tool_name.startswith("_") or not hasattr(self, tool_name):
            raise ValueError(f"Unknown tool: {tool_name}")
        self._logger.info(f"Executed tool: {tool_name}, args: ({tool_args})")
        return getattr(self, tool_name)(**tool_args)
