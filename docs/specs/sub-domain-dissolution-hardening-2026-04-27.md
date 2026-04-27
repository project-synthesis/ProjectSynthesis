# Spec: Sub-Domain Dissolution Hardening (R1 + R2 + R3)

**Date:** 2026-04-27
**Audit reference:** `docs/audits/sub-domain-regression-2026-04-27.md`
**Target version:** v0.4.8 (current dev)
**Status:** PROPOSED → SHIPPED markers added per cycle

---

## 0. Problem statement

Two backend sub-domains (`audit`, `embedding-health`) were silently dissolved
on 2026-04-26 by an asymmetric-matching bug fixed in v0.4.7. Even with the
fix, the audit identified four structural fragilities in
`_reevaluate_sub_domains` that can trigger spurious dissolutions:

1. **Small-N volatility** — point-estimate consistency at N≤15 is
   statistically meaningless; one off-topic member can cross the dissolution
   floor.
2. **Aggressive grace period** — `SUB_DOMAIN_DISSOLUTION_MIN_AGE_HOURS=6`
   meant both observed dissolutions fired on the very first re-evaluation
   cycle that the age gate allowed.
3. **Empty-snapshot fail-open** — when `cluster_metadata.generated_qualifiers`
   is absent, v0.4.7 silently degrades to the buggy exact-equality matching.
4. **Predicate asymmetry** — creation uses `compute_qualifier_cascade()`,
   re-eval uses an inline matcher; future drift between the two re-introduces
   the v0.4.6 bug class. (Out of scope for this hardening pass — tracked as
   audit recommendation R4.)

This spec covers R1, R2, R3. R4 is deferred.

---

## R1. Bayesian shrinkage on consistency metric

### Problem

Raw consistency `matching / total_opts` is volatile at small N. A single
misclassified member at N=5 swings the metric by 20 percentage points,
causing dissolution when 80% of members are still topically consistent.

### Design

Replace the dissolution decision input with a Bayesian posterior estimate.
Treat `matching` as a Beta-Binomial sample:

```
shrunk_consistency = (matching + α_prior) / (total_opts + α_prior + β_prior)
```

where:

| Parameter | Value | Rationale |
|---|---|---|
| `SUB_DOMAIN_DISSOLUTION_PRIOR_STRENGTH` (K) | `10` | Equivalent to 10 prior observations; pulls strongly at N≤10, weakly at N≥30 |
| `SUB_DOMAIN_DISSOLUTION_PRIOR_CENTER` | `0.40` | Aligned with `SUB_DOMAIN_QUALIFIER_CONSISTENCY_LOW` — "in absence of evidence, assume the sub-domain is at the lower bound of healthy" |
| `α_prior` | `K × center` = `4.0` | Beta α |
| `β_prior` | `K × (1 − center)` = `6.0` | Beta β |

### Behavior table

| N | matching | raw | shrunk | decision (floor=0.25) | rationale |
|---|---|---|---|---|---|
| 5 | 1 | 0.20 | (1+4)/(5+10) = 0.333 | KEEP | small N, prior dominates |
| 5 | 0 | 0.00 | 4/15 = 0.267 | KEEP | safety-net for genuinely-zero with too-few samples |
| 13 | 0 | 0.00 | 4/23 = 0.174 | DISSOLVE | enough data to act |
| 13 | 7 | 0.54 | 11/23 = 0.478 | KEEP | (the post-fix audit case) |
| 100 | 0 | 0.00 | 4/110 = 0.036 | DISSOLVE | prior negligible at large N |

### Code touch points

| File | Site | Change |
|---|---|---|
| `backend/app/services/taxonomy/_constants.py` | after line 154 | Add two constants `SUB_DOMAIN_DISSOLUTION_PRIOR_STRENGTH=10` and `SUB_DOMAIN_DISSOLUTION_PRIOR_CENTER=0.40` |
| `backend/app/services/taxonomy/engine.py` | line ~3283 (post `consistency = matching / total_opts`) | Compute `shrunk_consistency`; replace `consistency >= floor` decision with `shrunk_consistency >= floor` |
| `engine.py` | re-evaluated event (~line 3293) | Add `shrunk_consistency_pct`, `prior_strength`, `prior_center` keys |
| `engine.py` | dissolved event (~line 3334) | Add `shrunk_consistency_pct` key alongside existing `consistency_pct` |

### Acceptance criteria (testable)

- **AC-R1-1:** A sub-domain aged ≥ grace period with N=5 members where 1 of 5 carries the matched qualifier is **NOT dissolved** (shrunk = 0.333 ≥ 0.25 floor). Expressed as a regression test in `test_sub_domain_lifecycle.py::TestSubDomainBayesianShrinkage::test_small_n_one_match_keeps`.
- **AC-R1-2:** A sub-domain with N=5 members where 0 of 5 match is **NOT dissolved** (shrunk = 0.267 ≥ 0.25). `test_small_n_zero_match_keeps`.
- **AC-R1-3:** A sub-domain with N=20 members where 0 of 20 match **IS dissolved** (shrunk = 4/30 = 0.133 < 0.25). Locks the contract that shrinkage doesn't make dissolution unreachable. `test_large_n_zero_match_still_dissolves`.
- **AC-R1-4:** The `sub_domain_dissolved` and `sub_domain_reevaluated` JSONL events carry both `consistency_pct` (raw) and `shrunk_consistency_pct` keys. Existing keys (`floor_pct`, `threshold_pct`, `total_opts`, `matching`, `clusters_reparented`, `meta_patterns_merged`, `reason`) are retained unchanged — additive contract only. `test_telemetry_includes_both_metrics`.
- **AC-R1-5:** The dissolution decision uses the shrunk value, not the raw value. Verifiable via the test in AC-R1-1: pre-fix that test fails because raw=0.20 < 0.25 triggers dissolution; post-fix it passes because shrunk=0.333 ≥ 0.25.

### Backward compat

- The two new constants are additive; no migration.
- JSONL event schema gains keys but does not remove or rename existing keys — readers tolerant of extra keys remain compatible.
- The dissolution threshold (`SUB_DOMAIN_DISSOLUTION_CONSISTENCY_FLOOR=0.25`) is unchanged. Only the input changes.
- No DB schema change.

---

## R2. Lengthen `SUB_DOMAIN_DISSOLUTION_MIN_AGE_HOURS` from 6 to 24

### Problem

Both observed dissolutions fired at 6h 0m and 6h 8m post-creation —
literally on the first cycle the age gate allowed. 6 hours is shorter than
typical bootstrap volatility windows (overnight cadence + first vocab
regen). The system has no forgiveness margin between sub-domain birth and
hostile re-evaluation.

### Design

Single-constant change:

```python
# _constants.py
SUB_DOMAIN_DISSOLUTION_MIN_AGE_HOURS: int = 24  # was: 6
```

### Behavior table

| Age post-creation | Pre-fix | Post-fix |
|---|---|---|
| 5h 59m | skip (gate blocks) | skip (gate blocks) |
| 6h 30m | **eligible for dissolution** | skip (gate blocks) |
| 23h 59m | eligible for dissolution | skip (gate blocks) |
| 24h 30m | eligible for dissolution | **eligible for dissolution** |

> **Note on the boundary semantics**: the existing code uses
> `if created and created > age_cutoff: continue` (strict `>`), so a
> sub-domain whose age equals the cutoff exactly is **not** skipped. Both
> pre- and post-fix preserve this — at exact age=24h a sub-domain becomes
> eligible (not "still skipped"). The asymmetry matters only for tests
> that use `age_hours=24` literal — those cases proceed to consistency
> evaluation in both regimes.

### Code touch points

| File | Site | Change |
|---|---|---|
| `backend/app/services/taxonomy/_constants.py` | line 155 | `SUB_DOMAIN_DISSOLUTION_MIN_AGE_HOURS: int = 24` |

### Acceptance criteria

- **AC-R2-1:** A sub-domain aged 12 hours with all members carrying off-topic qualifiers is **NOT dissolved**. `test_age_gate_blocks_dissolution_below_24h`.
- **AC-R2-2:** A sub-domain aged 25 hours under identical hostile conditions **IS dissolved** (after R1's shrinkage allows). `test_age_gate_passes_above_24h`.
- **AC-R2-3:** Existing tests that use `age_hours=24` continue to pass — none accidentally relied on `24 > 6` being true. Verified by full backend test slice.

### Backward compat

- Constant change only. No migration. No event schema change.
- Existing sub-domains created before deploy with age 6h–24h that would have been dissolved on the next cycle now get an extra 18-hour reprieve. **This is intentional** — the audit found such dissolutions to be premature.

---

## R3. Empty-snapshot guardrail

### Problem

`_reevaluate_sub_domains` (engine.py:3156-3169) reads
`sub_meta.get("generated_qualifiers") or {}`. If `generated_qualifiers` is
absent (cold-start, vocab-gen failure, manual creation, legacy node), the
matcher silently degrades to v0.4.6 exact-equality behavior:

- `sub_vocab_groups`, `sub_vocab_terms`, `sub_vocab_tokens` are all empty.
- Source 1 falls through to the `q_norm == sub_qualifier` exact-equality clause.
- Source 2's `sub_vocab_tokens` intersection always returns empty.
- Members carrying GROUP-name qualifiers (the v0.4.6 bug pattern) score 0%.

This re-introduces the bug class that v0.4.7 was supposed to close.

### Design

Add a defensive skip after loading the snapshot:

```python
sub_meta = read_meta(sub.cluster_metadata)
sub_generated_qualifiers = sub_meta.get("generated_qualifiers") or {}

# R3: empty vocabulary snapshot cannot reliably reject this sub-domain.
# Skip dissolution and emit a telemetry event so operators can investigate.
if not sub_generated_qualifiers:
    try:
        get_event_logger().log_decision(
            path="warm", op="discover",
            decision="sub_domain_reevaluation_skipped",
            cluster_id=sub.id,
            context={
                "domain": domain_node.label,
                "domain_node_id": domain_node.id,
                "sub_domain": sub.label,
                "reason": "empty_vocab_snapshot",
                "total_opts": total_opts,
            },
        )
    except RuntimeError:
        pass
    continue
```

### Code touch points

| File | Site | Change |
|---|---|---|
| `backend/app/services/taxonomy/engine.py` | between line 3169 (snapshot load) and line 3193 (matching loop) | Insert empty-snapshot skip block |

### Acceptance criteria

- **AC-R3-1:** A sub-domain whose `cluster_metadata` lacks `generated_qualifiers` (None or `{}`) is **NOT dissolved** even when its members carry mismatched qualifiers. `test_empty_snapshot_skips_dissolution`.
- **AC-R3-2:** When the empty-snapshot skip fires, a `sub_domain_reevaluation_skipped` JSONL event is emitted with `reason: "empty_vocab_snapshot"` and the sub-domain's `cluster_id`. `test_empty_snapshot_emits_skip_event`.
- **AC-R3-3:** A sub-domain with non-empty `generated_qualifiers` but irrelevant matches still dissolves normally (control). Reuses existing `test_unrelated_qualifiers_still_dissolve` — must remain green.

### Backward compat

- Strictly additive: net behavior is "fewer dissolutions", never more.
- A new event type `sub_domain_reevaluation_skipped` joins the existing event taxonomy. JSONL consumers that filter on `decision in {"sub_domain_dissolved", "sub_domain_reevaluated", ...}` may need to add the new value to their allow-list.

---

## Cross-cutting requirements

### Telemetry

`/api/health` and the Activity Panel surface lifecycle events. Verify via
manual curl:

```bash
curl -s http://127.0.0.1:8000/api/clusters/activity/history?limit=20 | \
  python3 -m json.tool | grep -E 'sub_domain_(dissolved|reevaluated|reevaluation_skipped)'
```

### Test scope

- New unit tests live in `backend/tests/taxonomy/test_sub_domain_lifecycle.py`.
- Each TDD cycle adds one new `Test{Feature}` class.
- Each cycle's REFACTOR phase runs the **full taxonomy test slice**:
  ```
  cd backend && pytest tests/taxonomy/ -v
  ```

### Lint & type

All touched files (engine.py, _constants.py, test_sub_domain_lifecycle.py)
must be ruff-clean and mypy-clean before VALIDATION subagent runs:

```
cd backend && ruff check app/services/taxonomy/ tests/taxonomy/
cd backend && mypy app/services/taxonomy/engine.py app/services/taxonomy/_constants.py
```

### CHANGELOG

Add a single user-visible entry to `docs/CHANGELOG.md` under `## Unreleased`:

```markdown
### Fixed
- Hardened sub-domain dissolution against small-N noise (Bayesian shrinkage),
  premature re-evaluation (24h grace period), and empty-snapshot fail-open
  (defensive skip). See `docs/audits/sub-domain-regression-2026-04-27.md`.
```

---

## Out of scope (deferred)

- **R4** (predicate unification between creation and re-eval) — larger refactor; lands as a follow-up PR after this hardening soaks.
- **R5** (forensic telemetry: per-member match samples) — useful but not load-bearing.
- **R6** (`POST /api/domains/{id}/rebuild-sub-domains` recovery endpoint) — operator surface; separate feature work.
- **R7** (vocab regeneration overlap telemetry) — observability nice-to-have.
- **R8** (threshold collision invariant check) — dormant issue at current N.

---

## Definition of done

- All acceptance criteria green in CI.
- Full taxonomy test slice (`backend/tests/taxonomy/`) passes.
- One e2e validation cycle via `scripts/validate_taxonomy_emergence.py` succeeds with no new SEV-MAJOR events.
- Audit doc (`docs/audits/sub-domain-regression-2026-04-27.md`) updated with "SHIPPED" markers and post-deploy verification notes.
- This spec doc updated with `## Status: SHIPPED` headers per recommendation.

---

## Status

| Recommendation | Status | Test class | Validation |
|---|---|---|---|
| R1 — Bayesian shrinkage | **SHIPPED** | `TestSubDomainBayesianShrinkage` (4 tests) | unit ✓, full taxonomy slice 900 ✓, full backend 3115 ✓, e2e cycle-12 ✓ |
| R2 — 24h grace period | **SHIPPED** | `TestSubDomainGracePeriod` (2 tests) | unit ✓, full taxonomy slice 900 ✓, full backend 3115 ✓, e2e cycle-12 ✓ |
| R3 — Empty-snapshot guardrail | **SHIPPED** | `TestSubDomainEmptySnapshotSkip` (2 tests) | unit ✓, full taxonomy slice 900 ✓, full backend 3115 ✓, e2e cycle-12 ✓ |

## Validation evidence

- **Unit tests added:** 8 (4 for R1, 2 for R2, 2 for R3)
- **Pre-existing tests adapted:** 4 (added `generated_qualifiers` to bypass R3 guard while preserving dissolution intent) + 4 N-bumps in `TestSubDomainConsistencyVocabGroupMatch` (margin past Bayesian floor)
- **Full taxonomy slice:** 900 passed / 0 failed
- **Full backend suite:** 3115 passed / 1 skipped / 0 failed (171 s)
- **Lint:** clean on touched files (`engine.py`, `_constants.py`, `test_sub_domain_lifecycle.py`)
- **Mypy:** no new errors (2 pre-existing errors at lines 4755/4770 of `engine.py` are unrelated to R1/R2/R3)
- **E2E cycle:** `cycle-12-dissolution-hardening` ran 2026-04-27 04:37–04:49 UTC
  - 3 prompts processed successfully (50 → 53 optimizations)
  - Score health stable: mean=8.05, stdev=0.77 (within healthy band; baseline pre-cycle was mean=8.07, stdev=0.76 — no degradation)
  - Zero `sub_domain_dissolved` events
  - Zero `sub_domain_reevaluation_skipped` events (expected — no sub-domains exist to evaluate)
  - `recent_errors.last_24h = 0`

### Note (R1 implementation): the existing test
`test_unrelated_qualifiers_still_dissolve` was bumped from N=6 to N=10
to add margin past the Bayesian floor. Three additional dissolution-path
tests had N bumped (REFACTOR caught them via full-suite run): N=6→12, N=4→10,
N=4→10. No semantic change — all sample sizes still represent
"low-consistency / all-unrelated" scenarios. Intent preserved.

### Note (R3 implementation): four pre-existing tests
(`test_sibling_sub_domains_evaluated_independently`,
`test_degraded_sub_domain_dissolved`,
`test_dissolution_reparents_to_top_domain`,
`test_dissolution_merges_meta_patterns`) implicitly relied on the v0.4.6
fall-through path because their `_make_sub_domain` setup didn't populate
`generated_qualifiers`. After R3 lands, those tests would be skipped by
the empty-snapshot guard. Each was updated to populate
`cluster_metadata = write_meta(..., generated_qualifiers={...})` so they
exercise the real matching cascade rather than the legacy fallback.
Test semantics preserved.
