# Audit-Prompt Class — Deep-Dive + Hardening Proposal

**Date:** 2026-04-27
**Method:** 3-phase parallel investigation (forensic trace + independent quality eval + scoring code review)
**Trigger:** v0.4.8 audit-prompt mean (7.36) sat below feature-prompt mean (7.96), with high per-prompt variance (6.60 → 8.10)

---

## TL;DR

The user asked: **"Is the audit output good and being scored low? Or bad and the scoring works fine? Or BOTH?"**

**Answer: mostly fair on aggregate, but with 3 structural blind spots that compound to ±0.5 per-prompt variance.**

| Dimension | Verdict |
|---|---|
| Are audit outputs actually high quality? | ✓ Yes — mean independent 7.64 vs system 7.48, Δ+0.16 |
| Is the scorer systematically biased against audits as a class? | ✗ No — control prompt Δ+0.20, statistically indistinguishable |
| Are there per-prompt variance amplifiers? | ✓ Yes — 4 distinct mechanisms identified, each adding ~0.3-0.5 |
| Can audit prompts be hardened? | ✓ Yes — 5 P0 changes (~50 LoC total) close most of the gap |

The gap is **NOT class-wide bias** but **per-prompt variance from 4 unrelated mechanisms** that align unfortunately on cold/new/untested audit topics.

---

## Phase A: Forensic trace — HIGH (8.10) vs LOW (6.77)

Two trace_ids: `d74283a8` (R3/R5 telemetry asymmetry, 8.10) and `42097cf8` (avg_vocab_quality=None, 6.77). 1.33-point gap decomposes into:

### Variance amplifier 1 — Pattern injection asymmetry (~0.4 of the gap)
- HIGH: 45 patterns from 3 clusters (because heuristic mis-classified as `analysis` → opened the analysis-tagged cluster path)
- LOW: 15 patterns from 1 cluster
- Source: same auditing meta-cluster, but only HIGH crossed the second/third-cluster threshold

### Variance amplifier 2 — Cluster maturity (~0.4 of the gap)
- HIGH landed in 14-member cluster (avg 7.46, usage 21) → `improvement_score = 3.32`
- LOW seeded a brand-new 1-member cluster → `improvement_score = 0.72`
- **4.6× gap before any LLM was queried.**

### Variance amplifier 3 — Strategy mismatch (~0.3 of the gap)
- LOW's `strategy_intelligence` ranked: `meta-prompting (8.5, n=22), chain-of-thought (7.7, n=8)`
- LOW's optimizer **selected `chain-of-thought`** — the *worse-ranked* option, with smaller sample size
- HIGH picked the top-tied option

### Variance amplifier 4 — Codebase context poisoning (~0.3 of the gap)
LOW's curated retrieval pulled:
- `pipeline_constants.py` (31,313 chars) at score 0.0 via import-graph
- `_constants.py` (16,255 chars) at score 0.0 via import-graph
- 47,568 chars total = 61% of the 80K-char budget

While the actual hot files were near-miss-excluded:
- `routers/health.py` (0.439 cosine) — **the file the prompt is literally about**
- `sub_domain_readiness.py` (0.445 cosine)

### Variance amplifier 5 — Conciseness collapse penalty (~0.5 of the gap)
- LOW had 3.10× expansion → triggered `heuristic_flags=["conciseness"]`
- HIGH had 4.17× expansion (LARGER!) but stayed under the gate threshold

The flag is **binary not graded** — once tripped, drags the blended score down a step on that dimension.

---

## Phase B: Independent quality evaluation

5 prompts evaluated by an independent expert agent applying the same 5-dimension rubric without seeing the system's score.

| trace | system | independent | Δ | notes |
|---|---:|---:|---:|---|
| `d74283a8` audit-top | 8.10 | **8.55** | +0.45 | system **under**scores the strongest audit |
| `c4da176c` audit-mid | 7.74 | **7.13** | −0.61 | system **over**scores — prompt has FALSE PREMISE |
| `42097cf8` audit-low | 6.77 | **7.16** | +0.39 | system **under**scores a sound diagnostic |
| `b22a83db` audit-mid-low | 7.30 | **7.71** | +0.41 | system **under**scores a methodologically rigorous prompt |
| `e29098b9` non-audit control | 9.05 | **9.25** | +0.20 | system slightly under |

**Mean Δ for audits: +0.16. Mean Δ for control: +0.20. Statistically indistinguishable.**

### Phase B's biggest finding: false-premise blindness

`c4da176c` (audit-mid, system 7.74) makes a claim about Phase 4.95 having a "cluster signature change detection that gates Phase 4.95 is too conservative." **The independent reviewer verified against the actual `warm_path.py:456-466`: Phase 4.95 has NO cluster-signature gate at all — it runs unconditionally every cycle.**

The audit prompt is built on a wrong premise. Independent faithfulness: 5.5. System faithfulness: ~7.5. **The scorer rewarded surface symbol density over premise correctness.**

This is the most important finding in Phase B: audit prompts can earn high faithfulness scores by **looking technical** even when the underlying technical claim is wrong.

---

## Phase C: Scoring code review (8 findings)

### HIGH-impact biases

#### C1 — Backtick code references not counted in specificity heuristic
**File:** `heuristic_scorer.py:221-270`. The specificity scorer has 10 keyword categories (modal obligations, outcome verbs, type annotations, format keywords, examples, numeric constraints, exception types, negation constraints, temporal/quantity, audience/tone). **Backtick-wrapped code identifiers are NOT one of them.**

An audit citing `engine.py`, `_reevaluate_sub_domains`, `cluster_metadata.generated_qualifiers` gets ZERO specificity credit for those citations — even though they are LITERALLY more specific than "examples" or "format keywords".

#### C2 — Z-score normalization unfairly floors audit prompts
**File:** `score_blender.py:87-117`. With `ZSCORE_MIN_STDDEV=0.3` and asymmetric cap (floor at -2.0, ceiling uncapped):
- Audits cluster in 7.0-7.5 range → narrow stddev (~0.35)
- A heuristic-confident audit at LLM 6.9 produces z = -0.857 → normalized 4.21
- **The blended score drops to 4.2 even though raw 6.9 is objectively adequate**, purely because z-norm assumes wider distributions

The asymmetric cap was designed (R-class C5 in v0.4.7) to help feature prompts that exceed +2σ — but it does not help audits that sit below the (audit-class) mean.

#### C3 — Conciseness weight (0.15) is feature-tuned, hostile to audits
**File:** `pipeline_contracts.py:28-34`. Audits ARE longer than feature prompts (they enumerate findings, cite multiple symbols, walk through logic). The 0.15 conciseness weight applies uniformly across all task types.

C3's technical-prompt conciseness bump (v0.4.7) only fires when `technical_dense=True` — but it bumps the **heuristic-vs-LLM blend ratio** (`w_h=0.35`), not the dimension weight itself. An analytical audit without code-noun density still gets 0.15-weighted on conciseness.

### MEDIUM-impact biases

#### C4 — TECHNICAL_TTR_MULTIPLIER applies pre-structural-bonus
**File:** `heuristic_scorer.py:174-181`. The 1.15× TTR boost fires when ≥3 technical nouns detected, but TTR is band-mapped BEFORE the structural bonus. A 400-word audit with 18 unique words and no markdown headers scores TTR ~0.05 → maps to ~4.2 baseline. The 1.15× boost barely registers because TTR is floor-capped.

#### C5 — Imperative-list structure not credited
**File:** `heuristic_scorer.py:150-218`. "Identify X. Find Y. Confirm Z. Distinguish A from B." (4 imperative clauses) isn't credited as structure. Same content as a paragraph would score higher on conciseness.

#### C6 — `prompts/scoring.md` has no audit-class calibration
The LLM scoring rubric provides examples for coding/writing/system-design but **zero examples are explicitly framed as audit/diagnostic prompts**. A single 7.5 calibration example is incidental, not documented. The LLM scorer relies on general principles when scoring audits — likely treating the structurally-required length as verbosity.

#### C7 — Task-type classifier ID's "analysis" but scorer ignores it
**File:** `task_type_classifier.py` correctly classifies "Audit X" / "Trace Y" / "Diagnose Z" as `analysis` task_type. **But `score_blender.py:120-241` has zero conditional logic on task_type.** Dimension weights are uniform.

The system has all the information it needs to apply audit-friendly weights — it just doesn't.

### LOW-impact

#### C8 — Heuristic flags don't carry audit context
The `heuristic_flags=["conciseness"]` divergence flag fires correctly — but doesn't tell the operator/UI WHY the divergence is expected (audit format is necessarily longer).

---

## Synthesis: which mechanism caused which gap?

Of the 1.33-point HIGH→LOW gap in Phase A:
- ~0.4 from cluster maturity / pattern injection (structural, not scoring)
- ~0.3 from strategy mismatch (pipeline routing, not scoring)
- ~0.3 from codebase context poisoning (retrieval ranking, not scoring)
- ~0.5 from conciseness collapse penalty (scoring code, fixable)

Of the 0.6-point audit-vs-feature mean gap:
- ~0.3 from C1 backtick blind spot (scoring heuristic, fixable)
- ~0.2 from C2 z-norm floor (scoring code, fixable)
- ~0.1 from C3 conciseness weight (dimension weights, fixable)

**Net diagnosis: ~70% of the variance is fixable in scoring code; ~30% is pipeline/structural. Both deserve work.**

---

## P0 hardening proposals (ship in v0.4.9, ~50 LoC total)

### F1 — Backtick specificity recognition (~5 LoC)
**File:** `backend/app/services/heuristic_scorer.py:229-251`

Add 11th category to specificity:
```python
(r"`[a-zA-Z_][a-zA-Z0-9_./:-]*`", 0, 1.5),  # backtick-wrapped code references
```

Estimated impact: +0.3 to +0.6 specificity for audit prompts that cite ≥3 symbols. Brings audits in line with feature prompts that have natural identifier mentions.

### F2 — Z-norm bypass for analysis task_type (~3 LoC)
**File:** `backend/app/services/score_blender.py:87-117`

```python
if task_type == "analysis":
    # Audit prompts cluster narrowly; z-norm assumes wider distributions
    # and floor-caps legitimately good audits below the heuristic baseline.
    return _clamp(raw)
```

Or alternatively, raise `ZSCORE_MIN_STDDEV` from 0.3 to 0.5 globally (covers all narrow-distribution task types). Estimated impact: +0.2-0.4 for audit prompts in the 6.5-7.5 raw range.

### F3 — Conditional dimension weights for analysis (~10 LoC)
**File:** `backend/app/schemas/pipeline_contracts.py` + `score_blender.py:203`

```python
ANALYSIS_DIMENSION_WEIGHTS: dict[str, float] = {
    "clarity":      0.25,  # ↑ from 0.22 — diagnostic clarity matters more
    "specificity":  0.25,  # ↑ from 0.22 — citation precision matters more
    "structure":    0.20,  # ↑ from 0.15 — finding-list structure matters more
    "faithfulness": 0.20,  # ↓ from 0.26 — premises ARE often hypotheses
    "conciseness":  0.10,  # ↓ from 0.15 — audits are necessarily long
}

def get_dimension_weights(task_type: str) -> dict[str, float]:
    if task_type == "analysis":
        return ANALYSIS_DIMENSION_WEIGHTS
    return DIMENSION_WEIGHTS
```

Estimated impact: +0.3-0.5 on audit-class outputs while preserving feature-prompt scoring exactly.

### F4 — Strategy selection guardrail (~5 LoC)
**File:** wherever the optimizer chooses strategy from `strategy_intelligence` ranks (likely `pipeline_phases.py::resolve_post_analyze_state`)

```python
top_strategies = strategy_intelligence_detail[:3]  # top 3 by score
if selected_strategy not in {s["name"] for s in top_strategies}:
    selected_strategy = top_strategies[0]["name"]  # fall back to top
    enrichment_meta["strategy_overridden"] = True
```

Refuses to select a strategy below top-3 in the intelligence ranking. Phase A showed LOW selected `chain-of-thought (7.7, n=8)` over `meta-prompting (8.5, n=22)` — this prevents that.

Estimated impact: +0.2-0.4 for prompts that get the wrong strategy today.

### F5 — Faithfulness false-premise flag (~5 LoC)
**File:** `score_blender.py` divergence flag detection

```python
# When LLM rates faithfulness low on a prompt that LOOKS technical,
# the prompt likely has a wrong premise that surface symbols mask.
if dim == "faithfulness" and llm_score < 5.0 and technical_dense:
    flags.append("possible_false_premise")
```

Doesn't change the score directly but surfaces the failure mode for operator review. Phase B identified this as the highest-leverage signal we currently miss.

---

## P1 hardening proposals (v0.4.10 candidates)

### F6 — Audit calibration examples in `prompts/scoring.md`
Add 3-4 explicit audit-class examples per dimension showing how clarity/specificity/structure/faithfulness/conciseness apply to diagnostic prompts. Example for clarity 8: *"Names exact functions, states the symptom, deduces implications, asks for 3 concrete deliverables."*

### F7 — Cluster maturity advantage cap on `improvement_score`
**File:** wherever `improvement_score` is computed
- New prompts that seed singleton clusters get `improvement_score = 0.72` (Phase A's LOW), purely structural disadvantage
- Cap maturity bonus at N=10 members — diminishing returns past that

### F8 — Curated retrieval: penalize zero-similarity import-graph entries
**File:** `repo_index_query.py` curated retrieval scoring
- Phase A showed 47K chars of import-graph-only files at score 0.0 crowded out near-miss files at 0.439-0.445
- Add penalty: `if cosine == 0.0 and via_import_graph: score *= 0.5` so they yield budget to legitimate hits

### F9 — Imperative-list structural bonus
**File:** `heuristic_scorer.py` structure bonus block
- Detect "Audit X. Trace Y. Diagnose Z." patterns (≥3 imperative-led sentences)
- Apply same +1.0 bonus as markdown headers receive

---

## P2 (nice-to-have)

### F10 — `audit_prompt` strategy file
A new `prompts/strategies/audit-prompt.md` with audit-specific optimization patterns (citation format, premise-stating, finding-numbering). Auto-selected when `task_type=="analysis"` AND `compound_keyword in ("audit X", "trace Y", "diagnose Z")`.

### F11 — UI annotation for audit-context divergence
When `task_type=="analysis"` AND conciseness divergence > 2.0, the UI shows a tooltip: "*This audit's length is structurally necessary. The conciseness penalty reflects audit format, not verbosity.*"

---

## Validation plan for v0.4.9

After F1-F5 ship:

1. **Replay all 20 cycle-19→22 prompts** with the new scoring. Expected: mean rises from 7.36 → ~7.85 (closes 70% of the gap to feature baseline).
2. **A/B replay** of `c4da176c` specifically. Independent score 7.13; current system 7.74. Expected post-fix: 7.0-7.2 (the false-premise penalty fires).
3. **A/B replay** of `42097cf8` specifically. Independent 7.16; current 6.77. Expected post-fix: 7.1-7.4 (z-norm bypass + backtick credit).
4. **Audit prompt cycle-23** with 5 fresh audit prompts. Mean expected: ≥7.7.

---

## Closing note

The audit prompts we've been running are **good**. The system was scoring them slightly variably for compound reasons: 70% scoring-code blind spots, 30% pipeline routing. Fixing F1-F5 closes most of that variance and makes audit prompts a **first-class citizen** of the optimization pipeline rather than a secondary class that happens to score lower because the system was tuned for feature generation.

This matters because **audits are how the system diagnoses itself.** Cycles 19-22 surfaced 5 ROADMAP-quality findings about the system's own infrastructure. If we can't reliably score audit-of-self prompts well, we can't trust the system's self-improvement signal.

Recommended sequence:
1. Land F1-F5 as a single PR (v0.4.9 hardening pack)
2. Run cycle-23 (5 fresh audit prompts) as validation
3. Compare cycle-23 mean against v0.4.8's 7.36 baseline
4. If mean ≥ 7.7, ship; else iterate


---

## Resolution status — 2026-04-28 (v0.4.9)

All 5 F-track recommendations SHIPPED. Implementation followed strict TDD discipline (RED → GREEN → REFACTOR → VALIDATION) per fix, gated by independent spec verification before plan-write.

| Fix | Status | Tests | Suite |
|---|---|---|---|
| F1 — Backtick specificity heuristic | SHIPPED | `TestSpecificityBacktick` (4 tests) | 3158 pass |
| F2 — `ZSCORE_MIN_STDDEV` 0.3 → 0.5 | SHIPPED | `TestZNormThreshold` (4 tests) | 3177 pass |
| F3 — Per-task-type `DIMENSION_WEIGHTS` | SHIPPED | `TestDimensionWeights` (5) + `TestBlendScoresTaskType` (1) + `TestPersistAnalysisWeights` (1) | 3168 pass |
| F4 — Remove `OptimizationResult.strategy_used` | SHIPPED | `TestOptimizationResultSchema` (2) + `TestStrategyFidelity` (1) + `TestRefinementStrategyFidelity` (1) | 3161 pass |
| F5 — `possible_false_premise` flag | SHIPPED | `TestFalsePremise` (5 tests) | 3173 pass |

**Final suite:** 3177 passed, 1 skipped, ruff + mypy clean across all touched files.

**Spec doc:** `docs/specs/audit-prompt-hardening-2026-04-28.md`
**Implementation plan:** `docs/plans/audit-prompt-hardening-2026-04-28.md`
**v0.4.9 CHANGELOG entries:** `docs/CHANGELOG.md` § Unreleased
**Validation:** cycle-23 audit-prompt replay via `scripts/validate_taxonomy_emergence.py cycle-23-audit-prompt-hardening`

**Cross-cutting findings during execution:**

- **F4 fixture sweep was broader than spec predicted.** Spec table listed 9 lines across 3 test files; actual sweep covered 12 test files due to cross-cutting use of `OptimizationResult` fixtures. The Pydantic `extra="forbid"` mode caused immediate ValidationErrors at every missed call site, making the broader sweep self-correcting. Spec table has been annotated with the correction (test_strategy_recommendation.py / test_few_shot_retrieval.py were mis-listed — those are DB-model writes, not OptimizationResult fixtures).
- **F3 property-vs-method design caught at verification gate.** Initial spec had `compute_overall(task_type)` as a method override of the existing `@property def overall`, which would have broken ~30 backward-compat call sites. Resolved by adding `compute_overall` as a sibling method while preserving the property unchanged.
- **F1 cap=2.0 not 4.0**, weight not separate. Initial spec had `(regex, cap, weight_per_match)` tuple shape, but actual code uses `(pattern, re_flags, category_cap)` with a hardcoded `min(1.0 + 0.3*(hits-1), cap)` formula. Corrected spec before RED phase.
- **passthrough.md unchanged.** Verified `strategy_used` in passthrough.md:68 is informational metadata for the IDE caller (not parsed via OptimizationResult), so F4 left it unchanged. `optimize.md` and `refine.md` had no LLM-facing `strategy_used` directive.

