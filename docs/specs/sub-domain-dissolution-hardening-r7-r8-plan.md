# Implementation Plan: R7 + R8

**Spec:** `docs/specs/sub-domain-dissolution-hardening-r7-r8.md` (PASS gating, minor #2 applied)
**Audit:** `docs/audits/sub-domain-regression-2026-04-27.md`
**Predecessors shipped:** R1+R2+R3 (2026-04-27 04:49 UTC), R4+R5+R6 (2026-04-27 06:53 UTC)

---

## Workflow

Two sequential TDD cycles. Each phase is dispatched as an independent
fresh-context subagent: RED → GREEN → REFACTOR → VALIDATION. Cycles
run **sequentially** since both touch the same module group.

---

## Cycle 7 — R7: Vocab regeneration overlap telemetry

### RED subagent

**Task:** Add `TestVocabRegenOverlap` class to
`backend/tests/taxonomy/test_sub_domain_lifecycle.py` (after the existing
test classes; locate the right insertion slot — likely near the end). Five
tests covering AC-R7-1..5.

The tests exercise the engine path through `_propose_sub_domains` (with
`vocab_only=True` to focus on vocab generation) and capture the
`vocab_generated_enriched` event from the `TaxonomyEventLogger` ring buffer.
Use `unittest.mock.patch` on `app.services.taxonomy.engine.generate_qualifier_vocabulary`
to control the returned `generated` dict. Use the existing pattern from
`TestSubDomainBayesianShrinkage::test_dissolution_event_carries_both_consistency_metrics`
for ring-buffer access.

Test 1 — `test_bootstrap_no_previous_groups`:
- Set up domain WITHOUT `generated_qualifiers` in metadata (bootstrap)
- Mock `generate_qualifier_vocabulary` to return `{"a": ["x"], "b": ["y"]}`
- Run engine `_propose_sub_domains(vocab_only=True)`
- Find `vocab_generated_enriched` event
- Assert `context["previous_groups"] == []`
- Assert `context["new_groups"] == ["a", "b"]` (sorted)
- Assert `context["overlap_pct"] == 0.0`

Test 2 — `test_full_overlap`:
- Set up domain WITH `generated_qualifiers={"a":["x"], "b":["y"]}`
- Mock vocab gen to return `{"a":["x2"], "b":["y2"]}` (same group names, different terms)
- Assert `context["overlap_pct"] == 100.0`

Test 3 — `test_zero_overlap_warns` (the audit-incident reproducer):
- Set up domain WITH `generated_qualifiers={"metrics":[], "tracing":[], "pattern-instrumentation":[]}`
- Mock vocab gen to return `{"concurrency":[], "observability":[], "embeddings":[], "security":[]}`
- Capture log output (use `pytest`'s `caplog` fixture)
- Assert `context["overlap_pct"] == 0.0`
- Assert a `WARNING` log line was emitted whose message contains `low overlap`

Test 4 — `test_partial_overlap_jaccard_math`:
- prev=`{"a":[],"b":[]}`, new=`{"b":[],"c":[]}` → intersect={b}, union={a,b,c}
- Assert `context["overlap_pct"] == 33.3` (rounded to 1 decimal)

Test 5 — `test_warning_suppressed_on_bootstrap_and_high_overlap`:
- Bootstrap case (no previous): assert NO WARNING
- 80% overlap case (prev={a,b,c,d}, new={a,b,c,e}): assert NO WARNING (since 80 >= 50)

Run:
```
cd backend && source .venv/bin/activate
pytest tests/taxonomy/test_sub_domain_lifecycle.py::TestVocabRegenOverlap -v
```

All 5 must FAIL with `KeyError` on missing `previous_groups` / `new_groups` / `overlap_pct` keys, OR with assertion failures on the missing WARNING log.

### GREEN subagent

**Task:** Modify `engine.py` lines ~2335-2358 (the
`vocab_generated_enriched` event emission block in `_propose_sub_domains`).

Insert overlap computation BEFORE the `try: get_event_logger().log_decision(...)` block:

```python
# R7 (audit 2026-04-27): vocab regeneration overlap telemetry —
# Jaccard intersection-over-union between previous and new group names.
prev_set = {g.lower() for g in (_existing_groups or [])}
new_set = {g.lower() for g in generated.keys()}
if prev_set or new_set:
    intersect = prev_set & new_set
    union = prev_set | new_set
    overlap_pct = round(100.0 * len(intersect) / len(union), 1) if union else 0.0
else:
    overlap_pct = 0.0
prev_groups_sorted = sorted(prev_set)
new_groups_sorted = sorted(new_set)

# WARNING when regen has low overlap — sub-domains anchored to the
# previous vocab may dissolve on next Phase 5 (one-shot per regen).
if prev_set and overlap_pct < 50.0:
    logger.warning(
        "Vocab regen low overlap for '%s': overlap=%.1f%% "
        "previous=%s new=%s — sub-domains anchored to the previous "
        "vocab may dissolve on next Phase 5 (audit R7)",
        domain_node.label, overlap_pct,
        prev_groups_sorted, new_groups_sorted,
    )
```

Then add 3 keys to the existing event context:

```python
"previous_groups": prev_groups_sorted,
"new_groups": new_groups_sorted,
"overlap_pct": overlap_pct,
```

Run the new tests; all 5 must pass. Sanity-check that all R1-R6 tests still pass.

### REFACTOR subagent

- Lint + mypy on touched files.
- Full taxonomy slice ≥ 958 (= 953 from R4-R6 baseline + 5 new R7 tests).
- Update `_propose_sub_domains` docstring step describing vocab-gen telemetry to mention the new keys.
- CHANGELOG: append the R7 line under `## Unreleased` → `### Added`.
- Spec doc R7 status: PROPOSED → IMPLEMENTED.

### VALIDATION subagent

Independent fresh-context check of constants, event keys, log behavior.

---

## Cycle 8 — R8: Threshold-collision invariant

### RED subagent

**Task:** Create a new test file
`backend/tests/taxonomy/test_constants_invariants.py` with class
`TestThresholdCollisionInvariant`. Three tests covering AC-R8-1..3.

```python
"""R8 (audit 2026-04-27): threshold-collision invariant tests."""

import importlib
import pytest

import app.services.taxonomy._constants as _c


class TestThresholdCollisionInvariant:

    def test_default_constants_satisfy_invariant(self):
        """AC-R8-1: current LOW=0.40 > FLOOR=0.25 — module imports clean."""
        assert _c.SUB_DOMAIN_QUALIFIER_CONSISTENCY_LOW > _c.SUB_DOMAIN_DISSOLUTION_CONSISTENCY_FLOOR

    def test_invariant_fires_on_violation(self, monkeypatch):
        """AC-R8-2: setting LOW below FLOOR + reload triggers AssertionError."""
        original = _c.SUB_DOMAIN_QUALIFIER_CONSISTENCY_LOW
        try:
            monkeypatch.setattr(
                _c, "SUB_DOMAIN_QUALIFIER_CONSISTENCY_LOW", 0.20,
            )
            with pytest.raises(AssertionError, match="Threshold collision"):
                importlib.reload(_c)
        finally:
            # Restore via reload (monkeypatch already cleared its patch on
            # context exit, but the reload above may have re-imported
            # without our patch — explicit reload returns to source values).
            importlib.reload(_c)
            assert _c.SUB_DOMAIN_QUALIFIER_CONSISTENCY_LOW == original

    def test_invariant_rejects_equality(self, monkeypatch):
        """AC-R8-3: LOW == FLOOR is also rejected (strict >, not >=)."""
        try:
            monkeypatch.setattr(
                _c, "SUB_DOMAIN_QUALIFIER_CONSISTENCY_LOW",
                _c.SUB_DOMAIN_DISSOLUTION_CONSISTENCY_FLOOR,
            )
            with pytest.raises(AssertionError):
                importlib.reload(_c)
        finally:
            importlib.reload(_c)
```

Run:
```
pytest tests/taxonomy/test_constants_invariants.py -v
```

Test 1 must PASS already (current constants are valid). Tests 2 and 3 must FAIL because the module-level assert doesn't yet exist — `importlib.reload` will succeed silently regardless of patched attributes.

### GREEN subagent

**Task:** Add the module-level assertion to `_constants.py` immediately after `SUB_DOMAIN_DISSOLUTION_CONSISTENCY_FLOOR` is defined (line ~154):

```python
SUB_DOMAIN_DISSOLUTION_CONSISTENCY_FLOOR: float = 0.25

# R8 (audit 2026-04-27): threshold-collision invariant — creation lower
# bound must always exceed dissolution floor.  Otherwise sub-domains can
# enter the unrecoverable degenerate state of being uncreatable AND
# dissolvable at the same consistency value.  Module-level fail-fast.
assert (
    SUB_DOMAIN_QUALIFIER_CONSISTENCY_LOW > SUB_DOMAIN_DISSOLUTION_CONSISTENCY_FLOOR
), (
    f"Threshold collision: SUB_DOMAIN_QUALIFIER_CONSISTENCY_LOW "
    f"({SUB_DOMAIN_QUALIFIER_CONSISTENCY_LOW}) must exceed "
    f"SUB_DOMAIN_DISSOLUTION_CONSISTENCY_FLOOR "
    f"({SUB_DOMAIN_DISSOLUTION_CONSISTENCY_FLOOR}). See audit R8."
)
```

Run the 3 R8 tests + a sanity-check of one taxonomy file:
```
pytest tests/taxonomy/test_constants_invariants.py -v
pytest tests/taxonomy/test_sub_domain_lifecycle.py -k 'BayesianShrinkage' -v
```

All R8 tests pass. R1-R6 tests still pass.

### REFACTOR subagent

- Lint + mypy.
- Full taxonomy slice ≥ 961 (= 958 + 3 R8 tests).
- Full backend suite ≥ 3145.
- CHANGELOG: append the R8 line under `## Unreleased` → `### Changed`.
- Spec doc R8 status: PROPOSED → IMPLEMENTED.

### VALIDATION subagent

Same protocol.

---

## Final integration validation (cycle-14)

After R7+R8 cycles report VALIDATION PASS:

1. **Full backend suite:** ≥ 3145.
2. **E2E cycle:** Add `cycle-14-r7-r8-validation` entry to
   `scripts/validate_taxonomy_emergence.py` PROMPT_SETS — 3 prompts
   designed to potentially trigger vocab regen on the backend domain
   (e.g., topical drift prompts that introduce new tech vocab). Run, wait,
   inspect events for any `vocab_generated_enriched` with new R7 keys.
3. **Lint sweep:** clean on all touched files.
4. **Live invariant check:** `python3 -c "import sys; sys.path.insert(0,'backend'); from app.services.taxonomy._constants import SUB_DOMAIN_QUALIFIER_CONSISTENCY_LOW, SUB_DOMAIN_DISSOLUTION_CONSISTENCY_FLOOR; print('R8 invariant holds:', SUB_DOMAIN_QUALIFIER_CONSISTENCY_LOW > SUB_DOMAIN_DISSOLUTION_CONSISTENCY_FLOOR)"`
5. **Doc updates:**
   - Audit doc R7+R8 → SHIPPED.
   - This spec doc R7+R8 → SHIPPED with validation evidence.

---

## Comprehensive PR-wide validation (R1-R8 systematic)

After cycle-14 completes, dispatch a long-running fresh-context validation
subagent that systematically verifies ALL 8 recommendations per the
methodology in memory `reference_e2e_validation_workflow.md`:

For each Ri (i ∈ 1..8):
- **Code:** specific file:line citations of the implementation (constants,
  function additions, event-context keys, route registration, etc.).
- **Tests:** the named test class is present and all tests pass.
- **CHANGELOG:** the corresponding line exists under `## Unreleased`.
- **Spec status:** marked SHIPPED in the relevant spec doc.
- **Live behavior (where observable):** event ring buffer or DB rows
  carry the expected shape.

Aggregate report: pass/fail per Ri, then overall PASS/FAIL for the PR.

---

## Risk register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| R7 WARNING log spam | Low | Low | Bounded by `_propose_sub_domains` per-domain cycle; only fires on `<50%` overlap regen (signal, not noise) |
| R8 module-level assert breaks test imports | Very Low | High | Verifier confirmed no test patches constants to violating values; assertion fires only at import-time on bad config |
| R7 event-key collision | None | — | Verifier confirmed zero collisions |
| Comprehensive validation reveals undetected regression | Low | Medium | Long-running subagent catches it; existing 3145-test suite plus this PR-wide systematic check provides defense-in-depth |
