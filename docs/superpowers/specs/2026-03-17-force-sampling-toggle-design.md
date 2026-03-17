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

- `_sanitize` (line ~165): the check iterates over a **hardcoded tuple** `("enable_explore", "enable_scoring", "enable_adaptation")` — extend it to `("enable_explore", "enable_scoring", "enable_adaptation", "force_sampling")`
- `_validate` (line ~196): same hardcoded tuple — extend identically
- These tuples are **not** auto-derived from `DEFAULTS`; both must be updated explicitly
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

**Implementation notes:**

1. **Hoist `PreferencesService`** — `synthesis_optimize` currently constructs `PreferencesService(DATA_DIR)` twice (once in each branch). Do not add a third. Instead, hoist a single `prefs = PreferencesService(DATA_DIR)` to the top of the function and reuse it in all branches. `PreferencesService.load()` does a disk read + write on every call; a redundant construction on every invocation is avoidable.

2. **Hoist `_resolve_workspace_guidance`** — call it once at the top (before the force-sampling check) and pass the result through to all branches. If force-sampling raises and falls through to normal routing, the existing code must reuse the already-fetched guidance rather than calling `_resolve_workspace_guidance` again. That function issues MCP `roots/list` RPCs and is not free.

Implementation sketch (after hoisting):

```python
# Hoisted — single construction and single workspace resolution
prefs = PreferencesService(DATA_DIR)
effective_strategy = strategy or prefs.get("defaults.strategy") or "auto"
guidance = await _resolve_workspace_guidance(ctx, workspace_path)

# Force-sampling short-circuit
if prefs.get("pipeline.force_sampling") and ctx and hasattr(ctx, "session") and ctx.session:
    try:
        return await _run_sampling_pipeline(
            ctx, prompt,
            effective_strategy if effective_strategy != "auto" else None,
            guidance,
        )
    except Exception as exc:
        logger.info("force_sampling requested but sampling failed, falling through: %s", type(exc).__name__)

# Normal routing continues below, reusing prefs / effective_strategy / guidance
```

`_run_sampling_pipeline` already sets `"pipeline_mode": "sampling"` in its return dict. The internal-pipeline return dict does not include `pipeline_mode`; the frontend active badge should key off `preferencesStore.pipeline.force_sampling` (client-side pref state) rather than the response field.

---

## Frontend UI

**Component:** `Navigator.svelte` (pipeline settings section, lines ~410–438)

- New toggle rendered identically to `enable_explore`, `enable_scoring`, `enable_adaptation`
- **Label:** "Force IDE sampling"
- **Sub-label:** "Use IDE's LLM for the 3-phase pipeline via MCP sampling"
- **Disabled state:** greyed out with tooltip *"No local provider to bypass — sampling is already the active path"* when `forgeStore.noProvider` is `true`. The toggle is disabled because there is no local provider to override, not because sampling is unavailable.
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
| `frontend/src/lib/components/layout/Navigator.svelte` | Add toggle + active badge next to strategy selector |
| `docs/CHANGELOG.md` | Add entry under `## Unreleased` → `Added` |

---

## Out of Scope

- `synthesis_analyze` — no sampling path, no change
- `synthesis_prepare_optimization` / `synthesis_save_result` — passthrough tools, unaffected
- REST `/api/optimize` — no MCP context, unaffected
- `schema_version` bump — not required for backwards-compatible additions
