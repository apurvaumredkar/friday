"""
Shared OAuth2 token management for Google Workspace agents.

This module is imported by calendar_agent, sheets_agent, and drive_agent.
It contains ONLY the get_access_token() function with no other dependencies.

Tokens are cached for 55 minutes (Google tokens expire in 60 minutes).
"""

import os
import time
import logging
import httpx

logger = logging.getLogger(__name__)

# Shared HTTP client for Google API calls (connection pooling)
_http_client: httpx.Client | None = None


def get_http_client() -> httpx.Client:
    """Get shared httpx.Client for Google API calls (connection pooling)."""
    global _http_client
    if _http_client is None:
        _http_client = httpx.Client(timeout=15.0)
    return _http_client


# Cached token and its expiry time
_cached_token: str | None = None
_token_expiry: float = 0  # Unix timestamp
_TOKEN_TTL = 55 * 60  # 55 minutes (tokens expire at 60)


def get_access_token() -> str:
    """
    Get a Google API access token using OAuth 2.0 refresh token.

    Returns a cached token if still valid, otherwise refreshes.

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
    global _cached_token, _token_expiry

    now = time.monotonic()
    if _cached_token and now < _token_expiry:
        return _cached_token

    logger.info("Refreshing Google OAuth access token")
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

    _cached_token = response.json()["access_token"]
    _token_expiry = now + _TOKEN_TTL
    logger.info("OAuth token refreshed and cached (55min TTL)")

    return _cached_token
