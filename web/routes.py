"""
web/routes.py
--------------
Flask HTTP routing layer.

All routes are registered on a Blueprint named ``"main"`` which is attached
to the app factory in ``app.py``.  This keeps HTTP concerns (request
parsing, response serialisation, HTTP error codes) completely separate from
the core pipeline logic.

Endpoints:
    GET  /          — Serve the single-page application shell.
    POST /analyse   — Accept a JSON payload, run the full 5-stage pipeline,
                      and return structured JSON results.
    GET  /health    — Lightweight liveness probe (returns 200 + version info).
"""

import logging
import time

from flask import Blueprint, render_template, request, jsonify # type: ignore[import]

from core.preprocessor import preprocess
from core.classifier import classify
from core.engine import run_analysis
from core.formatter import format_output

logger = logging.getLogger(__name__)

main = Blueprint("main", __name__, template_folder="templates")


# --------------------------------------------------------------------------- #
# Route: SPA Shell
# --------------------------------------------------------------------------- #
@main.route("/", methods=["GET"])
def index():
    """
    Serve the single-page application HTML shell.

    Returns
    -------
    Response
        Rendered ``index.html`` template.
    """
    return render_template("index.html")


# --------------------------------------------------------------------------- #
# Route: Analysis Pipeline
# --------------------------------------------------------------------------- #
@main.route("/analyse", methods=["POST"])
def analyse():
    """
    Run the full 5-stage AI analysis pipeline on submitted code/error input.

    Expects a JSON body with the key ``"code"`` containing the raw user input.

    Returns
    -------
    Response (JSON)
        On success: ``{"success": true, "data": {...}, "elapsed_ms": N}``
        On failure: ``{"success": false, "error": "...", "stage": "..."}``
        with appropriate HTTP status codes.
    """
    # 1. Parse request body.
    body = request.get_json(silent=True)
    if not body or "code" not in body:
        return jsonify({
            "success": False,
            "error": "Request must be JSON with a 'code' key.",
            "stage": "http_parsing",
        }), 400

    raw_input: str = body.get("code", "")
    t_start = time.perf_counter()

    # 2. Stage 1 — Preprocess.
    pre_result = preprocess(raw_input)
    if not pre_result.ok:
        return jsonify({
            "success": False,
            "error": pre_result.error,
            "stage": "preprocessor",
        }), 422

    # 3. Stage 2 — Classify.
    try:
        classification = classify(pre_result.payload)
    except RuntimeError as exc:
        logger.exception("Classifier failed.")
        return jsonify({
            "success": False,
            "error": str(exc),
            "stage": "classifier",
        }), 502

    # 4. Stages 3+4 — Template Engine + Groq Inference.
    try:
        bundle = run_analysis(pre_result.payload, classification)
    except RuntimeError as exc:
        logger.exception("Analysis engine failed.")
        return jsonify({
            "success": False,
            "error": str(exc),
            "stage": "engine",
        }), 502

    # 5. Stage 5 — Format Output.
    formatted = format_output(bundle)

    elapsed_ms = int((time.perf_counter() - t_start) * 1000)
    logger.info(
        "Analysis complete in %dms (classification=%s, input_chars=%d).",
        elapsed_ms,
        classification.label,
        pre_result.char_count,
    )

    return jsonify({
        "success": True,
        "data": formatted.to_dict(),
        "meta": {
            "elapsed_ms": elapsed_ms,
            "input_chars": pre_result.char_count,
            "classification": classification.label,
        },
    }), 200


# --------------------------------------------------------------------------- #
# Route: Health Check
# --------------------------------------------------------------------------- #
@main.route("/health", methods=["GET"])
def health():
    """
    Lightweight liveness probe for monitoring and load-balancer checks.

    Returns
    -------
    Response (JSON)
        ``{"status": "ok", "service": "code-analysis-agent"}``
    """
    return jsonify({
        "status": "ok",
        "service": "code-analysis-agent",
    }), 200
