from google.oauth2.credentials import Credentials
from utils.google_workspace_auth import SCOPES
from pyprojroot import here

ROOT = here()
TOKEN_PATH = ROOT / "data" / "google_token.json"

creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)
