"""Tests for event_bus.subscribe_for_run (Foundation P3, v0.4.18)."""
from __future__ import annotations

import asyncio

import pytest

pytestmark = pytest.mark.asyncio


async def test_subscribe_for_run_filters_by_run_id() -> None:
    """Only events with data.run_id == subscribed run_id are yielded.

    Note: existing EventBus.publish() takes (event_type, data) — data dict
    is what carries run_id, not "payload".
    """
    from app.services.event_bus import event_bus
    sub = event_bus.subscribe_for_run("run-1")

    event_bus.publish("probe_started", {"run_id": "run-1", "topic": "x"})
    event_bus.publish("probe_started", {"run_id": "other", "topic": "y"})
    event_bus.publish("probe_completed", {"run_id": "run-1"})

    received = []
    async def collect():
        async for evt in sub:
            received.append(evt)
            if evt.kind == "probe_completed":
                break
    await asyncio.wait_for(collect(), timeout=2)

    kinds = [e.kind for e in received]
    assert kinds == ["probe_started", "probe_completed"]
    assert all(e.payload.get("run_id") == "run-1" for e in received)


async def test_subscribe_for_run_excludes_events_without_run_id() -> None:
    """Events that don't carry run_id (taxonomy_changed, optimization_created,
    etc.) are filtered out at iteration time."""
    from app.services.event_bus import event_bus
    sub = event_bus.subscribe_for_run("run-x")

    event_bus.publish("taxonomy_changed", {"trigger": "test"})  # no run_id
    event_bus.publish("optimization_created", {"id": "o1"})  # no run_id
    event_bus.publish("probe_completed", {"run_id": "run-x"})

    received = []
    async def collect():
        async for evt in sub:
            received.append(evt)
            if evt.kind == "probe_completed":
                break
    await asyncio.wait_for(collect(), timeout=2)

    kinds = [e.kind for e in received]
    assert kinds == ["probe_completed"]


async def test_subscribe_for_run_replay_buffer_500ms() -> None:
    """Events fired within the last 500ms before subscription are replayed
    from EventBus._replay_buffer."""
    from app.services.event_bus import event_bus

    event_bus.publish("probe_started", {"run_id": "rb-1"})
    await asyncio.sleep(0.1)  # 100ms — within replay window

    sub = event_bus.subscribe_for_run("rb-1")
    received = []
    async def collect():
        async for evt in sub:
            received.append(evt)
            break  # first event only
    await asyncio.wait_for(collect(), timeout=2)

    assert len(received) == 1
    assert received[0].kind == "probe_started"


async def test_subscribe_for_run_aclose_terminates() -> None:
    """Calling aclose() on the subscription stops iteration cleanly."""
    from app.services.event_bus import event_bus
    sub = event_bus.subscribe_for_run("close-1")
    await sub.aclose()  # Should not raise


async def test_subscribe_for_run_does_not_break_existing_subscribers() -> None:
    """Adding a per-run subscription must not regress the global subscribe().

    EventBus.subscribe() is an async generator (yields per-event payloads), so
    consume it with `async for`, NOT `await`. Capture the first event and
    assert shape.
    """
    from app.services.event_bus import event_bus
    run_sub = event_bus.subscribe_for_run("coexist-1")

    # Subscribe to the global async-generator stream
    global_events = []
    async def consume_global():
        async for payload in event_bus.subscribe():
            global_events.append(payload)
            if len(global_events) >= 1:
                break
    global_task = asyncio.create_task(consume_global())
    await asyncio.sleep(0)  # let subscription register

    event_bus.publish("probe_completed", {"run_id": "coexist-1"})
    await asyncio.wait_for(global_task, timeout=2)

    # Global subscriber sees the event
    assert global_events[0]["event"] == "probe_completed"
    assert global_events[0]["data"]["run_id"] == "coexist-1"

    # Run-filtered subscriber also sees it
    received = []
    async def collect():
        async for evt in run_sub:
            received.append(evt)
            break
    await asyncio.wait_for(collect(), timeout=2)
    assert received[0].kind == "probe_completed"

    await run_sub.aclose()


async def test_subscribe_for_run_handles_bus_shutdown_sentinel_gracefully() -> None:
    """If event_bus.shutdown() pushes a non-dict sentinel into all subscriber
    queues, the subscription terminates cleanly via the non-dict guard in
    __anext__ — does NOT crash on .get() against the sentinel."""
    from app.services.event_bus import event_bus
    sub = event_bus.subscribe_for_run("shutdown-1")

    # Simulate a shutdown pushing a non-dict object directly into the queue
    sub._queue.put_nowait(object())  # non-dict, non-None

    received = []
    async def collect():
        async for evt in sub:
            received.append(evt)
    # Should terminate via StopAsyncIteration, no crash
    await asyncio.wait_for(collect(), timeout=2)
    assert received == []
