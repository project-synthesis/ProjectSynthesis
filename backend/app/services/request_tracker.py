import asyncio
import logging
from typing import Awaitable, Callable

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

logger = logging.getLogger(__name__)

class RequestTracker:
    """Tracks active requests to allow graceful draining on shutdown."""

    def __init__(self) -> None:
        self._in_flight: int = 0
        self._drain_event: asyncio.Event = asyncio.Event()
        self._drain_event.set()

    @property
    def in_flight(self) -> int:
        return self._in_flight

    def add_request(self) -> None:
        self._in_flight += 1
        self._drain_event.clear()

    def remove_request(self) -> None:
        self._in_flight = max(0, self._in_flight - 1)
        if self._in_flight == 0:
            self._drain_event.set()

    async def wait_for_drain(self, timeout: float = 5.0) -> bool:
        """Wait for all in-flight requests to complete.

        Returns True if drained, False if it timed out.
        """
        try:
            await asyncio.wait_for(self._drain_event.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            logger.warning(
                "RequestTracker drain timed out after %.1fs. %d requests still in flight.",
                timeout,
                self._in_flight
            )
            return False

class RequestTrackerMiddleware(BaseHTTPMiddleware):
    """FastAPI Middleware that integrates with a RequestTracker instance."""

    def __init__(self, app, tracker: RequestTracker) -> None:
        super().__init__(app)
        self.tracker = tracker

    async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        self.tracker.add_request()
        try:
            response = await call_next(request)
            return response
        finally:
            self.tracker.remove_request()
