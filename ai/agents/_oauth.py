"""
Shared OAuth2 token management for Google Workspace agents.

This module is imported by calendar_agent, sheets_agent, and drive_agent.
It contains ONLY the get_access_token() function with no other dependencies.
"""

import os
import httpx


def get_access_token() -> str:
    """
    Get a fresh Google API access token using OAuth 2.0 refresh token.

    Required environment variables:
        - GOOGLE_CLIENT_ID
        - GOOGLE_CLIENT_SECRET
        - GOOGLE_REFRESH_TOKEN

    Returns:
        Access token string for Google API authentication.

    Raises:
        httpx.HTTPStatusError: If token refresh fails.
        KeyError: If required environment variables are missing.
    """
    response = httpx.post(
        "https://oauth2.googleapis.com/token",
        data={
            "client_id": os.getenv("GOOGLE_CLIENT_ID"),
            "client_secret": os.getenv("GOOGLE_CLIENT_SECRET"),
            "refresh_token": os.getenv("GOOGLE_REFRESH_TOKEN"),
            "grant_type": "refresh_token",
        },
    )
    response.raise_for_status()
    return response.json()["access_token"]
