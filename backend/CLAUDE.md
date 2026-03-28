# Backend — Internal Reference

Low-level patterns and invariants for `backend/app/`. For high-level architecture, services list, and API surface, see the root `CLAUDE.md`.

## Routing system internals

### Process-level singleton pattern

FastMCP's Streamable HTTP transport calls `Server.run()` per client session, which re-enters the MCP lifespan each time. All singletons (RoutingManager, provider, taxonomy engine, context service) are initialized once via a `_process_initialized` module-level flag in `mcp_server.py`. The lifespan exit path has **no cleanup** — singletons survive all sessions and are reclaimed on process exit.

**Critical invariants:**
- `_process_initialized` guard relies on asyncio cooperative scheduling (no preemption between check and assignment). If the server were ever embedded in a threaded host, a `threading.Lock` would be needed.
- `_clear_stale_session()` runs in `__main__` (process startup), never in the lifespan. Clearing per-session would race with the middleware writing the session file.
- Per-session lifespan exit must never call `_shared.set_routing(None)` or `_shared.set_taxonomy_engine(None)` — that would destroy state set by other sessions.

### Tier decision — `resolve_route()`

Pure function in `services/routing.py`. No I/O, no side effects, deterministic.

```
Input:  RoutingState (frozen dataclass) + RoutingContext (caller, preferences)
Output: RoutingDecision (tier, provider, reason, degraded_from)
```

Priority chain (first match wins):

| Priority | Tier | Condition | Degrade path |
|----------|------|-----------|--------------|
| 1 | `passthrough` | `force_passthrough=True` | none |
| 2 | `sampling` | `force_sampling=True` + MCP caller + sampling capable + connected | internal, then passthrough |
| 3 | `internal` | Provider detected (CLI or API) | none |
| 4 | `sampling` | MCP caller + sampling capable + connected (auto) | passthrough |
| 5 | `passthrough` | Fallback | none |

**Caller gating:** REST callers (`caller="rest"`) never reach sampling tiers (2 or 4). Only MCP tool invocations (`caller="mcp"`) can route to sampling because the sampling request must flow back through the MCP session to the IDE.

### RoutingState fields

| Field | Type | Set by | Semantics |
|-------|------|--------|-----------|
| `provider` | `LLMProvider \| None` | `set_provider()` at startup, API key hot-reload | Never persisted — re-detected each restart |
| `sampling_capable` | `bool \| None` | `on_mcp_initialize()`, `on_sampling_disconnect()`, `on_mcp_disconnect()` | `None` = unknown/stale, treated as `False` |
| `mcp_connected` | `bool` | `on_mcp_initialize()`, `on_mcp_activity()`, disconnect methods | General MCP connection (any client) |
| `last_activity` | `datetime \| None` | `on_mcp_initialize()`, `on_mcp_activity()` | Only sampling clients refresh this |

### State transitions

```
                    on_mcp_initialize(True)
    [disconnected] ──────────────────────────> [sampling connected]
         ^                                           │
         │ on_mcp_disconnect()                       │ on_sampling_disconnect()
         │ (all SSE closed)                          │ (bridge SSE closed, CC remains)
         │                                           v
         └────────────── on_mcp_disconnect() ── [non-sampling connected]
                         (CC also leaves)        sampling_capable=None
                                                 mcp_connected=True
```

### Disconnect signals — two distinct methods

| Method | Trigger | Clears `sampling_capable` | Clears `mcp_connected` | When |
|--------|---------|---------------------------|------------------------|------|
| `on_mcp_disconnect()` | Last SSE stream of ANY kind closes | Yes (→ `None`) | Yes (→ `False`) | All clients gone |
| `on_sampling_disconnect()` | Last SAMPLING SSE closes, but non-sampling SSEs remain | Yes (→ `None`) | No (stays `True`) | Bridge leaves, Claude Code stays |

Both persist to `mcp_session.json` and broadcast `routing_state_changed`.

### Middleware — `_CapabilityDetectionMiddleware`

Class-level state on the ASGI middleware (survives RoutingManager lifecycle):

| Attribute | Type | Purpose |
|-----------|------|---------|
| `_sampling_session_ids` | `set[str]` | Session IDs that declared `sampling` in their `initialize` |
| `_sampling_sse_sessions` | `set[str]` | Subset with active SSE streams (proof of live connection) |
| `_active_sse_streams` | `int` | Total SSE count across all clients |
| `_last_activity_write` | `float` | Monotonic timestamp for 10s session-file write throttle |

**`_inspect_initialize` guard logic** (prevents non-sampling clients from overwriting sampling state):

```
if sampling=False:
    1. Check routing.state.sampling_capable is True → block (primary)
    2. Check _sampling_sse_sessions is non-empty  → block (defense in depth)
    3. Neither → allow on_mcp_initialize(False)
if sampling=True:
    Always allow on_mcp_initialize(True)
```

The secondary check (step 2) covers the brief startup race window before the RoutingManager is fully populated, using class-level SSE tracking that exists independently of the RoutingManager.

**Activity tracking rules:**
- `_touch_routing_activity()` — sampling clients ONLY (keeps routing `last_activity` fresh)
- `_touch_session_file()` — ALL clients (keeps `mcp_session.json` fresh for disconnect checker fallback)

### Cross-process communication

```
MCP server RoutingManager
  → _broadcast_state_change()
    → local EventBus.publish("routing_state_changed")
    → _cross_process_notify() → asyncio.create_task(notify_event_bus())
      → HTTP POST /api/events/_publish
        → FastAPI backend RoutingManager.sync_from_event()
          → local EventBus.publish("routing_state_changed")
            → Frontend SSE stream
```

`sync_from_event()` uses a `_missing` sentinel to distinguish "key absent" from `None` (since `sampling_capable=None` is a legitimate value after disconnect).

### Disconnect checker background task

Runs every 30s in the RoutingManager. Two modes:

**Connected mode** (`mcp_connected=True`):
- Check if `last_activity` is stale (>60s)
- Before disconnecting, read `mcp_session.json` — if file has fresh activity (from a client the RoutingManager missed), avert disconnect and update `last_activity` from file
- If both stale → disconnect

**Disconnected mode** (`mcp_connected=False`):
- Poll `mcp_session.json` for reconnection (fallback for lost HTTP events)
- If file has fresh activity → `reconnect_detected` event

### Persistence — `mcp_session.json`

Written by MCP server only (`is_mcp_process=True`). Read by both processes.

```json
{
  "sampling_capable": true,
  "written_at": "2026-03-27T19:00:00+00:00",
  "last_activity": "2026-03-27T19:05:00+00:00",
  "sse_streams": 2
}
```

**Staleness windows** (in `config.py`):
- `MCP_CAPABILITY_STALENESS_MINUTES` (30 min) — startup recovery only; discards stale `sampling_capable`
- `MCP_ACTIVITY_STALENESS_SECONDS` (300s) — legacy fallback for disconnect detection when `sse_streams` is absent

### `_update_state` thread-safety contract

All callers (`set_provider`, `on_mcp_initialize`, `on_mcp_activity`, `on_mcp_disconnect`, `on_sampling_disconnect`, `on_session_invalidated`, `sync_from_event`, `_disconnect_loop`) are synchronous between their read of `self._state` and the `_update_state()` write. No `await` between read and replace. Safe under asyncio cooperative scheduling. Do not add `await` calls between state reads and `_update_state()`.

## Sampling pipeline internals

### End-to-end flow: MCP tool call → sampling → result

```
MCP client calls synthesis_optimize
  → tools/optimize.py: handle_optimize()
    → routing.resolve(ctx) → RoutingDecision(tier="sampling")
    → context_service.enrich(tier="sampling")
    → sampling_pipeline.run_sampling_pipeline(ctx, prompt, ...)
      → Phase 0: Explore (optional, via SamplingLLMAdapter)
      → Phase 1: Analyze (structured tool calling → AnalysisResult)
      → Phase 2: Optimize (structured → OptimizationResult, free-text mode)
      → Phase 3: Score (structured → ScoreResult, hybrid blend)
      → Phase 4: Suggest (structured → SuggestionsOutput, free-text mode)
      → Persist to DB, emit events, return result
```

### Structured output fallback chain

Each phase tries structured output first, then degrades:

```
1. Tool calling: create_message(tools=[pydantic_schema], tool_choice=required)
   → Extract tool_use block from response
   → Parse tool_input via model_validate()

2. Tool calling fails (McpError/TypeError/AttributeError):
   → Append JSON schema as text instruction to user message
   → Call create_message() without tools
   → _parse_text_response():
     a. Direct JSON parse (starts with '{')
     b. Markdown code block extraction (```json...```)
     c. Brace-depth counting for bare JSON in prose

3. All parsing fails (analyze only):
   → _build_analysis_from_text(): keyword-based classification
   → Scans raw prompt for task_type/domain signals
   → Confidence: 0.4–0.8 based on matched keywords
```

### Free-text vs JSON phases

| Phase | Output model | JSON forced? | Why |
|-------|-------------|-------------|-----|
| Analyze | `AnalysisResult` | Yes | Structured classification needed |
| Optimize | `OptimizationResult` | No | Free-text preserves markdown quality |
| Score | `ScoreResult` | Yes (1024 token cap) | Numeric scores need exact parsing |
| Suggest | `SuggestionsOutput` | No | Natural language suggestions |

The bridge extension checks `params.tools[0].inputSchema.title` against `FREE_TEXT_SCHEMAS = {"OptimizationResult", "SuggestionsOutput"}` to decide whether to inject JSON schema instructions.

### Text cleaning pipeline

When LLM returns free-text (optimizer phase), output is cleaned before storage:

1. `strip_meta_header(text)` — removes "Here is the optimized prompt...", markdown fences, meta-headers like "# Optimized Prompt"
2. `split_prompt_and_changes(text)` — splits on 14 marker patterns ("## Summary of Changes", "**Changes**", table formats, etc.). Returns `(clean_prompt, changes_summary)`

Both live in `app/utils/text_cleanup.py` — shared by sampling pipeline, MCP save_result, and REST passthrough save. Cleanup runs BEFORE heuristic scoring so scores reflect clean text.

### SamplingLLMAdapter

Minimal `LLMProvider` wrapper for `CodebaseExplorer` compatibility. Only implements `complete_parsed()`. Ignores `model` parameter — IDE controls selection. Used exclusively in the explore phase when a repo is linked.

### Bridge system prompt workaround

VS Code's Language Model API has no native system role. The bridge works around this by:
1. Prepending system prompt as a user message wrapped in `<system-instructions>` tags
2. Following with an assistant message: "Understood. I will follow these instructions precisely."
3. Then appending the actual conversation messages

This establishes system context without breaking the user/assistant turn structure.

### Per-phase model capture

Each `create_message()` result includes `result.model` (the actual model ID used by the IDE). These are collected in a `model_ids` dict and persisted to the DB `Optimization` record. The health endpoint and history list display the per-phase model breakdown.

### Passthrough workflow (`services/passthrough.py`)

Used by `synthesis_prepare_optimization` (MCP) and `POST /api/optimize` when tier=passthrough:

1. `resolve_strategy()` — validates requested strategy, falls back to "auto"
2. `assemble_passthrough_prompt()` — renders `prompts/passthrough.md` with:
   - Raw prompt, strategy instructions, scoring rubric (4K char cap)
   - Optional: codebase guidance, codebase context, adaptation state, analysis summary, applied patterns
3. Returns `(assembled_prompt, resolved_strategy_name)` for external LLM processing

The external LLM's output is then saved via `synthesis_save_result` with hybrid scoring (heuristic + z-score normalization, no LLM scoring in passthrough).

### Pipeline constants (`services/pipeline_constants.py`)

Shared between internal and sampling pipelines:

| Constant | Value | Purpose |
|----------|-------|---------|
| `CONFIDENCE_GATE` | 0.7 | Trust analyzer's strategy selection above this |
| `DOMAIN_CONFIDENCE_GATE` | 0.6 | Trust domain classification above this |
| `FALLBACK_STRATEGY` | "auto" | Default when confidence is low |
| `ANALYZE_MAX_TOKENS` | 4096 | Analyze phase budget |
| `SCORE_MAX_TOKENS` | 4096 | Score phase budget |

`compute_optimize_max_tokens(prompt_len)`: scales dynamically from 16K to 128K based on input length.

### MCP server monkey patches

Two production patches in `mcp_server.py` fix SDK bugs:

1. **SSE reconnection patch** (lines 56-74): Allows GET requests without session ID. Enables fast bridge reconnection after server restarts without full re-handshake.

2. **SSE deadlock fix** (lines 86-191): `StreamableHTTPSessionManager._handle_stateful_request` holds `_session_creation_lock` during `handle_request()`. SSE GET streams never return → lock held forever → all new sessions deadlock. Fix: create transport under lock, handle request outside lock.

## Testing patterns

### Routing tests (`tests/test_routing.py`)

- `_state()` helper builds `RoutingState` with optional provider mock
- `_ctx()` helper builds `RoutingContext` with force flags
- `manager` fixture creates a `RoutingManager` with `tmp_path` (no file persistence unless `is_mcp_process=True`)
- Event assertions use `asyncio.Queue` subscribed to `EventBus._subscribers`
- Disconnect checker tests (`TestManagerDisconnectLoop`) use real `asyncio.sleep` with short intervals
