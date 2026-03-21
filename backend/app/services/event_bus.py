"""In-process event bus for real-time cross-client notifications."""

import asyncio
import logging
import time
from collections.abc import AsyncGenerator

logger = logging.getLogger(__name__)

# Sentinel pushed into subscriber queues during shutdown to unblock
# SSE connections so they close naturally before uvicorn's graceful
# shutdown timer kicks in.
_SHUTDOWN_SENTINEL = object()


class EventBus:
    """Pub/sub backed by asyncio.Queue per subscriber."""

    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue] = set()
        self._shutting_down = False

    def publish(self, event_type: str, data: dict) -> None:
        if self._shutting_down:
            return
        payload = {"event": event_type, "data": data, "timestamp": time.time()}
        dead = []
        for queue in self._subscribers:
            try:
                queue.put_nowait(payload)
            except asyncio.QueueFull:
                dead.append(queue)
        for q in dead:
            self._subscribers.discard(q)
            logger.warning("Dropped slow event subscriber")
        if self._subscribers:
            logger.debug("Published %s to %d subscribers", event_type, len(self._subscribers))

    async def subscribe(self) -> AsyncGenerator[dict, None]:
        queue: asyncio.Queue = asyncio.Queue(maxsize=100)
        self._subscribers.add(queue)
        logger.info("Event subscriber connected (total: %d)", len(self._subscribers))
        try:
            while True:
                event = await queue.get()
                if event is _SHUTDOWN_SENTINEL:
                    logger.info("Event subscriber received shutdown signal")
                    return
                yield event
        finally:
            self._subscribers.discard(queue)
            logger.info("Event subscriber disconnected (total: %d)", len(self._subscribers))

    def shutdown(self) -> None:
        """Signal all subscribers to disconnect.

        Puts a sentinel into every queue. Subscribers (both the
        ``subscribe()`` generator and the SSE router's manual queue)
        check for the sentinel and return, causing their HTTP
        connections to close naturally.
        """
        self._shutting_down = True
        count = len(self._subscribers)
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(_SHUTDOWN_SENTINEL)
            except asyncio.QueueFull:
                pass
        if count:
            logger.info("Shutdown sentinel sent to %d subscribers", count)

    def is_shutdown_event(self, event: object) -> bool:
        """Check if an event is the shutdown sentinel."""
        return event is _SHUTDOWN_SENTINEL

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)


event_bus = EventBus()
