"""Real-time SSE event stream endpoint."""

import json
import logging

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from app.services.event_bus import event_bus

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["events"])


@router.get("/events")
async def event_stream():
    """SSE endpoint -- streams real-time events to all connected clients."""
    logger.info("New SSE event stream connection")

    async def generate():
        async for event in event_bus.subscribe():
            event_type = event["event"]
            data = json.dumps(event["data"])
            yield f"event: {event_type}\ndata: {data}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
