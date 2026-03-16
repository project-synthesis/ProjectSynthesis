"""Tests for the in-process event bus."""

import asyncio

import pytest

from app.services.event_bus import EventBus


@pytest.fixture
def bus() -> EventBus:
    return EventBus()


@pytest.mark.asyncio
async def test_publish_to_subscriber(bus: EventBus) -> None:
    """Subscribe, publish, verify the subscriber receives the event."""
    received: list[dict] = []

    async def consume():
        async for event in bus.subscribe():
            received.append(event)
            break  # stop after first event

    task = asyncio.create_task(consume())
    await asyncio.sleep(0.01)  # let subscriber register

    bus.publish("test_event", {"key": "value"})
    await asyncio.wait_for(task, timeout=2.0)

    assert len(received) == 1
    assert received[0]["event"] == "test_event"
    assert received[0]["data"] == {"key": "value"}
    assert "timestamp" in received[0]


@pytest.mark.asyncio
async def test_multiple_subscribers(bus: EventBus) -> None:
    """Two subscribers should both receive the same event."""
    results_a: list[dict] = []
    results_b: list[dict] = []

    async def consume_a():
        async for event in bus.subscribe():
            results_a.append(event)
            break

    async def consume_b():
        async for event in bus.subscribe():
            results_b.append(event)
            break

    task_a = asyncio.create_task(consume_a())
    task_b = asyncio.create_task(consume_b())
    await asyncio.sleep(0.01)

    assert bus.subscriber_count == 2
    bus.publish("multi", {"n": 1})

    await asyncio.wait_for(asyncio.gather(task_a, task_b), timeout=2.0)

    assert len(results_a) == 1
    assert len(results_b) == 1
    assert results_a[0]["data"] == {"n": 1}
    assert results_b[0]["data"] == {"n": 1}


@pytest.mark.asyncio
async def test_no_subscribers(bus: EventBus) -> None:
    """Publishing with no subscribers should not raise."""
    bus.publish("orphan_event", {"x": 1})
    assert bus.subscriber_count == 0


@pytest.mark.asyncio
async def test_slow_subscriber_dropped(bus: EventBus) -> None:
    """A subscriber whose queue is full gets dropped on next publish."""
    # Manually add a full queue
    full_queue: asyncio.Queue = asyncio.Queue(maxsize=1)
    full_queue.put_nowait({"event": "filler", "data": {}, "timestamp": 0})
    bus._subscribers.add(full_queue)

    assert bus.subscriber_count == 1

    # This publish should drop the slow subscriber
    bus.publish("overflow", {"y": 2})

    assert bus.subscriber_count == 0


@pytest.mark.asyncio
async def test_subscriber_cleanup(bus: EventBus) -> None:
    """Subscriber count goes back to zero after the generator is closed."""
    assert bus.subscriber_count == 0

    gen = bus.subscribe()

    # Manually start the generator — registers the subscriber
    async def consume_one():
        return await gen.__anext__()

    task = asyncio.create_task(consume_one())
    await asyncio.sleep(0.01)
    assert bus.subscriber_count == 1

    bus.publish("cleanup_test", {})
    event = await asyncio.wait_for(task, timeout=2.0)
    assert event["event"] == "cleanup_test"

    # Explicitly close the generator to trigger finally
    await gen.aclose()
    assert bus.subscriber_count == 0
