"""
core/classifier.py
-------------------
Pipeline Stage 2 — Task Classifier.

Uses a fast, low-latency Groq call (llama-3.1-8b-instant) to determine
the nature of the user's input before the expensive deep-analysis step.

Classification outputs one of three labels:
    "code"        — The payload contains only source code (no stack traces).
    "error"       — The payload is primarily an exception / stack trace.
    "both"        — The payload contains source code **and** a stack trace.

The classifier returns a `ClassificationResult` dataclass that the engine
uses to select the correct Jinja2 templates and Groq temperatures for the
downstream analysis stage.

Fallback behaviour: if the LLM returns an unrecognised label the classifier
defaults to "both" so the engine runs the most thorough possible analysis.
"""

import logging
from dataclasses import dataclass

from config import settings
from infrastructure.groq_client import chat_completion

logger = logging.getLogger(__name__)

# Canonical set of valid classification labels.
_VALID_LABELS = {"code", "error", "both"}

# System prompt — kept short to minimise latency on the fast model.
_SYSTEM_PROMPT = (
    "You are a code-input classifier. "
    "Analyse the user's text and respond with EXACTLY ONE word: "
    "'code' if it contains only source code, "
    "'error' if it contains only an error message or stack trace, "
    "'both' if it contains source code AND an error/stack trace. "
    "Output only the single word. No punctuation, no explanation."
)


@dataclass(frozen=True)
class ClassificationResult:
    """
    Output of the Task Classifier stage.

    Attributes
    ----------
    label : str
        One of ``"code"``, ``"error"``, or ``"both"``.
    raw_response : str
        The unmodified string the LLM returned (useful for debugging).
    """

    label: str
    raw_response: str


def classify(payload: str) -> ClassificationResult:
    """
    Classify a pre-processed input payload using a lightweight LLM call.

    Parameters
    ----------
    payload : str
        Cleaned text from the preprocessor stage.

    Returns
    -------
    ClassificationResult
        Typed result containing the canonical label and the raw LLM output.
    """
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": payload[:2000]},  # Send only the head for speed.
    ]

    raw = chat_completion(
        model=settings.CLASSIFIER_MODEL,
        messages=messages,
        temperature=settings.CLASSIFIER_TEMPERATURE,
        max_tokens=10,  # We need exactly one word.
    )

    # Normalise: lowercase, strip whitespace and punctuation.
    normalised = raw.strip().lower().rstrip(".,;:!?")

    if normalised not in _VALID_LABELS:
        logger.warning(
            "Classifier returned unrecognised label '%s'. Defaulting to 'both'.",
            normalised,
        )
        normalised = "both"

    logger.debug("Classification result: '%s' (raw: '%s')", normalised, raw)
    return ClassificationResult(label=normalised, raw_response=raw)
