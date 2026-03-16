"""In-process event bus for real-time cross-client notifications."""

import asyncio
import logging
import time
from collections.abc import AsyncGenerator

logger = logging.getLogger(__name__)


class EventBus:
    """Pub/sub backed by asyncio.Queue per subscriber."""

    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue] = set()

    def publish(self, event_type: str, data: dict) -> None:
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
                yield event
        finally:
            self._subscribers.discard(queue)
            logger.info("Event subscriber disconnected (total: %d)", len(self._subscribers))

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)


event_bus = EventBus()
