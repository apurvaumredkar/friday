from . import creds
from ..base import BaseAgent
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from ollama import chat
import logging

MODEL = "granite4:3b"


class GoogleDriveAgent(BaseAgent):
    def __init__(self):
        self._logger = logging.getLogger(__name__)
        self.service = build("drive", "v3", credentials=creds)
        self.system_prompt = """You are an agent that who can execute functions using the Google Drive API to manage the files and folders in the user's Google Drive account. Analyse the input query from the user and use the appropriate tools to fulfill the given task."""
        self.tools = []
