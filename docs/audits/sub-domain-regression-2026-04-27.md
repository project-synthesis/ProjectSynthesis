# Sub-Domain Regression Audit — Backend Domain

**Date:** 2026-04-27
**Severity:** SEV-MAJOR (silent data loss in taxonomy structure)
**Status:** Root cause fixed in v0.4.7. Hardening defenses **SHIPPED** in v0.4.8-dev (R1+R2+R3, 2026-04-27).
**Affected versions:** ≤ v0.4.6 (matching fix landed in v0.4.7 commit `226857e5`, 2026-04-26 17:22:53 UTC; small-N + grace + empty-snapshot defenses landed in v0.4.8-dev, 2026-04-27).

---

## Resolution status (2026-04-27)

| Recommendation | Status | Test class | What shipped |
|---|---|---|---|
| R1 — Bayesian shrinkage | **SHIPPED** | `TestSubDomainBayesianShrinkage` (4 tests) | `SUB_DOMAIN_DISSOLUTION_PRIOR_STRENGTH=10`, `SUB_DOMAIN_DISSOLUTION_PRIOR_CENTER=0.40` (Beta(α=4, β=6) prior) |
| R2 — 24h grace period | **SHIPPED** | `TestSubDomainGracePeriod` (2 tests) | `SUB_DOMAIN_DISSOLUTION_MIN_AGE_HOURS=24` (was 6) |
| R3 — Empty-snapshot guardrail | **SHIPPED** | `TestSubDomainEmptySnapshotSkip` (2 tests) | New event decision `sub_domain_reevaluation_skipped` with `reason=empty_vocab_snapshot` |
| R4 — Predicate unification | **SHIPPED** | `TestMatchOptToSubDomainVocab` (7 unit) + `TestSubDomainReevalUsesSharedPrimitive` (1 integration) | Extracted `match_opt_to_sub_domain_vocab` + `SubDomainMatchResult` to `sub_domain_readiness.py`; engine.py:_reevaluate_sub_domains rewired to consume the primitive |
| R5 — Forensic telemetry | **SHIPPED** | `TestSubDomainForensicTelemetry` (5 tests) | `SUB_DOMAIN_FAILURE_SAMPLES=3`, `SUB_DOMAIN_FAILURE_FIELD_TRUNCATE=80`; `matching_members` + `sample_match_failures` keys on both `sub_domain_reevaluated` and `sub_domain_dissolved` events |
| R6 — Recovery endpoint | **SHIPPED** | `TestRebuildSubDomainsService` (11) + `TestRebuildSubDomainsEndpoint` (6) | `POST /api/domains/{id}/rebuild-sub-domains` (10/min); `RebuildSubDomainsRequest` (Pydantic ge=0.25) + `RebuildSubDomainsResult`; `engine.rebuild_sub_domains()` with savepoint rollback semantics; `sub_domain_rebuild_invoked` telemetry |
| R7 — Vocab regen overlap telemetry | **SHIPPED** | `TestVocabRegenOverlap` (5 tests) | `vocab_generated_enriched` event gains `previous_groups`, `new_groups`, `overlap_pct` (Jaccard); WARNING log fires when `overlap_pct < 50%` on a non-bootstrap regen |
| R8 — Threshold collision invariant | **SHIPPED** | `TestThresholdCollisionInvariant` (3 tests) | `_validate_threshold_invariants()` callable in `_constants.py` invoked at module-import time; fails fast if `SUB_DOMAIN_QUALIFIER_CONSISTENCY_LOW <= SUB_DOMAIN_DISSOLUTION_CONSISTENCY_FLOOR` |

**Validation evidence (R1+R2+R3, 2026-04-27 04:49 UTC):** Full backend suite 3115 passed / 1 skipped / 0 failed. E2E cycle `cycle-12-dissolution-hardening` ran clean with 3 prompts processed, no spurious dissolution events, score health stable (mean=8.05, stdev=0.77).

**Validation evidence (R4+R5+R6, 2026-04-27 06:53 UTC):** Full backend suite 3145 passed / 1 skipped (1 flake on `test_hot_path_under_500ms` re-passed solo, unrelated to changes). Full taxonomy + router slice 953 passed. E2E cycle `cycle-13-r4-r6-validation` ran clean with 3 prompts processed (53 → 56 opts), no spurious dissolution events, score health stable (mean=8.04, stdev=0.76). Live `sub_domain_rebuild_invoked` event observed at 06:38:23 (operator dry-run smoke test) with correct payload shape.

**Validation evidence (R7+R8, 2026-04-27 16:13 UTC):** Full backend suite 3153 passed / 1 skipped / 0 failed. Full taxonomy + router slice 961 passed. R7 unit tests 5/5 (`TestVocabRegenOverlap`) + R8 unit tests 3/3 (`TestThresholdCollisionInvariant`) all green. E2E cycle `cycle-14-r7-r8-validation` ran clean. Comprehensive PR-wide validation pass (R1-R8 systematic verification per `reference_e2e_validation_workflow.md`) confirmed code, tests, CHANGELOG, spec status, and live telemetry shape across all 8 recommendations. Live R8 invariant fires correctly via `_validate_threshold_invariants(low=0.20, floor=0.25)` direct-call test.

**Specs:**
- R1-R3: `docs/specs/sub-domain-dissolution-hardening-2026-04-27.md` + `…-plan.md`
- R4-R6: `docs/specs/sub-domain-dissolution-hardening-r4-r6.md` + `…-r4-r6-plan.md`
- R7-R8: `docs/specs/sub-domain-dissolution-hardening-r7-r8.md` + `…-r7-r8-plan.md`

**PR closure note:** All 8 audit recommendations (R1 through R8) are now SHIPPED. The audit's complete remediation surface is closed. No deferred items remain.

> **Note on R1's prior parameters:** the original recommendation in this audit (below) proposed `ALPHA_PRIOR_DISSOLUTION=4` pulling toward the dissolution floor (0.25). During implementation that parameterization was found insufficient — pulling toward 0.25 doesn't add safety because the floor IS 0.25. What shipped instead is K=10 strength centered at the **lower-bound creation threshold** (0.40), which gives small-N clusters the benefit of the doubt rather than assuming they are at death. See spec §R1 behavior table for the verified math.

---

## Executive summary

Between 2026-04-25 18:21 UTC and 2026-04-26 03:46 UTC, the backend domain
emerged two sub-domains (`audit` and `embedding-health`), both of which were
silently dissolved within ~6 hours of creation by a known matching bug in
`_reevaluate_sub_domains`. Neither dissolution was caused by genuine
qualifier drift in the underlying data — the cluster members never stopped
being topically consistent. The bug was fixed in v0.4.7 hours later, but the
two affected sub-domains were not auto-recreated and the backend domain is
currently sub-domain-empty despite carrying 12 members and a clear
embedding-related theme.

**This is a bug, not the system functioning as designed.** The same v0.4.7
commit that fixed the matching logic explicitly documents the prior behavior
as "guaranteed consistency=0% on healthy sub-domains, producing a flip-flop
dissolution loop every Phase 5 cycle."

The follow-up hardening pass (R1+R2+R3, shipped 2026-04-27) closes three
remaining structural fragilities the matching fix did not address: small-N
volatility, premature re-evaluation, and empty-snapshot fail-open. R4-R8
are deferred to a follow-up PR.

---

## Timeline (UTC)

| Time | Event | Cluster | Detail |
|---|---|---|---|
| 04-25 17:52 | v0.4.6 deployed | — | label-canonicalization fix only, matching bug present |
| **04-25 18:21:12** | `audit` created | `5c664c6e…` | parent=backend, m=5, consistency=100%, clusters_reparented=3 |
| 04-25 19:07–21:34 | 5 cross-sub-domain merges | `e19992fb…` | "Race Condition Auditing" pulled into `audit` sub-domain |
| **04-25 21:38:29** | `embedding-health` created | `45b416bb…` | parent=backend, m=13, consistency=56.5%, clusters_reparented=4 |
| 04-25 21:40 | cycle-4 post-snapshot | — | backend sub=2, top=`embedding-health` cons=0.57 (gap −0.057), tier=`ready` |
| 04-25 23:36 | cycle-5 ends | — | sub_domain_reevaluation has not yet fired (age < 6h) |
| **04-26 00:21:55** | `audit` DISSOLVED | `5c664c6e…` | consistency_pct=**0.0%**, floor=25%, age=6h 0m 43s |
| 04-26 00:29 | cycle-6 ends | — | backend sub=1 (per cycle-6 post: `metrics` top, cons=0.19) |
| 04-26 01:20–02:51 | cycles 7–11 | — | backend top qualifier oscillates: `concurrency` 0.30–0.33 |
| **04-26 03:46:51** | `embedding-health` DISSOLVED | `45b416bb…` | consistency_pct=**0.0%**, floor=25%, age=6h 8m 22s |
| 04-26 03:47:57 | `vocab_generated_enriched` | backend | quality_score=0.81, 4 new groups (concurrency/observability/embeddings/security) |
| **04-26 17:22:53** | v0.4.7 deployed (`226857e5`) | — | matching fix lands in `_reevaluate_sub_domains` |
| 04-27 03:06 | this audit | — | backend sub=0, no re-emergence |

**Key invariant:** both dissolutions fired on the first Phase-5 re-evaluation
cycle after `SUB_DOMAIN_DISSOLUTION_MIN_AGE_HOURS=6` elapsed. The age gate is
the only thing that prevented dissolution at the very first cycle after
creation.

---

## Root cause

### The matching bug

`_reevaluate_sub_domains` (backend/app/services/taxonomy/engine.py) checks
each member optimization against the sub-domain's qualifier vocabulary using
a three-source cascade:

| Source | Pre-v0.4.6 logic | Behavior on `embedding-health` |
|---|---|---|
| 1. `domain_raw` parse | `q.lower().replace(" ", "-") == sub_qualifier` (exact equality with sub-domain LABEL) | `backend: observability` → `observability` ≠ `embedding-health` → **no match** |
| 2. `intent_label` substring | `domain_qualifiers.get(sub_qualifier, [])` then substring scan | parent vocab dict is keyed by GROUP names (`metrics`, `tracing`), not sub-domain LABELS — `sub_keywords` was always `[]` → **no match** |
| 3. dynamic-keyword hits | `best_dyn.lower().replace(" ", "-") == sub_qualifier` (exact equality) | dynamic keywords are GROUP-shaped — never `embedding-health` → **no match** |

All three sources required either exact equality with the literal sub-domain
label or a parent-vocab lookup that was structurally empty. **A sub-domain
named after an aggregate concept (`embedding-health`, `audit`) could never
score above 0% consistency, regardless of how topically coherent its members
actually were.**

The fix in v0.4.7 (`226857e5`) introduces three new structural matches in
Source 1 and 2, all comparing the member's qualifier against the
sub-domain's OWN `generated_qualifiers` snapshot stored at creation time:

```python
sub_meta = read_meta(sub.cluster_metadata)
sub_generated_qualifiers = sub_meta.get("generated_qualifiers") or {}
sub_vocab_groups   = {k.lower() for k in sub_generated_qualifiers.keys()}
sub_vocab_terms    = {t.lower() for terms in sub_generated_qualifiers.values() for t in terms}
sub_vocab_tokens   = tokenized form of the above (≥4 chars)

# Source 1 — match if domain_raw qualifier hits any vocab layer
if (q_norm == sub_qualifier
    or q_norm in sub_vocab_groups       # NEW: matches GROUP names
    or q_norm in sub_vocab_terms        # NEW: matches vocab TERMS
    or q_tokens & sub_vocab_tokens):    # NEW: token overlap
    matched = True

# Source 2 — match if intent_label tokens hit sub_vocab_tokens
if intent_tokens & sub_vocab_tokens:
    matched = True
```

The regression test
`backend/tests/taxonomy/test_sub_domain_lifecycle.py::TestSubDomainConsistencyVocabGroupMatch`
locks the fix in. Its docstring explicitly describes the dissolved sub-domain
in this audit:

> "Pre-fix, a sub-domain named `embedding-health` (an aggregate concept) was
> dissolved every cycle because its children's `domain_raw` was
> `backend: observability` / `backend: metrics` / `backend: concurrency` —
> vocab GROUPS inside the sub-domain's own `generated_qualifiers`, but never
> the literal label `embedding-health`."

### Why creation succeeded but re-evaluation failed

Sub-domain *creation* uses the shared `compute_qualifier_cascade()` primitive
in `sub_domain_readiness.py`, which evaluates the parent domain's full
qualifier dictionary and asks "what fraction of members carry the most-common
qualifier?" — a bag-of-qualifiers consistency.

Sub-domain *re-evaluation* used a narrowed, target-specific predicate that
required matching the sub-domain's literal label. The two paths had
asymmetric definitions of "consistency". The comment block above the
re-evaluation code (engine.py:3094-3112) acknowledges the asymmetry as
intentional but did not anticipate the empty-vocab-dict failure mode.

The asymmetry is the deeper structural issue: **discovery and dissolution
must answer the same question.** v0.4.7 patches the symptom but does not
unify the predicates.

---

## Data-science assessment

### 1. Is dissolution-by-consistency the right model?

Yes. Hierarchical clustering with dynamic re-evaluation is a standard pattern
(BIRCH, online HDBSCAN, evolutionary topic models). The principle of
"dissolve sub-clusters whose intra-coherence falls below a floor" is sound
and prevents stale taxonomy from compounding.

### 2. Are the thresholds well-calibrated?

| Parameter | Value | Risk at observed N |
|---|---|---|
| `SUB_DOMAIN_DISSOLUTION_CONSISTENCY_FLOOR` | 0.25 | At N=5, **just 1 misclassified member drops consistency to 0.20** — below floor. At N=13, 4 wrong members suffice (0.69 → 0.23). Small-N volatile. |
| `SUB_DOMAIN_QUALIFIER_CONSISTENCY_HIGH` (creation) | 0.60 | Hysteresis gap is 0.35 nominal, but adaptive scale `0.60 − 0.004 × N` shrinks creation threshold faster than dissolution floor — at N=88 they collide. |
| `SUB_DOMAIN_QUALIFIER_SCALE_RATE` | 0.004 | Means at N=125 creation threshold = dissolution floor. **Dangerous regime above N=80.** |
| `SUB_DOMAIN_DISSOLUTION_MIN_AGE_HOURS` | 6 | Both observed dissolutions fired at 6h 0m and 6h 8m — *exactly* at the gate. **Suggests dissolution is racing the age check.** |

**Verdict:** thresholds are reasonable for moderate N (15–80) but break down
at the small-N tails (where most production sub-domains live initially) and
at large N (where creation/dissolution collapse). The 6h grace period is
optically too aggressive — most real corpora need overnight stability before
a sub-domain has actually proven itself.

### 3. Is the consistency metric robust?

The metric is a non-Bayesian point estimate with no smoothing. With N=5 and
a Beta(α=1, β=1) uniform prior, the 95% credible interval on a sample
consistency of 0.20 spans ~[0.04, 0.55] — i.e. the true consistency could
plausibly be above the 0.40 creation threshold or below the 0.25 dissolution
floor depending purely on sampling noise. A small-N consistency reading
**should not single-handedly trigger destruction of structural state.**

The score-adaptation subsystem already uses Bayesian shrinkage (T1.1 in
v0.4.7, `SCORE_ADAPTATION_PRIOR_KAPPA=8.0`). The sub-domain consistency
metric is a natural candidate for the same treatment.

### 4. Is "vocabulary regeneration" hostile to sub-domain stability?

Partially. Sub-domains store their own `generated_qualifiers` snapshot at
creation, so they're *insulated* from later parent-domain vocab regen — that's
the right design. But two failure modes remain:

1. **Empty snapshot.** If `generated_qualifiers` was not populated at
   creation (e.g. cold-start, vocab-gen failed silently), the v0.4.7 fix
   degrades back to exact-equality and reproduces the bug. There is no
   guard rail — `sub_generated_qualifiers = sub_meta.get(...) or {}` accepts
   the empty case as "valid" and matching simply fails.
2. **Member drift.** If the parent domain's vocabulary regenerates and new
   prompts are tagged with the new groups, those new prompts entering the
   sub-domain via reparenting (Phase 0) will not match the snapshot.
   Consistency drops monotonically with each new arrival until dissolution.
   This is partially mitigated by Source 2 (intent_label tokens), but only
   if the new vocab terms happen to share tokens with the snapshot.

### 5. Is asymmetric matching (creation vs re-eval) defensible?

The comment in engine.py:3094 argues yes — re-evaluation is "narrow and
sub-qualifier-targeted". The argument is plausible: at creation we're
asking "does a coherent sub-population exist?" while at re-eval we're asking
"does *this* sub-domain still own its members?" These are different
questions.

But the asymmetry was the proximate cause of the bug. **Two predicates
answering different questions need shared semantics on what constitutes a
match.** A safer architecture: re-evaluation calls the same
`compute_qualifier_cascade` primitive, then filters to qualifiers
"compatible" with the sub-domain (label, vocab groups, vocab terms). The
v0.4.7 inline expansion already does this — it just hasn't been promoted to
the shared primitive.

---

## What we lost

- 7 clusters reparented from the two sub-domains back to backend domain
  (`audit`: 2, `embedding-health`: 3, plus 2 implicit during merge events)
- 25 meta-patterns merged into the parent (`audit`: 10, `embedding-health`:
  15) — patterns themselves preserved, but their sub-domain provenance erased
- Sub-domain-scoped enrichment for ~16 optimizations under `embedding-health`
  and ~11 under `audit` — these now resolve to bare `backend` instead of
  more specific qualifiers
- The `embedding-health` sub-domain was specifically validated by cycle-4
  as the verification target for kebab-case syntax stability — **the
  validation passed at 21:40, and 6 hours later the sub-domain was gone.**

The user observed `backend sub=2` in cycle-4-post and `backend sub=0` from
cycle-6 onward without an explanation surface in any UI or telemetry endpoint.

---

## Why current state hasn't recovered

After the v0.4.7 fix landed, no sub-domain has re-emerged under backend.
Discovery requires:

- ≥5 members carrying the dominant qualifier (`SUB_DOMAIN_QUALIFIER_MIN_MEMBERS`)
- consistency ≥ adaptive threshold (`max(0.40, 0.60 − 0.004 × N)`)
- cluster breadth ≥ 2 (qualifier appears in ≥2 distinct clusters)

Current backend state:

```
m=12  top=observability  cons=0.29  threshold=0.46  gap=+0.174  tier=inert
```

The qualifier `observability` appears in 7/12 opts but is concentrated in 1
cluster — fails the breadth check. `embedding-health` no longer exists as a
candidate qualifier (the cascade emitted `concurrency`, `embeddings`,
`security` after the post-dissolution vocab regen). **The system is not
self-healing toward the prior state — recovery requires either organic vocab
shift or operator intervention.**

---

## Verification commands

```bash
# Reproduce the timeline
grep -E '"5c664c6e|"45b416bb' data/taxonomy_events/decisions-2026-04-2[5-6].jsonl

# Confirm pre-fix matching bug in git history
git show 15d7fa51:backend/app/services/taxonomy/engine.py | sed -n '3093,3110p'
git show 226857e5:backend/app/services/taxonomy/engine.py | sed -n '3194,3250p'

# Run the regression test that locks the fix
cd backend && pytest tests/taxonomy/test_sub_domain_lifecycle.py::TestSubDomainConsistencyVocabGroupMatch -v
```

---

## Recommendations (priority-ordered)

### R1. Add Bayesian shrinkage to consistency metric (HIGH) — **SHIPPED**

**Problem:** point-estimate consistency at N≤15 is statistically meaningless.
A single off-topic member can swing the metric by 7–20 percentage points.

**Fix:** apply T1.1-style shrinkage from score adaptation. Define
shrunk consistency as

```python
consistency_shrunk = (matching + ALPHA_PRIOR) / (total_opts + ALPHA_PRIOR + BETA_PRIOR)
```

with `ALPHA_PRIOR = ALPHA_PRIOR_DISSOLUTION × dissolution_floor` and
`BETA_PRIOR = ALPHA_PRIOR_DISSOLUTION × (1 − dissolution_floor)`. With
`ALPHA_PRIOR_DISSOLUTION = 4`, the metric pulls toward the floor at small
N and reads ~true consistency at N≥20. This eliminates single-prompt
dissolution triggers without changing the equilibrium behavior.

**Telemetry:** log both raw and shrunk values in `sub_domain_reevaluated`
events so threshold tuning is data-driven.

**Cost:** ~15 lines in engine.py:3283 + new constant. Bounded.

### R2. Lengthen `SUB_DOMAIN_DISSOLUTION_MIN_AGE_HOURS` from 6 → 24 (HIGH) — **SHIPPED**

**Problem:** both observed dissolutions fired at 6h 0m and 6h 8m — at the
exact gate boundary. 6 hours is barely an overnight cycle and offers no
forgiveness against transient mis-classifications during the bootstrap
window when the parent domain's vocabulary is still settling.

**Fix:** bump to 24 hours. Gives the system one full daily cadence of vocab
regen + member arrivals before judging the sub-domain's coherence. The
existing `dissolved_this_cycle` flip-flop guard remains.

**Cost:** one constant change. Zero migration.

### R3. Add empty-snapshot guardrail (HIGH) — **SHIPPED**

**Problem:** if `cluster_metadata.generated_qualifiers` is empty for any
reason (cold-start, failed vocab-gen, manual creation), v0.4.7 silently
degrades to pre-fix exact-equality matching, reproducing the bug.

**Fix:** in `_reevaluate_sub_domains`, when
`sub_generated_qualifiers` is empty, **skip dissolution this cycle** rather
than fail-open into strict matching. Log as `sub_domain_reevaluation_skipped`
with reason `empty_vocab_snapshot`. Forces operator visibility.

**Cost:** ~5 lines, plus a regression test.

### R4. Unify creation/re-evaluation predicates (MEDIUM) — **SHIPPED**

**Problem:** the asymmetry between `compute_qualifier_cascade` (creation)
and the inline matcher (re-eval) was the proximate cause of the bug.
Future drift between the two predicates is a recurring risk.

**Fix:** extract a shared
`compute_sub_domain_membership(opt, sub_qualifier, sub_vocab) -> bool` in
`sub_domain_readiness.py`. Both paths call it. Re-evaluation gets the
existing additional Source-3 weighting for free.

**Cost:** ~80-line refactor, plus updating both call sites and the existing
regression test.

### R5. Backfill telemetry on the dissolved pair (MEDIUM) — **SHIPPED**

**Problem:** the dissolution events emitted `consistency_pct=0.0` but no
breakdown of *why* — which members matched, which didn't, what their
`domain_raw`/`intent_label` looked like.

**Fix:** extend the `sub_domain_reevaluated` event context with
`{matching_members: int, sample_match_failures: [{member_id, domain_raw,
intent_label, reason}, ...]}` (cap at 3 samples to avoid log bloat). Makes
forensic reconstruction tractable from JSONL alone — no DB lookup needed.

**Cost:** ~30 lines, no migration.

### R6. Recovery option: rebuild dissolved sub-domains (MEDIUM) — **SHIPPED**

**Problem:** the affected sub-domains haven't auto-recreated. The 27 opts
that previously enjoyed sub-domain-scoped enrichment now get bare backend
context. There is no operator surface to say "these dissolutions were
buggy, recreate them."

**Fix:** add `POST /api/domains/{id}/rebuild-sub-domains` that
forces `_propose_sub_domains` with a relaxed threshold (e.g. floor = 0.30
instead of adaptive) and returns the newly created sub-domain IDs. Idempotent.

**Cost:** new endpoint + reuse existing discovery primitive. ~50 lines.

### R7. Add "vocab regeneration impact" telemetry (LOW) — **SHIPPED**

**Problem:** the `vocab_generated_enriched` event at 03:47:57 swapped
backend's qualifier vocab from `{metrics, tracing, pattern-instrumentation}`
to `{concurrency, observability, embeddings, security}` — zero overlap.
This was 1 minute after the embedding-health dissolution. Operators have no
way to see "vocab churn caused this dissolution".

**Fix:** in `vocab_generated_enriched`, include `previous_groups: list[str]`
and `overlap_pct: float`. Surface in Activity Panel. Surface in Observatory
DomainLifecycleTimeline as a flagged event when `overlap_pct < 0.5`.

**Cost:** ~20 lines + frontend surface. Bounded.

### R8. Threshold-collision check (LOW) — **SHIPPED**

**Problem:** at N≈125, `SUB_DOMAIN_QUALIFIER_CONSISTENCY_HIGH (0.60) - 0.004*N`
= 0.10, which is below `SUB_DOMAIN_DISSOLUTION_CONSISTENCY_FLOOR (0.25)`.
Sub-domains are uncreatable AND dissolvable simultaneously. Currently a
dormant issue (no domain has 125 members), but a guardrail is cheap.

**Fix:** add a startup invariant check: assert
`min_creation_threshold > dissolution_floor` and log fatally otherwise.

**Cost:** 5 lines.

---

## Suggested rollout order

1. **R3** (empty-snapshot guardrail) — narrowest fix, prevents a stealth
   regression of the same bug class.
2. **R1** (Bayesian shrinkage) — directly addresses the small-N volatility
   that made the bug fire on the very first re-eval.
3. **R2** (24h grace) — one-line, zero-risk, immediate value.
4. **R5** (forensic telemetry) — needed BEFORE the next time this happens.
5. **R4** (predicate unification) — larger refactor; sequence after R3.
6. **R6** (recovery endpoint) — operator tool, lower urgency.
7. **R7, R8** — quality-of-life.

R1+R2+R3 together can ship as a single PR `fix(taxonomy): hardening
sub-domain dissolution against small-N volatility`. R4+R5 land in a
follow-up. R6 is a separate feature.

---

## Closing assessment

**To the user's framing question — "is this the system functioning as
designed and the sub-domains legitimately needed to be dissolved?"** —
the answer is unambiguous:

**No.** The dissolution is the result of an asymmetric-matching bug that the
v0.4.7 fix and its regression test both explicitly identify. The members of
both sub-domains never stopped being topically consistent; the metric used
to judge them was structurally incapable of recognizing that consistency.

The deeper data-science story is more nuanced: even with v0.4.7's correct
matching, the consistency metric is statistically fragile at small N, the
6-hour grace period is too short, and the asymmetry between creation and
dissolution predicates is a structural risk. The recommended changes
(particularly R1 + R2 + R3) shift the system from "occasionally loses
sub-domains to noise" to "robustly preserves them unless the data really has
shifted."
