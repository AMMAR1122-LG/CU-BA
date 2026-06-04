"""
app.py
-------
Flask application factory and process entry point.

Running this file directly (``python app.py``) starts the development
server.  For production, point a WSGI server (gunicorn, uWSGI) at the
``create_app()`` factory instead:

    gunicorn "app:create_app()"

The factory pattern keeps the application testable by allowing multiple
isolated app instances to be created without side-effects.
"""

import logging
import sys

from flask import Flask  # type: ignore[import]

# Trigger config validation before anything else.  If GROQ_API_KEY is
# absent this raises RuntimeError immediately with a clear message.
from config import settings
from web.routes import main as main_blueprint


def create_app() -> Flask:
    """
    Construct and configure the Flask application.

    Returns
    -------
    Flask
        Fully configured application instance ready to serve requests.
    """
    app = Flask(
        __name__,
        static_folder="web/static",
        template_folder="web/templates",
    )
    app.secret_key = settings.SECRET_KEY

    # Register the main blueprint which owns all routes.
    app.register_blueprint(main_blueprint)

    # ------------------------------------------------------------------ #
    # Logging configuration
    # ------------------------------------------------------------------ #
    log_level = logging.DEBUG if settings.FLASK_DEBUG else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )

    logging.getLogger(__name__).info(
        "Application created (env=%s, debug=%s).",
        settings.FLASK_ENV,
        settings.FLASK_DEBUG,
    )

    return app


# --------------------------------------------------------------------------- #
# Development entry point
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    application = create_app()
    application.run(
        host="0.0.0.0",
        port=5000,
        debug=settings.FLASK_DEBUG,
        use_reloader=settings.FLASK_DEBUG,
    )
