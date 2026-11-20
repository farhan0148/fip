"""AI explanation layer. This package is the only place that talks to an AI provider.

The calculator (app/simulator.py and static/js/simulator-core.js) never imports from
here and never makes a network call. The AI only explains figures the calculator has
already produced.
"""

from .limiter import SlidingWindowLimiter
from .routes import bp


def register(app):
    app.config.setdefault("AI_RATE_LIMIT", 5)      # explanation requests ...
    app.config.setdefault("AI_RATE_WINDOW", 60)    # ... per user, per this many seconds
    app.extensions["ai_limiter"] = SlidingWindowLimiter(
        app.config["AI_RATE_LIMIT"], app.config["AI_RATE_WINDOW"]
    )
    app.register_blueprint(bp)
