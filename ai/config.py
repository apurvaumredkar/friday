"""Config loader for Friday AI models and endpoints.

Switch providers by changing "inference_provider" in config.json.
No code changes required.
"""

import os
import json
from pathlib import Path
from openai import OpenAI

_config = None
_client = None


def get_config():
    """Load and cache config from config.json."""
    global _config
    if _config is None:
        config_path = Path(__file__).parent.parent / "config.json"
        with open(config_path) as f:
            _config = json.load(f)
    return _config


def get_provider() -> str:
    """Get the active inference provider name."""
    return get_config()["inference_provider"]


def _get_provider_config() -> dict:
    """Get the config for the active provider."""
    config = get_config()
    provider = config["inference_provider"]
    return config["providers"][provider]


def get_model(name: str) -> str:
    """Get model ID by name (root, tool) for the active provider."""
    return _get_provider_config()["models"][name]


def get_base_url() -> str:
    """Get base URL for the active provider."""
    return _get_provider_config()["base_url"]


def get_api_key() -> str:
    """Get API key for the active provider from environment."""
    env_var = _get_provider_config()["api_key_env"]
    return os.getenv(env_var, "")


def get_client() -> OpenAI:
    """Get shared OpenAI client singleton for the active provider."""
    global _client
    if _client is None:
        _client = OpenAI(base_url=get_base_url(), api_key=get_api_key())
    return _client
