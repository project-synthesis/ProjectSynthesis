import asyncio
from contextlib import asynccontextmanager

import pytest
from fastapi import FastAPI, Request

from app.services.request_tracker import RequestTracker, RequestTrackerMiddleware

events = []
tracker = RequestTracker()

@asynccontextmanager
async def custom_lifespan(app: FastAPI):
    events.append("startup")
    yield
    events.append("shutdown_started")
    await tracker.wait_for_drain(timeout=5.0)
    events.append("wal_checkpoint")
    events.append("shutdown_complete")

asgi_app = FastAPI(lifespan=custom_lifespan)
asgi_app.add_middleware(RequestTrackerMiddleware, tracker=tracker)

@asgi_app.get("/slow")
async def slow_endpoint(request: Request):
    events.append("request_started")
    await asyncio.sleep(0.5)
    events.append("request_complete")
    return {"status": "done"}

@pytest.mark.asyncio
async def test_lifespan_waits_for_request_tracker():
    events.clear()

    lifespan_gen = custom_lifespan(asgi_app)
    await lifespan_gen.__aenter__()

    # Simulate an ASGI request manually (it's simpler and bypasses ASGITransport lifespan handling entirely)
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/slow",
        "headers": [],
        "query_string": b"",
    }

    async def mock_receive():
        return {"type": "http.request", "body": b""}

    async def mock_send(message):
        pass

    # Fire the ASGI app call concurrently
    req_task = asyncio.create_task(asgi_app(scope, mock_receive, mock_send))

    # Wait until it hits the endpoint
    while "request_started" not in events:
        await asyncio.sleep(0.01)

    # Trigger shutdown (Phase 4.5) while in-flight
    shutdown_task = asyncio.create_task(lifespan_gen.__aexit__(None, None, None))

    # Wait for completion
    await req_task
    await shutdown_task

    assert events == [
        "startup",
        "request_started",
        "shutdown_started",
        "request_complete",
        "wal_checkpoint",
        "shutdown_complete"
    ]

