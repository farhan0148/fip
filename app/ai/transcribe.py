"""Speech to text through Groq. Server side only. One request per audio file, short timeout, no automatic retry.

Used for two explicit actions only: a customer pressing "Record" in Ask AI, and an FC creating the AI draft for a
finished, consented meeting recording. Nothing here runs on a page load.
"""

import logging
import os

import groq

from . import client

log = logging.getLogger("app.ai")

TIMEOUT_SECONDS = 60.0
DEFAULT_MODEL = "whisper-large-v3-turbo"
LANGUAGE_HINTS = {"en", "zh", "ms", "ta"}


def model_name():
    return client._setting("GROQ_TRANSCRIBE_MODEL") or DEFAULT_MODEL


def transcribe(audio, filename, mime, language=None):
    """Returns {"text": str, "segments": [{"start", "end", "text"}]}. Raises client.AIUnavailable(reason)."""
    api_key = client._setting("GROQ_API_KEY")
    if not api_key:
        raise client.AIUnavailable("not_configured")
    kwargs = {"file": (filename, audio, mime), "model": model_name(), "response_format": "verbose_json", "temperature": 0}
    if language in LANGUAGE_HINTS:
        kwargs["language"] = language
    try:
        provider = client.Groq(api_key=api_key, timeout=TIMEOUT_SECONDS, max_retries=0)
        result = provider.audio.transcriptions.create(**kwargs)
    except groq.APITimeoutError:
        log.warning("transcription failed: timeout")
        raise client.AIUnavailable("timeout") from None
    except groq.APIStatusError as exc:
        status = getattr(exc, "status_code", None)
        log.warning("transcription failed: http %s", status if status is not None else "?")
        raise client.AIUnavailable("busy" if status == 429 else "error") from None
    except Exception as exc:  # never include the message: it could echo request details
        log.warning("transcription failed: %s", type(exc).__name__)
        raise client.AIUnavailable("error") from None

    def field(obj, name, default=None):
        return obj.get(name, default) if isinstance(obj, dict) else getattr(obj, name, default)

    text = (field(result, "text", "") or "").strip()
    if not text:
        raise client.AIUnavailable("empty")
    segments = []
    for seg in field(result, "segments", None) or []:
        seg_text = (field(seg, "text", "") or "").strip()
        if seg_text:
            segments.append({"start": float(field(seg, "start", 0) or 0), "end": float(field(seg, "end", 0) or 0),
                             "text": seg_text})
    return {"text": text, "segments": segments or [{"start": 0.0, "end": 0.0, "text": text}]}
