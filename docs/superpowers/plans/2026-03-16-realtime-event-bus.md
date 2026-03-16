# Real-Time Bidirectional Event Bus — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When an optimization is created from ANY source (web UI, MCP tool, REST API), all connected clients see it immediately — the web UI auto-refreshes History and shows the result, and MCP clients can subscribe to optimization events.

**Architecture:** A lightweight in-process event bus (`EventBus`) backed by `asyncio.Queue` per subscriber. Backend services publish events (optimization_created, feedback_submitted, refinement_turn). The frontend connects to a persistent SSE endpoint (`GET /api/events`) that streams these events. MCP tools publish to the same bus after completing operations. No Redis, no external message broker — single-process in-memory pub/sub.

**Tech Stack:** Python asyncio (Queue + set of subscribers), FastAPI StreamingResponse (SSE), Svelte EventSource

---

## Design

### Event flow

```
MCP tool call (synthesis_optimize)
    → pipeline completes → db.commit()
    → event_bus.publish("optimization_created", {id, trace_id, task_type, score, ...})
    → all SSE subscribers receive it instantly

Web UI forge (POST /api/optimize)
    → pipeline completes → db.commit()
    → event_bus.publish("optimization_created", {id, trace_id, ...})
    → MCP clients subscribed to events see it

Web UI feedback (POST /api/feedback)
    → feedback persisted → adaptation updated
    → event_bus.publish("feedback_submitted", {optimization_id, rating})
```

### Event types

| Event | Published when | Payload |
|-------|---------------|---------|
| `optimization_created` | Any optimization completes (pipeline or passthrough) | `{id, trace_id, task_type, strategy_used, overall_score, provider, status}` |
| `optimization_failed` | Pipeline error | `{trace_id, error}` |
| `feedback_submitted` | User submits feedback | `{optimization_id, rating}` |
| `refinement_turn` | Refinement turn completes | `{optimization_id, version, overall_score}` |

### SSE endpoint

```
GET /api/events
→ Content-Type: text/event-stream
→ event: optimization_created
→ data: {"id": "...", "overall_score": 7.5, ...}
```

The frontend connects on page load. If the connection drops, EventSource auto-reconnects.

### Why not WebSocket?

SSE is simpler, one-directional (server→client), works through nginx without upgrade headers, and we already use SSE for optimization streaming. The frontend only needs to RECEIVE events — it sends actions via REST/POST. True bidirectional (WebSocket) adds complexity without benefit here.

---

## File Structure

### Create

| File | Responsibility |
|------|---------------|
| `backend/app/services/event_bus.py` | In-process pub/sub with asyncio.Queue per subscriber |
| `backend/app/routers/events.py` | `GET /api/events` SSE endpoint |
| `backend/tests/test_event_bus.py` | Event bus tests |

### Modify

| File | Changes |
|------|---------|
| `backend/app/services/pipeline.py` | Publish `optimization_created` after persist |
| `backend/app/services/feedback_service.py` | Publish `feedback_submitted` after create |
| `backend/app/services/refinement_service.py` | Publish `refinement_turn` after persist |
| `backend/app/mcp_server.py` | Publish `optimization_created` in save_result |
| `backend/app/main.py` | Include events router |
| `frontend/src/lib/api/client.ts` | Add `connectEventStream()` function |
| `frontend/src/lib/stores/forge.svelte.ts` | Listen for `optimization_created` events |
| `frontend/src/routes/+page.svelte` | Connect to event stream on mount |

---

## Chunk 1: Backend Event Bus + SSE Endpoint

### Task 1: EventBus Service

**Files:**
- Create: `backend/app/services/event_bus.py`
- Create: `backend/tests/test_event_bus.py`

The event bus is a singleton with publish/subscribe:

```python
# backend/app/services/event_bus.py
"""In-process event bus for real-time cross-client notifications."""

import asyncio
import json
import logging
import time
from collections.abc import AsyncGenerator

logger = logging.getLogger(__name__)


class EventBus:
    """Pub/sub event bus backed by asyncio.Queue per subscriber."""

    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue] = set()

    def publish(self, event_type: str, data: dict) -> None:
        """Publish an event to all subscribers. Non-blocking, fire-and-forget."""
        payload = {"event": event_type, "data": data, "timestamp": time.time()}
        dead = []
        for queue in self._subscribers:
            try:
                queue.put_nowait(payload)
            except asyncio.QueueFull:
                dead.append(queue)
        # Clean up slow subscribers
        for q in dead:
            self._subscribers.discard(q)
            logger.warning("Dropped slow event subscriber")
        if self._subscribers:
            logger.debug("Published %s to %d subscribers", event_type, len(self._subscribers))

    async def subscribe(self) -> AsyncGenerator[dict, None]:
        """Subscribe to events. Yields event dicts as they arrive."""
        queue: asyncio.Queue = asyncio.Queue(maxsize=100)
        self._subscribers.add(queue)
        logger.debug("New subscriber (total: %d)", len(self._subscribers))
        try:
            while True:
                event = await queue.get()
                yield event
        finally:
            self._subscribers.discard(queue)
            logger.debug("Subscriber removed (total: %d)", len(self._subscribers))

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)


# Singleton instance
event_bus = EventBus()
```

**Tests (5):**
1. `test_publish_to_subscriber` — subscribe, publish, receive
2. `test_multiple_subscribers` — two subscribers both receive
3. `test_no_subscribers` — publish with none, no error
4. `test_slow_subscriber_dropped` — fill queue, verify dropped
5. `test_subscriber_cleanup` — subscribe, cancel, verify removed

- [ ] **Steps: TDD → commit**

### Task 2: SSE Events Router

**Files:**
- Create: `backend/app/routers/events.py`
- Modify: `backend/app/main.py`

```python
# backend/app/routers/events.py
"""Real-time event stream endpoint."""

import json
import logging
from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from app.services.event_bus import event_bus

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["events"])


@router.get("/events")
async def event_stream():
    """SSE endpoint — streams real-time events to connected clients."""
    async def generate():
        async for event in event_bus.subscribe():
            data = json.dumps(event)
            yield f"event: {event['event']}\ndata: {json.dumps(event['data'])}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
```

Wire into main.py with the existing router pattern.

- [ ] **Steps: implement → test manually with curl → commit**

### Task 3: Publish Events from Pipeline + MCP + Feedback

**Files:**
- Modify: `backend/app/services/pipeline.py`
- Modify: `backend/app/mcp_server.py`
- Modify: `backend/app/services/feedback_service.py`
- Modify: `backend/app/services/refinement_service.py`

In `pipeline.py`, after the DB commit in the success path:
```python
from app.services.event_bus import event_bus
event_bus.publish("optimization_created", {
    "id": opt_id,
    "trace_id": trace_id,
    "task_type": analysis.task_type,
    "strategy_used": strategy_name,
    "overall_score": optimized_scores.overall,
    "provider": provider.name,
    "status": "completed",
})
```

In `mcp_server.py` `synthesis_save_result`, after commit:
```python
event_bus.publish("optimization_created", {
    "id": opt_id,
    "trace_id": trace_id,
    "task_type": task_type or "unknown",
    "strategy_used": strategy_used or "unknown",
    "overall_score": overall,
    "provider": "mcp_passthrough",
    "status": "completed",
})
```

In `feedback_service.py`, after creating feedback:
```python
event_bus.publish("feedback_submitted", {
    "optimization_id": optimization_id,
    "rating": rating,
})
```

In `refinement_service.py`, after persisting a turn:
```python
event_bus.publish("refinement_turn", {
    "optimization_id": optimization_id,
    "version": new_version,
    "overall_score": optimized_scores.overall,
})
```

- [ ] **Steps: implement → test with curl to /api/events in one terminal, trigger optimization in another → commit**

---

## Chunk 2: Frontend Integration

### Task 4: Frontend Event Stream Client

**Files:**
- Modify: `frontend/src/lib/api/client.ts`

Add a persistent EventSource connection:

```typescript
export function connectEventStream(onEvent: (type: string, data: any) => void): EventSource {
    const es = new EventSource(`${BASE_URL}/events`);

    es.addEventListener('optimization_created', (e) => {
        onEvent('optimization_created', JSON.parse(e.data));
    });

    es.addEventListener('feedback_submitted', (e) => {
        onEvent('feedback_submitted', JSON.parse(e.data));
    });

    es.addEventListener('refinement_turn', (e) => {
        onEvent('refinement_turn', JSON.parse(e.data));
    });

    es.onerror = () => {
        // EventSource auto-reconnects on error
    };

    return es;  // caller can call es.close() on unmount
}
```

### Task 5: Wire into Page + Stores

**Files:**
- Modify: `frontend/src/routes/+page.svelte`
- Modify: `frontend/src/lib/components/layout/Navigator.svelte`

In `+page.svelte`, connect on mount:
```typescript
onMount(async () => {
    // ... existing health check ...

    // Connect to real-time event stream
    const es = connectEventStream((type, data) => {
        if (type === 'optimization_created') {
            // Dispatch to navigator to refresh history
            window.dispatchEvent(new CustomEvent('optimization-event', { detail: data }));
        }
    });

    return () => es.close();
});
```

In `Navigator.svelte`, listen for the event:
```typescript
onMount(() => {
    const handler = () => { historyLoaded = false; };
    window.addEventListener('optimization-event', handler);
    return () => window.removeEventListener('optimization-event', handler);
});
```

This means: when ANY optimization is created (from web UI, MCP, or REST API), the History panel auto-refreshes. The user sees new items appear without navigating away.

- [ ] **Steps: implement → verify with MCP call while UI is open → commit**

---

## Exit Conditions

1. `GET /api/events` returns SSE stream
2. Pipeline publishes `optimization_created` after completing
3. MCP `save_result` publishes `optimization_created` after saving
4. Feedback service publishes `feedback_submitted`
5. Refinement service publishes `refinement_turn`
6. Frontend auto-connects to event stream on mount
7. History panel auto-refreshes when events arrive from any source
8. EventSource auto-reconnects on disconnect
9. All tests pass
