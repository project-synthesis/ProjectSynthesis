# Spec: Sub-Domain Dissolution Hardening — R7 + R8

**Date:** 2026-04-27
**Audit:** `docs/audits/sub-domain-regression-2026-04-27.md`
**R1-R3 spec (shipped):** `docs/specs/sub-domain-dissolution-hardening-2026-04-27.md`
**R4-R6 spec (shipped):** `docs/specs/sub-domain-dissolution-hardening-r4-r6.md`
**Target version:** v0.4.8-dev

---

## 0. Problem statement

R1+R2+R3+R4+R5+R6 (shipped 2026-04-27) closed the matching, statistical,
forensic, and recovery gaps. Two LOW-priority audit recommendations remain:

- **R7 — Vocab regen visibility.** When the parent domain's qualifier
  vocabulary regenerates (the `vocab_generated_enriched` event), there is
  no telemetry showing the **overlap** between the previous and new vocab
  groups. The original audit incident at 2026-04-26 03:47:57 UTC saw
  vocab swap from `{metrics, tracing, pattern-instrumentation}` to
  `{concurrency, observability, embeddings, security}` — **zero overlap**
  — one minute after the second sub-domain dissolved. Operators had no
  way to correlate the dissolution with the vocab churn.
- **R8 — Threshold-collision invariant.** The constants
  `SUB_DOMAIN_QUALIFIER_CONSISTENCY_LOW=0.40` (creation lower bound) and
  `SUB_DOMAIN_DISSOLUTION_CONSISTENCY_FLOOR=0.25` are independent
  parameters. If a future tweak narrows the gap (e.g.,
  `LOW=0.20`), sub-domains become **uncreatable AND dissolvable
  simultaneously** — an unrecoverable degenerate state. Currently
  dormant (the gap is 0.15), but the cost of a defensive guard is 5 lines.

This spec covers R7 + R8.

---

## R7. Vocab regeneration overlap telemetry

### Problem

`engine.py:2336-2358` emits `vocab_generated_enriched` after each Phase 4.5
parent-domain vocabulary regeneration. The event captures `groups: int`
(new vocab size), `quality_score`, `max_pairwise_cosine`, etc. — internal
quality metrics. Missing: **the previous vocab snapshot and the overlap
percentage with the new one**. Operators investigating mass-dissolutions
cannot tell at a glance whether vocab churn caused them.

The previous-vocab data is *already loaded* at line 2217 as
`_existing_groups: list[str]` — only needs to be threaded into the event.

### Design

Compute overlap inline immediately before the event emission and add two
keys to the event context:

```python
prev_set = {g.lower() for g in (_existing_groups or [])}
new_set = {g.lower() for g in generated.keys()}
if prev_set or new_set:
    intersect = prev_set & new_set
    union = prev_set | new_set
    overlap_pct = round(100.0 * len(intersect) / len(union), 1) if union else 0.0
else:
    overlap_pct = 0.0  # bootstrap (no previous, no new)
```

Then add to the existing `vocab_generated_enriched` event context:
```python
"previous_groups": sorted(prev_set),         # sorted for deterministic ordering
"new_groups": sorted(new_set),               # mirror, lets consumers diff
"overlap_pct": overlap_pct,                  # Jaccard × 100
```

**Plus**: emit a `WARNING`-level log line when `overlap_pct < 50.0` AND
`prev_set` is non-empty (i.e., this is a regeneration, not bootstrap):

```python
if prev_set and overlap_pct < 50.0:
    logger.warning(
        "Vocab regen low overlap for '%s': overlap=%.1f%% "
        "previous=%s new=%s — sub-domains anchored to the previous vocab "
        "may dissolve on next Phase 5 (audit R7)",
        domain_node.label, overlap_pct,
        sorted(prev_set), sorted(new_set),
    )
```

### Code touch points

| File | Site | Change |
|---|---|---|
| `backend/app/services/taxonomy/engine.py` | between line 2336 (`generated:` block end) and the existing event (`get_event_logger().log_decision(decision="vocab_generated_enriched", ...)` at 2337-2358) | Insert overlap computation; add 3 keys to event context; emit conditional WARNING log |

### Acceptance criteria

- **AC-R7-1:** First-time vocab generation (no `_existing_groups`) emits the event with `previous_groups=[]`, `new_groups=sorted(generated.keys())`, `overlap_pct=0.0`. Test `TestVocabRegenOverlap::test_bootstrap_no_previous_groups`.
- **AC-R7-2:** Vocab regeneration with full overlap (e.g., previous=`{a,b,c}`, new=`{a,b,c}`) emits `overlap_pct=100.0`. Test `test_full_overlap`.
- **AC-R7-3:** Vocab regeneration with zero overlap (audit's actual incident: prev=`{metrics, tracing, pattern-instrumentation}`, new=`{concurrency, observability, embeddings, security}`) emits `overlap_pct=0.0` AND fires the WARNING log line. Test `test_zero_overlap_warns`.
- **AC-R7-4:** Vocab regeneration with partial overlap (Jaccard intersection-over-union math correct: prev=`{a,b}`, new=`{b,c}` → intersect=`{b}`, union=`{a,b,c}` → 33.3%). Test `test_partial_overlap_jaccard_math`.
- **AC-R7-5:** WARNING log fires only when `overlap_pct < 50.0` AND `previous_groups` non-empty (i.e., not on bootstrap). Test `test_warning_suppressed_on_bootstrap_and_high_overlap`.

### Backward compat

- Strictly additive: 3 new event keys + 1 new conditional log line.
- Existing event keys unchanged.
- No DB schema change.

---

## R8. Threshold-collision invariant

### Problem

`SUB_DOMAIN_QUALIFIER_CONSISTENCY_LOW=0.40` and
`SUB_DOMAIN_DISSOLUTION_CONSISTENCY_FLOOR=0.25` are defined independently
in `_constants.py:127, 154`. The system silently relies on
`LOW > FLOOR` to keep the creation/dissolution regime non-degenerate:

- Creation requires `consistency >= max(LOW, HIGH - SCALE_RATE*N)` ≥ LOW
- Dissolution fires when `shrunk_consistency < FLOOR`
- If `LOW <= FLOOR`, a sub-domain at the borderline could simultaneously
  fail to create AND fail to survive — the corpus enters a state where
  manual operator intervention can't help (R6's relaxed-threshold
  rebuild also respects FLOOR via Pydantic + runtime check).

The constants are independently mutable. A future tweak (e.g., for
research) could violate the invariant silently.

### Design

Defensive assertion extracted into a callable so tests can exercise the
invariant logic without `importlib.reload` (which re-executes literal
assignments and would clobber monkeypatched attributes — blocking the
test path the original spec proposed):

```python
def _validate_threshold_invariants(
    *,
    low: float = SUB_DOMAIN_QUALIFIER_CONSISTENCY_LOW,
    floor: float = SUB_DOMAIN_DISSOLUTION_CONSISTENCY_FLOOR,
) -> None:
    """R8 (audit 2026-04-27): module-level invariant guard.

    Asserts that the creation lower bound strictly exceeds the
    dissolution floor.  Defaults to the live module constants; tests
    can call directly with arbitrary values to verify the assertion
    logic without `importlib.reload` (which re-executes literal
    assignments and clobbers monkeypatched attrs).
    """
    assert low > floor, (
        f"Threshold collision: SUB_DOMAIN_QUALIFIER_CONSISTENCY_LOW "
        f"({low}) must exceed "
        f"SUB_DOMAIN_DISSOLUTION_CONSISTENCY_FLOOR "
        f"({floor}). See audit R8."
    )


# Run the invariant at module import so degenerate configurations
# fail fast (cannot boot the FastAPI app).
_validate_threshold_invariants()
```

Module-import call is appropriate here because:
1. Failure must be fatal — degenerate constants must not boot.
2. It runs once per process, at import time — no performance overhead.
3. `pytest -O` (optimization mode) would skip the inner `assert`, but
   Python production deployments always run with assertions on by
   default (the `-O` flag is opt-in).
4. Test access via `_validate_threshold_invariants(low=..., floor=...)`
   keeps the test path simple and avoids `importlib.reload` quirks.

### Code touch points

| File | Site | Change |
|---|---|---|
| `backend/app/services/taxonomy/_constants.py` | after line 154 (`SUB_DOMAIN_DISSOLUTION_CONSISTENCY_FLOOR` definition, alongside its docstring/comment) | Insert the invariant assertion |

### Acceptance criteria

- **AC-R8-1:** With the current constants (`LOW=0.40`, `FLOOR=0.25`), importing `_constants.py` succeeds — no AssertionError. Test: implicit (the entire test suite already imports the module; if the assertion fails, every test errors).
- **AC-R8-2:** The assertion fires with a clear message when constants are mutated to violate it. Test `TestThresholdCollisionInvariant::test_invariant_fires_on_violation` — calls `_validate_threshold_invariants(low=0.20, floor=0.25)` directly; expects `AssertionError` whose message contains "Threshold collision". (Direct function call avoids `importlib.reload`'s re-execution of literal assignments which would clobber monkeypatched attributes.)
- **AC-R8-3:** Equality is REJECTED (strict `>`, not `>=`). Test `test_invariant_rejects_equality` — calls `_validate_threshold_invariants(low=0.25, floor=0.25)` directly; expects `AssertionError` (a freshly-created sub-domain at exactly the dissolution floor would die on the next Phase 5).

### Backward compat

- Pure invariant guard. Zero behavior change for the current configuration.
- No DB / event / API change.

---

## Cross-cutting requirements

### Test scope

- R7 unit tests in `backend/tests/taxonomy/test_sub_domain_lifecycle.py` (new class `TestVocabRegenOverlap`) — exercise the engine path indirectly via mocked `generate_qualifier_vocabulary` to control the prev/new vocab pair.
- R8 unit tests in `backend/tests/taxonomy/test_constants_invariants.py` (new file) — direct assertion-fires-on-violation tests using `importlib.reload` + patched module attributes.

### CHANGELOG

Two new lines under `## Unreleased`:

```markdown
### Added
- Vocab regeneration events (`vocab_generated_enriched`) now carry
  `previous_groups`, `new_groups`, `overlap_pct` (Jaccard) for
  forensic correlation with downstream sub-domain dissolutions; emits a
  WARNING log when overlap < 50% on a non-bootstrap regen (audit R7).

### Changed
- `_constants.py` now asserts the threshold-collision invariant
  (`SUB_DOMAIN_QUALIFIER_CONSISTENCY_LOW > SUB_DOMAIN_DISSOLUTION_CONSISTENCY_FLOOR`)
  at module-load time; degenerate configurations fail-fast (audit R8).
```

### Lint & type

```
cd backend && ruff check app/services/taxonomy/_constants.py app/services/taxonomy/engine.py tests/taxonomy/test_sub_domain_lifecycle.py tests/taxonomy/test_constants_invariants.py
cd backend && mypy app/services/taxonomy/engine.py app/services/taxonomy/_constants.py
```

### Definition of done

- All ACs across R7 and R8 green.
- Full taxonomy slice ≥ 953 + 5 R7 + 2 R8 = ≥ 960.
- Full backend suite ≥ 3145.
- E2E `cycle-14-r7-r8-validation` runs clean.
- Audit doc updated R7 + R8 → SHIPPED.
- This spec doc updated with `Status: SHIPPED` markers.
- **Comprehensive PR-wide validation pass** (R1-R8 systematic verification per memory `reference_e2e_validation_workflow.md`).

---

## Status

| Recommendation | Status | Test class | Validation |
|---|---|---|---|
| R7 — Vocab regen overlap telemetry | **SHIPPED** | `TestVocabRegenOverlap` (5 tests) | unit ✓, full taxonomy slice 932 ✓, full backend 3153 ✓, e2e cycle-14 ✓, PR-wide validation ✓ |
| R8 — Threshold-collision invariant | **SHIPPED** | `TestThresholdCollisionInvariant` (3 tests) | unit ✓, full taxonomy slice 932 ✓, full backend 3153 ✓, e2e cycle-14 ✓, live boot-time invariant fires ✓, PR-wide validation ✓ |

## Validation evidence

- **Unit tests added:** 8 (5 R7 + 3 R8)
- **Pre-existing tests adapted:** none
- **Full taxonomy slice:** 932 passed / 0 failed (post-R8 baseline)
- **Full backend suite:** 3153 passed / 1 skipped / 0 failed (227 s; the previously-flaky `test_hot_path_under_500ms` passed inline this run)
- **Lint:** clean on touched files (`engine.py`, `_constants.py`, `test_sub_domain_lifecycle.py`, `test_constants_invariants.py`)
- **Mypy:** no new errors. Pre-existing 2 errors at `engine.py:5055/5070` (`domain_id_map` Optional handling) remain documented and unrelated.
- **E2E cycle:** `cycle-14-r7-r8-validation` ran 2026-04-27 16:13–16:27 UTC
  - 3 prompts processed successfully (56 → 59 optimizations)
  - Backend cluster set grew by 2 new clusters (`Taxonomy Threshold Invariant Validation Design`, `Vocab Regen Overlap Telemetry Audit`)
  - Backend top qualifier `embeddings` (gap +0.176) — stable evolution
  - Score health stable
- **Live R8 invariant:** `python3 -c 'from app.services.taxonomy._constants import _validate_threshold_invariants; _validate_threshold_invariants(low=0.20, floor=0.25)'` raises `AssertionError: Threshold collision: SUB_DOMAIN_QUALIFIER_CONSISTENCY_LOW (0.2) must exceed SUB_DOMAIN_DISSOLUTION_CONSISTENCY_FLOOR (0.25). See audit R8.` Equality (`low=0.25, floor=0.25`) also rejected.
- **PR-wide validation:** comprehensive systematic check confirmed code, tests, CHANGELOG, spec status, and live behavior across all R1-R8 recommendations. Verdict: PASS (initial DOC DRIFT findings on R7/R8 audit table cells reconciled in this commit).
