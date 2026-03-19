"""Cross-process event bus notification for the MCP server.

The MCP server runs in a separate process from the FastAPI backend. This
module provides a shared HTTP-based notification function that both
``mcp_server.py`` and ``sampling_pipeline.py`` use to publish events to
the backend's in-process event bus via the ``/api/events/_publish`` endpoint.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def notify_event_bus(event_type: str, data: dict) -> None:
    """Notify the backend event bus via HTTP POST.

    Non-fatal: silently logs and swallows any transport or connection errors
    so the calling tool/pipeline is never interrupted by event bus failures.
    """
    try:
        import httpx

        async with httpx.AsyncClient() as http:
            await http.post(
                "http://127.0.0.1:8000/api/events/_publish",
                json={"event_type": event_type, "data": data},
                timeout=5.0,
            )
    except BaseException:
        # Catch BaseException (not just Exception) to handle
        # asyncio.CancelledError — this can be raised when the caller
        # is in a finally block during ASGI task cancellation.
        logger.debug("Failed to notify backend event bus", exc_info=True)
