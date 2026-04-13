"""Cross-process event bus notification for the MCP server.

The MCP server runs in a separate process from the FastAPI backend. This
module provides a shared HTTP-based notification function that both
``mcp_server.py`` and ``sampling_pipeline.py`` use to publish events to
the backend's in-process event bus via the ``/api/events/_publish`` endpoint.

Reliability: the ``optimization_created`` event is the sole trigger for
taxonomy extraction (embedding + cluster assignment).  A dropped event
leaves the optimization orphaned (no cluster_id, no embeddings) until
the next server restart backfill.  For this reason, critical events get
a retry with a short delay.

Observability events (``taxonomy_activity``) also get a retry — dropped
events cause intermittent gaps in the frontend Activity panel.
"""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)

# Events that drive data-integrity pipelines (taxonomy extraction,
# domain caches) or observability (activity panel).  These get a retry
# on failure.
_CRITICAL_EVENTS = frozenset({
    "optimization_created",
    "taxonomy_changed",
    "domain_created",
    "taxonomy_activity",
    "classification_agreement_record",
    "classification_agreement_strategy_intel",
})

# Longer timeout for events that may arrive during warm-path processing,
# which holds DB sessions for 10-20s.  Default 5s is too aggressive.
_TIMEOUT_BY_EVENT: dict[str, float] = {
    "taxonomy_activity": 15.0,
}
_DEFAULT_TIMEOUT = 5.0

_PUBLISH_URL = "http://127.0.0.1:8000/api/events/_publish"

# Reusable httpx client — avoids per-call connection overhead for
# high-frequency events like optimization_status (~10 calls per pipeline).
_httpx_client: object = None  # httpx.AsyncClient, lazily initialized


def _get_client() -> object:
    """Lazy-init a module-level reusable httpx client."""
    global _httpx_client
    if _httpx_client is None or _httpx_client.is_closed:
        import httpx
        _httpx_client = httpx.AsyncClient()
    return _httpx_client


async def notify_event_bus(event_type: str, data: dict) -> None:
    """Notify the backend event bus via HTTP POST.

    Non-fatal: logs and swallows any transport or connection errors so the
    calling tool/pipeline is never interrupted by event bus failures.

    Critical and observability events get one retry after a 1-second delay.
    """
    max_attempts = 2 if event_type in _CRITICAL_EVENTS else 1
    timeout = _TIMEOUT_BY_EVENT.get(event_type, _DEFAULT_TIMEOUT)

    for attempt in range(1, max_attempts + 1):
        try:
            http = _get_client()
            resp = await http.post(
                _PUBLISH_URL,
                json={"event_type": event_type, "data": data},
                timeout=timeout,
            )
            resp.raise_for_status()
            return  # success
        except BaseException as exc:
            # Catch BaseException (not just Exception) to handle
            # asyncio.CancelledError from ASGI task cancellation.
            if attempt < max_attempts:
                logger.warning(
                    "notify_event_bus(%s) attempt %d/%d failed: %s — retrying in 1s",
                    event_type, attempt, max_attempts, exc,
                )
                try:
                    await asyncio.sleep(1.0)
                except BaseException:
                    break  # Cancelled during sleep — give up
            else:
                # Final attempt failed — always warn so failures are visible
                logger.warning(
                    "notify_event_bus(%s) FAILED after %d attempts: %s  "
                    "[data_id=%s]",
                    event_type, max_attempts, exc,
                    data.get("id", data.get("op", "?")),
                )
