# Implementation Plan: Sub-Domain Dissolution Hardening

**Spec:** `docs/specs/sub-domain-dissolution-hardening-2026-04-27.md`
**Audit:** `docs/audits/sub-domain-regression-2026-04-27.md`
**Date:** 2026-04-27

---

## Workflow

Three independent TDD cycles, each running:

```
RED → GREEN → REFACTOR → VALIDATION
```

Each phase is dispatched as an independent fresh-context subagent.
Cycles run **sequentially** (not parallel) because all three modify
`_reevaluate_sub_domains` in `backend/app/services/taxonomy/engine.py` —
parallel execution would create merge conflicts.

After all three cycles complete, run a final integration validation
(full backend suite + e2e cycle).

---

## Cycle 1 — R1: Bayesian shrinkage

### RED subagent

**Task:** Write the four failing tests for AC-R1-1..4 in
`backend/tests/taxonomy/test_sub_domain_lifecycle.py` as a new class
`TestSubDomainBayesianShrinkage`. Insert the class **after**
`TestSubDomainConsistencyVocabGroupMatch` (around line 1473). Use the
existing helpers `_make_engine`, `_make_domain`, `_make_sub_domain`,
`_make_cluster`, `_make_opt`. Each test must:

- Set up sub-domain aged ≥ `SUB_DOMAIN_DISSOLUTION_MIN_AGE_HOURS + 5` (24+5=29h post-R2; the test should compute relative to the constant, not hardcode).
- Configure cluster_metadata with `generated_qualifiers` so R3's empty-snapshot guard does NOT short-circuit the test.
- Call `engine._reevaluate_sub_domains(db, domain, existing_labels)`.
- Assert dissolution / non-dissolution per AC.

The four tests:
1. `test_small_n_one_match_keeps_via_shrinkage` — N=5, 1 match → shrunk≈0.333, NOT dissolved
2. `test_small_n_zero_match_keeps_via_shrinkage` — N=5, 0 matches → shrunk≈0.267, NOT dissolved
3. `test_large_n_zero_match_still_dissolves` — N=20, 0 matches → shrunk≈0.133, IS dissolved
4. `test_dissolution_event_carries_both_consistency_metrics` — capture event via `get_event_logger()` ring buffer; assert `consistency_pct` AND `shrunk_consistency_pct` both present

Subagent must run the new tests and confirm they fail with the expected reasons (consistency=raw value, no shrunk_consistency_pct in event). Output the actual failure messages.

### GREEN subagent

**Task:** Make the RED tests pass with the **minimum** code. Two files:

1. `backend/app/services/taxonomy/_constants.py` — add two constants after line 154:
   ```python
   # Bayesian Beta-Binomial prior on consistency (R1, audit 2026-04-27).
   # Equivalent to K=10 prior observations; α/β = K*center / K*(1-center).
   # Pulls strongly at small N, fades at N≥30. Center aligned with
   # SUB_DOMAIN_QUALIFIER_CONSISTENCY_LOW (0.40) — "absent evidence,
   # assume the sub-domain is at the lower bound of healthy."
   SUB_DOMAIN_DISSOLUTION_PRIOR_STRENGTH: int = 10
   SUB_DOMAIN_DISSOLUTION_PRIOR_CENTER: float = 0.40
   ```

2. `backend/app/services/taxonomy/engine.py`:
   - Update import block (line 3058) to include the two new constants
   - After `consistency = matching / total_opts` (line 3283), compute:
     ```python
     k_prior = SUB_DOMAIN_DISSOLUTION_PRIOR_STRENGTH
     alpha_prior = k_prior * SUB_DOMAIN_DISSOLUTION_PRIOR_CENTER
     shrunk_consistency = (matching + alpha_prior) / (total_opts + k_prior)
     ```
   - Replace `if consistency >= SUB_DOMAIN_DISSOLUTION_CONSISTENCY_FLOOR:` (line 3310) with `if shrunk_consistency >= SUB_DOMAIN_DISSOLUTION_CONSISTENCY_FLOOR:`
   - Update both `sub_domain_reevaluated` (line 3293) and `sub_domain_dissolved` (line 3334) event contexts: add `"shrunk_consistency_pct": round(shrunk_consistency * 100, 1)` and `"prior_strength": k_prior` keys
   - Update the `passed` field in `sub_domain_reevaluated` to use `shrunk_consistency`
   - Update the `logger.info` line near 3324 to log both metrics

Run the four new tests. They must pass. Output the test results.

### REFACTOR subagent

**Task:** Make the cycle's code production-ready. Per the TDD protocol memory, REFACTOR's job is "make this cycle's code shipped is production-ready" — apply judgment across the heuristics:

- **Integration fit:** any other place in the codebase that computes consistency for sub-domains? grep `consistency = matching` and similar; if found, decide whether shrinkage applies.
- **Code patterns:** docstring on the new computation explaining the prior, why the center is 0.40, and citing the audit doc; type-annotate any new local variables.
- **Test health:** run the FULL `backend/tests/taxonomy/` slice (not just the new test). Report any flaky/regression tests.
- **Lint:** `ruff check app/services/taxonomy/engine.py app/services/taxonomy/_constants.py tests/taxonomy/test_sub_domain_lifecycle.py` clean.
- **Type:** `mypy app/services/taxonomy/engine.py app/services/taxonomy/_constants.py` clean (or document any pre-existing ignores).
- **Docstrings:** the docstring of `_reevaluate_sub_domains` (line 3036) lists its 4 steps — append a note about Bayesian shrinkage in step 3.
- **CHANGELOG:** Add the line under `## Unreleased` → `### Fixed`. Don't bump version.

Output: list of files touched, the diff summary, and confirmation that all REFACTOR-phase commands ran clean.

### VALIDATION subagent

**Task:** Independent fresh-context check that R1 ships clean.

1. Confirm RED tests still pass after REFACTOR (no scope creep changed behavior).
2. Run `cd backend && pytest tests/taxonomy/ -v` — report counts (pass/fail/skip).
3. Run `cd backend && ruff check app/services/taxonomy/ tests/taxonomy/`.
4. Spot-check one `sub_domain_dissolved` JSONL event (most-recent line in `data/taxonomy_events/`) — confirm reading code can ignore unknown fields (it should). Report any concern.
5. Output: VALIDATION PASS / VALIDATION FAIL with one-line reason.

---

## Cycle 2 — R2: 24h grace period

### RED subagent

**Task:** Add `TestSubDomainGracePeriod` class to `test_sub_domain_lifecycle.py`. Two tests:

1. `test_age_below_24h_blocks_dissolution` — sub-domain aged 12h with hostile members (0% consistency raw) → NOT dissolved (gate skips).
2. `test_age_above_24h_proceeds_to_evaluation` — same sub-domain aged 25h → dissolution attempted (verifiable via `sub_domain_reevaluated` event presence; whether it actually dissolves depends on R1's shrinkage but the gate must NOT skip).

Pre-cycle (R2 not yet merged), test 1 fails because the existing 6h gate lets the dissolution fire at 12h. Test 2 should pass already (25h > 6h gate) — it's a guard against future regression.

Run the failing test, output the message.

### GREEN subagent

**Task:** Single-constant change in `_constants.py`:

```python
SUB_DOMAIN_DISSOLUTION_MIN_AGE_HOURS: int = 24  # was: 6 (R2, audit 2026-04-27)
```

Inline comment on the change explains the rationale ("both observed dissolutions fired at 6h+; bumped to 24h to give one full daily cycle of bootstrap volatility"). Run both new tests; both must pass. Output test results.

### REFACTOR subagent

**Task:**

- Grep for any other code or test that asserts `SUB_DOMAIN_DISSOLUTION_MIN_AGE_HOURS == 6` or hardcodes literal 6 in the sub-domain context. Audit + report.
- Update any docstring or comment in engine.py that mentions "6 hours" in the sub-domain dissolution context.
- Run full `backend/tests/taxonomy/` slice; report regressions.
- Confirm no test in 6h..24h band silently flips. (The verification subagent already confirmed this; REFACTOR re-verifies after seeing actual test output.)
- Update CHANGELOG entry to mention the grace period bump (or fold into the R1 line if a single line suffices).

### VALIDATION subagent

Same protocol as Cycle 1. Output VALIDATION PASS / FAIL.

---

## Cycle 3 — R3: Empty-snapshot guardrail

### RED subagent

**Task:** Add `TestSubDomainEmptySnapshotSkip` class. Two tests:

1. `test_empty_snapshot_skips_dissolution` — sub-domain aged > 24h with `cluster_metadata.generated_qualifiers = None` (or absent) and members carrying mismatched qualifiers; pre-fix would dissolve via fall-through to exact-equality (matching=0). Expected: NOT dissolved.
2. `test_empty_snapshot_emits_skip_event` — same scenario; capture events via `get_event_logger()` ring buffer; assert `decision == "sub_domain_reevaluation_skipped"` and `context.reason == "empty_vocab_snapshot"`.

Run, confirm RED, output messages.

### GREEN subagent

**Task:** Insert the skip block in `engine.py` between snapshot load (line 3169) and matching loop (line 3193):

```python
# R3 (audit 2026-04-27): when generated_qualifiers is empty, the sub_vocab_*
# sets are empty and matching falls back to v0.4.6 exact-equality behavior
# — guaranteed 0% consistency on healthy sub-domains. Skip rather than
# fail-open. Emit skip event for operator visibility.
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

Run both new tests; both must pass. Confirm the existing
`TestSubDomainConsistencyVocabGroupMatch` and
`test_unrelated_qualifiers_still_dissolve` tests still pass (control:
non-empty snapshot proceeds normally).

### REFACTOR subagent

**Task:**

- Verify no other path in `_reevaluate_sub_domains` reaches the matching loop with empty `sub_generated_qualifiers` post-fix.
- Confirm the new event decision name `sub_domain_reevaluation_skipped` is documented somewhere consumers might look (e.g., add a one-line entry to wherever the existing event names are listed — `services/taxonomy/event_logger.py` or a comment near `decision_types`).
- Lint + mypy on touched files.
- Full taxonomy test slice.
- Append CHANGELOG note (single line OK if R1+R2 already mention "hardened sub-domain dissolution").

### VALIDATION subagent

Same protocol. Output VALIDATION PASS / FAIL.

---

## Final integration validation

After all three cycles report VALIDATION PASS:

1. **Full backend test suite:** `cd backend && pytest -v 2>&1 | tail -50`. Report counts.
2. **E2E cycle:** Add a new entry `cycle-12-dissolution-hardening` to `scripts/validate_taxonomy_emergence.py` `PROMPT_SETS` (3 prompts spread across backend domains). Run:
   ```
   python3 scripts/validate_taxonomy_emergence.py cycle-12-dissolution-hardening > /tmp/cycle12.log 2>&1
   ```
   Wait for completion (no run_in_background). Inspect `/tmp/cycle12.log`. Confirm:
   - No `sub_domain_dissolved` events for sub-domains aged < 24h
   - No regression in score_health (`mean ≈ 7.0–7.5` band; current baseline is 8.13 ± 0.75 — should not get worse)
   - Pre/post snapshots in `data/validation/cycle-12-*.json` show no spurious sub-domain churn

3. **Lint sweep:** `cd backend && ruff check . && mypy app/services/taxonomy/`.
4. **Update docs:**
   - `docs/specs/sub-domain-dissolution-hardening-2026-04-27.md` — change `Status` table rows from PROPOSED to SHIPPED with commit refs.
   - `docs/audits/sub-domain-regression-2026-04-27.md` — append a new `## Resolution status` section noting R1+R2+R3 shipped, validation cycle reference, and what remains (R4+).

5. **Single commit** capturing all three cycles + doc updates. Commit message:
   ```
   fix(taxonomy): harden sub-domain dissolution against small-N noise + premature re-evaluation + empty-snapshot fail-open

   R1: Bayesian Beta(α=4, β=6) shrinkage on consistency metric
   R2: SUB_DOMAIN_DISSOLUTION_MIN_AGE_HOURS 6 → 24
   R3: skip dissolution when generated_qualifiers snapshot is empty

   Audit: docs/audits/sub-domain-regression-2026-04-27.md
   Spec:  docs/specs/sub-domain-dissolution-hardening-2026-04-27.md
   ```

---

## Risk register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| R1 prior over-protects, dissolution becomes unreachable | Low | Medium | AC-R1-3 explicitly tests N=20/0-match dissolves. Behavior table validated by verifier. |
| R2 lets stale sub-domains linger past their utility | Low | Low | Sub-domain still re-evaluated every cycle; just gets one daily cycle of grace. |
| R3 hides genuine vocab-gen failures | Medium | Medium | Skip event is logged with reason. Operators can grep `decision="sub_domain_reevaluation_skipped"` to detect chronic empty-snapshot states. Worth adding a future health-endpoint counter (R7-adjacent). |
| Three changes interact unexpectedly | Medium | High | Cycles run sequentially; each VALIDATION subagent runs the full test slice; final integration validation runs e2e cycle. |
| Auto-recovery: dissolved sub-domains do not re-emerge | High (already true) | Low | Out of scope (R6 — operator endpoint). The hardening prevents future losses; recovery is separate work. |

---

## Subagent dispatch protocol

Each subagent receives its task verbatim (RED / GREEN / REFACTOR /
VALIDATION sections above) plus this preamble:

> You are a fresh-context subagent executing one TDD phase. Read the spec
> (`docs/specs/sub-domain-dissolution-hardening-2026-04-27.md`) and the
> plan (this document) before acting. Stay strictly in scope. Output a
> one-paragraph summary of what you did, file paths touched, and any
> command output that proves your phase succeeded.
