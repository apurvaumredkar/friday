from google.auth.transport.requests import Request 
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
import os
import logging
from pyprojroot import here

logger = logging.getLogger(__name__)
ROOT = here()

SCOPES = ["https://www.googleapis.com/auth/tasks"]
TOKEN_PATH = ROOT / "data" / "google_token.json"

class GoogleTasksAgent:
    def __init__(self):
        self.creds = None
        if os.path.exists(TOKEN_PATH):
            self.creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)
            
        if not self.creds or not self.creds.valid:
            if self.creds and self.creds.expired and self.creds.refresh_token:
                self.creds.refresh(Request())
                logger.info("Google OAuth2.0 credentials loaded and refreshed.")
            else:
                logger.error("Invalid Google OAuth2.0 credentials! Run OAuth flow again manually.")

        self.service = build("tasks", "v1", credentials=self.creds)

    
    def create_task(self, task, tasklist="@default"):
        assert "title" in task, "Task title not given."
        try:
            result = self.service.tasks().insert(tasklist=tasklist, body=task).execute()
            out = f"Task created: {result.get('id')}"
            logger.info(out)
            return out
        except Exception as e:
            logger.error(e)
            return f"Task creation failed."