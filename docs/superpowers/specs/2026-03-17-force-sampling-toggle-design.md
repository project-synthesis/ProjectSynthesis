# Force Sampling Toggle — Design Spec

**Date:** 2026-03-17
**Status:** Approved
**Scope:** MCP server routing, backend preferences, frontend store + UI

---

## Problem

The MCP server has three execution paths in `synthesis_optimize`:

1. **Internal pipeline** — local provider (`claude_cli` or `ANTHROPIC_API_KEY`) does the 3-phase work
2. **MCP sampling** — no local provider, IDE's LLM does the 3-phase work via `ctx.session.create_message()`
3. **Passthrough template** — no local provider, no sampling support; returns raw assembled prompt for manual processing

Path 2 (sampling) only activates when no provider is detected at MCP server startup. There is currently no way to force it when a local provider exists. Users running Claude Code MAX (which has `claude_cli` on PATH) cannot route through the IDE's LLM without physically removing the CLI.

---

## Solution

Add a `pipeline.force_sampling` boolean preference (default `false`). When `true`, `synthesis_optimize` skips the local provider and routes directly to the MCP sampling pipeline — provided the MCP client supports `ctx.session.create_message()`. Graceful degradation applies if the client does not support sampling.

---

## Data Model

### Backend — `preferences.py`

Add to `DEFAULTS["pipeline"]`:

```python
"pipeline": {
    "enable_explore": True,
    "enable_scoring": True,
    "enable_adaptation": True,
    "force_sampling": False,   # new
}
```

- `_sanitize`: extend the boolean-type check loop to include `"force_sampling"`
- `_validate`: extend the validation loop to include `"force_sampling"`
- `schema_version` stays at `1` — backwards-compatible (missing key deep-merges to default `False`)

### Frontend — `preferences.svelte.ts`

```ts
export interface PipelinePrefs {
  enable_explore: boolean;
  enable_scoring: boolean;
  enable_adaptation: boolean;
  force_sampling: boolean;   // new
}
```

`DEFAULTS.pipeline.force_sampling = false`. No new store methods needed — existing `setPipelineToggle("force_sampling", value)` handles it.

---

## MCP Server Routing

File: `backend/app/mcp_server.py`, function `synthesis_optimize`.

New routing order (inserted before the existing `if not provider:` block):

```
force_sampling=True AND ctx.session available?
  → _run_sampling_pipeline()
  → on exception: log, fall through to normal routing

provider exists?
  → internal pipeline

else
  → try sampling
  → on exception: passthrough template
```

Implementation sketch:

```python
prefs = PreferencesService(DATA_DIR)
if prefs.get("pipeline.force_sampling") and ctx and hasattr(ctx, "session") and ctx.session:
    guidance = await _resolve_workspace_guidance(ctx, workspace_path)
    effective_strategy = strategy or prefs.get("defaults.strategy") or "auto"
    try:
        return await _run_sampling_pipeline(
            ctx, prompt,
            effective_strategy if effective_strategy != "auto" else None,
            guidance,
        )
    except Exception as exc:
        logger.info("force_sampling requested but sampling failed, falling through: %s", type(exc).__name__)
```

`_run_sampling_pipeline` already sets `"pipeline_mode": "sampling"` in its return dict — no new result fields needed.

---

## Frontend UI

**Component:** `Inspector.svelte` (pipeline settings section)

- New toggle rendered identically to `enable_explore`, `enable_scoring`, `enable_adaptation`
- **Label:** "Force IDE sampling"
- **Sub-label:** "Use IDE's LLM for the 3-phase pipeline via MCP sampling"
- **Disabled state:** greyed out with tooltip *"Requires MCP client with sampling support"* when `forgeStore.noProvider` is `true` (force_sampling is meaningless with no provider to override)
- **Active indicator:** `sampling` badge next to the strategy selector when `force_sampling` is `true` and `!forgeStore.noProvider`

---

## Error Handling & Edge Cases

| Scenario | Behaviour |
|---|---|
| `force_sampling=true`, client has no `ctx.session` | Falls through to normal routing silently |
| `force_sampling=true`, sampling raises | Logs exception, falls through to internal pipeline or passthrough template |
| `force_sampling=true`, `synthesis_analyze` called | Unaffected — `synthesis_analyze` always requires a local provider |
| Toggle flipped mid-session | Reactive on next MCP call — `PreferencesService` reads from disk per-call |
| `force_sampling=true`, called via REST `/api/optimize` | Irrelevant — REST path never touches MCP routing |

---

## Files Changed

| File | Change |
|---|---|
| `backend/app/services/preferences.py` | Add `force_sampling: False` to `DEFAULTS`; extend `_sanitize` and `_validate` boolean loops |
| `backend/app/mcp_server.py` | Add force-sampling short-circuit at top of `synthesis_optimize` |
| `frontend/src/lib/stores/preferences.svelte.ts` | Add `force_sampling: boolean` to `PipelinePrefs` and `DEFAULTS` |
| `frontend/src/lib/components/layout/Inspector.svelte` | Add toggle + active badge |

---

## Out of Scope

- `synthesis_analyze` — no sampling path, no change
- `synthesis_prepare_optimization` / `synthesis_save_result` — passthrough tools, unaffected
- REST `/api/optimize` — no MCP context, unaffected
- `schema_version` bump — not required for backwards-compatible additions
