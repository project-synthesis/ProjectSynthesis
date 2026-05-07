"""In-process event bus for real-time cross-client notifications."""

import asyncio
import logging
import time
from collections import deque
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# Sentinel pushed into subscriber queues during shutdown to unblock
# SSE connections so they close naturally before uvicorn's graceful
# shutdown timer kicks in.
_SHUTDOWN_SENTINEL = object()

# Replay buffer size — keeps the last N events for SSE reconnection replay.
# During warm-path bursts, 30+ events per phase can churn through quickly,
# causing reconnecting clients to miss events if the buffer is too small.
# ~500 events × ~1KB each ≈ 500KB memory, negligible.
_REPLAY_BUFFER_SIZE = 500


class EventBus:
    """Pub/sub backed by asyncio.Queue per subscriber.

    Features:
    - Monotonically increasing sequence numbers on every event
    - Bounded replay buffer for SSE ``Last-Event-ID`` reconnection
    - Overflow-safe: drops oldest queued event instead of removing subscriber
    """

    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue] = set()
        self._shutting_down = False
        self._sequence: int = 0
        self._replay_buffer: deque[dict] = deque(maxlen=_REPLAY_BUFFER_SIZE)

    def publish(self, event_type: str, data: dict | Any) -> None:
        if self._shutting_down:
            return
        self._sequence += 1
        payload = {
            "event": event_type,
            "data": data,
            "timestamp": time.time(),
            "seq": self._sequence,
        }
        self._replay_buffer.append(payload)
        for queue in self._subscribers:
            try:
                queue.put_nowait(payload)
            except asyncio.QueueFull:
                # Drop oldest event from this subscriber's queue to make room,
                # keeping the connection alive instead of killing the subscriber.
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                try:
                    queue.put_nowait(payload)
                except asyncio.QueueFull:
                    pass  # Truly stuck — skip this event for this subscriber
                logger.warning(
                    "Event bus queue overflow for subscriber — dropped oldest event"
                )
        if self._subscribers:
            logger.debug(
                "Published %s (seq=%d) to %d subscribers",
                event_type, self._sequence, len(self._subscribers),
            )

    def replay_since(self, seq: int) -> list[dict]:
        """Return all buffered events with sequence number > *seq*.

        Used by the SSE endpoint to replay missed events on reconnection
        via the ``Last-Event-ID`` header.
        """
        return [e for e in self._replay_buffer if e["seq"] > seq]

    @property
    def current_sequence(self) -> int:
        """Current sequence counter (last published event's seq)."""
        return self._sequence

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

    def subscribe_for_run(self, run_id: str) -> "_RunSubscription":
        """Filtered subscription. Yields events where data.run_id == run_id.

        Includes 500ms replay window from the existing _replay_buffer.
        Excludes events without run_id in their data dict (taxonomy_changed,
        optimization_created, etc.).
        """
        return _RunSubscription(self, run_id)


@dataclass
class _EventForRun:
    """Lightweight envelope for SSE consumers of a per-run subscription."""
    kind: str
    payload: dict


class _RunSubscription:
    """Filtered async iterator yielding only events where payload.run_id == run_id.

    Backed by a per-instance asyncio.Queue that the parent EventBus pushes to
    via the existing _subscribers set. Filter happens at iteration time so
    subscribers that don't carry run_id are excluded silently.
    """

    def __init__(self, bus: "EventBus", run_id: str) -> None:
        self._bus = bus
        self._run_id = run_id
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=256)
        self._closed = False

        # Register on bus's existing subscribers set
        self._bus._subscribers.add(self._queue)

        # 500ms ring-buffer replay from the existing _replay_buffer
        now = time.time()
        for payload in list(self._bus._replay_buffer):
            if now - payload["timestamp"] > 0.5:
                continue
            data = payload.get("data") or {}
            if isinstance(data, dict) and data.get("run_id") == run_id:
                try:
                    self._queue.put_nowait(payload)
                except asyncio.QueueFull:
                    pass  # replay best-effort

    def __aiter__(self) -> "_RunSubscription":
        return self

    async def __anext__(self) -> _EventForRun:
        while True:
            payload = await self._queue.get()
            # Two distinct sentinel conditions: aclose() pushes None;
            # bus.shutdown() pushes the bus's _SHUTDOWN_SENTINEL singleton.
            # Handle both safely without crashing on .get() of a non-dict.
            if payload is None:
                self._cleanup()
                raise StopAsyncIteration
            if not isinstance(payload, dict):
                # _SHUTDOWN_SENTINEL or any other non-dict marker
                self._cleanup()
                raise StopAsyncIteration
            data = payload.get("data") or {}
            if isinstance(data, dict) and data.get("run_id") == self._run_id:
                return _EventForRun(
                    kind=payload.get("event"),
                    payload=data,
                )

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._queue.put_nowait(None)  # sentinel
        except asyncio.QueueFull:
            pass
        self._cleanup()

    def _cleanup(self) -> None:
        self._bus._subscribers.discard(self._queue)


event_bus = EventBus()
