# Routing Architecture — Project Synthesis

Comprehensive reference for the intelligent routing system that directs optimization requests to the appropriate execution tier based on available providers, MCP client capabilities, and user preferences.

## Table of Contents

1. [Overview](#overview)
2. [Tier Priority Chain](#tier-priority-chain)
3. [Hybrid Phase Routing](#hybrid-phase-routing)
4. [System Architecture](#system-architecture)
5. [Component Deep Dive](#component-deep-dive)
6. [State Machine](#state-machine)
7. [Multi-Client Coordination](#multi-client-coordination)
8. [Disconnect Detection](#disconnect-detection)
9. [Cross-Process Communication](#cross-process-communication)
10. [Persistence and Recovery](#persistence-and-recovery)
11. [Common Scenarios](#common-scenarios)
12. [Failure Modes and Mitigations](#failure-modes-and-mitigations)
13. [Configuration Reference](#configuration-reference)

---

## Overview

The routing system answers one question for every optimization request: **which execution tier should process this?** The answer depends on what providers are available, whether an MCP client supports sampling, user preference toggles, and where the request originated.

Three execution tiers exist:

| Tier | What it does | When used |
|------|-------------|-----------|
| **internal** | Runs the full pipeline locally via Claude CLI or Anthropic API | Default when a provider is detected |
| **sampling** | Delegates pipeline phases to the IDE's LLM via MCP `sampling/createMessage` | When an MCP bridge with sampling capability is connected |
| **passthrough** | Assembles the prompt and returns it for an external LLM to process | Fallback when nothing else is available, or user forces it |

The system is split across two processes (FastAPI backend on port 8000, MCP server on port 8001) that coordinate via HTTP events and a shared session file.

---

## Tier Priority Chain

A pure function `resolve_route()` determines the tier. It takes an immutable state snapshot and request context, and returns a deterministic decision with no I/O.

```
                    ┌─────────────────────────────────────────────┐
                    │          resolve_route(state, ctx)          │
                    └─────────────────┬───────────────────────────┘
                                      │
                    ┌─────────────────▼───────────────────────────┐
                    │  1. force_passthrough?                      │
                    │     YES → tier=passthrough (unconditional)  │
                    └─────────────────┬───────────────────────────┘
                                      │ NO
                    ┌─────────────────▼───────────────────────────┐
                    │  2. force_sampling?                         │
                    │     YES + MCP + capable + connected         │
                    │       → tier=sampling                       │
                    │     YES + provider exists                   │
                    │       → tier=internal (degraded)            │
                    │     YES + nothing                           │
                    │       → tier=passthrough (degraded)         │
                    └─────────────────┬───────────────────────────┘
                                      │ NO
                    ┌─────────────────▼───────────────────────────┐
                    │  3. Internal provider available?            │
                    │     YES → tier=internal                     │
                    └─────────────────┬───────────────────────────┘
                                      │ NO
                    ┌─────────────────▼───────────────────────────┐
                    │  4. MCP caller + sampling capable?          │
                    │     YES → tier=sampling (auto, degraded     │
                    │            from internal)                   │
                    └─────────────────┬───────────────────────────┘
                                      │ NO
                    ┌─────────────────▼───────────────────────────┐
                    │  5. Fallback                                │
                    │     → tier=passthrough (degraded)           │
                    └─────────────────────────────────────────────┘
```

**Caller gating:** Only MCP tool invocations (`caller="mcp"`) can reach sampling tiers. REST API calls (`caller="rest"`) skip tiers 2 and 4 because the sampling request must flow back through the MCP session to the IDE — a REST caller has no MCP session to sample through. (`_can_sample()` enforces `ctx.caller == "mcp"` as of v0.4.2.)

**Degradation:** When a preferred tier is unavailable, the system degrades gracefully. `RoutingDecision.degraded_from` records what tier was originally requested, allowing the UI to show informative messages (e.g., "sampling unavailable, using internal provider"). Tier 4 auto-sampling (MCP caller, sampling capable, no internal provider) and tier 5 passthrough fallback both set `degraded_from="internal"` — internal is the natural tier, not sampling.

---

## Hybrid Phase Routing

**Shipped:** v0.4.2 (2026-04-23). See [CHANGELOG](CHANGELOG.md#v042--2026-04-23).

The pipeline runs four LLM-facing phases: `analyze`, `optimize`, `score`, `suggest`. Historically, when routing resolved to `sampling`, every phase was dispatched through the IDE's `sampling/createMessage` channel — five IDE round-trips per request. The fast phases (analyze, score, suggest) produce small structured JSON and dominated latency without benefiting from the user's IDE model.

Hybrid Phase Routing breaks the tier choice into per-phase decisions encoded in `RoutingDecision.providers_by_phase`:

```python
providers_by_phase: dict[str, str] | None  # e.g., {"analyze": "internal", "optimize": "sampling", "score": "internal", "suggest": "internal"}
```

**Resolution rules (from `_resolve_with_fallback` in `routing.py:154`):**

| `tier` | Internal provider available? | `providers_by_phase` |
|--------|------------------------------|----------------------|
| `internal` | — | `None` (all phases run on internal) |
| `sampling` | Yes | `{analyze: internal, score: internal, suggest: internal, optimize: sampling}` |
| `sampling` | No (force_sampling with no internal) | `{analyze: sampling, score: sampling, suggest: sampling, optimize: sampling}` |
| `passthrough` | — | `None` (no LLM calls — prompt assembled and returned) |

Tier 4 auto-sampling (MCP caller + capable + no internal provider) also emits all-sampling `providers_by_phase`. The concrete effect: when a provider *is* detected and the user is sampling-capable, `resolve_route()` returns tier 3 `internal` (via the "internal beats auto-sampling" priority), so the all-sampling path only triggers under `force_sampling` or when truly no provider exists.

**Consumers:** `MCPSamplingProvider` (the MCP-sampling-as-LLMProvider wrapper introduced in v0.4.2) reads `providers_by_phase` when dispatching each phase, so the primary `PipelineOrchestrator` can orchestrate all three tiers with one code path. The legacy dedicated `sampling_pipeline.run_sampling_pipeline()` + `run_sampling_analyze()` entry points are retained for analyze-only diagnostics and for legacy MCP tool call sites that delegate on `tier=="sampling"`.

**Latency impact:** Typical `analyze + score + suggest` roundtrip drops from 3 × IDE-sampling-latency to 3 × internal-provider-latency (CLI or API); `optimize` still pays one sampling roundtrip. At Sonnet 4.6 IDE sampling latency ~5-12 s and local Haiku 4.5 analyze ~1-3 s, that's a 15-30 s net win per request.

---

## System Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                        MCP Server (port 8001)                       │
│                                                                     │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │        _CapabilityDetectionMiddleware (ASGI, class-level)    │   │
│  │                                                              │   │
│  │  _sampling_session_ids: set[str]  ← survives session churn  │   │
│  │  _sampling_sse_sessions: set[str] ← active SSE proof        │   │
│  │  _active_sse_streams: int         ← total SSE count         │   │
│  │                                                              │   │
│  │  POST → intercept initialize → guard logic → on_mcp_init()  │   │
│  │  GET  → track SSE open/close → on_*_disconnect()            │   │
│  │  ALL  → touch session file (throttled 10s)                  │   │
│  └──────────────┬───────────────────────────────────────────────┘   │
│                 │                                                    │
│  ┌──────────────▼───────────────────────────────────────────────┐   │
│  │            RoutingManager (process-level singleton)           │   │
│  │                                                              │   │
│  │  RoutingState (frozen dataclass):                            │   │
│  │    provider, sampling_capable, mcp_connected, last_activity  │   │
│  │                                                              │   │
│  │  Background: _disconnect_loop (30s poll)                     │   │
│  │  Persistence: mcp_session.json (write-through)               │   │
│  │  Events: routing_state_changed → EventBus + cross-process    │   │
│  └──────────────┬───────────────────────────────────────────────┘   │
│                 │ HTTP POST /api/events/_publish                     │
└─────────────────┼───────────────────────────────────────────────────┘
                  │
┌─────────────────▼───────────────────────────────────────────────────┐
│                     FastAPI Backend (port 8000)                      │
│                                                                     │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │            RoutingManager (app.state.routing)                 │   │
│  │                                                              │   │
│  │  sync_from_event() ← receives MCP state changes             │   │
│  │  resolve() → resolve_route(state, ctx)                       │   │
│  │  _disconnect_loop (30s poll on mcp_session.json)             │   │
│  └──────────────┬───────────────────────────────────────────────┘   │
│                 │ SSE                                                │
└─────────────────┼───────────────────────────────────────────────────┘
                  │
┌─────────────────▼───────────────────────────────────────────────────┐
│                   SvelteKit Frontend (port 5199)                     │
│                                                                     │
│  routing.svelte.ts — purely reactive, never makes routing decisions │
│  Receives: routing_state_changed SSE events                         │
│  Shows: tier badges, toasts on connect/disconnect, available tiers  │
│  Polls: GET /api/health every 60s (display only)                    │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Component Deep Dive

### RoutingState (frozen dataclass)

Immutable snapshot of system capabilities at the time of a routing decision.

```python
@dataclass(frozen=True)
class RoutingState:
    provider: LLMProvider | None       # Detected at startup, never persisted
    provider_name: str | None          # Human-readable ("claude_cli", "anthropic_api")
    sampling_capable: bool | None      # None = unknown/stale (treated as False)
    mcp_connected: bool                # Any MCP client connected
    last_capability_update: datetime | None
    last_activity: datetime | None     # Only sampling clients refresh this
```

### RoutingContext (per-request)

```python
@dataclass(frozen=True)
class RoutingContext:
    preferences: dict[str, Any]        # User prefs snapshot (force_passthrough, force_sampling)
    caller: Literal["rest", "mcp"]     # Where the request originated
```

### RoutingDecision (result)

```python
@dataclass(frozen=True)
class RoutingDecision:
    tier: Literal["internal", "sampling", "passthrough"]
    provider: LLMProvider | None       # Set when the internal provider is used
                                       # (tier=internal, OR tier=sampling under Hybrid Phase Routing)
    provider_name: str | None
    reason: str                        # Human-readable explanation
    degraded_from: str | None          # Original tier if degraded (auto-sampling + passthrough
                                       # fallback set "internal" because internal is the natural tier)
    providers_by_phase: dict[str, str] | None  # Hybrid Phase Routing (v0.4.2):
                                       # per-phase provider selection when tier=sampling
                                       # (None for tier=internal and tier=passthrough)
```

### RoutingManager

Thin orchestration wrapper holding live state, managing the disconnect checker background task, and broadcasting events. Both FastAPI and MCP server own their own instance. The MCP server's instance is a process-level singleton (initialized once, never replaced).

**Key methods:**

| Method | Called by | Effect |
|--------|----------|--------|
| `set_provider(provider)` | Startup, API key hot-reload | Sets provider, broadcasts `provider_changed` |
| `on_mcp_initialize(sampling)` | Middleware on `initialize` | Sets `sampling_capable` + `mcp_connected`, persists, broadcasts |
| `on_mcp_activity()` | Middleware on sampling-client POST/SSE | Updates `last_activity`, persists |
| `on_mcp_disconnect()` | Middleware when all SSE close | Clears both `mcp_connected` + `sampling_capable` |
| `on_sampling_disconnect()` | Middleware when last sampling SSE closes | Clears only `sampling_capable`, keeps `mcp_connected=True` |
| `on_session_invalidated()` | Middleware on 400/404 response | Clears both fields (stale session) |
| `sync_from_event(data)` | FastAPI on cross-process event | Updates MCP fields from MCP server's broadcast |
| `resolve(ctx)` | Request handlers | Delegates to `resolve_route()`, logs decision |

---

## State Machine

### Connection states

```
                             on_mcp_initialize(sampling=True)
                 ┌───────────────────────────────────────────────┐
                 │                                               │
                 │                                               ▼
         ┌───────────────┐                          ┌────────────────────┐
         │  DISCONNECTED │                          │ SAMPLING CONNECTED │
         │               │                          │                    │
         │ sampling=None │◄────── on_mcp_          │ sampling=True      │
         │ connected=F   │        disconnect()      │ connected=True     │
         │               │        (all SSE gone)    │                    │
         └───────┬───────┘                          └─────────┬──────────┘
                 │                                            │
                 │  on_mcp_initialize                         │ on_sampling_disconnect()
                 │  (sampling=False)                          │ (bridge SSE closed,
                 │                                            │  CC SSE remains)
                 │                                            │
                 ▼                                            ▼
         ┌──────────────────┐                      ┌────────────────────┐
         │  NON-SAMPLING    │◄──── on_mcp_        │ NON-SAMPLING       │
         │  CONNECTED       │      disconnect()    │ CONNECTED          │
         │                  │      (CC SSE gone)   │ (post-bridge)      │
         │  sampling=False  │                      │ sampling=None      │
         │  connected=True  │                      │ connected=True     │
         └──────────────────┘                      └────────────────────┘
```

### Available tiers by state

| State | `available_tiers` (with provider) | `available_tiers` (no provider) |
|-------|----------------------------------|--------------------------------|
| Disconnected | `[internal, passthrough]` | `[passthrough]` |
| Sampling connected | `[internal, sampling, passthrough]` | `[sampling, passthrough]` |
| Non-sampling connected | `[internal, passthrough]` | `[passthrough]` |

---

## Multi-Client Coordination

Multiple MCP clients can connect simultaneously. The most common configuration:

```
  ┌──────────────┐     ┌─────────────────────┐
  │  Claude Code  │     │  MCP Copilot Bridge │
  │  (VS Code     │     │  (VS Code ext)      │
  │   extension)  │     │                     │
  │               │     │  sampling=True      │
  │  sampling=    │     │  SSE: active        │
  │    False      │     │                     │
  └──────┬────────┘     └──────────┬──────────┘
         │                         │
         │    Streamable HTTP      │
         └──────────┬──────────────┘
                    │
         ┌──────────▼──────────┐
         │    MCP Server       │
         │                     │
         │  RoutingManager:    │
         │  sampling=True      │
         │  connected=True     │
         └─────────────────────┘
```

### The guard problem and solution

Claude Code periodically reconnects (new Streamable HTTP session, new `initialize` with `sampling=False`). Without protection, this would overwrite the bridge's `sampling_capable=True`.

**Dual-layer guard in `_inspect_initialize`:**

1. **Primary:** `routing.state.sampling_capable is True` — authoritative, checks the singleton RoutingManager
2. **Secondary:** `_sampling_sse_sessions` is non-empty — defense in depth, uses class-level middleware state that exists independently of the RoutingManager

Both checks must fail before a `sampling=False` initialize is allowed to call `on_mcp_initialize()`.

### Per-session lifespan problem (historical)

FastMCP's Streamable HTTP transport enters the lifespan per session, not per server. Previously, each session created a new RoutingManager and replaced the previous one in `_shared._routing`. This caused:

1. Bridge's `sampling=True` lost when Claude Code reconnected
2. Old session exits nullified `_shared._routing` via cleanup code
3. Guard checked the wrong RoutingManager (new one had `sampling=None`)
4. No disconnect events because the bridge's RoutingManager was already gone

**Fix:** `_process_initialized` module-level flag ensures singletons are created once. Lifespan exit has no cleanup — singletons survive all sessions.

---

## Disconnect Detection

### Instant detection (SSE-based, in middleware)

```
SSE stream closes
    │
    ├── Last SSE of ANY kind? → on_mcp_disconnect()      (full disconnect)
    │
    └── Last SAMPLING SSE?    → on_sampling_disconnect()  (partial disconnect)
         (non-sampling SSEs
          still open)
```

### Fallback detection (polling-based, in RoutingManager)

Background task runs every 30 seconds:

```
Every 30s:
    │
    ├── mcp_connected=False?
    │     └── Read mcp_session.json
    │           ├── Fresh activity? → reconnect_detected event
    │           └── Stale/missing?  → stay disconnected
    │
    └── mcp_connected=True?
          └── last_activity stale (>60s)?
                ├── Read mcp_session.json
                │     ├── File fresh? → disconnect_averted (update last_activity)
                │     └── File stale? → disconnect event
                └── Fresh? → do nothing
```

The session file check before disconnecting prevents false positives when the MCP server has fresh activity that the RoutingManager missed (e.g., the MCP server processes a bridge POST but the cross-process HTTP notification was lost).

---

## Cross-Process Communication

The MCP server and FastAPI backend run in separate processes. State changes propagate via:

### Primary path: HTTP events (real-time)

```
MCP RoutingManager._broadcast_state_change()
  → _cross_process_notify()
    → asyncio.create_task(notify_event_bus(event_type, payload))
      → HTTP POST http://127.0.0.1:8000/api/events/_publish
        → FastAPI events router
          → RoutingManager.sync_from_event(data)
            → EventBus.publish("routing_state_changed")
              → SSE stream to frontend
```

### Fallback path: session file (polling)

Both processes can read `mcp_session.json`. The MCP server is the sole writer. The backend's disconnect checker reads it as a fallback when HTTP events are lost.

### Event payload

```python
RoutingStatePayload = {
    "trigger": str,          # "mcp_initialize", "mcp_disconnect", "sampling_disconnect", etc.
    "provider": str | None,
    "sampling_capable": bool | None,
    "mcp_connected": bool,
    "available_tiers": list[str],
}
```

`sync_from_event()` uses a `_missing` sentinel object to distinguish "key absent" from `None`. This is critical because `sampling_capable=None` is a legitimate value (meaning "unknown/stale") that must be synchronized, while an absent key means "don't change this field."

---

## Persistence and Recovery

### `mcp_session.json` format

```json
{
  "sampling_capable": true,
  "written_at": "2026-03-27T19:00:00+00:00",
  "last_activity": "2026-03-27T19:05:00+00:00",
  "sse_streams": 2
}
```

### Writers

| Writer | Method | When |
|--------|--------|------|
| `RoutingManager._persist()` | `write_session()` (full rewrite) | On any state change (sampling init, disconnect, etc.) |
| Middleware `_touch_session_file()` | `write()` (update last_activity + sse_streams) | Every 10s from any client POST/SSE |
| Middleware `_write_optimistic_session()` | `write_session(current_sampling)` | Session-less GET reconnection — preserves current `sampling_capable` and calls `on_mcp_activity()`. Since v0.4.2, does **not** force `sampling_capable=True`: both the VS Code bridge (sampling) and plain Claude Code (non-sampling) send session-less reconnects, and assuming `True` for all of them produced a stuck `sampling_capable=True` after the bridge disconnected. The authoritative value is set by the real `initialize` handshake that follows within milliseconds. |

### Recovery on startup

`RoutingManager._recover_state()` reads the session file during construction:

1. If no file → defaults (`sampling=None, connected=False`)
2. If file exists:
   - Check `written_at` against `MCP_CAPABILITY_STALENESS_MINUTES` (30 min)
   - If stale → discard `sampling_capable` (set to `None`)
   - Check `sse_streams` for disconnect detection
   - Parse `last_activity` for timestamp recovery

### Lifecycle

```
Process start (__main__)
  └── _clear_stale_session()  ← delete old file
        │
First session arrives
  └── _mcp_lifespan (guarded by _process_initialized)
        └── RoutingManager() → _recover_state()
              └── Reads mcp_session.json (if middleware wrote one during startup race)
```

---

## Common Scenarios

### Scenario 1: Normal startup with Claude Code + Bridge

```
1. Process starts              → clears stale session file
2. Claude Code connects        → first session, triggers singleton init
                                  RoutingManager created (defaults)
                                  Provider detected (claude_cli)
                                  on_mcp_initialize(False)
                                  tiers: [internal, passthrough]
3. Bridge connects             → on_mcp_initialize(True)
                                  tiers: [internal, sampling, passthrough]
4. Claude Code reconnects      → guard fires (sampling_capable=True)
                                  state unchanged ✓
5. Optimization via MCP        → resolve_route() → tier=internal (provider available)
                                  OR tier=sampling (if force_sampling=True)
```

### Scenario 2: Bridge disconnects (VS Code closes)

```
1. State: sampling=True, connected=True
2. Bridge SSE closes           → _sampling_sse_sessions becomes empty
                                  on_sampling_disconnect()
                                  sampling=None, connected=True (CC still there)
                                  tiers: [internal, passthrough]
3. Frontend receives SSE       → updates tier display, shows toast
4. CC still polling            → session file stays fresh, no false disconnect
```

### Scenario 3: All clients disconnect

```
1. State: sampling=None, connected=True (CC only)
2. CC SSE closes               → _active_sse_streams = 0
                                  on_mcp_disconnect()
                                  sampling=None, connected=False
                                  tiers: [internal, passthrough]
```

### Scenario 4: Server restart recovery

```
1. Old mcp_session.json exists  → cleared by __main__
2. First client connects        → if bridge (sampling=True):
                                    middleware writes session file
                                    RoutingManager recovers sampling=True
                                  if CC (sampling=False):
                                    RoutingManager starts with defaults
                                    bridge's next connect sets sampling=True
```

---

## Failure Modes and Mitigations

| Failure | Detection | Mitigation |
|---------|-----------|------------|
| Bridge crashes without closing SSE | `last_activity` goes stale (>60s) | Disconnect checker fires, checks session file, disconnects if both stale |
| Cross-process HTTP event lost | Backend's disconnect checker polls `mcp_session.json` | File-based fallback recovers state within 30s |
| `mcp_session.json` corrupted | `_recover_state()` catches all exceptions | Falls back to safe defaults (`sampling=None, connected=False`) |
| Multiple bridges connect | Each `initialize` updates `sampling_capable` | Last writer wins; `_sampling_session_ids` tracks all |
| RoutingManager not yet initialized | Middleware's `else` branch (no routing) | Writes to session file; RoutingManager recovers during singleton init |

---

## Configuration Reference

All constants in `backend/app/config.py`:

| Constant | Default | Purpose |
|----------|---------|---------|
| `MCP_CAPABILITY_STALENESS_MINUTES` | 30 | Startup recovery: discard `sampling_capable` from file older than this |
| `MCP_ACTIVITY_STALENESS_SECONDS` | 300 | Legacy fallback: disconnect detection when `sse_streams` field is absent |

Disconnect checker internal constants (in `routing.py`):

| Constant | Default | Purpose |
|----------|---------|---------|
| `_dc_staleness` | 60s | In-memory activity staleness threshold before checking file |
| Sleep interval | 30s | Polling frequency for both connected and disconnected modes |

Middleware constants (in `mcp_server.py`):

| Constant | Default | Purpose |
|----------|---------|---------|
| `_ACTIVITY_WRITE_THROTTLE` | 10s | Minimum interval between session file writes |
