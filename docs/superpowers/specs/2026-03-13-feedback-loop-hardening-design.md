# Feedback Loop Hardening & Optimization

**Date:** 2026-03-13
**Status:** Approved
**Scope:** Full-stack — backend services, database schema, pipeline integration, frontend UX, MCP tools, observability
**Supersedes:** Addresses gaps identified in end-to-end audit of the feedback system; complements `2026-03-13-quality-feedback-loops-design.md`

## Overview

End-to-end hardening of the feedback loop system across two phases. Phase 1 fixes backend bugs, wires dead integrations, and builds the framework performance model. Phase 2 redesigns the frontend UX with three-tier progressive disclosure and a result assessment engine.

**Audit findings addressed:** 15 issues across backend, frontend, MCP, and pipeline layers (see Appendix A).

---

## Phase 1: Backend Fixes & Integration Wiring

### 1.1 Framework-Aware Adaptive Pipeline

**Problem:** Strategy affinities are computed but never injected into the LLM prompt. The optimizer receives no adaptation state. Validation scoring doesn't account for framework-specific quality profiles.

**Solution:** Build a closed-loop framework performance model.

#### New database table: `framework_performance`

```sql
CREATE TABLE framework_performance (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    task_type TEXT NOT NULL,
    framework TEXT NOT NULL,
    avg_scores TEXT,              -- JSON: {dim: float}
    user_rating_avg FLOAT DEFAULT 0.0,
    issue_frequency TEXT,         -- JSON: {issue_id: count}
    sample_count INTEGER DEFAULT 0,
    elasticity_snapshot TEXT,     -- JSON: {dim: float}
    last_updated DATETIME,
    UNIQUE(user_id, task_type, framework)
);
CREATE INDEX ix_framework_perf_user_task ON framework_performance(user_id, task_type);
```

#### Framework validation profiles (static config)

Each framework has intrinsic emphasis/de-emphasis multipliers. Keys use the existing hyphenated convention from `strategy_selector.py`:

```python
# Default profile applied to any framework not explicitly listed
DEFAULT_FRAMEWORK_PROFILE = {
    "emphasis": {},
    "de_emphasis": {},
    "entropy_tolerance": 1.0,
}

FRAMEWORK_PROFILES = {
    "chain-of-thought": {
        "emphasis": {"structure_score": 1.3, "clarity_score": 1.2},
        "de_emphasis": {"conciseness_score": 0.8},
        "entropy_tolerance": 0.7,
    },
    "step-by-step": {
        "emphasis": {"structure_score": 1.3, "clarity_score": 1.1},
        "de_emphasis": {"conciseness_score": 0.8},
        "entropy_tolerance": 0.7,
    },
    "persona-assignment": {
        "emphasis": {"faithfulness_score": 1.3, "specificity_score": 1.2},
        "de_emphasis": {"structure_score": 0.9},
        "entropy_tolerance": 1.2,
    },
    "CO-STAR": {
        "emphasis": {"clarity_score": 1.2, "faithfulness_score": 1.2},
        "de_emphasis": {"conciseness_score": 0.85},
        "entropy_tolerance": 1.0,
    },
    "RISEN": {
        "emphasis": {"faithfulness_score": 1.3, "specificity_score": 1.2},
        "de_emphasis": {},
        "entropy_tolerance": 0.9,
    },
    "structured-output": {
        "emphasis": {"structure_score": 1.3, "specificity_score": 1.2},
        "de_emphasis": {"clarity_score": 0.9},
        "entropy_tolerance": 0.8,
    },
    "constraint-injection": {
        "emphasis": {"specificity_score": 1.3, "faithfulness_score": 1.1},
        "de_emphasis": {"conciseness_score": 0.85},
        "entropy_tolerance": 0.9,
    },
    "few-shot-scaffolding": {
        "emphasis": {"specificity_score": 1.3, "clarity_score": 1.1},
        "de_emphasis": {"conciseness_score": 0.75},
        "entropy_tolerance": 1.1,
    },
    "context-enrichment": {
        "emphasis": {"faithfulness_score": 1.2, "specificity_score": 1.2},
        "de_emphasis": {"conciseness_score": 0.8},
        "entropy_tolerance": 1.0,
    },
    "role-task-format": {
        "emphasis": {"structure_score": 1.2, "clarity_score": 1.1},
        "de_emphasis": {},
        "entropy_tolerance": 1.0,
    },
}
```

Lookup: `FRAMEWORK_PROFILES.get(framework, DEFAULT_FRAMEWORK_PROFILE)`. Unknown frameworks get neutral multipliers.

**User ID resolution:** The `framework_performance` table uses the same `user_id` resolution as the rest of the feedback system. When no auth token is present, `user_id` defaults to `"anonymous"`. Anonymous users accumulate performance data normally — the `UNIQUE(user_id, task_type, framework)` constraint pools all anonymous sessions into shared rows. This is acceptable because anonymous usage is single-tenant (local deployment). Multi-tenant deployments require auth.

#### Closed-loop data flow

1. **Strategy selection** — Load `framework_performance` for user+task_type. Compute composite score per framework:
   ```
   composite = weighted_avg(historical_scores, user_weights)
             × (1.0 + 0.3 × user_rating_avg)          # satisfaction: [-1,1] → [0.7, 1.3]
             × exp(-0.01 × days_since_last_used)        # recency: 0 days=1.0, 30 days=0.74, 90 days=0.41
   ```
   Inject full performance profile into strategy prompt with statistical context.
2. **Optimizer** — Receives selected framework's validation profile and user dimension weights as soft hints in the optimization prompt.
3. **Validator** — Applies framework profile multipliers combined with user weights: `effective_weight = framework_emphasis[dim] × user_weight[dim]`.
4. **Post-validation update** — Record scores + framework to `framework_performance`. When user feedback arrives, update `user_rating_avg`.
5. **Retry oracle** — Framework-aware focus selection; don't focus on dimensions the framework de-emphasizes.

#### New API endpoints (in new `backend/app/routers/framework.py`)

- `GET /api/framework-performance/{task_type}` — user's framework performance data
- `GET /api/framework-profiles` — static framework validation profiles

#### New MCP tool

- `synthesis_get_framework_performance` — query performance data per user/task_type

---

### 1.2 Hardened Algorithms

#### 1.2a Adaptation concurrency — debounced recomputation with versioning

Replace the racy `_user_busy` check with a debounced, version-aware system:

1. On feedback submit: increment `adaptation_version` (atomic, per-user in module-level dict), schedule recomputation with 500ms debounce window.
2. **Debounce mechanism:** Use `asyncio.get_event_loop().call_later(0.5, ...)` wrapping a coroutine that fires the recomputation. Each new feedback cancels the previous timer handle via `handle.cancel()` before scheduling a new one. Timer handles stored in a per-user dict `_debounce_handles: dict[str, asyncio.TimerHandle]`. This replaces the current `BackgroundTasks.add_task()` approach — the debounce timer IS the background task.
3. When debounce fires: acquire per-user async lock, read current version, fetch ALL feedbacks, compute adaptation, write to DB with version. If version changed during computation, re-queue once (not infinitely — max 1 re-queue to prevent livelock).
4. Emit `adaptation_updated` SSE event for frontend reactivity.

#### 1.2b Elasticity tracking — framework-aware responsiveness model

Track elasticity for ALL dimensions on every attempt (not just focused ones). Store per-framework:

```python
elasticity_matrix: dict[str, dict[str, float]]
# {framework: {dimension: elasticity}}
```

Update with exponential moving average (α=0.4). Cold-start prior: all dimensions start at 0.5 (neutral) for any new framework. Focus selection uses: `expected_improvement = score_gap × elasticity × framework_emphasis`. After optimization completes, write elasticity snapshot to `framework_performance.elasticity_snapshot`.

#### 1.2c Progressive damping — confidence-weighted adaptation

Replace `MIN_FEEDBACKS_FOR_ADAPTATION = 3` with `MIN_FEEDBACKS_FOR_ADAPTATION = 1` plus progressive damping:

```python
# Base damping: logarithmic ramp, capped at MAX_DAMPING
base = min(MAX_DAMPING, BASE_DAMPING * log(1 + n))  # 0.065 base, 0.15 max
# n=1→0.045, n=2→0.071, n=3→0.090, n=5→0.116, n=10→0.15 (cap)

# Consistency: measures rating alignment [0.0 = max disagreement, 1.0 = all same]
consistency = measure_rating_variance(feedbacks, recent_window=5)
# Blend recent (60%) and overall (40%) consistency
blended = 0.6 * recent_consistency + 0.4 * overall_consistency

# Confidence multiplier: scales base damping
# Range [0.5, 1.2] — noisy feedback halves damping, consistent feedback boosts 20%
confidence_multiplier = 0.5 + 0.7 * blended

# Final damping: apply multiplier BEFORE the cap, so consistency matters at all scales
# Note: at high n, base already hits MAX_DAMPING. The multiplier can push it above
# MAX_DAMPING (consistency reward) or pull it well below (noise penalty).
# Cap at MAX_DAMPING * 1.2 = 0.18 to allow a small consistency bonus even at scale.
CONSISTENCY_CEILING = MAX_DAMPING * 1.2  # 0.18
effective_damping = min(CONSISTENCY_CEILING, base * confidence_multiplier)
```

This ensures the consistency multiplier remains relevant at all sample sizes. At `n=10+`, base hits 0.15, but inconsistent feedback still pulls it down to 0.075 (0.15 × 0.5), while consistent feedback allows up to 0.18 (0.15 × 1.2).

#### 1.2d Retry oracle — framework-integrated 7-gate hardening

- **Gate 0 (new, advisory pre-check):** Framework mismatch warning — runs before the existing gate sequence, does NOT change gate numbering. Existing gates 1-7 keep their numbers. Gate 0 is advisory only (emits diagnostic, never blocks). Fires if `user_rating_avg < -0.3` and `sample_count >= 3`.
- **Gate 2 (budget exhaustion):** Fix off-by-one (`>=` not `>`). Add framework-aware budget extension (+1 retry if high elasticity for failing dimensions).
- **Gate 3:** Two-tier cycle detection — hard (hash match) + soft (entropy < 0.10 AND dimension deltas < 0.3).
- **Gate 4:** Framework-adjusted entropy threshold: `base_threshold × framework.entropy_tolerance`.
- **Gate 5:** Per-framework momentum tracking. Framework switch resets momentum.
- **Gate 6:** Integrate with elasticity matrix — regressions in high-elasticity dims allow one more attempt.
- **Gate 7:** Use historical framework performance to compute expected gain ceiling.
- **Cleanup:** Remove dead `task_baseline` parameter. Return gate name as enum from `should_retry()`. Add default weights for missing dimensions in weighted score calculation.

#### 1.2e Prompt diff — structural + semantic awareness

Replace sentence-level Jaccard with multi-signal composite. **Rename to `compute_prompt_divergence()`** to avoid name collision with the existing `compute_prompt_entropy()`. The existing function is replaced entirely — all call sites in `retry_oracle.py` (lines 126, 159, 224) update to the new name and semantics:

```python
def compute_prompt_divergence(prompt_a: str, prompt_b: str) -> float:
    """Returns 0.0 (identical) to 1.0 (completely different)."""
    return 0.5 * token_jaccard + 0.3 * structural_delta + 0.2 * length_delta
```

Where `structural_delta` captures line/paragraph/list/code_block structure changes. Add soft cycle detection via `CycleResult(type="hard"|"soft")`. The `RetryOracle` internal field `_last_entropy` is renamed to `_last_divergence`.

---

### 1.3 Corrected Issues & Session Compaction

#### Predefined issue categories

Two groups (Fidelity + Quality), 8 total:

| Group | Issue ID | Label |
|-------|----------|-------|
| Fidelity | `lost_key_terms` | Lost important terminology or domain language |
| Fidelity | `changed_meaning` | Changed the original intent or meaning |
| Fidelity | `hallucinated_content` | Added claims or details not in the original |
| Fidelity | `lost_examples` | Removed or weakened important examples |
| Quality | `too_verbose` | Unnecessarily long or repetitive |
| Quality | `too_vague` | Lost specificity or important details |
| Quality | `wrong_tone` | Tone doesn't match intended audience |
| Quality | `broken_structure` | Formatting, flow, or organization degraded |

#### Proactive issue suggestion engine

`suggest_likely_issues()` analyzes scores, framework history, and user issue patterns to pre-highlight the 2-3 most likely issues. Three signal sources:

1. Low dimension scores mapped to likely issues
2. Framework-specific issue history from `framework_performance.issue_frequency`
3. User-global issue patterns from `user_adaptation.issue_frequency`

#### Full integration map

1. **Adaptation engine:** `apply_issue_signals()` layers issue frequency as secondary signal on override deltas, mapped to dimension weights via `ISSUE_DIMENSION_MAP`:

```python
ISSUE_DIMENSION_MAP = {
    "lost_key_terms":       {"faithfulness_score": +1.0, "specificity_score": +0.5},
    "changed_meaning":      {"faithfulness_score": +1.0},
    "hallucinated_content": {"faithfulness_score": +0.8, "specificity_score": +0.3},
    "lost_examples":        {"specificity_score": +1.0, "faithfulness_score": +0.3},
    "too_verbose":          {"conciseness_score": +1.0},
    "too_vague":            {"specificity_score": +1.0, "clarity_score": +0.3},
    "wrong_tone":           {"clarity_score": +1.0},
    "broken_structure":     {"structure_score": +1.0},
}
# Values are directional weights (positive = boost that dimension's weight).
# Normalized by total feedback count before application.
```
2. **Framework performance:** `issue_frequency` column tracks per-framework issue counts.
3. **Optimizer:** `build_issue_guardrails()` injects specific, actionable constraints (max 4, ranked by frequency) when issues are reported ≥2 times.
4. **Validator:** `build_issue_verification_prompt()` adds targeted verification passes (term check, intent check, addition check) for recurring issues.
5. **Retry oracle:** Boost `expected_improvement` by 1.5x for dimensions mapped to frequent user issues.

#### Session compaction activation

Wire `session_context.py` into `refinement_service.py`:

- Trigger compaction when session exceeds `MAX_SESSION_CONTEXT_BYTES`
- Compaction prompt preserves: accumulated issues, dimension trajectory, active guardrails, current best version
- Fix: reset `turn_count` after compaction
- Fix: reject compaction output below 15% of original length (fallback to truncation)
- Make context budget provider-aware: `needs_compaction()` accepts a `max_context_bytes` parameter (caller provides it). `refinement_service.py` computes `max_context_bytes = int(provider.context_window * 0.3 * 4)` once per session and passes it to `needs_compaction()`. The default `MAX_SESSION_CONTEXT_BYTES = 256_000` remains as fallback when provider context window is unknown

---

### 1.4 Observability Layer — Layered Disclosure

Four layers of observability, each adding depth only when requested:

| Layer | Visibility | Content |
|-------|-----------|---------|
| L0: Status Pulse | Always visible (inline badge) | Dot color + "Adapted (8 feedbacks)" + top priority |
| L1: Cause → Effect | On feedback submit + optimization complete | Toast with effects / impact card with deltas |
| L2: Adaptation Summary | Inspector panel (opt-in) | Priorities bar, guardrails, framework preferences, issue resolution, threshold |
| L3: Full Diagnostics | Expandable within L2 | Raw weights, elasticity matrix, gate history, computation timestamps |

#### New backend endpoints

- `GET /api/feedback/pulse` — `AdaptationPulse` (L0)
- `GET /api/feedback/summary` — `AdaptationSummary` (L2)
- New SSE events:
  - `adaptation_injected` — emitted after `load_adaptation()` in pipeline, before strategy stage. Payload: `{user_id, threshold, weight_source ("user"|"default"), active_guardrails: [str], framework_affinities: {task_type: {preferred, avoid}}}`
  - `adaptation_impact` — emitted after final validation, before result assembly. Payload: `{improvements: [{dim, prev, curr}], regressions: [{dim, prev, curr}], resolved_issues: [str], has_meaningful_change: bool}`
  - `issue_suggestions` — emitted after validation, alongside scores. Payload: `{suggestions: [{issue_id, reason, confidence}]}`

#### New MCP tool

- `synthesis_get_adaptation_summary` — human-readable L2 summary

#### Backend logging

Structured parameterized logging across all feedback services:

| Service | Events |
|---------|--------|
| `feedback_service` | `feedback_submitted`, `feedback_loaded`, `feedback_aggregate_computed` |
| `adaptation_engine` | `adaptation_recomputed`, `adaptation_skipped_debounce`, `adaptation_below_threshold` |
| `pipeline` | `adaptation_injected`, `framework_performance_updated` |
| `retry_oracle` | `retry_gate_decision`, `elasticity_updated`, `focus_selected` |
| `prompt_diff` | `cycle_detected` |
| `session_context` | `session_compacted`, `compaction_rejected` |

All use `logger.info("event_name", extra={...})` — parameterized, never f-string interpolation.

---

### 1.5 Validation Hardening & Schema Changes

#### Service-layer input validation

Defense-in-depth validation in `feedback_service.py`:

- `validate_dimension_overrides()` — reject unknown dimension keys and values outside 1-10
- `validate_corrected_issues()` — reject unknown issue IDs, deduplicate. Also update the Pydantic `FeedbackCreate` model_validator (currently only validates dimensions) to validate `corrected_issues` against `CORRECTABLE_ISSUES` keys

#### Database schema changes

1. **New table:** `framework_performance` (see 1.1)
2. **New table:** `adaptation_events` — audit trail (id, user_id, event_type, payload JSON, created_at). Index: `ix_adaptation_events_user_created ON adaptation_events(user_id, created_at)`. 90-day retention: purge rows older than 90 days at the start of each `recompute_adaptation()` call (piggyback on existing write path, no separate cron needed).
3. **Alter `user_adaptation`:** add `issue_frequency` (JSON), `adaptation_version` (INTEGER), `damping_level` (FLOAT), `consistency_score` (FLOAT)
4. **Alter `optimizations`:** add `framework` (TEXT), `active_guardrails` (JSON)
5. **New index:** `ix_feedback_optimization_id` on `feedback(optimization_id)`

#### Stats endpoint fix

Replace `limit=1000` Python re-aggregation with `COUNT/GROUP BY` SQL query + pre-computed adaptation state. O(1) regardless of feedback count.

#### Response schema cleanup

- Deprecate `avg_override_delta` and `most_corrected_dimension` — return `null` for one version cycle, then remove in the next release (non-breaking deprecation)
- Add: `FeedbackConfirmation`, `AdaptationPulse`, `AdaptationSummary`, `AdaptationImpactReport`, `ResultAssessment`
- Add `corrected_issues` to `FeedbackCreate` and MCP `SubmitFeedbackInput`

---

## Phase 2: Frontend UX Redesign

### 2.1 Three-Tier Feedback UX

Progressive disclosure — each tier reveals more depth without repeating controls.

#### Tier 1 — Inline Strip (always visible below optimized prompt)

- Two thumbs (up/down), adaptation pulse dot (L0), impact delta flash, "Details" button
- Thumbs-up: auto-submit + confirmation toast (3s, cyan border)
- Thumbs-down: auto-expand Tier 2 inline (spring entrance 300ms)

#### Tier 2 — Feedback Panel (expands inline or in inspector)

- 3-button rating bar (up/neutral/down)
- Issue checkboxes in two groups (Fidelity/Quality) with proactive suggestions pre-highlighted
- 5-column dimension override grid with +/- controls showing current → override values
- Optional comment textarea
- Explicit "SAVE FEEDBACK" button

#### Tier 3 — Adaptation Intelligence (Inspector panel, always accessible)

- Priority bar chart (dimension weights as visual bars, sorted by weight)
- Active guardrails with trigger counts
- Issue resolution tracking (resolved/monitoring status)
- Framework intelligence (per-task-type performance with positive/negative ratios)
- Quality threshold visualization (slider with default reference)
- Expandable L3 Technical Details

#### Tier transition behavior

- Thumbs up → auto-submit + toast, no expansion
- Thumbs down → expand Tier 2 with rating pre-set to -1 (Tier 2 rating bar shows thumbs-down active), pre-suggest issues
- Details button → open Tier 3 in Inspector
- Rating change in Tier 2: up collapses panel, neutral keeps it open
- All tiers share single `feedback` store — no duplicate state

---

### 2.2 Toast Confirmation System

Three variants:

| Variant | Trigger | Duration | Border Color | Content |
|---------|---------|----------|-------------|---------|
| Quick positive | Thumbs up auto-submit | 3s | Cyan | "Feedback saved — reinforcing this style" |
| Detailed | Tier 2 save with issues | 5s | Purple | Summary + up to 2 effects + stage note |
| Error | API failure | Persistent | Red | Error message + auto-retry once, then retry button |

---

### 2.3 Result Assessment Engine

New service: `result_intelligence.py`. Runs post-pipeline, consumes all pipeline output + user history + adaptation state.

#### Output structure: `ResultAssessment`

- **Verdict** (STRONG/SOLID/MIXED/WEAK) — composite of score margin, gate confidence, priority alignment, framework delta
- **Confidence** (HIGH/MEDIUM/LOW) — weighted blend of score vs threshold (35%), gate type (25%), priority alignment (20%), framework history (20%)
- **Headline** — one sentence capturing essence, personalized to user's priorities
- **Dimension insights** — per-dimension: score, priority label, historical context (percentile), delta from previous, framework benchmark, elasticity, status (strong/adequate/weak)
- **Trade-off detection** — gained vs lost dimensions, net impact relative to user weights, whether trade-off is typical for framework
- **Retry journey** — narrative (first_try/improved/struggled/plateau), attempt sparkline, acceptance gate, focus areas
- **Framework fit report** — fit score (excellent/good/fair/poor), historical comparison, recommendation if poor fit
- **Improvement potential** — top 3 dimensions ranked by (user_weight × elasticity), with potential label and explanation
- **Actionable guidance** — max 3 next actions computed from full assessment (thumbs_up/refine/change_framework/adjust_prompt/report_issues)
- **Historical percentile + trend** — rank in user's history, direction over last N optimizations

#### First-time user fallbacks

When historical data is absent, each computation returns explicit empty-state values:

- `historical_percentile` → `PercentileContext(rank=1, total=1, percentile=1.0, label="First optimization")`
- `framework_fit` → `FrameworkFitReport(fit_score="unknown", sample_count=0, recommendation=None)`
- `dimension_insights[].context` → `"First optimization — no baseline yet"`
- `dimension_insights[].delta_from_previous` → `None`
- `trade_offs` → `[]` (requires ≥2 attempts)
- `improvement_potential` → `[]` (requires elasticity data from ≥1 retry)
- `trend` → `TrendAnalysis(direction="insufficient_data", window=0, label="Need 3+ optimizations for trend")`
- `next_actions` → always computed (verdict + confidence are available from the current run alone)

#### Progressive disclosure rendering

| Layer | Visibility | Trigger | Content |
|-------|-----------|---------|---------|
| L0: Verdict Bar | Always visible | — | Score circle, verdict, confidence badge, headline, retry sparkline |
| L1: Dimension Map | Click verdict bar | Chevron ▾ | Per-dimension rows with score, priority tag, delta, context, elasticity bar |
| L2: Journey + Framework | Click dimension row | Row click | Retry bar chart, framework fit report, trade-off pattern |
| Actions | Always visible | — | Guided next steps computed from full assessment |

#### Grid layout specification

- Verdict bar: `flex` — 44px score circle, `flex: 1` info, sparkline, chevron
- Dimension rows: `flex` — 36px score, 1px divider, `flex: 1` content, right-aligned elasticity
- L2 panels: `grid-template-columns: 1fr 1fr`, gap 8px
- Retry journey bars: `grid-template-columns: 1fr 1fr 1fr`, equal width
- Framework intelligence rows: `grid-template-columns: auto 1fr auto auto`
- Action cards: `grid-template-columns: 3fr 2fr`
- Dimension overrides: `grid-template-columns: repeat(5, 1fr)`, gap 4px

---

### 2.4 Accessibility

- Keyboard: Tab order through all tiers, Enter/Space activates, arrow keys within groups
- ARIA: `role="radio"` on thumbs, `role="checkbox"` on issues, `role="meter"` on priority bars, `role="status"` on toasts/impact cards
- Focus: `1px solid rgba(0, 229, 255, 0.3)`, offset 2px
- Reduced motion: all transitions → 0.01ms duration
- Toast: `aria-live="polite"`

### 2.5 Error Recovery

Replace all silent `catch {}` in feedback store with structured error handling. Auto-retry once after 2s at the store level (`feedback.svelte.ts`), NOT in `client.ts` (avoid duplicate retry layers). On second failure, surface error toast with retry button. Errors always propagate to UI.

---

## Appendix A: Audit Findings

| # | Issue | Severity | Status |
|---|-------|----------|--------|
| 1 | Strategy affinities not injected into LLM prompt | HIGH | Fixed in 1.1 |
| 2 | Adaptation state never loaded in frontend | HIGH | Fixed in 2.1 (Tier 3) |
| 3 | No success/error toast on feedback submission | HIGH | Fixed in 2.2 |
| 4 | Concurrency race in adaptation recompute | MEDIUM | Fixed in 1.2a |
| 5 | Silent error swallowing in frontend | MEDIUM | Fixed in 2.5 |
| 6 | `corrected_issues` dead code | MEDIUM | Fixed in 1.3 |
| 7 | `session_context.py` compaction unreachable | MEDIUM | Fixed in 1.3 |
| 8 | Elasticity tracking only records focused dimensions | MEDIUM | Fixed in 1.2b |
| 9 | FeedbackInline auto-submit without confirmation | MEDIUM | Fixed in 2.1 (toast) |
| 10 | `/api/feedback/stats` hardcoded limit=1000 | LOW | Fixed in 1.5 |
| 11 | No feedback logging | LOW | Fixed in 1.4 |
| 12 | `avg_override_delta` hardcoded to None | LOW | Fixed in 1.5 |
| 13 | MCP missing `corrected_issues` parameter | LOW | Fixed in 1.5 |
| 14 | No optimization_id index on feedback table | LOW | Fixed in 1.5 |
| 15 | MIN_FEEDBACKS_FOR_ADAPTATION = 3 undocumented | LOW | Fixed in 1.2c |

## Appendix B: New Files

| File | Purpose |
|------|---------|
| `backend/app/services/result_intelligence.py` | Post-pipeline result assessment engine (depends on adaptation_engine, retry_oracle, pipeline) |
| `backend/app/services/framework_profiles.py` | Static framework validation profiles + trade-off patterns |
| `backend/app/routers/framework.py` | Framework performance + profiles REST endpoints |
| `frontend/src/lib/components/editor/ResultAssessment.svelte` | Progressive disclosure result UI |
| `frontend/src/lib/components/editor/FeedbackTier2.svelte` | Expanded feedback panel (issues + overrides) |
| `frontend/src/lib/components/layout/InspectorAdaptation.svelte` | Redesigned Tier 3 adaptation intelligence |

## Appendix C: Modified Files

| File | Changes |
|------|---------|
| `backend/app/services/adaptation_engine.py` | Debounced recompute, progressive damping, issue signal integration |
| `backend/app/services/retry_oracle.py` | Framework-aware gates, elasticity fix, enum gate names |
| `backend/app/services/prompt_diff.py` | Multi-signal entropy, soft cycle detection |
| `backend/app/services/feedback_service.py` | Validation, logging, corrected_issues activation |
| `backend/app/services/session_context.py` | Wire into refinement, fix turn_count, provider-aware budget |
| `backend/app/services/pipeline.py` | Inject adaptation into strategy/optimizer prompts, emit new SSE events |
| `backend/app/services/strategy_selector.py` | Consume affinities + performance data in LLM prompt |
| `backend/app/services/optimizer.py` | Receive framework profile + user weights as soft hints |
| `backend/app/services/validator.py` | Framework-calibrated scoring, issue verification passes |
| `backend/app/routers/feedback.py` | New endpoints (pulse, summary), corrected_issues in schema |
| `backend/app/mcp_server.py` | corrected_issues parity, new tools |
| `backend/app/schemas/feedback.py` | New response schemas, corrected_issues in FeedbackCreate |
| `backend/app/schemas/mcp_models.py` | corrected_issues in SubmitFeedbackInput, new output models |
| `backend/app/models/feedback.py` | No schema change (column exists) |
| `backend/app/models/optimization.py` | Add framework, active_guardrails columns |
| `frontend/src/lib/stores/feedback.svelte.ts` | Error handling, adaptation loading, pulse state |
| `frontend/src/lib/components/editor/FeedbackInline.svelte` | Tier 1 redesign with pulse + toast integration |
| `frontend/src/lib/components/editor/ForgeArtifact.svelte` | Result assessment integration, adaptation loading |
| `frontend/src/lib/api/client.ts` | New API functions for pulse, summary, framework performance |
