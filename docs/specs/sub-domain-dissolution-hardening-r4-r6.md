# Spec: Sub-Domain Dissolution Hardening — R4 + R5 + R6

**Date:** 2026-04-27
**Audit:** `docs/audits/sub-domain-regression-2026-04-27.md`
**R1-R3 spec (shipped):** `docs/specs/sub-domain-dissolution-hardening-2026-04-27.md`
**Target version:** v0.4.8-dev

---

## 0. Problem statement

R1+R2+R3 (shipped 2026-04-27) closed the immediate fragilities: small-N
volatility (Bayesian shrinkage), premature re-evaluation (24h grace), and
empty-snapshot fail-open. Three structural concerns remain:

- **R4 — Predicate asymmetry.** `_propose_sub_domains` uses
  `compute_qualifier_cascade` (best-qualifier-wins, parent-domain vocabulary).
  `_reevaluate_sub_domains` uses an inline matcher (does THIS sub-domain own
  this opt?). The two are intentionally different *questions* but their
  per-opt matching mechanics drift. The v0.4.6 bug arose because the inline
  matcher narrowed beyond the cascade — and there's no shared primitive
  guarding against the same drift recurring.
- **R5 — Forensic gap.** The `sub_domain_dissolved` event records
  `consistency_pct=0.0` but no breakdown of *why*. Reconstructing a
  dissolution requires reading raw opts and re-running the matching logic
  by hand. The audit took ~30 minutes of forensic work that should have been
  one log line.
- **R6 — No recovery surface.** The two dissolved sub-domains
  (`audit`, `embedding-health`) and their 27 orphaned-from-sub-domain opts
  have no operator path back. Discovery's adaptive threshold +
  cluster-breadth gate hasn't re-triggered in 24+ hours and isn't expected
  to without organic vocab shift.

This spec covers R4, R5, R6.

---

## R4. Extract shared per-opt sub-domain matcher

### Problem

`_reevaluate_sub_domains` (engine.py: matching cascade at lines ~3225-3313, vocab construction at ~3167-3223) inlines the per-opt
matching cascade. Source 1 / 2 / 3 each compute a boolean. The engine then
sums booleans into `matching` and divides by `total_opts`.

The matching logic is **single-purpose** code at a specific call site, not
a primitive. Three problems flow from this:

1. R5's forensic telemetry needs reasons-for-no-match (which source tried
   what, why it missed). With the matching inlined, R5 has to re-read the
   raw values inside the engine module.
2. The engine module is already 4800+ lines — extracting orthogonal pure
   logic improves readability and unit-testability.
3. Future tools that want to ask "does this opt match sub-domain X's vocab?"
   (debugger panels, the rebuild endpoint from R6, etc.) would otherwise
   need to copy-paste the matching cascade.

### Design

Add a pure function to `backend/app/services/taxonomy/sub_domain_readiness.py`:

```python
@dataclass(frozen=True)
class SubDomainMatchResult:
    """Outcome of matching ONE optimization against ONE sub-domain's vocab.

    Pure data carrier, no methods.  ``matched=True`` means the opt belongs
    to the sub-domain; ``matched=False`` means it does not — the ``reason``
    field then describes which match attempts were tried and why each missed.
    """
    matched: bool
    source: Literal["domain_raw", "intent_label", "tf_idf"] | None
    matched_value: str | None  # the qualifier/keyword that triggered the match
    reason: str  # human-readable; empty when matched=True


def match_opt_to_sub_domain_vocab(
    *,
    domain_raw: str | None,
    intent_label: str | None,
    raw_prompt: str | None,
    sub_qualifier: str,                     # sub-domain label, lowercase kebab
    sub_vocab_groups: set[str],             # group names from generated_qualifiers keys
    sub_vocab_terms: set[str],              # flattened vocab terms (lowercase)
    sub_vocab_tokens: set[str],             # ≥4-char tokens of groups+terms
    sub_keywords_legacy: list[str],         # parent-domain qualifier keywords (often empty)
    dynamic_keywords: list[tuple[str, float]],  # TF-IDF keyword/weight pairs
) -> SubDomainMatchResult:
    """Targeted: does this opt belong to THIS sub-domain's vocab?

    Implements the v0.4.7 three-source matching cascade verbatim:

    - Source 1 (``domain_raw``): exact qualifier label OR vocab group
      OR vocab term OR token-overlap with ``sub_vocab_tokens``.
    - Source 2 (``intent_label``): tokens from the intent label intersect
      ``sub_vocab_tokens``.
    - Source 2b (legacy): substring scan against ``sub_keywords_legacy``
      (parent-keyed; almost always empty, retained for back-compat with
      any future caller that pre-populates that field).  Spec contract
      requires this path to remain present byte-equivalent.
    - Source 3 (``raw_prompt`` + dynamic keywords): best-weight TF-IDF
      keyword that normalises to ``sub_qualifier``, with min-hit gating
      based on weight.

    Pure: no I/O, no side effects, no DB.  Safe to call from any context.

    The semantics are intentionally per-opt-per-vocab — they answer "does
    THIS opt belong to THIS sub-domain", not "what's the best qualifier
    for this opt".  The latter is ``compute_qualifier_cascade``'s job.
    """
```

### Engine refactor

`_reevaluate_sub_domains` (engine.py:3030+) loses ~80 lines of inline
matching logic. The matching loop becomes:

```python
matching = 0
match_results: list[SubDomainMatchResult] = []
for domain_raw, intent_label, raw_prompt in opt_rows:
    result = match_opt_to_sub_domain_vocab(
        domain_raw=domain_raw,
        intent_label=intent_label,
        raw_prompt=raw_prompt,
        sub_qualifier=sub_qualifier,
        sub_vocab_groups=sub_vocab_groups,
        sub_vocab_terms=sub_vocab_terms,
        sub_vocab_tokens=sub_vocab_tokens,
        sub_keywords_legacy=sub_keywords_legacy,
        dynamic_keywords=dynamic_keywords,
    )
    match_results.append(result)
    if result.matched:
        matching += 1

consistency = matching / total_opts
# (Bayesian shrinkage + dissolution decision unchanged)
```

`match_results` is then available for R5's forensic telemetry to consume.

### Code touch points

| File | Change |
|---|---|
| `backend/app/services/taxonomy/sub_domain_readiness.py` | Add `SubDomainMatchResult` dataclass + `match_opt_to_sub_domain_vocab` function (~80 lines including module-level constants and helpers extracted from engine.py) |
| `backend/app/services/taxonomy/engine.py` | Replace inline matching loop in `_reevaluate_sub_domains` (~80 lines deleted, ~10 lines added). Net file shrinks ~70 lines. |

### Acceptance criteria

- **AC-R4-1:** `match_opt_to_sub_domain_vocab` exists in `sub_domain_readiness.py` as a pure function (no I/O). Unit test class `TestMatchOptToSubDomainVocab` covers:
  - Source 1 hit via exact label, vocab group, vocab term, token-overlap (4 sub-tests)
  - Source 2 hit via intent_label tokens
  - Source 3 hit via dynamic TF-IDF keyword
  - All-sources-miss → `matched=False`, `reason` non-empty, `source=None`
- **AC-R4-2:** `_reevaluate_sub_domains` consumes the new primitive and the engine's existing tests (R1+R2+R3 + vocab-group-match + sub-domain dissolution) continue to pass without modification (proves behavioral equivalence). The full taxonomy test slice must remain at ≥900 passed.
- **AC-R4-3:** No call site outside `_reevaluate_sub_domains` mistakenly imports the per-opt matcher (it's a local engine-side primitive). The function is exported from `sub_domain_readiness.py` for reuse but not yet called from elsewhere.
- **AC-R4-4:** The three-source matching mechanics in the new primitive byte-equivalent to the v0.4.7 inline implementation. Verifiable via diff-style test: feed the same inputs, expect the same booleans across the existing `TestSubDomainConsistencyVocabGroupMatch::test_unrelated_qualifiers_still_dissolve` and the bumped sibling tests — already in CI, must remain green.

### Backward compat

- No public API change. `match_opt_to_sub_domain_vocab` is new but additive.
- No DB or event-schema change.
- Engine method signatures unchanged.

---

## R5. Forensic dissolution telemetry

### Problem

Today's `sub_domain_dissolved` event:

```json
{
  "ts": "2026-04-26T03:46:51.345783+00:00",
  "decision": "sub_domain_dissolved",
  "context": {
    "domain": "backend",
    "sub_domain": "embedding-health",
    "consistency_pct": 0.0,
    "shrunk_consistency_pct": 0.0,        // R1 added
    "prior_strength": 10,                 // R1 added
    "floor_pct": 25.0,
    "clusters_reparented": 3,
    "meta_patterns_merged": 15,
    "reason": "qualifier_consistency_below_floor"
  }
}
```

`consistency_pct=0.0` tells you the dissolution criterion fired but not
*why*. Forensic reconstruction needs:
- How many of the N members matched (already implicit from `consistency_pct × total_opts` but require math)
- For non-matching members: what their `domain_raw` and `intent_label` look like
- Which sources the matcher tried and missed

### Design

Extend both `sub_domain_reevaluated` and `sub_domain_dissolved` event
contexts with two new keys:

```python
{
    # ... existing keys ...
    "matching_members": int,         # already computed; surface explicitly
    "sample_match_failures": [       # capped at SUB_DOMAIN_FAILURE_SAMPLES
        {
            "cluster_id": str,        # member's parent cluster (truncates the per-opt id chain)
            "domain_raw": str | None, # truncated to 80 chars
            "intent_label": str | None,
            "reason": str,            # from SubDomainMatchResult.reason
        },
        ...
    ],
}
```

Constants in `_constants.py`:

```python
SUB_DOMAIN_FAILURE_SAMPLES: int = 3  # cap to avoid log bloat
SUB_DOMAIN_FAILURE_FIELD_TRUNCATE: int = 80  # max chars per text field in samples
```

The samples are a **deterministic prefix** (first 3 in iteration order) of
the non-matching members, NOT a random sample — this keeps forensic
reproduction stable across re-runs and makes the test deterministic.

### Code touch points

| File | Change |
|---|---|
| `backend/app/services/taxonomy/_constants.py` | Add 2 new constants |
| `backend/app/services/taxonomy/engine.py` | In `_reevaluate_sub_domains`, after collecting `match_results` from R4's primitive, build `sample_match_failures` from the first 3 `matched=False` results; add `matching_members` and `sample_match_failures` keys to BOTH the `sub_domain_reevaluated` and `sub_domain_dissolved` event contexts |

The `cluster_id` per opt requires expanding the existing `opt_q` SELECT
to also return `Optimization.cluster_id`. This is a tiny change to the
query; the cluster_id was already implicit (we'd selected on it via `child_ids`).

### Acceptance criteria

- **AC-R5-1:** A dissolution event under hostile conditions emits a `sample_match_failures` list of length ≤ 3, each entry carrying `cluster_id`, `domain_raw`, `intent_label`, `reason`. Test `TestSubDomainForensicTelemetry::test_dissolution_event_carries_sample_failures`.
- **AC-R5-2:** When `matching_members > 0`, the failure samples are drawn ONLY from `matched=False` opts, never from matched ones. Test `test_sample_failures_exclude_matched_opts`.
- **AC-R5-3:** Text fields in samples are truncated to `SUB_DOMAIN_FAILURE_FIELD_TRUNCATE=80` characters. Test `test_long_text_truncated`.
- **AC-R5-4:** `matching_members` count in the event matches the integer `matching` computed by the engine (cross-check, not redundancy — guards against the count surfacing differently than the floor decision uses). Test `test_matching_members_matches_engine_count`.
- **AC-R5-5:** When all opts match (no failures), `sample_match_failures` is `[]` and `matching_members` equals `total_opts`. Test `test_all_match_emits_empty_failures`.

### Backward compat

- Additive event keys only.
- Two new constants. No migration. No frontend update needed (Activity Panel renders `context` permissively).

---

## R6. Rebuild-sub-domains recovery endpoint

### Problem

The two affected sub-domains have not auto-recreated. Discovery's
adaptive threshold (`max(0.40, 0.60 - 0.004 × N)`) plus the
`SUB_DOMAIN_MIN_CLUSTER_BREADTH=2` gate hasn't re-fired even with the
v0.4.7 matching fix and the v0.4.8 hardening (R1+R2+R3). 27 opts that
previously enjoyed sub-domain-scoped enrichment now resolve to bare
backend, with no operator path to recover.

### Design

New REST endpoint:

```
POST /api/domains/{domain_id}/rebuild-sub-domains
Body: { "min_consistency": float | null, "dry_run": bool = false }
Response: RebuildSubDomainsResult
```

#### Request schema (`backend/app/schemas/domains.py`)

```python
class RebuildSubDomainsRequest(BaseModel):
    min_consistency: Optional[float] = Field(
        default=None,
        ge=0.25,  # = SUB_DOMAIN_DISSOLUTION_CONSISTENCY_FLOOR; below this
                  # any created sub-domain would be dissolved on the next
                  # Phase 5 cycle (immediate-dissolution loop).
        le=1.0,
        description=(
            "Override the adaptive consistency threshold for this rebuild. "
            "Default (None) keeps the standard "
            "max(0.40, 0.60 - 0.004*N) formula. "
            "Recommended override: 0.30 — relaxes from the default 0.40 "
            "lower bound. The value MUST be ≥ 0.25 (= "
            "SUB_DOMAIN_DISSOLUTION_CONSISTENCY_FLOOR) — Pydantic enforces "
            "this at validation time AND `rebuild_sub_domains` re-asserts "
            "the floor at runtime as a defense-in-depth check."
        ),
    )
    dry_run: bool = Field(
        default=False,
        description=(
            "When True, computes which sub-domains WOULD be created without "
            "modifying state. Returns the planned list."
        ),
    )
```

#### Response schema

```python
class RebuildSubDomainsResult(BaseModel):
    domain_id: str
    domain_label: str
    threshold_used: float          # the actual threshold applied this run
    proposed: list[str]            # sub-domain qualifier labels considered
    created: list[str]             # sub-domains actually created (empty when dry_run)
    skipped_existing: list[str]    # qualifiers whose sub-domain already exists (idempotent)
    dry_run: bool
```

#### Service method

Add a new method to `TaxonomyEngine` in `engine.py`:

```python
async def rebuild_sub_domains(
    self,
    db: AsyncSession,
    domain_id: str,
    *,
    min_consistency_override: float | None = None,
    dry_run: bool = False,
) -> dict:
    """Operator recovery: re-run sub-domain discovery on a single domain
    with optional threshold relaxation.

    Idempotent: existing sub-domains are not recreated.  Eligible new
    qualifiers below the standard adaptive threshold (but above
    ``min_consistency_override``) are created.  Floor is always
    ``SUB_DOMAIN_DISSOLUTION_CONSISTENCY_FLOOR=0.25`` — recovery cannot
    create sub-domains the dissolution path would immediately kill.

    Transaction semantics: single transaction per call.  All sub-domain
    creations within one rebuild are atomic — if any creation fails,
    the entire batch rolls back (matches ``promote_to_domain``'s
    pattern).  Telemetry event ``sub_domain_rebuild_invoked`` fires
    for every call (including dry runs and zero-creation calls) so the
    operator audit trail is complete.  When ``created`` is non-empty,
    additionally publishes a ``taxonomy_changed`` event to
    ``event_bus`` so the readiness TTL cache invalidates and the
    resident engine's dirty_set picks up the new sub-domain nodes.

    Returns dict with keys: domain_label, threshold_used, proposed,
    created, skipped_existing.
    """
```

Implementation: extends `_propose_sub_domains` semantics for ONE domain,
reusing `compute_qualifier_cascade` for the qualifier scan, and creates
sub-domains for any qualifier whose consistency exceeds
`min_consistency_override or adaptive_threshold` AND that satisfies the
existing breadth gate AND that doesn't already exist.

#### Router

```python
@router.post(
    "/{domain_id}/rebuild-sub-domains",
    response_model=RebuildSubDomainsResult,
    dependencies=[Depends(RateLimit(lambda: "10/minute"))],
)
async def rebuild_sub_domains(
    domain_id: str,
    request: RebuildSubDomainsRequest,
    db: AsyncSession = Depends(get_db),
) -> RebuildSubDomainsResult:
    """Operator-triggered sub-domain rebuild.

    Error envelope follows `promote_to_domain` pattern:
        404 — domain_id not found
        422 — node is not a domain (state != "domain") OR
              min_consistency below SUB_DOMAIN_DISSOLUTION_CONSISTENCY_FLOOR
              (defense-in-depth duplicate of Pydantic ge=0.25)
        503 — DB OperationalError (transient contention)
        500 — uncaught (logged with traceback)

    See R6 in `docs/audits/sub-domain-regression-2026-04-27.md`.
    """
```

#### Telemetry event

A new event decision `sub_domain_rebuild_invoked`:

```json
{
  "decision": "sub_domain_rebuild_invoked",
  "cluster_id": "<domain_node_id>",
  "context": {
    "domain": "backend",
    "min_consistency_override": 0.30,
    "threshold_used": 0.30,
    "dry_run": false,
    "proposed_count": 2,
    "created_count": 1,
    "skipped_existing_count": 1,
  }
}
```

### Code touch points

| File | Change |
|---|---|
| `backend/app/schemas/domains.py` | Add `RebuildSubDomainsRequest`, `RebuildSubDomainsResult` |
| `backend/app/services/taxonomy/engine.py` | Add `rebuild_sub_domains` method (~70 lines, mostly delegating to existing primitives) |
| `backend/app/routers/domains.py` | Add `POST /{domain_id}/rebuild-sub-domains` handler (~30 lines) |
| `backend/tests/routers/test_domains.py` (new file if not present) OR existing router test file | Test class `TestRebuildSubDomainsEndpoint` covering 200/404/422/429 paths and dry_run semantics |
| `backend/tests/taxonomy/test_sub_domain_lifecycle.py` | Test class `TestRebuildSubDomainsService` for the engine method (idempotency, threshold override, telemetry) |

### Acceptance criteria

- **AC-R6-1:** `POST /api/domains/{nonexistent_id}/rebuild-sub-domains` returns 404. `test_rebuild_404_unknown_domain`.
- **AC-R6-2:** `POST /api/domains/{cluster_id}/rebuild-sub-domains` where `cluster_id` is a non-domain node returns 422 with `must be a domain`. `test_rebuild_422_non_domain_node`.
- **AC-R6-3:** `min_consistency=2.0` returns 422 (above range). `test_rebuild_422_invalid_threshold_above`.
- **AC-R6-4:** `min_consistency=0.10` returns 422 (below `SUB_DOMAIN_DISSOLUTION_CONSISTENCY_FLOOR=0.25`) — Pydantic catches it; the engine method ALSO raises `ValueError` if called directly with such a value (defense-in-depth). `test_rebuild_422_min_consistency_below_dissolution_floor`.
- **AC-R6-5:** `dry_run=true` does NOT create any sub-domain; response carries `proposed` non-empty if conditions are met. The `sub_domain_rebuild_invoked` event STILL fires with `dry_run=true` in context (operator audit trail). `test_rebuild_dry_run_no_state_change`.
- **AC-R6-6:** First rebuild creates the eligible sub-domains; immediate second rebuild creates none (idempotency) and lists them in `skipped_existing`. `test_rebuild_idempotent`.
- **AC-R6-7:** A non-dry-run `sub_domain_rebuild_invoked` event is emitted with the expected context keys including `dry_run=false`. `test_rebuild_emits_telemetry_non_dry`.
- **AC-R6-8:** When `created` is non-empty, a `taxonomy_changed` event is published via `event_bus` so the readiness cache invalidates. `test_rebuild_emits_taxonomy_changed_when_creating`.
- **AC-R6-9:** When `created` is empty (idempotent re-run, dry-run, or no eligible qualifiers), NO `taxonomy_changed` event fires. `test_rebuild_no_taxonomy_changed_on_zero_creates`.
- **AC-R6-10:** If sub-domain creation raises mid-batch, the entire transaction rolls back — no partial sub-domain leftovers. Verified via injecting a failure into the inner creation and asserting `db.scalar(select(count())).where(state="domain", parent_id=domain.id)` is unchanged. `test_rebuild_rolls_back_on_partial_failure`.
- **AC-R6-11:** Default `min_consistency=None` uses the same adaptive formula as discovery; the `threshold_used` field reflects the actual computed value. `test_rebuild_default_threshold_matches_discovery`.

### Backward compat

- New endpoint, new schemas — purely additive.
- New event decision — additive.
- No DB schema change.
- Existing `/api/domains/*` endpoints unchanged.

### Implementation note

The spec proposed reusing `compute_qualifier_cascade` for the qualifier scan,
but the cascade admits a qualifier only when it already lives in the parent
domain's vocabulary (`generated_qualifiers` keys ∪ `signal_keywords` lemmas
— see the `known_qualifiers` gating in
`sub_domain_readiness.compute_qualifier_cascade`). That gate is correct for
*organic* discovery during the warm-path Phase 5 cycle, but it is exactly
wrong for the operator-recovery scenario this endpoint exists to serve:
post-mass-dissolution the parent domain's `generated_qualifiers` may have
been emptied or scoped away from the qualifiers the operator wants to
re-promote, and the cascade would silently return `qualifier_counts={}`
producing a zero-proposal recovery. The shipped implementation therefore
reads each optimization's `domain_raw` directly via `parse_domain`, counts
occurrences locally, and lets the breadth + consistency gates do their work
without a vocabulary precondition. User-visible semantics (threshold formula,
`SUB_DOMAIN_MIN_CLUSTER_BREADTH` gate, idempotency, transactional rollback,
telemetry) are unchanged.

---

## Cross-cutting requirements

### Test scope

- Unit tests for R4 land in `backend/tests/taxonomy/test_sub_domain_readiness.py` (a new test class — the file already exists).
- Engine integration tests for R4+R5 land in `backend/tests/taxonomy/test_sub_domain_lifecycle.py` (new test classes).
- Router tests for R6 land in `backend/tests/routers/` (existing structure).

### CHANGELOG

Three new lines under `## Unreleased` → categories:

```markdown
### Changed
- Sub-domain re-evaluation matching cascade extracted to a shared pure primitive `match_opt_to_sub_domain_vocab` in `sub_domain_readiness.py` (audit `docs/audits/sub-domain-regression-2026-04-27.md` R4).

### Added
- Dissolution events now carry forensic detail: `matching_members` count + up to 3 `sample_match_failures` per event (R5).
- Operator endpoint `POST /api/domains/{id}/rebuild-sub-domains` for sub-domain recovery with optional threshold override and dry-run support (R6).
```

### Lint & type

All touched files must pass:

```
cd backend && ruff check app/services/taxonomy/ app/routers/domains.py app/schemas/domains.py tests/taxonomy/ tests/routers/
cd backend && mypy app/services/taxonomy/sub_domain_readiness.py app/services/taxonomy/engine.py app/routers/domains.py app/schemas/domains.py
```

### Definition of done

- All ACs across R4, R5, R6 green in CI.
- Full taxonomy slice ≥ 900 + N (where N = count of new tests) passes.
- Full backend suite passes (current baseline 3115).
- E2E `cycle-13-r4-r6-validation` runs clean (3 prompts + a manual `rebuild-sub-domains` POST).
- Audit doc updated with R4-R6 SHIPPED markers.
- This spec doc updated with `Status: SHIPPED` markers per recommendation.

---

## Out of scope (not addressed by this spec)

- **R7** (vocab regeneration overlap telemetry) — observability-only, defer.
- **R8** (threshold collision invariant check) — dormant, defer.
- Restoring the historical `audit` and `embedding-health` sub-domain identities specifically. R6 will create new sub-domains based on current data; if today's qualifier distribution still produces those same labels, they'll re-emerge — but R6 makes no promise of name preservation.

---

## Status

| Recommendation | Status | Test class | Validation |
|---|---|---|---|
| R4 — Predicate unification | **SHIPPED** | `TestMatchOptToSubDomainVocab` (7 unit) + `TestSubDomainReevalUsesSharedPrimitive` (1 integration) | unit ✓, full taxonomy slice 953 ✓, full backend 3145 ✓, e2e cycle-13 ✓ |
| R5 — Forensic telemetry | **SHIPPED** | `TestSubDomainForensicTelemetry` (5 tests) | unit ✓, full taxonomy slice 953 ✓, full backend 3145 ✓, e2e cycle-13 ✓ |
| R6 — Rebuild endpoint | **SHIPPED** | `TestRebuildSubDomainsService` (11 engine) + `TestRebuildSubDomainsEndpoint` (6 router) | unit ✓, full taxonomy + router slice 953 ✓, full backend 3145 ✓, live smoke ✓ (`sub_domain_rebuild_invoked` event observed at 2026-04-27 06:38:23 UTC) |

## Validation evidence

- **Unit tests added across R4-R6:** 30 (8 + 5 + 17)
- **Pre-existing tests adapted:** none
- **Full taxonomy + router slice:** 953 passed / 0 failed
- **Full backend suite:** 3145 passed / 1 skipped / 0 failed (1 flake on `test_hot_path_under_500ms` re-passed solo, unrelated to R4-R6)
- **Lint:** clean on all touched files (engine.py, sub_domain_readiness.py, _constants.py, schemas/domains.py, routers/domains.py, both test files)
- **Mypy:** no new errors. Two pre-existing errors at engine.py:5017/5032 (line numbers shifted from 4694/4709 baseline due to ~165-line method addition) — `domain_id_map` Optional handling unrelated to R4-R6
- **E2E cycle:** `cycle-13-r4-r6-validation` ran 2026-04-27 06:43–06:53 UTC
  - 3 prompts processed successfully (53 → 56 optimizations)
  - Score health stable: count=55, mean=8.04, stdev=0.76 (baseline pre-cycle was mean=8.05, stdev=0.77)
  - Backend top qualifier shifted `observability` → `embeddings` (organic vocab evolution)
  - Zero `sub_domain_dissolved` events
  - Zero spurious `sub_domain_reevaluation_skipped` events
- **Live R6 smoke:** at 2026-04-27 06:38:23 UTC, the endpoint was exercised via `POST /api/domains/{backend_id}/rebuild-sub-domains` with `{"min_consistency": 0.30, "dry_run": true}`. Response: 200 with `RebuildSubDomainsResult` shape (`dry_run=True`, `created=[]`). Telemetry event `sub_domain_rebuild_invoked` was emitted with the correct payload.

## Implementation notes

- **R4 (cascade-vs-parse_domain deviation):** R6's `rebuild_sub_domains` does NOT reuse `compute_qualifier_cascade` despite the spec's earlier suggestion. The cascade gates qualifiers via `known_qualifiers` (built from the parent domain's `cluster_metadata.generated_qualifiers` + `signal_keywords`) — in operator-recovery scenarios where vocabulary may be empty/disrupted, that gate would return zero proposals. R6 instead uses `parse_domain` directly per-opt, builds `qualifier_to_cluster_ids` locally, and applies the breadth + consistency gates inline. User-visible semantics unchanged. Note added to engine.py docstring + spec §R6.
- **R6 transaction semantics:** sub-domain creation is wrapped in `db.begin_nested()` (SAVEPOINT). Mid-batch failure rolls back ALL partial creates without expiring the test's outer ORM session — verified by `test_rebuild_rolls_back_on_partial_failure`.
