from . import creds
from ..base import BaseAgent
from googleapiclient.discovery import build


class GoogleTasksAgent(BaseAgent):
    def __init__(self):
        super().__init__()
        self.service = build("tasks", "v1", credentials=creds)
        self.system_prompt = f"""You are an agent who can execute functions using the Google Tasks API. Analyse the input query from the user and use the appropriate tools to fulfill the given task.

Here are the available tasklists in the user's Google Tasks account, choose the correct one to add/edit/delete tasks from:
{self._get_tasklists()}

Run ```list_tasks``` tool to fetch the required task ID and metadata before using the ```update_task``` & ```delete_task```.
        
RULES
- Be descriptive when modifying task detail.
- Use the exact tasklist name as specified above."""
        self.tools = [
            self.create_task,
            self.list_tasks,
            self.update_task,
            self.delete_task,
        ]

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
    
    def _get_task(self, task_id, tasklist_id="@default"):
        try:
            task = self.service.tasks().get(task=task_id, tasklist=tasklist_id).execute()
            return task
        except Exception as e:
            err = f"Failed to fetch task: {e}"
            self._logger.error(err)
            return err

    def create_task(self, title, due=None, tasklist_id="@default"):
        """
        Creates the task in the specified tasklist.

        Args:
            title (str): Title of the task to be created.
            due (str, optional): Due date of the task in RFC 3339 format.
            tasklist_id (str, optional): Specific tasklist from the user's account. Defaults to '@default'

        Returns:
            result (str): Result of function execution.
        """
        try:
            task_body = {"title": title, "due": due}
            result = (
                self.service.tasks()
                .insert(tasklist=tasklist_id, body=task_body)
                .execute()
            )
            return f'"{result["title"]}" created successfully with ID: {result["id"]})'
        except Exception as e:
            err = f"Failed to create task: {str(e)}"
            self._logger.error(err)
            return err

    def list_tasks(self, tasklist_id="@default"):
        """
        Fetches all the tasks from the specified tasklist ID.

        Args:
            tasklist_id (str): Tasklist ID. Defaults to "@default"

        Returns:
            result (dict): Collection of tasks with their metadata.
        """
        try:
            result = self.service.tasks().list(tasklist=tasklist_id).execute()["items"]
            return result
        except Exception as e:
            err = f"Failed to fetch tasks: {str(e)}"
            self._logger.error(err)
            return {"ERROR": {err}}

    def update_task(
        self,
        task_id,
        tasklist_id="@default",
        title=None,
        notes=None,
        completed=None,
        status=None,
        due=None,
    ):
        """
        Updates the task details for the given task ID from the corresponding tasklist ID.

        Args:
            task_id (str): Task ID.
            tasklist_id (str): Tasklist ID. Defaults to "@default"
            title (str): Title of the task. Maximum length allowed: 1024 characters. Pass the same title again if no change is needed.
            completed (str, optional): Completion date of the task (as a RFC 3339 timestamp). Leaves tasks as incomplete if omitted.
            due (str, optional): Scheduled date for the task (as an RFC 3339 timestamp).
            notes (str, optional): Notes describing the task.
            status (str, optional): Status of the task. This is either "needsAction" or "completed".
        """
        try:
            body = {
                "id": task_id,
                "due": due,
                "title": title,
                "notes": notes,
                "completed": completed,
                "status": status,
            }
            result = (
                self.service.tasks()
                .update(task=task_id, tasklist=tasklist_id, body=body)
                .execute()
            )
            return f"Task has been updated. Details:\n{self._get_task(task_id=task_id, tasklist_id=tasklist_id)}"
        except Exception as e:
            err = f"Failed to update task: {str(e)}"
            self._logger.error(err)
            return f"ERROR: {err}"

    def delete_task(self, task_id, tasklist_id="@default"):
        """
        Delete a task from the specified tasklist.

        Args:
            task_id (str): Task ID.
            tasklist_id (str, optional): Defaults to "@default"
        """
        try:
            result = (
                self.service.tasks()
                .delete(task=task_id, tasklist=tasklist_id)
                .execute()
            )
            return f"Task {task_id} deleted successfully."
        except Exception as e:
            err = f"Failed to delete task: {str(e)}"
            self._logger.error(err)
            return f"ERROR: {err}"
