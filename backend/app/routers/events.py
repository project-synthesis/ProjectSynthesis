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
    # E1b: Cross-process classification agreement bridge.
    # Internal counter updates only — no SSE broadcast, no event bus publish.
    if body.event_type == "classification_agreement_record":
        try:
            from app.services.classification_agreement import get_classification_agreement
            get_classification_agreement().record(
                heuristic_task_type=body.data.get("heuristic_task_type", ""),
                heuristic_domain=body.data.get("heuristic_domain", ""),
                llm_task_type=body.data.get("llm_task_type", ""),
                llm_domain=body.data.get("llm_domain", ""),
                prompt_snippet=body.data.get("prompt_snippet", ""),
            )
        except Exception as _ca_exc:
            logger.warning("Cross-process classification_agreement record failed: %s", _ca_exc)
        return OkResponse()
    if body.event_type == "classification_agreement_strategy_intel":
        try:
            from app.services.classification_agreement import get_classification_agreement
            get_classification_agreement().record_strategy_intel(
                had_intel=body.data.get("had_intel", False),
            )
        except Exception as _si_exc:
            logger.warning("Cross-process strategy_intel record failed: %s", _si_exc)
        return OkResponse()

    # General event routing — publish to in-process event bus for SSE delivery.
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
        except RuntimeError:
            # Event logger not yet initialized — lazy-init with defaults
            # so the ring buffer starts capturing immediately instead of
            # silently dropping events that arrive before lifespan completes.
            try:
                from app.services.taxonomy.event_logger import TaxonomyEventLogger, set_event_logger
                _tel = TaxonomyEventLogger(publish_to_bus=False)
                set_event_logger(_tel)
                _tel._buffer.append(body.data)
                logger.info(
                    "taxonomy_activity received before lifespan init "
                    "— lazy-initialized event logger for ring buffer"
                )
            except Exception as _init_exc:
                logger.warning(
                    "Failed to lazy-init event logger for ring buffer: %s",
                    _init_exc,
                )
        except Exception as _mirror_exc:
            logger.warning(
                "Failed to mirror taxonomy_activity to ring buffer: %s",
                _mirror_exc,
            )

    return OkResponse()


@router.get("/events")
async def event_stream(
    request: Request,
    last_event_id: str | None = None,
) -> StreamingResponse:
    """SSE endpoint -- streams real-time events with periodic keepalive.

    Supports ``Last-Event-ID`` header for reconnection replay: when an
    ``EventSource`` reconnects, the browser automatically sends the last
    received ``id`` so the server can replay missed events from its
    in-memory buffer.

    Also accepts ``last_event_id`` as a query parameter for manual
    reconnection (new ``EventSource`` instances created after custom
    backoff don't carry the header). The header takes priority.
    """
    resolved_last_id = request.headers.get("Last-Event-ID") or last_event_id
    logger.info(
        "New SSE event stream connection (Last-Event-ID=%s)", resolved_last_id,
    )

    async def generate():
        queue: asyncio.Queue = asyncio.Queue(maxsize=100)
        event_bus._subscribers.add(queue)
        # Track highest replayed seq to deduplicate events that arrived in
        # the queue while replay was in progress.
        replayed_up_to: int = 0
        try:
            # Replay missed events on reconnection
            if resolved_last_id is not None:
                try:
                    last_seq = int(resolved_last_id)
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
                    logger.warning("Invalid Last-Event-ID: %s", resolved_last_id)

            # Send sync event with current sequence so client knows its starting point
            yield (
                f"id: {event_bus.current_sequence}\n"
                f"event: sync\n"
                f"data: {{\"seq\": {event_bus.current_sequence}}}\n\n"
            )

            while True:
                try:
                    # 30s timeout: comfortably under the frontend's 90s
                    # staleness window so real events OR keepalive sync
                    # reset the timer with margin. Pre-v0.4.12 this was
                    # 45s + comment-only keepalive ("``: keepalive\\n\\n``")
                    # which browsers consume at the TCP layer for
                    # connection-keepalive but DO NOT fire any JS event
                    # handler for. The frontend's staleness detector
                    # therefore saw "no events for 90s" during long-
                    # running probes (LLM calls in subprocesses, zero
                    # DB activity → zero event_bus publishes) and
                    # falsely reported the connection as disconnected
                    # while it was actually healthy. We now emit a real
                    # ``event: sync`` every 30s so the frontend's
                    # ``recordSyncOrKeepalive`` handler fires, resetting
                    # staleness regardless of write-side traffic.
                    event = await asyncio.wait_for(queue.get(), timeout=30.0)
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
                    # Periodic sync keepalive: a real, JS-visible
                    # ``event: sync`` so the frontend's staleness timer
                    # resets. Carries the current sequence so clients
                    # that missed any events get the cue to reconcile.
                    yield (
                        f"id: {event_bus.current_sequence}\n"
                        f"event: sync\n"
                        f"data: {{\"seq\": {event_bus.current_sequence},"
                        f" \"keepalive\": true}}\n\n"
                    )
                except asyncio.CancelledError:
                    # Uvicorn's graceful shutdown timer expired — exit cleanly
                    # instead of letting the CancelledError propagate through
                    # StreamingResponse and produce an ASGI exception traceback.
                    return
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
