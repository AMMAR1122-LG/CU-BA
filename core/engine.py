"""
core/engine.py
---------------
Pipeline Stages 3 & 4 — Prompt Template Engine + Groq API Layer.

Stage 3 — Prompt Template Engine:
    Loads the appropriate Jinja2 `.jinja2` template files from
    `core/templates/` based on the classification label and the analysis
    task type (``"explanation"``, ``"bug_detection"``, or ``"improvement"``),
    then renders the final prompt string by injecting the cleaned payload.

Stage 4 — Groq API Layer:
    Calls `infrastructure.groq_client.chat_completion()` with the rendered
    prompt, using the correct model and temperature for each task type.

The engine exposes a single public function `run_analysis()` that
orchestrates all three analysis tasks and returns their raw LLM outputs
bundled in an `AnalysisBundle` dataclass for the formatter to consume.

Template resolution is performed once at module load time so the
`Environment` object is reused across requests.
"""

import logging
from dataclasses import dataclass
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, TemplateNotFound

from config import settings
from core.classifier import ClassificationResult
from infrastructure.groq_client import chat_completion

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------ #
# Jinja2 environment — templates directory resolved relative to this file.
# ------------------------------------------------------------------ #
_TEMPLATES_DIR = Path(__file__).parent / "templates"
_jinja_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATES_DIR)),
    autoescape=False,  # Prompts are plain text, not HTML.
    trim_blocks=True,
    lstrip_blocks=True,
)


@dataclass(frozen=True)
class AnalysisBundle:
    """
    Raw LLM outputs for all three analysis dimensions.

    Attributes
    ----------
    explanation_raw : str
        Markdown explanation produced by the explanation template.
    bug_detection_raw : str
        Markdown bug report produced by the bug_detection template.
    improvement_raw : str
        Markdown improvement recommendations from the improvement template.
    classification_label : str
        The label determined by the classifier (``"code"``, ``"error"``, ``"both"``).
    """

    explanation_raw: str
    bug_detection_raw: str
    improvement_raw: str
    classification_label: str


def _render_template(template_name: str, context: dict) -> str:
    """
    Load and render a Jinja2 template file by name.

    Parameters
    ----------
    template_name : str
        Filename of the template, e.g. ``"explanation.jinja2"``.
    context : dict
        Variables passed into the template rendering context.

    Returns
    -------
    str
        The fully rendered prompt string.

    Raises
    ------
    RuntimeError
        If the template file cannot be found on disk.
    """
    try:
        template = _jinja_env.get_template(template_name)
        return template.render(**context)
    except TemplateNotFound as exc:
        raise RuntimeError(
            f"Prompt template '{template_name}' not found in {_TEMPLATES_DIR}. "
            f"Ensure the file exists."
        ) from exc


def _call_analysis(
    *,
    template_name: str,
    payload: str,
    classification_label: str,
    temperature: float,
) -> str:
    """
    Render a prompt template and execute the Groq completion.

    Parameters
    ----------
    template_name : str
        Which `.jinja2` file to use.
    payload : str
        The cleaned user input to inject into the template.
    classification_label : str
        Passed into the template so bug_detection can contextualise its output.
    temperature : float
        Sampling temperature for this specific task.

    Returns
    -------
    str
        Raw Markdown text from the LLM.
    """
    context = {
        "payload": payload,
        "classification": classification_label,
    }
    prompt = _render_template(template_name, context)

    logger.debug(
        "Calling Groq model=%s temp=%.1f for template='%s'.",
        settings.ANALYSIS_MODEL,
        temperature,
        template_name,
    )

    return chat_completion(
        model=settings.ANALYSIS_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=temperature,
        max_tokens=4096,
    )


def run_analysis(
    payload: str,
    classification: ClassificationResult,
) -> AnalysisBundle:
    """
    Orchestrate all three analysis passes and return bundled raw outputs.

    The three tasks run sequentially (explanation → bug detection →
    improvement suggestions) to avoid overwhelming the Groq free tier
    with parallel concurrent requests.

    Parameters
    ----------
    payload : str
        Cleaned and validated user input from the preprocessor.
    classification : ClassificationResult
        Output of the classifier stage.

    Returns
    -------
    AnalysisBundle
        Dataclass containing raw Markdown strings for all three analyses.
    """
    label = classification.label

    # --- Task 1: Code Explanation ---
    logger.info("Running explanation analysis (classification=%s).", label)
    explanation_raw = _call_analysis(
        template_name="explanation.jinja2",
        payload=payload,
        classification_label=label,
        temperature=settings.EXPLANATION_TEMPERATURE,
    )

    # --- Task 2: Bug Detection ---
    logger.info("Running bug detection analysis (classification=%s).", label)
    bug_detection_raw = _call_analysis(
        template_name="bug_detection.jinja2",
        payload=payload,
        classification_label=label,
        temperature=settings.BUG_FIX_TEMPERATURE,
    )

    # --- Task 3: Improvement Suggestions ---
    logger.info("Running improvement analysis (classification=%s).", label)
    improvement_raw = _call_analysis(
        template_name="improvement.jinja2",
        payload=payload,
        classification_label=label,
        temperature=settings.BUG_FIX_TEMPERATURE,
    )

    return AnalysisBundle(
        explanation_raw=explanation_raw,
        bug_detection_raw=bug_detection_raw,
        improvement_raw=improvement_raw,
        classification_label=label,
    )
