from . import creds
from ..base import BaseAgent
from googleapiclient.discovery import build


class GoogleTasksAgent(BaseAgent):
    def __init__(self):
        super().__init__()
        self.service = build("tasks", "v1", credentials=creds)
        self.system_prompt = f"""You are a deterministic Google Tasks execution agent.

Your job is to convert user intent into correct Google Tasks API tool calls.
You MUST use exact tasklist IDs and task IDs. Never guess. Never fabricate IDs.

------------------------------------------------------------
AVAILABLE TASKLISTS (SOURCE OF TRUTH)
------------------------------------------------------------
{self._get_tasklists()}

These are the ONLY valid tasklist IDs. You must copy IDs EXACTLY as written.
Do NOT modify them. Do NOT shorten them. Do NOT infer them.

------------------------------------------------------------
CRITICAL ID RULES
------------------------------------------------------------
1. Titles are NOT IDs.
2. Natural language names are NOT IDs.
3. You must always pass the literal ID string shown above.
4. If unsure which tasklist to use, ask for clarification.
5. Never invent a task ID or tasklist ID.

------------------------------------------------------------
TASK SELECTION PROCEDURE (MANDATORY)
------------------------------------------------------------

When the user wants to UPDATE, DELETE, or MOVE a task:

STEP 1 — Identify the correct tasklist.
STEP 2 — Call `list_tasks` for that tasklist.
STEP 3 — From the returned results, locate the task by matching title semantically.
STEP 4 — Extract the exact `id` field from the tool output.
STEP 5 — Use that exact `id` in `update_task`, `delete_task`, or `move_task`.

You are NOT allowed to call update/delete/move without first calling `list_tasks`.

------------------------------------------------------------
TASK CREATION RULES
------------------------------------------------------------

When creating a task:
- Choose the most appropriate tasklist based on user intent.
- If unclear, ask a clarification question.
- Use minimal but meaningful titles.
- Pass the exact tasklist_id string shown above.

------------------------------------------------------------
MATCHING RULES
------------------------------------------------------------

When matching tasks:
- Match primarily on `title`
- Never use `updated` timestamp as due date
- Only use the `due` field as due date
- If multiple tasks have similar titles, ask user to clarify

------------------------------------------------------------
BEHAVIOR CONSTRAINTS
------------------------------------------------------------

- Be precise.
- Be literal.
- Do not assume.
- Do not hallucinate IDs.
- If required information is missing, ask the user.
- Prefer clarification over incorrect tool execution.

You exist to produce correct tool calls, not conversational responses.
"""
        self.messages = [{"role": "system", "content": self.system_prompt}]
        self.tools = [
            self.create_task,
            self.list_tasks,
            self.move_task,
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
            result (str): Collection of tasks with their metadata (ID, Due Date & Status).
        """
        try:
            result = self.service.tasks().list(tasklist=tasklist_id).execute()["items"]
            user_tasks = ""
            for i, r in enumerate(result):
                user_tasks += f"{i+1}. {r["title"]} [Task ID: {r["id"]}] Due: {r.get("due", None)}, Status: {r["status"]}\n"
            return user_tasks
        except Exception as e:
            err = f"Failed to fetch tasks: {str(e)}"
            self._logger.error(err)
            return err

    def move_task(self, task_id, source, destination):
        """
        Moves a task from one tasklist to another.

        Args:
            task_id (str): Task ID for the task to be moved
            source (str): Source Tasklist ID
            destination (str): Destination Tasklist ID

        Returns:
            result (dict): Updated task metadata
        """
        try:
            result = (
                self.service.tasks()
                .move(tasklist=source, task=task_id, destinationTasklist=destination)
                .execute()
            )
            return result
        except Exception as e:
            err = f"Failed to move task: {str(e)}"
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

        Returns:
            result (dict): Updated task metadata.
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
            return result
        except Exception as e:
            err = f"Failed to update task: {str(e)}"
            self._logger.error(err)
            return {"ERROR": {err}}

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


# --- Notes from direct API testing (Feb 20, 2026) ---
#
# list_tasks() — raw response is very noisy. Each task dict contains ~10 fields
# (kind, etag, selfLink, webViewLink, position, links, updated...) that are
# useless to the model. Returning raw dicts causes field confusion in small models:
# granite4 was observed reading `updated` and labeling it as the due date because
# `due` was absent and `updated` was the only timestamp visible in the string.
# Fix: pre-format output here (id, title, status, due only) so Python does the
# parsing, not the LLM.
#
# `due` field missing from UI-set tasks: confirmed via direct API testing (Feb 21).
# Tasks dated through the Google Tasks web/mobile UI do not populate the `due`
# field in the API at all. Tasks created via the API with an explicit `due`
# parameter DO return the field correctly. This is a known Google Tasks v1 API
# gap — the UI was redesigned ~2018-2019 and the API was never updated to match.
# No workaround exists in v1. The agent is blind to due dates on any task the
# user created or dated manually through the UI.
#
# @default resolves to "My Tasks" — confirmed equivalent to passing the explicit ID.
