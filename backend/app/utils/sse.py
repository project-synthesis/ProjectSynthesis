"""Shared SSE formatting utility."""
import json
import logging

logger = logging.getLogger(__name__)


def format_sse(event_type: str, data: dict) -> str:
    try:
        payload = json.dumps({"event": event_type, **data})
    except (TypeError, ValueError) as exc:
        logger.error("SSE serialization failed for event '%s': %s", event_type, exc)
        payload = json.dumps({"event": "error", "error": "Internal error"})
    return f"data: {payload}\n\n"
