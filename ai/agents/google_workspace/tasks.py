from . import creds
from googleapiclient.discovery import build
from ollama import chat
import logging

MODEL = "granite4:3b"


class GoogleTasksAgent:
    def __init__(self):
        self._logger = logging.getLogger(__name__)
        self.service = build("tasks", "v1", credentials=creds)
        self.system_prompt = f"""
You are an agent who can execute functions using the Google Tasks API. Analyse the input query from the user and use the appropriate tools to fulfill the given task.

Here are the available tasklists in the user's Google Tasks account, choose the correct one to add/edit/delete tasks from:{self._get_tasklists()}

RULES
- Be descriptive when modifying task detail.
- Use the exact tasklist name as specified above
"""
        self.tools = [
            {
                "type": "function",
                "function": {
                    "name": "create_task",
                    "description": "Adds a new task to a specified task list.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "tasklist_id": {
                                "type": "string",
                                "description": "ID of the tasklist to modify task in.",
                            },
                            "title": {
                                "type": "string",
                                "description": "The title/headline of the task.",
                            },
                        },
                        "required": ["tasklist_id", "title"],
                    },
                },
            }
        ]

    def execute(self, instructions):
        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": instructions},
        ]

        response = chat(
            model=MODEL,
            messages=messages,
            tools=self.tools,
            stream=False,
            options={"temperature": 0.2},
        )

        if response.message.tool_calls:
            for call in response.message.tool_calls:
                if call.function.name == "create_task":
                    result = self.create_task(**call.function.arguments)

    def _get_tasklists(self):
        """
        Retrieves user's Google tasklists and formats to plug into agent context.
        """
        try:
            user_tasklists = self.service.tasklists().list().execute()["items"]
            out = ""
            for i in user_tasklists:
                out += "\n- " + i["title"] + " [ID: " + i["id"] + "]"
            self._logger.info("Googe Tasklists fetched.")
            return out

        except Exception as e:
            err = f"Failed to fetch tasklists: {e}"
            self._logger.error(err)
            return err

    def create_task(self, title, tasklist_id="@default", **kwargs):
        try:
            task_body = {"title": title}
            result = (
                self.service.tasks()
                .insert(tasklist=tasklist_id, body=task_body)
                .execute()
            )
            self._logger.info(f"GoogleTasksAgent executed create_task.")
            return f'"{result["title"]}" created successfully in Google Tasks (Task ID: {result["id"]})'

        except Exception as e:
            self._logger.error(f"Failed to create task: {e}")
            return {"status": "error", "message": str(e)}
