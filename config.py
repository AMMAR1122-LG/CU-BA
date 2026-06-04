"""
config.py
---------
Centralized configuration manager.

Loads environment variables from .env via python-dotenv, validates that every
required key is present, and exposes a single `settings` object that every
other module imports.  Raises a clear RuntimeError at import time if any
required value is missing, so the application fails fast before the first
HTTP request rather than mid-flight.
"""

import os
from dotenv import load_dotenv

# Load .env file into os.environ as early as possible.
load_dotenv()


def _require(key: str) -> str:
    """
    Retrieve an environment variable or raise a descriptive RuntimeError.

    Parameters
    ----------
    key : str
        Name of the environment variable to look up.

    Returns
    -------
    str
        The non-empty string value of the variable.

    Raises
    ------
    RuntimeError
        If the variable is absent or blank.
    """
    value = os.getenv(key, "").strip()
    if not value:
        raise RuntimeError(
            f"[config] Required environment variable '{key}' is missing or empty. "
            f"Copy .env.example → .env and fill in the value before starting."
        )
    return value


class Settings:
    """
    Immutable bag-of-settings populated from environment variables.

    All application modules should import the module-level `settings`
    singleton rather than calling os.getenv directly.
    """

    # ------------------------------------------------------------------ #
    # Required
    # ------------------------------------------------------------------ #
    GROQ_API_KEY: str = _require("GROQ_API_KEY")

    # ------------------------------------------------------------------ #
    # Optional with sensible defaults
    # ------------------------------------------------------------------ #
    FLASK_ENV: str = os.getenv("FLASK_ENV", "production")
    FLASK_DEBUG: bool = os.getenv("FLASK_DEBUG", "False").lower() == "true"
    SECRET_KEY: str = os.getenv("SECRET_KEY", "dev-secret-change-in-prod")

    # ------------------------------------------------------------------ #
    # Model identifiers
    # ------------------------------------------------------------------ #
    CLASSIFIER_MODEL: str = "llama-3.1-8b-instant"
    ANALYSIS_MODEL: str = "llama-3.3-70b-versatile"

    # ------------------------------------------------------------------ #
    # Model temperatures
    # ------------------------------------------------------------------ #
    CLASSIFIER_TEMPERATURE: float = 0.1
    BUG_FIX_TEMPERATURE: float = 0.2
    EXPLANATION_TEMPERATURE: float = 0.5

    # ------------------------------------------------------------------ #
    # Groq retry / rate-limit settings
    # ------------------------------------------------------------------ #
    MAX_RETRIES: int = 4
    INITIAL_BACKOFF_SECONDS: float = 1.0
    BACKOFF_MULTIPLIER: float = 2.0

    # ------------------------------------------------------------------ #
    # Input guardrails
    # ------------------------------------------------------------------ #
    MAX_INPUT_CHARS: int = 12_000  # ~3 000 tokens at avg 4 chars/token


# Module-level singleton — import this everywhere.
settings = Settings()
