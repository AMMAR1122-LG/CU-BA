"""
infrastructure/groq_client.py
------------------------------
Thin wrapper around the official Groq Python SDK.

Responsibilities:
- Instantiate a single shared Groq client authenticated with the API key
  from `config.settings`.
- Expose a `chat_completion()` helper that executes a chat request with
  automatic exponential-backoff retries so free-tier rate limits do not
  surface as hard failures to callers.

All other modules call `chat_completion()` rather than touching the Groq
SDK directly, keeping the retry and error-handling logic in one place.
"""

import time
import logging

from groq import Groq, RateLimitError, APIStatusError, APIConnectionError

from config import settings

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------ #
# Shared client singleton — one TCP connection pool for the process.
# ------------------------------------------------------------------ #
_client = Groq(api_key=settings.GROQ_API_KEY)


def chat_completion(
    *,
    model: str,
    messages: list[dict],
    temperature: float = 0.2,
    max_tokens: int = 4096,
) -> str:
    """
    Execute a Groq chat-completion request with exponential-backoff retries.

    The function retries on RateLimitError and transient network errors
    up to `settings.MAX_RETRIES` times, doubling the wait after each
    attempt starting at `settings.INITIAL_BACKOFF_SECONDS`.

    Parameters
    ----------
    model : str
        Groq model identifier, e.g. ``"llama-3.3-70b-versatile"``.
    messages : list[dict]
        OpenAI-compatible message list, e.g.
        ``[{"role": "user", "content": "..."}]``.
    temperature : float
        Sampling temperature passed directly to the API.
    max_tokens : int
        Maximum tokens in the completion response.

    Returns
    -------
    str
        The raw text content of the first choice's message.

    Raises
    ------
    RuntimeError
        After all retry attempts are exhausted, wrapping the last
        exception with a human-readable description.
    """
    delay = settings.INITIAL_BACKOFF_SECONDS
    last_exception: Exception | None = None

    for attempt in range(1, settings.MAX_RETRIES + 1):
        try:
            response = _client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            return response.choices[0].message.content

        except RateLimitError as exc:
            last_exception = exc
            logger.warning(
                "Groq rate limit hit (attempt %d/%d). Backing off %.1fs.",
                attempt,
                settings.MAX_RETRIES,
                delay,
            )
            time.sleep(delay)
            delay *= settings.BACKOFF_MULTIPLIER

        except APIConnectionError as exc:
            last_exception = exc
            logger.warning(
                "Groq connection error (attempt %d/%d): %s. Backing off %.1fs.",
                attempt,
                settings.MAX_RETRIES,
                exc,
                delay,
            )
            time.sleep(delay)
            delay *= settings.BACKOFF_MULTIPLIER

        except APIStatusError as exc:
            # 5xx server errors are worth retrying; 4xx (bad request) are not.
            if exc.status_code and exc.status_code < 500:
                raise RuntimeError(
                    f"Groq API client error {exc.status_code}: {exc.message}"
                ) from exc
            last_exception = exc
            logger.warning(
                "Groq server error %s (attempt %d/%d). Backing off %.1fs.",
                exc.status_code,
                attempt,
                settings.MAX_RETRIES,
                delay,
            )
            time.sleep(delay)
            delay *= settings.BACKOFF_MULTIPLIER

    raise RuntimeError(
        f"Groq API unavailable after {settings.MAX_RETRIES} attempts. "
        f"Last error: {last_exception}"
    ) from last_exception
