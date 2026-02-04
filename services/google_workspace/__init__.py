"""
Google Workspace services package.

DEPRECATED: This package's main modules have been merged into ai.agents.

Only google_oauth_setup.py remains for manual OAuth token generation.
For Calendar, Sheets, and Drive operations, import from ai.agents instead.
"""

# Only export OAuth setup utility (manual token generation)
from . import google_oauth_setup

__all__ = ['google_oauth_setup']
