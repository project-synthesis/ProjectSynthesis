# Intelligent Routing Service

**Date:** 2026-03-19
**Status:** Approved
**Scope:** Multi-file refactor — new `RoutingService`, backend migration of routing logic, frontend simplification

## Problem

Routing decisions for the optimization pipeline are scattered across ~12 files:

- `mcp_server.py` has a 200-line if/elif chain implementing 5 execution tiers
- `routers/optimize.py` has separate logic that only supports tier 3 (local provider), returning 503 otherwise
- The frontend (`forge.svelte.ts`, `+page.svelte`) runs its own routing decision tree: health polling, auto-passthrough toggling, and API endpoint selection
- State is split between preferences (force flags), forge store (health), and `mcp_session.json` (capability detection)
- No centralized logging or tracing of routing decisions

## Solution

A **hybrid architecture**: pure resolver function + thin orchestration manager.

### Core Principles

1. **Decision logic is a pure function** — deterministic, no I/O, trivially testable
2. **State is a separate immutable dataclass** — serializable, shareable, pluggable source
3. **Manager is thin infrastructure** — owns timer, events, persistence, delegates decisions to the resolver
4. **Frontend becomes reactive** — never makes routing decisions, only reflects backend state
5. **One instance per process** — FastAPI and MCP server each own their RoutingManager

## Architecture

### Data Model

Three frozen dataclasses in `backend/app/services/routing.py`:

```python
@dataclass(frozen=True)
class RoutingState:
    """Immutable snapshot of system capabilities."""
    provider: LLMProvider | None
    provider_name: str | None             # "claude_cli" | "anthropic_api" | None
    sampling_capable: bool | None         # True/False/None (None = unknown/stale)
    mcp_connected: bool
    last_capability_update: datetime | None
    last_activity: datetime | None

@dataclass(frozen=True)
class RoutingContext:
    """Per-request context influencing the routing decision."""
    preferences: dict                     # Frozen preferences snapshot
    caller: Literal["rest", "mcp"]        # Gates tier eligibility (MCP callers can sample, REST cannot)

@dataclass(frozen=True)
class RoutingDecision:
    """Output — which tier to use and why."""
    tier: Literal["internal", "sampling", "passthrough"]
    provider: LLMProvider | None
    provider_name: str | None
    reason: str                           # Human-readable, logged + sent to frontend
    degraded_from: str | None = None      # If auto-fallback, what we fell from
```

**Default/initial state** (no recovery file): `provider=None`, `provider_name=None`, `sampling_capable=None`, `mcp_connected=False`, both timestamps `None`. The resolver treats `sampling_capable=None` the same as `False` — unknown means unavailable until proven otherwise.

### Pure Resolver

```python
def resolve_route(state: RoutingState, ctx: RoutingContext) -> RoutingDecision:
```

Five-tier priority chain (preserves current system's behavior):

| Priority | Condition | Tier | Notes |
|----------|-----------|------|-------|
| 1 | `force_passthrough=True` | passthrough | User override, highest priority |
| 2 | `force_sampling=True` + `caller=="mcp"` + `sampling_capable==True` + `mcp_connected` | sampling | User override; degrades gracefully if caller is REST or no MCP (see note below) |
| 3 | Local provider available (CLI or API) | internal | Preferred automatic path |
| 4 | No provider + `caller=="mcp"` + `sampling_capable==True` + `mcp_connected` | sampling | Automatic fallback |
| 5 | Nothing available | passthrough | Terminal fallback, always reachable |

Properties:
- **Deterministic** — same inputs, same output
- **REST-safe** — `caller=="rest"` gates out tiers 2/4 (sampling requires MCP context). No lying about session state needed.
- **Graceful degradation** — `force_sampling` from a REST caller or without MCP availability degrades to best available tier with `degraded_from` tag. This is the single intentional exception to "fail fast": the user explicitly opted into sampling but the context doesn't support it — blocking the entire pipeline would be worse than falling through.
- **Three-state sampling** — `sampling_capable=None` (unknown/stale) is treated as `False` by the resolver. Only an explicit `True` from middleware enables sampling tiers.

### RoutingManager

Thin orchestration wrapper (~150 lines):

```
RoutingManager
├── State updates (called by middleware/lifespan)
│   ├── set_provider(provider)           ← startup + API key hot-reload
│   ├── on_mcp_initialize(sampling_capable) ← ASGI middleware
│   └── on_mcp_activity()                ← middleware, throttled 10s
├── Public API
│   └── resolve(ctx) → RoutingDecision   ← calls resolve_route(), logs decision
├── Background
│   └── _disconnect_checker()            ← 60s timer, flips mcp_connected on staleness
└── Internal
    ├── _broadcast_state_change()         ← publishes routing_state_changed SSE
    ├── _available_tiers()                ← computes reachable tiers for display
    ├── _persist()                        ← write-through to mcp_session.json
    └── _log_decision()                   ← structured logging per decision
```

**Lifecycle:**
- Created in app lifespan, stored on `app.state.routing`
- Recovers from `mcp_session.json` on startup (with staleness checks)
- Background `_disconnect_checker` task started on init, cancelled on shutdown
- `mcp_session.json` becomes write-through persistence, not primary state source

### SSE Communication

Two SSE channels:

1. **`routing_state_changed`** (ambient) — fired when available tiers change. Payload:
   ```json
   {"provider": "claude_cli", "sampling_capable": true, "mcp_connected": true, "available_tiers": ["internal", "sampling", "passthrough"]}
   ```

2. **`routing`** (per-request) — first event in every optimize SSE stream. Payload:
   ```json
   {"tier": "internal", "provider": "claude_cli", "reason": "Local provider: claude_cli", "degraded_from": null}
   ```

### MCP Real-Time Detection (Preserved and Optimized)

Current flow: ASGI middleware → file write → SSE event → frontend polls health → mutates preferences

Refactored flow: ASGI middleware → `manager.on_mcp_initialize()` (in-memory) → `_broadcast_state_change()` SSE → frontend updates display

Key optimization: **in-memory state is primary**, file is write-through for restart recovery. Connection detection is near-instant. Disconnect detection uses the existing `MCP_ACTIVITY_STALENESS_SECONDS` (300s / 5 min) window — this constant is preserved unchanged. The `_disconnect_checker` polls every 60s, so worst-case disconnect detection latency is ~360s (5-min window + 60s poll interval). This is comparable to the current system (~310s). The constants `MCP_CAPABILITY_STALENESS_MINUTES` (30 min) and `MCP_ACTIVITY_STALENESS_SECONDS` (300s) in `config.py` are unchanged.

### Passthrough Flow in Unified Endpoint

When `resolve()` returns `tier="passthrough"`, the unified `POST /api/optimize` endpoint handles it inline via SSE — no separate API call needed:

**SSE event sequence for passthrough:**
1. `routing` event — `{"tier": "passthrough", "reason": "...", "degraded_from": "..."}`
2. `passthrough` event — `{"assembled_prompt": "...", "strategy": "...", "trace_id": "..."}`
3. Stream ends (no `analyzing`/`optimizing`/`scoring` phases)

The frontend reads the `routing` event, sees `tier: "passthrough"`, and switches to passthrough UI mode (show assembled prompt, copy button). No `preparePassthrough()` call needed.

**Save endpoint preserved:** `POST /api/optimize/passthrough/save` remains unchanged — the user pastes their external LLM result back, and it gets scored + persisted. This is a user-initiated action, not a routing decision.

**Existing passthrough endpoints deprecated:**
- `POST /api/optimize/passthrough` (prepare) — no longer called by frontend. Kept temporarily for backward compatibility, marked deprecated.
- Frontend removes all direct calls to this endpoint.

### Refinement Router

`POST /api/refine` also uses the routing service. Refinement turns are fresh pipeline invocations, so they go through `manager.resolve()` with the same logic. Today refinement requires a local provider (503 otherwise) — after this refactor, refinement can also route to passthrough, making it usable without a provider.

### Two-Process Model

The FastAPI app (port 8000) and MCP server (port 8001) run as separate processes. Each creates its own `RoutingManager` instance.

- **MCP server's instance** is authoritative for sampling state (it has the ASGI middleware that intercepts `initialize`)
- **FastAPI's instance** is authoritative for provider state (it manages API key lifecycle)
- **MCP server is sole writer** to `mcp_session.json` — avoids write-write race conditions. FastAPI's instance reads the file on startup for recovery and reads `sampling_capable` from it when needed, but never writes.
- Both call the same `resolve_route()` pure function — consistent decisions guaranteed

## Integration Map

### New Files

| File | Purpose |
|------|---------|
| `backend/app/services/routing.py` | `RoutingState`, `RoutingContext`, `RoutingDecision`, `resolve_route()`, `RoutingManager` |
| `backend/tests/test_routing.py` | Unit + integration tests for resolver and manager |

### Modified Files — Backend

| File | Change |
|------|--------|
| `main.py` | Create `RoutingManager` in lifespan, start disconnect checker, replace `app.state.provider` with `app.state.routing` |
| `mcp_server.py` | Replace 200-line if/elif with `manager.resolve()`, ASGI middleware calls `manager.on_mcp_initialize()` / `on_mcp_activity()`, remove `_write_mcp_session_caps()`. MCP server remains sole writer to `mcp_session.json`. |
| `routers/optimize.py` | Replace 503 dead end with `manager.resolve()`, emit `routing` SSE event, stream passthrough inline (see Passthrough Flow section). Mark `POST /api/optimize/passthrough` deprecated. |
| `routers/refinement.py` | Add `manager.resolve()` call. Refinement becomes usable without a local provider (passthrough fallback). |
| `routers/health.py` | Read from `app.state.routing` live state instead of `mcp_session.json`. Return `available_tiers` in response. |
| `routers/providers.py` | API key set calls `app.state.routing.set_provider(new_provider)`. API key **delete** calls `app.state.routing.set_provider(None)` (new behavior — today delete only removes the file). |
| `services/preferences.py` | Remove `auto_passthrough` preference |

### Modified Files — Frontend

| File | Change |
|------|--------|
| `forge.svelte.ts` | Remove `preparePassthrough()` branch and `noProvider`-gated passthrough logic. Always call `optimizeSSE()`. Handle new `routing` SSE event (set `routingDecision` state for UI mode switching) and `passthrough` SSE event (display assembled prompt). |
| `preferences.svelte.ts` | Remove `auto_passthrough` from `PipelinePrefs` interface and all references |
| `frontend/src/routes/app/+page.svelte` | **Remove entirely:** `handleMcpDisconnect()`, `handleMcpReconnect()`, all `auto_passthrough` guards, adaptive polling interval derivation for routing. **Keep:** 60s fixed-interval health polling for StatusBar display. **Add:** listener for `routing_state_changed` SSE event to update available tiers display. |
| `StatusBar.svelte` | Show `available_tiers` from `routing_state_changed` SSE event instead of computing from health response |
| Settings UI | Remove auto-passthrough toggle. Keep `force_sampling` / `force_passthrough` as user overrides. Disable states derived from `routing_state_changed` event. |

### Demoted Files

| File | Change |
|------|--------|
| `services/mcp_session_file.py` | Kept — used only by `RoutingManager._persist()` and startup recovery. No longer read by health endpoint or frontend. |

### Unchanged Files

- `services/pipeline.py` — receives provider from caller, runs phases
- `services/sampling_pipeline.py` — called when `decision.tier == "sampling"`
- `services/passthrough.py` — called when `decision.tier == "passthrough"`
- `services/event_bus.py` — gains `routing_state_changed` event type, no code changes (accepts any string). CLAUDE.md event type list should be updated at implementation time: add `routing_state_changed`, drop `mcp_session_changed`.
- Rate limiting — stays on REST endpoint
- Trace logging — `RoutingManager._log_decision()` uses existing structured logger

## Error Handling

| Scenario | Behavior |
|----------|----------|
| Provider disappears (API key deleted) | `set_provider(None)` → next resolve falls to tier 4/5 → SSE notifies frontend |
| MCP connects while REST in-flight | In-flight request unaffected (immutable decision). Next request sees updated state. |
| MCP disconnects during sampling | Sampling call fails, error propagates (fail-fast). Disconnect checker fires within 60s, updates state. |
| `force_sampling` + `force_passthrough` both true | Prevented by preferences validation (422). Structurally impossible at resolver level. |
| Startup with stale `mcp_session.json` | Staleness checks applied: >30 min → `sampling_capable=False`, stale activity → `mcp_connected=False` |
| REST request, no provider, no MCP | Tier 5 passthrough. Streams assembled template. No 503. |
| Provider fails mid-execution | Error propagates to caller. No cross-tier fallback. Existing `call_provider_with_retry()` handles transient retries within tier. |

## Testing Strategy

### Layer 1: `resolve_route()` Unit Tests (~20 parametrized cases)

Pure function tests — no mocks, no async, sub-millisecond:
- All 5 tiers with their exact trigger conditions
- Force flag priority and graceful degradation
- REST caller never reaches sampling tiers
- `degraded_from` tagging for fallback scenarios

### Layer 2: `RoutingManager` Integration Tests (~10 cases)

Async tests with real `EventBus`, mock filesystem:
- `set_provider()` → state update + SSE event
- `on_mcp_initialize()` → state update + persistence + SSE event
- Optimistic skip (no downgrade when fresh True exists)
- `on_mcp_activity()` → reconnection detection
- Disconnect checker → staleness flip + SSE event
- Startup recovery from stale vs fresh session file

### Layer 3: End-to-End Smoke Test (1-2 cases)

- `POST /api/optimize` streams `routing` SSE event before pipeline phases
- Routing tier matches expected provider state

## Structured Log Format

`RoutingManager._log_decision()` emits structured log entries at INFO level:

```
routing.decision caller=rest tier=internal provider=claude_cli reason="Local provider: claude_cli" degraded_from=null duration_us=12
routing.decision caller=mcp tier=sampling provider=null reason="No local provider — using MCP sampling" degraded_from=internal duration_us=8
routing.state_change event=mcp_initialize sampling_capable=true mcp_connected=true available_tiers=internal,sampling,passthrough
routing.state_change event=disconnect sampling_capable=true mcp_connected=false available_tiers=internal,passthrough
```

Fields: `caller`, `tier`, `provider`, `reason`, `degraded_from`, `duration_us` (resolver execution time). State changes log the trigger event and resulting available tiers.

## Design Decisions

| Decision | Rationale |
|----------|-----------|
| Hybrid (pure function + manager) over stateful singleton | Pure resolver is future-proof — portable, testable, scales to multi-instance. Manager is thin infra glue. |
| Full backend migration over frontend-assists | Frontend routing logic is a bug surface. Backend owns all state needed for decisions. Frontend becomes purely reactive. |
| Single decision, fail fast over cascading failover | Cross-tier fallback adds complexity for an edge case. Retry-within-tier already exists. Router architecture supports adding failover later. One intentional exception: `force_sampling` degrades gracefully when no MCP context is available — blocking the pipeline is worse than falling through. |
| `caller` field gates sampling eligibility, not `has_mcp_session` | REST callers structurally cannot participate in MCP sampling. Using `caller` makes this explicit without requiring callers to lie about session state. |
| MCP server is sole writer to `mcp_session.json` | Avoids write-write race between FastAPI and MCP processes. FastAPI reads on startup for recovery. MCP server writes through on every state change. |
| `sampling_capable: bool \| None` three-state | Preserves the current health endpoint's distinction: `True` (confirmed), `False` (confirmed absent), `None` (unknown/stale). Resolver treats `None` as `False`. |
| First SSE event over separate pre-flight endpoint | Zero extra latency. Existing communication channel. Frontend reads one event to set UI mode. |
| In-memory state over file-primary | Eliminates file I/O on hot path. File is write-through persistence for restart recovery. MCP detection goes from "next file read" to near-instant. |
| One instance per process over shared state service | Simpler. Each process has its own authoritative domain (MCP server → sampling, FastAPI → provider). File provides cross-process recovery. |
| Remove auto_passthrough preference | Backend owns degradation now. No preference to toggle — it's automatic and transparent. |
