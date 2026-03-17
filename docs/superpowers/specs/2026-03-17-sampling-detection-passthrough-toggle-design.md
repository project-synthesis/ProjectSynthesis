# Design Spec: Sampling Capability Detection + Force Passthrough Toggle
**Date:** 2026-03-17
**Status:** Approved

---

## Overview

Two related features:

1. **Runtime sampling capability detection** — disable the `force_sampling` toggle when the connected MCP client does not advertise the `sampling/createMessage` capability, with live state written to a shared file and surfaced via the health endpoint.
2. **`force_passthrough` toggle** — a new pipeline preference that forces `synthesis_optimize` to return the assembled passthrough template regardless of provider/sampling state, and makes the frontend forge enter passthrough mode.

These features are governed by a three-tier priority hierarchy: sampling > internal pipeline > passthrough.

---

## Priority Hierarchy

| Tier | Condition | Pipeline path |
|------|-----------|---------------|
| 1 | Sampling capable | MCP sampling (IDE's LLM, full 3-phase) |
| 2 | Provider exists | Internal pipeline |
| 3 | Neither | Passthrough template (manual) |

`force_sampling` pins to Tier 1. `force_passthrough` pins to Tier 3. They are mutually exclusive — only one can be `true` at a time. When Tier 1 is confirmed working, Tier 3 is pointless and its toggle is disabled.

---

## Section 1: Sampling Capability Detection

### Data flow

```
MCP tool call → check ctx.session.client_params.capabilities.sampling
             → write data/mcp_session.json
             → FastAPI /api/health reads file
             → frontend reads health
             → Navigator.svelte disables force_sampling toggle
```

### `data/mcp_session.json` schema

Written by `mcp_server.py` on every `synthesis_optimize` call:

```json
{
  "sampling_capable": false,
  "written_at": "2026-03-17T18:45:00Z"
}
```

Check: `ctx.session.client_params.capabilities.sampling is not None` — presence of the key (even as `{}`) means supported. Wrapped in `try/except` — if `ctx` is None or attribute lookup fails, file is not written.

### FastAPI `/api/health` change

Reads `data/mcp_session.json` and appends `sampling_capable: bool | null` to the health response:
- `bool` — file exists and is ≤ 5 minutes old
- `null` — file absent or older than 5 minutes (no active MCP session or stale)

### Frontend changes

- `forge.svelte.ts`: new `samplingCapable = $state<boolean | null>(null)` field
- `+page.svelte`: health already read on mount; add `forgeStore.samplingCapable = h.sampling_capable ?? null`
- `Navigator.svelte`: `force_sampling` toggle `disabled={forgeStore.noProvider || forgeStore.samplingCapable === false}`
  - Tooltip when disabled from lack of sampling: `"Your MCP client does not support sampling"`
  - Tooltip when disabled from no provider: unchanged (`"No local provider to bypass — sampling is already the active path"`)

---

## Section 2: Mutual Exclusion

`force_sampling` and `force_passthrough` cannot both be `true`.

### Server-side: `preferences.py` `_validate()`

```python
if prefs["pipeline"].get("force_sampling") and prefs["pipeline"].get("force_passthrough"):
    raise ValueError("force_sampling and force_passthrough are mutually exclusive")
```

`PATCH /api/preferences` returns `422` if both are sent as `true`.

### Client-side: `preferences.svelte.ts` `setPipelineToggle()`

When enabling one, the patch payload includes both fields:

```ts
// User enables force_sampling
// → sends: { pipeline: { force_sampling: true, force_passthrough: false } }

// User enables force_passthrough
// → sends: { pipeline: { force_passthrough: true, force_sampling: false } }
```

The store never sends a payload that would trigger the server-side `422`.

---

## Section 3: `force_passthrough` Toggle

### Preference

`pipeline.force_passthrough: bool = False` — added to:
- `DEFAULTS["pipeline"]`
- `_sanitize` hardcoded tuple
- `_validate` hardcoded tuple

### MCP routing in `synthesis_optimize`

Checked **first**, before `force_sampling`:

```python
if prefs.get("pipeline.force_passthrough"):
    logger.info("synthesis_optimize: force_passthrough=True — returning passthrough template")
    assembled, strategy_name = assemble_passthrough_prompt(...)
    # persist pending optimization record
    return {..., "pipeline_mode": "passthrough"}
```

Docstring updated to 5 execution paths:
1. `force_passthrough=True` → passthrough template directly
2. `force_sampling=True` + client supports sampling → sampling pipeline
3. Local provider exists → internal pipeline
4. No provider + client supports sampling → sampling pipeline
5. No provider + no sampling → passthrough template

### Frontend `forge.svelte.ts`

`forge()` method condition:

```ts
// Before:
if (this.noProvider) {

// After:
if (this.noProvider || preferencesStore.pipeline.force_passthrough) {
```

No other forge logic changes — the existing passthrough flow (assemble → display template → user submits result) is reused as-is.

### `Navigator.svelte`

New toggle in Pipeline section after `force_sampling`:
- Label: `Force passthrough`
- Tooltip on label: `"Bypass all pipelines — returns assembled template for manual processing"`
- **Disabled** when `forgeStore.samplingCapable === true`
- Disabled tooltip: `"Sampling is available — use Force IDE sampling instead"`
- **PASSTHROUGH badge** in Defaults section: visible when `force_passthrough === true`, amber/yellow accent (`#f59e0b`) to visually distinguish from SAMPLING badge's cyan

### Toggle disabled state matrix

| `samplingCapable` | `noProvider` | `force_sampling` | `force_passthrough` |
|-------------------|--------------|-----------------|---------------------|
| `true` | false | Available | **Disabled** |
| `true` | true | Available (sampling = natural path) | **Disabled** |
| `false` | false | **Disabled** | Available |
| `false` | true | **Disabled** | Available |
| `null` | false | Available | Available |
| `null` | true | Available | Available |

---

## Section 4: Testing

### `backend/tests/test_preferences.py`

**`TestForcePassthrough`** (mirrors `TestForceSampling`):
- `test_default_is_false`
- `test_can_be_patched_true`
- `test_can_be_patched_false`
- `test_non_boolean_rejected_by_validate`
- `test_non_boolean_sanitized_to_default`
- `test_missing_key_merges_to_false` (backward-compat)
- `test_get_dot_path`

**`TestMutualExclusion`**:
- `test_both_true_raises_value_error` — `patch({force_sampling: True, force_passthrough: True})` raises `ValueError`
- `test_force_sampling_true_when_passthrough_already_true_raises` — set passthrough true, then try to set sampling true
- `test_both_false_is_valid` — sanity check
- `test_only_force_sampling_true_valid`
- `test_only_force_passthrough_true_valid`

---

## Files Changed

| File | Change |
|------|--------|
| `backend/app/services/preferences.py` | Add `force_passthrough` to DEFAULTS/sanitize/validate; add mutual exclusion check in `_validate()` |
| `backend/app/mcp_server.py` | Write `mcp_session.json` in `synthesis_optimize`; add `force_passthrough` routing (first check); update force_sampling to not retry if sampling not in capabilities; update docstring to 5 paths |
| `backend/app/routers/health.py` | Read `mcp_session.json`, add `sampling_capable` to health response |
| `backend/tests/test_preferences.py` | Add `TestForcePassthrough` and `TestMutualExclusion` |
| `frontend/src/lib/stores/preferences.svelte.ts` | Add `force_passthrough` to `PipelinePrefs` and DEFAULTS; update `setPipelineToggle` to send mutual-exclusion patch |
| `frontend/src/lib/stores/forge.svelte.ts` | Add `samplingCapable` state field; update `forge()` passthrough condition |
| `frontend/src/routes/app/+page.svelte` | Set `forgeStore.samplingCapable` from health response |
| `frontend/src/lib/components/layout/Navigator.svelte` | Update `force_sampling` disabled logic; add `force_passthrough` toggle + PASSTHROUGH badge |
| `docs/CHANGELOG.md` | Add entries under Unreleased |
