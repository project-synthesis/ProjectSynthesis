# Pipeline Performance Optimization — Design Spec

**Date:** 2026-03-23
**Status:** Approved
**Scope:** Backend providers, pipeline orchestrator, preferences, frontend settings, prompt templates

## Problem

The pipeline makes suboptimal use of Anthropic API performance features:

1. **Effort parameter overused** — Analyze and score phases use `effort="medium"` for tasks that are classification/evaluation. `"low"` is sufficient and 30-40% faster.
2. **Max tokens oversized** — Analyze and score default to 16384 tokens but produce 500-2000 tokens. Oversized caps waste model compute.
3. **System prompt caching inactive** — `agent-guidance.md` (~300 tokens) is below all caching thresholds (Sonnet: 2048, Opus/Haiku: 4096). The `cache_control: ephemeral` marker is silently ignored for 4/5 phases.
4. **Scoring cache TTL too short** — `scoring.md` (17KB) caches correctly but uses default 5-minute TTL. A 1-hour TTL saves on repeated cache writes for a completely static rubric.
5. **Effort not user-configurable per phase** — Only `optimizer_effort` is exposed. Power users cannot tune analyze/score speed vs quality.

## Design

### 1. Effort Parameter Tuning

**Defaults changed:**
- Analyze: `"medium"` -> `"low"`
- Score: `"medium"` -> `"low"`
- Optimize/Refine: unchanged (`"high"`, user-configurable)
- Suggest: unchanged (Haiku, effort not supported)

**New preference keys:**
- `pipeline.analyzer_effort` — default `"low"`, valid: `{"low", "medium", "high", "max"}`
- `pipeline.scorer_effort` — default `"low"`, valid: `{"low", "medium", "high", "max"}`

**Validation expansion:**
- `VALID_EFFORTS` in `preferences.py`: `{"high", "max"}` -> `{"low", "medium", "high", "max"}`
- All three effort keys (`analyzer_effort`, `optimizer_effort`, `scorer_effort`) validated against the same expanded set
- This means `optimizer_effort` now also accepts `"low"` and `"medium"`. Users who explicitly set `optimizer_effort="low"` do so at their own risk — the Opus optimize phase will produce shorter, less thorough output. This is intentional flexibility.
- `max` is Opus-only — setting `effort="max"` on a Sonnet model triggers `ProviderBadRequestError` (Anthropic API, non-retryable) or a non-zero CLI exit (Claude CLI, also surfaced as non-retryable). Both error paths are graceful — the user sees the failure and can adjust settings.

**Validation changes in `preferences.py`:**
- `DEFAULTS["pipeline"]` gains `"analyzer_effort": "low"` and `"scorer_effort": "low"`
- `_sanitize()` must validate all three effort keys (not just `optimizer_effort`)
- `_validate()` must also check the two new keys — currently only validates `optimizer_effort`. Without this, a `PATCH` with `analyzer_effort="turbo"` would succeed but get silently corrected on next `load()`.
- Existing `optimizer_effort` values of `"high"` remain valid under the expanded set

**Provider behavior:**
- Anthropic API: `output_config: {"effort": value}` — already implemented, just new values flowing through
- Claude CLI: `--effort value` flag — already wired in `claude_cli.py:78-81`
- Haiku: effort skipped regardless of preference value (existing gate in `base.py`)

### 2. Max Tokens Reduction

**New constants in `pipeline_constants.py`:**
```python
ANALYZE_MAX_TOKENS = 4096
SCORE_MAX_TOKENS = 4096
```

**Applied in:**
- `pipeline.py` analyze phase call (was: default 16384)
- `pipeline.py` score phase call (was: default 16384)
- `refinement_service.py` analyze phase call
- `refinement_service.py` score phase call

**Not changed:**
- Optimize/Refine: dynamic `compute_optimize_max_tokens()` — correct as-is
- Suggest: `max_tokens=2048` — correct as-is

### 3. System Prompt Expansion + Caching Activation

#### 3a) Expand `agent-guidance.md`

Grow from ~300 tokens to **5000+ tokens** (~20KB) to cross all caching thresholds with comfortable margin. The highest threshold is 4096 tokens (Opus/Haiku) — targeting 5000+ gives ~20% headroom. Content is genuinely useful guidance, not padding:

- **Pipeline identity** — role definition, quality mandate
- **Output quality standards** — precision over verbosity, faithfulness to user intent, structural clarity
- **Domain expertise rules** — how to handle coding vs writing vs analysis vs creative prompts differently
- **Strategy application principles** — when to apply heavily vs lightly, how to blend strategies
- **Anti-patterns** — verbosity inflation, hallucinated constraints, over-formatting, instruction injection
- **Prompt engineering best practices** — the model should follow when rewriting
- **Context handling** — how to use codebase context, workspace intelligence, adaptation state, applied patterns when present

This system prompt is shared by analyze, optimize, suggest, and refine phases. One cache entry serves all four. The scoring phase uses `scoring.md` (separate cache entry, already effective).

**Acceptance criteria:** The expanded content must be reviewed against existing pipeline output quality on 5+ representative prompts before merge. The system prompt affects every non-scoring phase — any quality regression here is pipeline-wide.

#### 3b) Scoring TTL Extension

Change `scoring.md` system prompt cache from default 5min to `"ttl": "1h"`.

- Write cost: 1.25x -> 2x (one-time per hour)
- Read cost: 0.1x (unchanged)
- Break-even: ~2 scoring calls per hour (typical usage far exceeds this)

#### 3c) Provider TTL Support

Add `cache_ttl: str | None = None` parameter through the full provider chain. Every layer must accept and forward the parameter:

```
pipeline._call_provider(cache_ttl="1h")                      # pipeline.py wrapper
  -> call_provider_with_retry(cache_ttl="1h")                 # base.py standalone function
    -> provider.complete_parsed(cache_ttl="1h")                # base.py abstract / anthropic_api.py
      -> _build_kwargs(cache_ttl="1h")                         # anthropic_api.py static helper
        -> cache_control = {"type": "ephemeral", "ttl": "1h"}  # SDK call
```

**Specific changes required (all must be updated):**
1. `base.py` — `LLMProvider.complete_parsed()` abstract signature (line 100): add `cache_ttl: str | None = None`
2. `base.py` — `LLMProvider.complete_parsed_streaming()` default impl (line 120): add `cache_ttl` param, forward to `complete_parsed()`
3. `base.py` — `call_provider_with_retry()` function (line 166): add `cache_ttl` kwarg. Note: this function uses explicit named params (not `**kwargs`) at the `call_fn()` call site (line 195) — `cache_ttl` must be added to that explicit argument list.
4. `anthropic_api.py` — `_build_kwargs()`: accept `cache_ttl`, conditionally add `"ttl"` key to `cache_control` dict
5. `anthropic_api.py` — `complete_parsed()` and `complete_parsed_streaming()`: accept and forward `cache_ttl`
6. `claude_cli.py` — `complete_parsed()`: accept `cache_ttl` (ignored, CLI handles caching internally)
7. `pipeline.py` — `_call_provider()` wrapper (line 76): accept and forward `cache_ttl`
8. `refinement_service.py` — `_call_provider()` wrapper: accept and forward `cache_ttl`

Default `None` = standard 5-minute ephemeral (no `ttl` key in dict). Only the scoring phase passes `"1h"`.

### 4. Frontend — Effort Controls

Add EFFORT subsection to settings panel in `Navigator.svelte` (where MODELS, PIPELINE, and DEFAULTS sections live):

```
EFFORT
  Analyzer      low
  Optimizer     high
  Scorer        low
```

- Same editable-text-field pattern as MODELS section
- Values validated client-side: `low`, `medium`, `high`, `max`
- Wired to `PATCH /api/preferences` with keys `pipeline.analyzer_effort`, `pipeline.optimizer_effort`, `pipeline.scorer_effort`

**`PipelinePrefs` type update:** The `PipelinePrefs` interface in `preferences.svelte.ts` currently contains only boolean toggles. The three effort fields are strings, breaking the existing type shape. Add them as `string` fields with defaults. Note: `optimizer_effort` already exists in the backend `DEFAULTS` but is missing from the frontend `DEFAULTS` — this existing gap is fixed as part of this work.

### 5. Sampling Pipeline Alignment

`sampling_pipeline.py` uses `ModelPreferences.speed` hints (IDE-controlled). The sampling pipeline reads the new `analyzer_effort` and `scorer_effort` preference keys for future use but **does not change `ModelPreferences` in this iteration**. MCP sampling has no effort parameter — the IDE controls model behavior. Mapping effort to intelligence/speed ratios is deferred until the MCP sampling spec supports effort natively.

### 6. Refinement Service Alignment

`refinement_service.py` mirrors pipeline changes:
- Analyze call: `analyzer_effort` preference + `ANALYZE_MAX_TOKENS`
- Score call: `scorer_effort` preference + `SCORE_MAX_TOKENS`
- Refine call: already uses `optimizer_effort` — no change
- System prompt: same expanded `agent-guidance.md` via `PromptLoader` — no code change

### 7. Trace Logging Enhancement

Include effort level in trace logger output for each phase. Currently `trace_logger.log_phase()` records duration and token counts but not effort. Since effort is now per-phase and user-configurable, logging it helps diagnose performance regressions.

Include `"effort"` as a key in the existing `result` dict argument when calling `trace_logger.log_phase()`. No changes to `trace_logger.py` itself — it already accepts a freeform `result: dict`. Example: `result={"task_type": analysis.task_type, "strategy": analysis.selected_strategy, "effort": effort_value}`.

## File Changes

| File | Changes |
|------|---------|
| `prompts/agent-guidance.md` | Expand from ~300 to 5000+ tokens with quality standards, domain rules, anti-patterns |
| `backend/app/providers/base.py` | Add `cache_ttl: str \| None = None` to `complete_parsed()`, `complete_parsed_streaming()`, and `call_provider_with_retry()` |
| `backend/app/providers/anthropic_api.py` | Add `cache_ttl` to `_build_kwargs()`, `complete_parsed()`, `complete_parsed_streaming()` |
| `backend/app/providers/claude_cli.py` | Accept `cache_ttl` parameter in `complete_parsed()` (ignored) |
| `backend/app/services/pipeline.py` | Replace hardcoded `effort="medium"` with `prefs.get("pipeline.analyzer_effort", prefs_snapshot) or "low"` (analyze) and `prefs.get("pipeline.scorer_effort", prefs_snapshot) or "low"` (score). Add max_tokens constants, cache_ttl for score, effort in trace logs. |
| `backend/app/services/pipeline_constants.py` | Add `ANALYZE_MAX_TOKENS`, `SCORE_MAX_TOKENS` |
| `backend/app/services/preferences.py` | Add `analyzer_effort`/`scorer_effort` defaults, expand `VALID_EFFORTS`, update `_sanitize()` |
| `backend/app/services/refinement_service.py` | Same effort + max_tokens + cache_ttl changes as pipeline.py |
| `backend/app/services/sampling_pipeline.py` | Read new effort preference keys (no behavioral change) |
| `frontend/src/lib/stores/preferences.svelte.ts` | Add effort string fields to `PipelinePrefs`, add `optimizer_effort` to defaults |
| `frontend/src/lib/components/layout/Navigator.svelte` | Add EFFORT section to settings panel |
| `backend/tests/test_pipeline.py` | Update for new effort defaults, max_tokens, cache_ttl |
| `backend/tests/test_preferences.py` | Validate new effort keys accept/reject, correct defaults |
| `docs/CHANGELOG.md` | Document under Unreleased |

## Test Plan

**`test_preferences.py`:**
- `analyzer_effort` and `scorer_effort` accept `{"low", "medium", "high", "max"}`
- Invalid values (e.g., `"turbo"`) are rejected/sanitized
- `optimizer_effort` now also accepts `"low"` and `"medium"` under expanded set
- Missing keys gain correct defaults on load (`analyzer_effort="low"`, `scorer_effort="low"`)

**`test_pipeline.py`:**
- Analyze phase call passes `effort="low"` (or preference value) and `max_tokens=4096`
- Score phase call passes `effort="low"` (or preference value) and `max_tokens=4096`
- Score phase call passes `cache_ttl="1h"`
- Optimize phase call unchanged: `effort="high"`, `streaming=True`, dynamic max_tokens

**`test_refinement_service.py` (if exists, or add to existing test file):**
- Same effort/max_tokens/cache_ttl assertions as pipeline tests for refinement analyze and score calls

## Backward Compatibility

- Existing `preferences.json` files gain new defaults via `PreferencesService.load()` merge-with-defaults pattern
- No migration needed — missing keys get defaults on load
- API responses unchanged — effort values are internal to provider calls
- Frontend gracefully handles missing effort fields (falls back to defaults)
- `VALID_EFFORTS` expansion means previously-invalid values like `"low"` for `optimizer_effort` are now accepted. This is a minor behavioral change (manual file edits with `"low"` no longer get sanitized), not a compatibility break.

## Expected Impact

| Metric | Before | After |
|--------|--------|-------|
| Analyze latency | ~3-5s | ~2-3s (effort low) |
| Score latency | ~3-5s | ~2-3s (effort low) |
| System prompt cache hits | 0% (analyze/optimize/suggest/refine) | ~90%+ (above threshold) |
| Scoring cache writes/hour | ~12 (5min TTL) | ~1 (1h TTL) |
| Per-phase token spend | baseline | ~30-40% reduction on analyze+score |
