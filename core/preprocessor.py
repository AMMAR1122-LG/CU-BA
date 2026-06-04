"""
core/preprocessor.py
---------------------
Pipeline Stage 1 — Input Preprocessor.

Responsibilities:
- Reject empty or whitespace-only submissions immediately.
- Enforce the character-length ceiling defined in `config.settings`.
- Strip null bytes and non-printable control characters that would
  confuse downstream tokenisers.
- Normalise Windows line endings (CRLF) to Unix (LF) for consistent
  processing throughout the pipeline.
- Return a clean `PreprocessorResult` dataclass so every downstream stage
  receives a typed, trustworthy payload rather than raw user input.

No Groq calls are made here. This stage is pure Python and should never
raise an unhandled exception — it always returns a result whose `ok` flag
callers must check.
"""

import re
import logging
from dataclasses import dataclass

from config import settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PreprocessorResult:
    """
    Output of the Input Preprocessor stage.

    Attributes
    ----------
    ok : bool
        True when the payload passed all checks and is ready for classification.
    payload : str
        Cleaned input text (populated only when ok=True).
    error : str
        Human-readable rejection reason (populated only when ok=False).
    char_count : int
        Length of the cleaned payload in characters.
    """

    ok: bool
    payload: str
    error: str
    char_count: int


# Characters to strip: null bytes and ASCII control codes (0x00–0x1F)
# except for horizontal tab (0x09) and newline (0x0A), which are
# semantically meaningful inside code blocks.
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0B-\x0C\x0E-\x1F\x7F]")


def preprocess(raw_input: str) -> PreprocessorResult:
    """
    Sanitise and validate raw user input before it enters the AI pipeline.

    Parameters
    ----------
    raw_input : str
        The unmodified string submitted by the user via the HTTP form.

    Returns
    -------
    PreprocessorResult
        A typed result object. Check `.ok` before consuming `.payload`.
    """
    if not isinstance(raw_input, str):
        logger.warning("Preprocessor received non-string input type: %s", type(raw_input))
        return PreprocessorResult(
            ok=False,
            payload="",
            error="Input must be a text string.",
            char_count=0,
        )

    # 1. Normalise line endings.
    cleaned = raw_input.replace("\r\n", "\n").replace("\r", "\n")

    # 2. Strip dangerous control characters.
    cleaned = _CONTROL_CHAR_RE.sub("", cleaned)

    # 3. Strip leading/trailing whitespace on the outer boundary only.
    #    Inner indentation is intentionally preserved because it is
    #    syntactically significant in Python, YAML, etc.
    cleaned = cleaned.strip()

    # 4. Reject empty submissions.
    if not cleaned:
        return PreprocessorResult(
            ok=False,
            payload="",
            error="Input is empty. Please paste some code or an error trace.",
            char_count=0,
        )

    # 5. Enforce maximum length.
    char_count = len(cleaned)
    if char_count > settings.MAX_INPUT_CHARS:
        return PreprocessorResult(
            ok=False,
            payload="",
            error=(
                f"Input is too long ({char_count:,} characters). "
                f"Please trim it to under {settings.MAX_INPUT_CHARS:,} characters "
                f"({settings.MAX_INPUT_CHARS // 4:,} tokens approx)."
            ),
            char_count=char_count,
        )

    logger.debug("Preprocessor accepted input: %d chars.", char_count)
    return PreprocessorResult(
        ok=True,
        payload=cleaned,
        error="",
        char_count=char_count,
    )
