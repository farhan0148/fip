"""Groq client. Server side only.

GROQ_API_KEY and GROQ_MODEL are read from the server environment (.env). There is no
default model: check https://console.groq.com/docs/models and set GROQ_MODEL yourself.
The key and the prompts are never logged and never returned to the browser.
"""

import logging
import os

import groq
from groq import Groq

log = logging.getLogger("app.ai")

TIMEOUT_SECONDS = 10.0   # per attempt
MAX_RETRIES = 1          # one retry on connection errors, 408, 409, 429 and 5xx
MAX_OUTPUT_TOKENS = 500
_PLACEHOLDER_PREFIX = "replace-with"


class AIUnavailable(Exception):
    """The provider could not give an answer. `reason` is a short code, never provider text."""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def _setting(name):
    value = (os.environ.get(name) or "").strip()
    return "" if value.lower().startswith(_PLACEHOLDER_PREFIX) else value


def is_configured():
    return bool(_setting("GROQ_API_KEY") and _setting("GROQ_MODEL"))


def complete_json(system_prompt, user_message):
    """Ask the model for one JSON object and return its raw text.

    Raises AIUnavailable("not_configured" | "timeout" | "error" | "empty").
    """
    api_key, model = _setting("GROQ_API_KEY"), _setting("GROQ_MODEL")
    if not api_key or not model:
        raise AIUnavailable("not_configured")
    try:
        client = Groq(api_key=api_key, timeout=TIMEOUT_SECONDS, max_retries=MAX_RETRIES)
        completion = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            temperature=0,
            max_tokens=MAX_OUTPUT_TOKENS,
            response_format={"type": "json_object"},
        )
    except groq.APITimeoutError:
        log.warning("groq call failed: timeout")
        raise AIUnavailable("timeout") from None
    except groq.APIStatusError as exc:
        # Status code only. The response body can echo request details.
        log.warning("groq call failed: http %s", getattr(exc, "status_code", "?"))
        raise AIUnavailable("error") from None
    except Exception as exc:  # connection errors, SDK errors; log the class, not the message
        log.warning("groq call failed: %s", type(exc).__name__)
        raise AIUnavailable("error") from None

    try:
        content = completion.choices[0].message.content
    except (AttributeError, IndexError, TypeError):
        raise AIUnavailable("empty") from None
    if not isinstance(content, str) or not content.strip():
        raise AIUnavailable("empty")
    return content
