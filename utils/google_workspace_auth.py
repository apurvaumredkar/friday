import os.path
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.errors import HttpError
from pyprojroot import here
import logging

logger = logging.getLogger(__name__)
SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/tasks",
]
ROOT = here()
CREDENTIALS_PATH = ROOT / "data" / "google_credentials.json"
TOKEN_PATH = ROOT / "data" / "google_token.json"


def run_auth():
    creds = None
    if os.path.exists(TOKEN_PATH):
        creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)
        logger.info("Google auth token validated.")
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            logger.info("Running Google auth flow.")
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_PATH, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_PATH, "w") as t:
            t.write(creds.to_json())
            logger.info("Credentials refreshed and token stored.")


if __name__ == "__main__":
    run_auth()
