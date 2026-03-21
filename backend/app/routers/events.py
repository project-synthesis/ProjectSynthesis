"""Real-time SSE event stream endpoint."""

import asyncio
import json
import logging

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.services.event_bus import event_bus

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["events"])


class InternalEventRequest(BaseModel):
    event_type: str = Field(description="Event type identifier (e.g. 'optimization_created').")
    data: dict = Field(description="Event payload data dictionary.")


class OkResponse(BaseModel):
    ok: bool = Field(default=True, description="Operation success indicator.")


@router.post("/events/_publish")
async def publish_event(body: InternalEventRequest, request: Request) -> OkResponse:
    """Internal endpoint for cross-process event publishing (used by MCP server).

    For ``routing_state_changed`` events, the backend's RoutingManager is
    synced first and handles local event publishing (avoiding duplicates).
    All other event types are published directly to the bus.
    """
    if body.event_type == "routing_state_changed":
        routing = getattr(request.app.state, "routing", None)
        if routing:
            # sync_from_event publishes to event_bus only if state changed.
            # Do NOT also publish the raw event — that would double-notify
            # SSE subscribers (frontend would show duplicate toasts).
            routing.sync_from_event(body.data)
        else:
            # No routing manager — publish raw event as fallback
            event_bus.publish(body.event_type, body.data)
    else:
        event_bus.publish(body.event_type, body.data)

    return OkResponse()


@router.get("/events")
async def event_stream() -> StreamingResponse:
    """SSE endpoint -- streams real-time events with periodic keepalive."""
    logger.info("New SSE event stream connection")

    async def generate():
        queue: asyncio.Queue = asyncio.Queue(maxsize=100)
        event_bus._subscribers.add(queue)
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=25.0)
                    if event_bus.is_shutdown_event(event):
                        return
                    event_type = event["event"]
                    data = json.dumps(event["data"])
                    yield f"event: {event_type}\ndata: {data}\n\n"
                except asyncio.TimeoutError:
                    # SSE keepalive comment (ignored by EventSource, keeps connection alive)
                    yield ": keepalive\n\n"
        finally:
            event_bus._subscribers.discard(queue)
            logger.info("SSE subscriber disconnected")

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
