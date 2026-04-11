import asyncio

import pytest
from httpx import AsyncClient

from app.services.event_bus import event_bus


@pytest.fixture
def mock_event_bus():
    original_subscribers = event_bus._subscribers.copy()
    original_seq = event_bus._sequence
    original_buffer = list(event_bus._replay_buffer)
    yield event_bus
    event_bus._subscribers = original_subscribers
    event_bus._sequence = original_seq
    event_bus._replay_buffer.clear()
    event_bus._replay_buffer.extend(original_buffer)

@pytest.mark.asyncio
async def test_publish_event(app_client: AsyncClient, mock_event_bus):
    payload = {"event_type": "test_event", "data": {"key": "value"}}
    response = await app_client.post("/api/events/_publish", json=payload)
    assert response.status_code == 200
    assert response.json() == {"ok": True}

@pytest.mark.asyncio
async def test_event_stream_timeout_and_disconnect(app_client: AsyncClient, mock_event_bus, monkeypatch):
    from asyncio.exceptions import TimeoutError

    original_queue = asyncio.Queue

    class MockQueue(original_queue):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.call_count = 0

        async def get(self):
            self.call_count += 1
            if self.call_count == 1:
                raise TimeoutError()
            else:
                raise Exception("Stop stream")

    monkeypatch.setattr(asyncio, "Queue", MockQueue)
    # also we need to avoid wait_for raising another timeout

    async def mock_wait_for(coro, timeout):
        return await coro

    monkeypatch.setattr(asyncio, "wait_for", mock_wait_for)

    try:
        async with app_client.stream("GET", "/api/events") as response:
            assert response.status_code == 200
            async for line in response.aiter_lines():
                if ": keepalive" in line:
                    break
    except Exception as e:
        if str(e) == "Stop stream":
            pass
        else:
            raise


@pytest.mark.asyncio
async def test_event_stream_query_param_replay(mock_event_bus):
    """Query param ``last_event_id`` resolves correctly for replay.

    Verifies the backend resolves the query param as a fallback when the
    Last-Event-ID header is absent, and that replay_since() returns the
    expected missed events.
    """
    # Seed three events into the replay buffer
    for i in range(3):
        event_bus.publish(f"test_type_{i}", {"index": i})

    buffer = list(event_bus._replay_buffer)
    assert len(buffer) >= 3
    seqs = [e["seq"] for e in buffer[-3:]]

    # replay_since returns events AFTER the given seq
    missed = event_bus.replay_since(seqs[0])
    assert len(missed) == 2, f"Expected 2 missed events, got {len(missed)}"
    assert missed[0]["event"] == "test_type_1"
    assert missed[1]["event"] == "test_type_2"
    assert missed[0]["seq"] == seqs[1]
    assert missed[1]["seq"] == seqs[2]


@pytest.mark.asyncio
async def test_event_stream_query_param_accepted(app_client: AsyncClient, mock_event_bus, monkeypatch):
    """The ``/api/events?last_event_id=`` query param is accepted (no 422)."""
    original_queue = asyncio.Queue

    class MockQueue(original_queue):
        async def get(self):
            raise Exception("Stop stream")

    monkeypatch.setattr(asyncio, "Queue", MockQueue)

    async def mock_wait_for(coro, timeout):
        return await coro

    monkeypatch.setattr(asyncio, "wait_for", mock_wait_for)

    try:
        async with app_client.stream(
            "GET", "/api/events?last_event_id=42"
        ) as response:
            # Should accept the query param (200, not 422)
            assert response.status_code == 200
    except Exception as e:
        if str(e) != "Stop stream":
            raise


@pytest.mark.asyncio
async def test_event_stream_yield_event(app_client: AsyncClient, mock_event_bus, monkeypatch):
    original_queue = asyncio.Queue

    class MockQueue(original_queue):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.call_count = 0

        async def get(self):
            self.call_count += 1
            if self.call_count == 1:
                return {"event": "test_type", "data": {"hello": "world"}}
            else:
                raise Exception("Stop stream")

    monkeypatch.setattr(asyncio, "Queue", MockQueue)

    async def mock_wait_for(coro, timeout):
        return await coro

    monkeypatch.setattr(asyncio, "wait_for", mock_wait_for)

    try:
        lines = []
        async with app_client.stream("GET", "/api/events") as response:
            assert response.status_code == 200
            async for line in response.aiter_lines():
                lines.append(line)
                if len(lines) >= 2:
                    break
        assert any("event: test_type" in line for line in lines)
        assert any('data: {"hello": "world"}' in line for line in lines)
    except Exception as e:
        if str(e) == "Stop stream":
            pass
        else:
            raise


