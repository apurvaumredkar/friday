from . import creds
from ..base import BaseAgent
from googleapiclient.discovery import build


class GoogleTasksAgent(BaseAgent):
    def __init__(self):
        super().__init__()
        self.service = build("tasks", "v1", credentials=creds)
        self.system_prompt = f"""
You are an agent who can execute functions using the Google Tasks API. Analyse the input query from the user and use the appropriate tools to fulfill the given task. Here are the available tasklists in the user's Google Tasks account, choose the correct one to add/edit/delete tasks from:{self._get_tasklists()}

RULES
- Be descriptive when modifying task detail.
- Use the exact tasklist name as specified above.
- Acknowledge tool calls with a response.
"""
        self.tools = [self.create_task]

    def _get_tasklists(self):
        try:
            user_tasklists = self.service.tasklists().list().execute()["items"]
            out = ""
            for i in user_tasklists:
                out += "\n- " + i["title"] + " [ID: " + i["id"] + "]"
            self._logger.info("Tasklists fetched.")
            return out
        except Exception as e:
            err = f"Failed to fetch tasklists: {e}"
            self._logger.error(err)
            return err

    def create_task(self, title, tasklist_id="@default"):
        """
        Creates the task in the specified tasklist.

        Args:
            title (str): Title of the task to be created.
            tasklist_id (str, optional): Specific tasklist from the user's account. Defaults to '@default'

        Returns:
            result (str): Result of function execution.
        """
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
            self._logger.error(f"Failed to create task: {str(e)}")
            return f"Failed to create task: {str(e)}"
