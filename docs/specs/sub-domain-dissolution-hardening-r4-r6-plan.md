# Implementation Plan: R4 + R5 + R6

**Spec:** `docs/specs/sub-domain-dissolution-hardening-r4-r6.md` (PASS WITH MINOR FIXES — applied 2026-04-27)
**Audit:** `docs/audits/sub-domain-regression-2026-04-27.md`
**Predecessor:** R1+R2+R3 shipped 2026-04-27 (`sub-domain-dissolution-hardening-2026-04-27.md`)

---

## Workflow

Three sequential TDD cycles. Each phase is dispatched as an independent
fresh-context subagent:

```
RED → GREEN → REFACTOR → VALIDATION
```

R4 → R5 → R6 ordering is required because:
- R5 consumes `SubDomainMatchResult` from R4 — must land first.
- R6 is independent but lands last to keep the e2e validation cycle simple.

---

## Cycle 4 — R4: Extract `match_opt_to_sub_domain_vocab`

### RED subagent

**Task:** Add a new test class `TestMatchOptToSubDomainVocab` to
`backend/tests/taxonomy/test_sub_domain_readiness.py` (file already exists).
Tests must fail because the function does not yet exist.

Tests:
1. `test_source_1_exact_label_match` — `domain_raw="backend: embedding-health"`, `sub_qualifier="embedding-health"` → matched=True, source="domain_raw", matched_value="embedding-health".
2. `test_source_1_vocab_group_match` — `domain_raw="backend: observability"`, `sub_vocab_groups={"observability"}` → matched=True, source="domain_raw".
3. `test_source_1_vocab_term_match` — `domain_raw="backend: tracing"`, `sub_vocab_terms={"tracing", "monitoring"}` → matched=True.
4. `test_source_1_token_overlap_match` — `domain_raw="backend: cache-eviction"`, `sub_vocab_tokens={"cache"}` → matched=True (token-overlap on `cache`).
5. `test_source_2_intent_label_token_match` — `intent_label="Cache Eviction Policy Audit"`, `sub_vocab_tokens={"cache"}` → matched=True, source="intent_label".
6. `test_source_3_dynamic_keyword_match` — `raw_prompt="audit the asyncio race condition in our handler"`, `dynamic_keywords=[("asyncio", 0.9)]`, `sub_qualifier="asyncio"` → matched=True, source="tf_idf", matched_value="asyncio".
7. `test_all_sources_miss_returns_unmatched_with_reason` — totally unrelated input → matched=False, source=None, reason non-empty (e.g., "no source matched").

Also add a second class `TestSubDomainReevalUsesSharedPrimitive` with one
**integration** test that calls `engine._reevaluate_sub_domains` with a
known input and asserts the dissolution decision is the same as today's
behavior — proves byte-equivalence (AC-R4-2 + AC-R4-4).

Run:
```
cd backend && source .venv/bin/activate && pytest tests/taxonomy/test_sub_domain_readiness.py::TestMatchOptToSubDomainVocab -v
```

Expected: all 7 tests fail with `ImportError` or `AttributeError` (function doesn't exist yet).

The integration test in `TestSubDomainReevalUsesSharedPrimitive` should already pass since R4 hasn't started rewiring — it's a regression guard for after GREEN.

### GREEN subagent

**Task:** Add `SubDomainMatchResult` dataclass + `match_opt_to_sub_domain_vocab` function to `backend/app/services/taxonomy/sub_domain_readiness.py`. Mirror the v0.4.7 inline matching cascade VERBATIM — Source 1 (exact label, vocab group, vocab term, token overlap), Source 2 (intent_label tokens × sub_vocab_tokens), Source 2b (legacy substring scan against sub_keywords_legacy), Source 3 (dynamic-keyword min-hits with weight gating).

Then refactor `_reevaluate_sub_domains` in `backend/app/services/taxonomy/engine.py` to import and call the new primitive in place of the inline matching loop. The vocab construction (sub_vocab_groups / sub_vocab_terms / sub_vocab_tokens / sub_keywords_legacy / dynamic_keywords) stays in the engine — only the per-opt boolean cascade moves.

Verify:
```
pytest tests/taxonomy/test_sub_domain_readiness.py::TestMatchOptToSubDomainVocab -v
pytest tests/taxonomy/test_sub_domain_readiness.py::TestSubDomainReevalUsesSharedPrimitive -v
pytest tests/taxonomy/test_sub_domain_lifecycle.py -v 2>&1 | tail -10
```

Both new test classes pass. Existing `TestSubDomainConsistencyVocabGroupMatch`, `TestSubDomainBayesianShrinkage`, `TestSubDomainGracePeriod`, `TestSubDomainEmptySnapshotSkip` and dissolution tests pass unchanged (proves byte-equivalence).

### REFACTOR subagent

- **Integration fit:** confirm the imported primitive is used at exactly one site (engine.py); the function is exported but no other call sites yet.
- **Type:** mypy clean on `sub_domain_readiness.py` and `engine.py` (no new errors).
- **Lint:** ruff clean on touched files.
- **Test health:** full taxonomy slice ≥ 907 (= 900 + 7 new R4 tests + 1 integration test).
- **Docstring:** the function docstring mentions Source 2b legacy path explicitly; the `_reevaluate_sub_domains` docstring is updated to mention the extracted primitive.
- **CHANGELOG:** Add the R4 line under `## Unreleased` → `### Changed`.

### VALIDATION subagent

Independent fresh-context check:
1. New primitive exists at expected location, byte-equivalence test passes.
2. Full taxonomy slice green.
3. Full backend suite green (≥ 3115).
4. Lint+mypy clean on touched files.
5. Spec status row R4: PROPOSED → IMPLEMENTED.

---

## Cycle 5 — R5: Forensic dissolution telemetry

### RED subagent

**Task:** Add `TestSubDomainForensicTelemetry` class to `test_sub_domain_lifecycle.py` (after `TestSubDomainEmptySnapshotSkip`). Five tests covering AC-R5-1..5:

1. `test_dissolution_event_carries_sample_failures` — sub-domain dissolves with N=20, all unmatched; capture event ring buffer; assert `context.sample_match_failures` length ≤ 3, each entry has `cluster_id`/`domain_raw`/`intent_label`/`reason`.
2. `test_sample_failures_exclude_matched_opts` — half match, half don't; assert all entries in `sample_match_failures` correspond to non-matching cluster_ids (cross-check by collecting expected non-match cluster_ids).
3. `test_long_text_truncated` — synthetic intent_label of 200 chars; assert event field `intent_label` ≤ 80 chars.
4. `test_matching_members_matches_engine_count` — 13 members, 7 match, dissolution does NOT fire (R1 shrinkage); assert `sub_domain_reevaluated` event has `matching_members=7`.
5. `test_all_match_emits_empty_failures` — N=20, all match; assert `sample_match_failures=[]` and `matching_members=20`.

Run:
```
pytest tests/taxonomy/test_sub_domain_lifecycle.py::TestSubDomainForensicTelemetry -v
```

Expected: all 5 fail (telemetry keys don't exist yet).

### GREEN subagent

1. Add 2 constants to `_constants.py`:
   ```python
   SUB_DOMAIN_FAILURE_SAMPLES: int = 3
   SUB_DOMAIN_FAILURE_FIELD_TRUNCATE: int = 80
   ```

2. In `engine.py::_reevaluate_sub_domains`:
   - Modify the SQL `select(...)` to also return `Optimization.cluster_id` so we have it for sample reporting.
   - After the matching loop, build the failure samples:
     ```python
     sample_failures = [
         {
             "cluster_id": str(cid)[:SUB_DOMAIN_FAILURE_FIELD_TRUNCATE],
             "domain_raw": (raw or "")[:SUB_DOMAIN_FAILURE_FIELD_TRUNCATE] or None,
             "intent_label": (intent or "")[:SUB_DOMAIN_FAILURE_FIELD_TRUNCATE] or None,
             "reason": result.reason,
         }
         for (raw, intent, _prompt, cid), result in zip(opt_rows_with_cid, match_results)
         if not result.matched
     ][:SUB_DOMAIN_FAILURE_SAMPLES]
     ```
   - Add `matching_members=matching` and `sample_match_failures=sample_failures` to BOTH the `sub_domain_reevaluated` and `sub_domain_dissolved` event contexts.

Run the new tests; all 5 must pass.

### REFACTOR subagent

- Run the **full taxonomy slice**. Total now ≥ 912 (= 907 + 5 R5 tests).
- Lint + mypy.
- CHANGELOG: append the R5 line under `### Added`.
- Spec doc status row: PROPOSED → IMPLEMENTED.
- Docstring: `_reevaluate_sub_domains` step 4 now mentions the forensic samples.

### VALIDATION subagent

Same protocol. Output PASS / FAIL.

---

## Cycle 6 — R6: Rebuild-sub-domains recovery endpoint

### RED subagent

**Task:** Two test files — engine-level + router-level.

#### Engine tests in `tests/taxonomy/test_sub_domain_lifecycle.py` — `TestRebuildSubDomainsService` class

1. `test_rebuild_default_threshold_matches_discovery` — call `engine.rebuild_sub_domains(db, domain_id)` (no override); confirm `threshold_used` matches `max(0.40, 0.60 - 0.004*N)`.
2. `test_rebuild_with_override_uses_override` — call with `min_consistency_override=0.30`; confirm `threshold_used == 0.30`.
3. `test_rebuild_idempotent_skips_existing` — set up domain with 1 existing sub-domain; rebuild returns `skipped_existing=[<that label>]` and `created=[]`.
4. `test_rebuild_creates_new_sub_domain_below_default_threshold` — set up domain where one qualifier has consistency=0.32 (between 0.30 override and 0.40 default); rebuild with override creates it.
5. `test_rebuild_dry_run_no_creation` — same scenario as 4, but `dry_run=True`; assert no sub-domain created in DB; `proposed` non-empty.
6. `test_rebuild_emits_taxonomy_changed_when_creating` — assert `event_bus` published `taxonomy_changed` after a creating rebuild.
7. `test_rebuild_no_taxonomy_changed_on_zero_creates` — idempotent re-run; assert no `taxonomy_changed` event.
8. `test_rebuild_emits_telemetry_non_dry` — `sub_domain_rebuild_invoked` event with `dry_run=False`, `created_count`, `skipped_existing_count`, `threshold_used` all populated.
9. `test_rebuild_emits_telemetry_dry_run` — same but with `dry_run=True`.
10. `test_rebuild_rejects_below_floor_runtime_check` — call engine method directly with `min_consistency_override=0.10`; expect `ValueError` (runtime defense-in-depth).
11. `test_rebuild_rolls_back_on_partial_failure` — inject a failure inside the loop (mock `_create_sub_domain` to raise on the 2nd sub-domain); assert NEITHER sub-domain persisted (single-transaction semantics).

#### Router tests in `tests/routers/test_domains.py` — `TestRebuildSubDomainsEndpoint` class

12. `test_rebuild_endpoint_404_unknown_domain` — POST to `/api/domains/{nonexistent}/rebuild-sub-domains` → 404.
13. `test_rebuild_endpoint_422_non_domain_node` — POST to a cluster with `state="active"` → 422 with `must be a domain`.
14. `test_rebuild_endpoint_422_min_consistency_above_range` — POST with `min_consistency=2.0` → 422 (Pydantic).
15. `test_rebuild_endpoint_422_min_consistency_below_floor` — POST with `min_consistency=0.10` → 422 (Pydantic ge=0.25).
16. `test_rebuild_endpoint_200_dry_run` — POST `dry_run=true` → 200 with `RebuildSubDomainsResult` shape, no DB mutation.
17. `test_rebuild_endpoint_200_idempotent_re_run` — POST twice in succession; second response shows all in `skipped_existing`.

Run:
```
pytest tests/taxonomy/test_sub_domain_lifecycle.py::TestRebuildSubDomainsService tests/routers/test_domains.py::TestRebuildSubDomainsEndpoint -v
```

Expected: all 17 fail (the engine method and the router don't exist).

Note: if `tests/routers/test_domains.py` doesn't exist yet, create it with the standard FastAPI `TestClient` boilerplate from a similar router test (look at any existing `test_*_router.py`).

### GREEN subagent

**Task:** Three changes:

1. **Schemas** — `backend/app/schemas/domains.py`: add `RebuildSubDomainsRequest` and `RebuildSubDomainsResult` per spec.

2. **Service method** — `backend/app/services/taxonomy/engine.py`: add `rebuild_sub_domains` method following spec. Reuse `compute_qualifier_cascade` for the qualifier scan; re-use the existing sub-domain creation helper that `_propose_sub_domains` calls (`_create_sub_domain` or whatever it's named — locate via grep). Single transaction, runtime floor check, telemetry event for both dry and non-dry, `event_bus.publish("taxonomy_changed", ...)` only when `created` is non-empty AND non-dry.

3. **Router** — `backend/app/routers/domains.py`: add `POST /{domain_id}/rebuild-sub-domains` handler with the standard 404/422/503 envelope. Rate limit `10/minute`.

Run all 17 tests. All must pass.

### REFACTOR subagent

- Full taxonomy + routers slice. Total ≥ 929 (= 912 + 11 engine + 6 router).
- Full backend suite green (≥ 3115 + 17 = 3132).
- Lint + mypy on touched files.
- Update root `CLAUDE.md` MCP tool / router table — actually this isn't an MCP tool, just a REST endpoint, so skip unless the routers table mentions specific endpoints (it does — see `clusters.py` row). Append `/{domain_id}/rebuild-sub-domains` to the `domains.py` row.
- CHANGELOG: append R6 line under `### Added`.
- Spec doc status row: PROPOSED → IMPLEMENTED.

### VALIDATION subagent

Same protocol.

---

## Final integration validation

After R4+R5+R6 cycles all VALIDATION PASS:

1. **Full backend suite:** `pytest -v 2>&1 | tail -15`. Total ≥ 3132.

2. **E2E cycle:** Add `cycle-13-r4-r6-validation` to `scripts/validate_taxonomy_emergence.py` PROMPT_SETS — 3 prompts diverse across backend/database/devops. Run:
   ```
   python3 scripts/validate_taxonomy_emergence.py cycle-13-r4-r6-validation > /tmp/cycle13.log 2>&1
   ```
   Wait for completion. Confirm:
   - Cycle ran clean.
   - Score health stable.
   - No spurious dissolution events.
   - If any `sub_domain_reevaluated` event fires, it carries `matching_members` + `sample_match_failures` + `shrunk_consistency_pct` (R5 + R1 keys both present).

3. **Manual rebuild call against live backend:**
   ```
   BACKEND_DOMAIN_ID=$(curl -s http://127.0.0.1:8000/api/domains | python3 -c "import json,sys; print([d['id'] for d in json.load(sys.stdin) if d['label']=='backend'][0])")
   curl -s -X POST "http://127.0.0.1:8000/api/domains/$BACKEND_DOMAIN_ID/rebuild-sub-domains" \
        -H "Content-Type: application/json" \
        -d '{"min_consistency": 0.30, "dry_run": true}' | python3 -m json.tool
   ```
   Confirm response shape matches `RebuildSubDomainsResult` with `dry_run=true`. If `proposed` is non-empty, optionally re-run with `dry_run=false` to actually create the sub-domain(s).

4. **Lint sweep:** `ruff check app/services/taxonomy/ app/routers/domains.py app/schemas/domains.py tests/taxonomy/ tests/routers/`.

5. **Doc updates:**
   - `docs/specs/sub-domain-dissolution-hardening-r4-r6.md` — Status table all SHIPPED with validation evidence.
   - `docs/audits/sub-domain-regression-2026-04-27.md` — Resolution status table R4/R5/R6 → SHIPPED.

---

## Risk register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| R4 byte-equivalence drift | Low | High | AC-R4-4 + the existing 4 dissolution tests + `TestSubDomainReevalUsesSharedPrimitive` integration test |
| R5 telemetry log bloat | Low | Low | Cap at 3 samples × 80 chars = 240 chars max additional payload per event |
| R6 immediate-dissolution loop (`min_consistency=0.25`) | Medium | Medium | Pydantic `ge=0.25` floor + runtime defense-in-depth + AC-R6-4 |
| R6 partial-state on creation failure | Medium | High | Single-transaction semantics + AC-R6-10 rollback test |
| R6 stale readiness cache | Low | Low | Spec explicit `taxonomy_changed` publish on creation + AC-R6-8/AC-R6-9 |

---

## Subagent dispatch protocol

Each subagent receives its phase task verbatim (RED / GREEN / REFACTOR /
VALIDATION sections above) plus this preamble:

> You are a fresh-context subagent executing one TDD phase. Read the spec
> (`docs/specs/sub-domain-dissolution-hardening-r4-r6.md`) and this plan
> before acting. Stay strictly in scope. Output a one-paragraph summary
> of what you did, file paths touched, and command output that proves
> your phase succeeded.
