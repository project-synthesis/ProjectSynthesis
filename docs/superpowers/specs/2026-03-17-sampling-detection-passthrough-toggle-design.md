# Design Spec: Sampling Capability Detection + Force Passthrough Toggle
**Date:** 2026-03-17
**Status:** Approved — pending implementation

---

## Overview

Two related features:

1. **Runtime sampling capability detection** — disable the `force_sampling` toggle when the connected MCP client does not advertise the `sampling/createMessage` capability, with live state written to a shared file and surfaced via the health endpoint.
2. **`force_passthrough` toggle** — a new pipeline preference that forces `synthesis_optimize` to return the assembled passthrough template regardless of provider/sampling state, and makes the frontend forge enter passthrough mode.

These features form a three-tier **capability hierarchy** (sampling > internal pipeline > passthrough), with two force flags that override automatic selection. The flags are mutually exclusive. Routing always checks `force_passthrough` first (it is a deliberate manual override with highest routing precedence), then `force_sampling`, then automatic provider/sampling detection.

---

## Capability Hierarchy

| Tier | Condition | Pipeline path | Force flag |
|------|-----------|---------------|------------|
| 1 | Client supports MCP sampling | Sampling pipeline (IDE's LLM) | `force_sampling` |
| 2 | Local provider exists | Internal 3-phase pipeline | — |
| 3 | Neither | Passthrough template (manual) | `force_passthrough` |

`force_sampling` pins to Tier 1. `force_passthrough` pins to Tier 3. When Tier 1 is confirmed working, Tier 3 is redundant — the `force_passthrough` toggle is disabled.

**Routing order in `synthesis_optimize` (highest precedence first):**
1. `force_passthrough=True` → return passthrough template immediately
2. `force_sampling=True` + sampling capable → sampling pipeline
3. No provider + sampling capable → sampling pipeline
4. Provider exists → internal pipeline
5. No provider + no sampling → passthrough template

Because the flags are mutually exclusive (enforced at save time), paths 1 and 2 cannot both be active simultaneously. The order is defensive.

---

## Section 1: Sampling Capability Detection

### Data flow

```
MCP tool call → check ctx.session.client_params.capabilities.sampling
             → write data/mcp_session.json  (always, before routing branches)
             → FastAPI /api/health reads file
             → frontend reads health on mount
             → Navigator.svelte disables force_sampling toggle
```

### `data/mcp_session.json` schema

Written by `mcp_server.py` on **every `synthesis_optimize` call**, **before** any routing branches (including before the `force_passthrough` early-return). This ensures the file stays fresh regardless of which execution path is taken.

```json
{
  "sampling_capable": false,
  "written_at": "2026-03-17T18:45:00Z"
}
```

Detection: `ctx.session.client_params.capabilities.sampling is not None` — presence of the key (even as `{}`) means supported. Entire detection + write is wrapped in `try/except` — if `ctx` is `None` or attribute lookup fails, the file is not written for that call.

### FastAPI `/api/health` change

Reads `data/mcp_session.json` and appends `sampling_capable: bool | null` to the health response:
- `bool` — file exists and is ≤ 5 minutes old
- `null` — file absent or older than 5 minutes (no active MCP session or stale)

### Frontend changes

- `forge.svelte.ts`: new `samplingCapable = $state<boolean | null>(null)` field
- `frontend/src/routes/app/+page.svelte`: inside the `.then((h) => { ... })` health callback (where `h` is the health response and `forgeStore.noProvider = !h.provider` is already set), add:
  ```ts
  forgeStore.samplingCapable = h.sampling_capable ?? null;
  ```
- `Navigator.svelte`: `force_sampling` toggle disabled condition:
  ```svelte
  disabled={forgeStore.noProvider || forgeStore.samplingCapable === false || preferencesStore.pipeline.force_passthrough}
  ```
  Tooltip priority (first matching wins):
  1. `noProvider`: `"No local provider to bypass — sampling is already the active path"`
  2. `samplingCapable === false`: `"Your MCP client does not support sampling"`
  3. `force_passthrough`: `"Disable Force passthrough first"`

---

## Section 2: Mutual Exclusion

`force_sampling` and `force_passthrough` cannot both be `true`.

### Server-side: `preferences.py` `_validate()`

In `_validate()`, the argument is a plain dict (not a `PreferencesService` instance):

```python
if prefs["pipeline"].get("force_sampling") and prefs["pipeline"].get("force_passthrough"):
    raise ValueError("force_sampling and force_passthrough are mutually exclusive")
```

`PATCH /api/preferences` returns `422` if both are sent as `true`.

### Client-side: `preferences.svelte.ts` `setPipelineToggle()`

When enabling one, the patch payload explicitly clears the other:

```ts
// User enables force_sampling
// → sends: { pipeline: { force_sampling: true, force_passthrough: false } }

// User enables force_passthrough
// → sends: { pipeline: { force_passthrough: true, force_sampling: false } }

// Disabling either
// → sends only the targeted key (no need to clear the other — it's already false)
```

The store never sends a payload that would trigger the server-side `422`.

---

## Section 3: `force_passthrough` Toggle

### Preference

`pipeline.force_passthrough: bool = False` — added to:
- `DEFAULTS["pipeline"]` dict
- The tuple literal inside `for toggle in (...)` at the `_sanitize` method (line ~166 of `preferences.py`)
- The tuple literal inside `for toggle in (...)` at the `_validate` method (line ~197 of `preferences.py`)

Note: these are tuple literals inside `for toggle in` loops, not module-level constants.

### MCP routing in `synthesis_optimize`

In `synthesis_optimize`, `prefs` is a `PreferencesService` instance. Use the dot-path accessor:

```python
if prefs.get("pipeline.force_passthrough"):
    logger.info("synthesis_optimize: force_passthrough=True — returning passthrough template")
    assembled, strategy_name = assemble_passthrough_prompt(...)
    # persist pending optimization record
    return {..., "pipeline_mode": "passthrough"}
```

This is checked **first** — immediately after writing `mcp_session.json` and before the `force_sampling` block.

Docstring updated to 5 execution paths (as listed in the Capability Hierarchy routing order above).

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
- Disabled condition: `forgeStore.samplingCapable === true || preferencesStore.pipeline.force_sampling`
  - Tooltip when disabled by sampling: `"Sampling is available — use Force IDE sampling instead"`
  - Tooltip when disabled by `force_sampling`: `"Disable Force IDE sampling first"`
- **PASSTHROUGH badge** in Defaults section: visible when `force_passthrough === true`, amber accent (`#f59e0b` / `var(--color-warn, #f59e0b)`) to visually distinguish from SAMPLING badge's cyan

### Toggle disabled-state matrix

`force_passthrough` available = not `(samplingCapable === true)` and not `force_sampling`
`force_sampling` available = not `noProvider` and not `(samplingCapable === false)` and not `force_passthrough`

| `samplingCapable` | `noProvider` | `force_sampling` | `force_passthrough` toggle | `force_sampling` toggle |
|---|---|---|---|---|
| `true` | false | false | **Disabled** (sampling works) | Available |
| `true` | false | true | **Disabled** (sampling works + mutual excl.) | Available |
| `true` | true | false | **Disabled** (sampling works) | Available |
| `false` | false | false | Available | **Disabled** (client can't sample) |
| `false` | false | true | **Disabled** (mutual excl.) | **Disabled** (client can't sample) |
| `false` | true | false | Available | **Disabled** (noProvider + no sampling) |
| `null` | false | false | Available | Available |
| `null` | false | true | **Disabled** (mutual excl.) | Available |
| `null` | false | false | Available | **Disabled** (mutual excl.) when `force_passthrough=true` |
| `null` | true | false | Available | **Disabled** (noProvider) |

*When `samplingCapable=null` (no active MCP session), both toggles are available — this is intentional. The user may be configuring preferences before connecting an MCP client.*

*Edge case: when `force_passthrough=true` and `ctx` is `None` (no active MCP session), the `mcp_session.json` write is silently skipped. The health endpoint will return `sampling_capable: null` once the file goes stale (>5 min). This is correct behavior — no MCP session means no sampling capability can be determined, and `null` correctly leaves both toggles available.*

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
- `test_force_sampling_true_when_passthrough_already_true_raises` — save `force_passthrough=True`, then patch `force_sampling=True` (without clearing passthrough) raises `ValueError`
- `test_both_false_is_valid`
- `test_only_force_sampling_true_valid`
- `test_only_force_passthrough_true_valid`

---

## Files Changed

| File | Change |
|------|--------|
| `backend/app/services/preferences.py` | Add `force_passthrough` to DEFAULTS/sanitize/validate tuples; add mutual exclusion check in `_validate()` |
| `backend/app/mcp_server.py` | Write `mcp_session.json` before routing branches; add `force_passthrough` check (first); update docstring to 5 paths |
| `backend/app/routers/health.py` | Read `mcp_session.json`, add `sampling_capable: bool \| null` to health response |
| `backend/tests/test_preferences.py` | Add `TestForcePassthrough` and `TestMutualExclusion` |
| `frontend/src/lib/stores/preferences.svelte.ts` | Add `force_passthrough` to `PipelinePrefs` and DEFAULTS; update `setPipelineToggle` to send mutual-exclusion patch |
| `frontend/src/lib/stores/forge.svelte.ts` | Add `samplingCapable` state field; update `forge()` passthrough condition |
| `frontend/src/routes/app/+page.svelte` | Set `forgeStore.samplingCapable = h.sampling_capable ?? null` in health callback |
| `frontend/src/lib/components/layout/Navigator.svelte` | Update `force_sampling` disabled logic; add `force_passthrough` toggle + PASSTHROUGH badge |
| `docs/CHANGELOG.md` | Add entries under Unreleased |
