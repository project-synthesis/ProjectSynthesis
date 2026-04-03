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

    # Mirror taxonomy_activity events into the backend's ring buffer so
    # the /api/clusters/activity endpoint returns cross-process events.
    if body.event_type == "taxonomy_activity":
        try:
            from app.services.taxonomy.event_logger import get_event_logger
            get_event_logger()._buffer.append(body.data)
        except (RuntimeError, Exception):
            pass  # Non-fatal

    return OkResponse()


@router.get("/events")
async def event_stream(request: Request) -> StreamingResponse:
    """SSE endpoint -- streams real-time events with periodic keepalive.

    Supports ``Last-Event-ID`` header for reconnection replay: when an
    ``EventSource`` reconnects, the browser automatically sends the last
    received ``id`` so the server can replay missed events from its
    in-memory buffer.
    """
    last_event_id = request.headers.get("Last-Event-ID")
    logger.info(
        "New SSE event stream connection (Last-Event-ID=%s)", last_event_id,
    )

    async def generate():
        queue: asyncio.Queue = asyncio.Queue(maxsize=100)
        event_bus._subscribers.add(queue)
        # Track highest replayed seq to deduplicate events that arrived in
        # the queue while replay was in progress.
        replayed_up_to: int = 0
        try:
            # Replay missed events on reconnection
            if last_event_id is not None:
                try:
                    last_seq = int(last_event_id)
                    missed = event_bus.replay_since(last_seq)
                    for evt in missed:
                        event_type = evt["event"]
                        data = json.dumps(evt["data"])
                        seq = evt["seq"]
                        yield f"id: {seq}\nevent: {event_type}\ndata: {data}\n\n"
                        replayed_up_to = max(replayed_up_to, seq)
                    logger.info(
                        "Replayed %d missed events (since seq %d)", len(missed), last_seq,
                    )
                except (ValueError, TypeError):
                    logger.warning("Invalid Last-Event-ID: %s", last_event_id)

            # Send sync event with current sequence so client knows its starting point
            yield (
                f"id: {event_bus.current_sequence}\n"
                f"event: sync\n"
                f"data: {{\"seq\": {event_bus.current_sequence}}}\n\n"
            )

            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=25.0)
                    if event_bus.is_shutdown_event(event):
                        return
                    # Skip events already sent during replay (deduplication)
                    seq = event.get("seq", 0)
                    if seq and seq <= replayed_up_to:
                        continue
                    event_type = event["event"]
                    data = json.dumps(event["data"])
                    yield f"id: {seq}\nevent: {event_type}\ndata: {data}\n\n"
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
