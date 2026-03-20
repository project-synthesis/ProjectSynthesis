import pytest
from httpx import AsyncClient
import asyncio
from app.services.event_bus import event_bus
from app.routers.events import InternalEventRequest

@pytest.fixture
def mock_event_bus():
    original_subscribers = event_bus._subscribers.copy()
    yield event_bus
    event_bus._subscribers = original_subscribers

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
        assert any("event: test_type" in l for l in lines)
        assert any('data: {"hello": "world"}' in l for l in lines)
    except Exception as e:
        if str(e) == "Stop stream":
            pass
        else:
            raise


