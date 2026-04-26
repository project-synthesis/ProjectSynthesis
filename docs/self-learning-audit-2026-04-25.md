# Self-Learning Capacity Audit — 2026-04-25

E2E validation cycle 5 (post-fix-clean) ran two semantically-similar prompts through the live pipeline and exposed every learning loop the system implements today. This audit documents what fired, where the signal-to-noise ratio is high, where the loops stall, and what to build next.

The system *does* self-learn — the question is how to amplify the signal without burning more infrastructure than the gains justify.

> **Reading guide:** §1–§3 are observational (what the system does today, with measurements from cycle 5). §4 is gap analysis. §5–§8 are tiered recommendations. Each recommendation lists *effort*, *risk*, and *expected lift* so the roadmap can be sequenced by leverage rather than novelty.

---

## 1. Self-learning loops that fired in cycle 5

The system implements **eight observable feedback loops**. Cycle 5's two prompts (HNSW-cliff profiling + DomainSignalLoader cache audit) exercised six of them; the other two are wired but un-stimulated under test conditions.

| # | Loop | Evidence on cycle 5 | Wired? | Stimulated? |
|---|---|---|---|---|
| L1 | Pattern injection (5-signal composite fusion) | 65 patterns from 4 clusters into prompt 1; 19 patterns from 1 cluster + sub-domain into prompt 2 | ✅ | ✅ |
| L2 | Few-shot retrieval (dual: input-similar + output-similar) | `max_sim=0.82` on prompt 1, `0.74` on prompt 2 | ✅ | ✅ |
| L3 | Score-correlated phase-weight adaptation (per-cluster) | Race Condition Auditing (m=10) departed neutral baseline by ±0.30 on `analysis.w_topic` | ✅ | ✅ |
| L4 | Strategy intelligence with evidence boost | Log: *"Data-recommended strategy 'meta-prompting' (evidence=4, boost=0.07) overrides low-confidence analyzer pick"* | ✅ | ✅ |
| L5 | Sub-domain organic emergence (Haiku qualifier vocab) | `embedding-health` emerged from backend, 32 kebab-case terms across 4 groups | ✅ | ✅ |
| L6 | Meta-pattern bank (Haiku extraction from cluster cohort) | 19 patterns in Prometheus, 15 each in 4 other clusters; sample patterns are concrete and transferable | ✅ | ✅ |
| L7 | Strategy affinity (explicit 👍/👎 feedback) | Table empty — feedback path not exercised under automated test | ✅ | ❌ |
| L8 | TaskTypeTelemetry (heuristic vs LLM agreement) | 4 rows, all `source=haiku_fallback` for `coding` — heuristic A1/A2/A3 path didn't write rows | ✅ | partial |

---

## 2. Quantitative performance baseline (n=33 optimizations)

| Signal | Value | Notes |
|---|---|---|
| Score health | mean=8.58, stddev=0.37 | Tight distribution — see §4.3 (low variance hampers signal) |
| Pipeline duration (avg) | 289 s = 4.8 min | Analyze 42 s + Optimize 110 s + Score 91 s + overhead |
| Pattern-injection density (cycle 5) | 65 patterns / 31,743 tokens (prompt 1) | ~24,609 chars of injected patterns out of 126,975 total — 19 % of the optimize input is reused-knowledge |
| Few-shot recall sim | 0.82 / 0.74 (cycle 5) | Strong neighbors found within 33-prompt corpus — early-corpus regime |
| Cluster maturity distribution | 1 of 12 active clusters past learning threshold (m≥10) | Race Condition Auditing only |
| Sub-domain count | 1 (`embedding-health`) | 1 emerged, 1 dissolved (`audit`) → net +1 in two cycles |
| Domain qualifier corpus | 10 distinct `domain_raw` values on 33 optimizations | Diverse enough to drive emergence |
| `mean(improvement_score)` | ≈ 4.5 (cycle 5: 4.56 / 4.80) | Optimized > original by ~50% on the 0–10 scale |
| Score correlation: `meta-prompting` vs others | `meta-prompting` dominates backend domain (8.66–9.05 range) | Adaptation is correctly favoring it |

### What's working well

- **Pattern reuse is real, not bootstrap:** 65 injected patterns came from clusters with *prior similar prompts*, not from a static seed. That's L1 doing its job.
- **Sub-domain routing is end-to-end:** prompt 2 was classified as `backend: metrics`, mapped to `Qualifier Embedding Cache Instrumentation` *via the embedding-health sub-domain* — the qualifier-cache codepath that we'd just spent a debugging cycle ensuring is correct.
- **Weight learning produces non-trivial deltas:** Race Condition Auditing's `analysis.w_topic +0.30 / w_output -0.15` is a real learned signal — not bootstrap drift.
- **Score honesty:** lower scores on the more niche cycle-5 prompts (HNSW-cliff, cache eviction) prove the LLM judge isn't ceiling-saturating. Score is informative.

---

## 3. Architectural grade — by component

| Component | Grade | Rationale |
|---|---|---|
| Pattern injection (L1) | **A** | 5-signal composite fusion, score-weighted centroids, cross-cluster + sub-domain + global tiers. Industry-strong. |
| Sub-domain emergence (L5) | **A−** | Organic Haiku-vocab + adaptive consistency threshold. Loses a half-grade for the `audit` flip-flop we just debugged. |
| Meta-pattern bank (L6) | **B+** | Reusable, concrete, scoped per cluster. Loses ground because patterns *accumulate without curation* (see §4.5). |
| Phase-weight learning (L3) | **B** | Real signal, but bootstrap threshold at m=10 means most clusters never learn (only 1 of 12 today). |
| Strategy intelligence (L4) | **B** | Evidence boost is a good idea, but it's a one-way ratchet — no exploration. |
| Few-shot retrieval (L2) | **B** | Dual-retrieval is smart, but picks max-similarity exemplars (no diversity). |
| Strategy affinity (L7) | **C** | Wired but un-stimulated. No implicit feedback path (refinement turns, rollbacks). |
| Task-type telemetry (L8) | **C+** | 4 rows in 33 prompts means 87% of classifications never write telemetry. Logging is incomplete. |

**Overall self-learning grade: B+.** The skeleton is excellent; specific limbs are atrophied.

---

## 4. Gap analysis — where the loops stall

### 4.1. Bootstrap threshold is a learning bottleneck

`compute_score_correlated_target()` requires **≥ 10 above-median samples** before per-cluster weights learn. With 33 total optimizations spread across 12 clusters, only 1 cluster has crossed it. The other 11 use the global Layer-1 task-type bias indefinitely.

**Symptom:** Most prompts get backend's task-type-coding bias profile — no per-cluster specialization.

**Quick-fix candidates:** Bayesian shrinkage (small samples blend with global prior), lowered threshold + lower alpha, or hierarchical pooling (cluster → sub-domain → domain → global).

### 4.2. Strategy choice is exploitative-only — no exploration

When the data path overrides the analyzer (`Data-recommended … overrides low-confidence analyzer pick`), it always picks the historically-best strategy. That means:

- Once `meta-prompting` wins a few times for backend, `chain-of-thought` never gets tested again
- We can never discover that `chain-of-thought` would have been better for *this specific sub-domain*
- No counterfactual signal, ever

Cycle 5 prompt 2 *did* select `chain-of-thought` (because the intent label "cache eviction policy audit" triggered an analysis-task heuristic), and scored 7.66 vs prompt 1's 8.21 with `meta-prompting`. We have no way to know whether prompt 2's lower score was strategy-driven or topic-difficulty-driven.

### 4.3. Score variance is too narrow for signal

stddev=0.37 over n=33 means a 1-stddev move on the score is barely outside scoring noise. **Most learning signals are sub-stddev.** When `improvement_score` has the same problem, weight adaptation is fitting to noise as much as to truth.

Possible causes:
- LLM-as-judge ceiling effect (most prompts get 8–9 because the optimizer is good)
- Single-axis scoring (overall blends 5 dimensions; opposing signals cancel)
- No relative scoring (no head-to-head A/B between candidates)

### 4.4. No causal attribution — can't tell which pattern helped

When 65 patterns are injected, we credit/discredit *the whole bundle*. We can't say "pattern 17 lifted clarity by +1.2 but pattern 42 hurt conciseness by -0.6." Score correlations are at the cluster level, not the pattern level.

### 4.5. Meta-pattern bank is accumulative without curation

| Cluster | patterns |
|---|---|
| Prometheus Instrumentation | 19 |
| Embedding Correctness Audits | 15 |
| Race Condition Auditing | 15 |
| Sqlalchemy Async Factory | 15 |
| embedding-health (sub-domain) | 15 |

19 patterns into a 4-member cluster is **near-saturation density**. There's no mechanism to:
- Merge near-duplicate patterns
- Retire patterns that correlate with low-score outputs
- Cap per-cluster bank size

**Risk:** as cycles continue, pattern noise grows linearly while signal stays flat.

### 4.6. Sub-domain isolation is approximate

When prompt 2 was routed to `embedding-health`, it received 1 pattern from `embedding-health` (sim=0.0 — boundary edge) PLUS 1 pattern from `Qualifier Embedding Cache Instrumentation` (sim=0.47). Cross-cluster injection respects topic similarity but not sub-domain hierarchy — a sub-domain isn't actually a *protected* knowledge silo.

Whether this is good or bad is open. Probably good (transfer between siblings), but unmeasured.

### 4.7. No implicit feedback channel

We have explicit feedback (👍/👎, table empty). We *don't* have:
- **Refinement-turn count** as a quality signal (more turns ⇒ unsatisfied user)
- **Time-to-rollback** (fast rollback ⇒ first answer was bad)
- **Copy-paste latency** (fast copy ⇒ user accepted output)
- **Subsequent-prompt similarity** (next prompt is a refined version ⇒ first wasn't enough)

These are higher-fidelity than 👍/👎 because they're collected without effort.

### 4.8. Embedding model is fixed

`all-MiniLM-L6-v2` (384-dim) is solid general-purpose but never adapts to domain vocabulary. Race conditions, async sessions, embedding warmup, qualifier vocab — all collapse to the same 384-dim space. A domain-fine-tuned adapter (like a 16-dim residual on top of MiniLM) could materially sharpen cluster boundaries.

### 4.9. The A4 Haiku fallback is over-firing

`task_type_telemetry` shows 4 rows, all `haiku_fallback` for `coding`. The static heuristic A1/A2/A3 path apparently *never* wrote a high-confidence row in the corpus. That means we're paying Haiku tokens on every classification.

Either:
- The confidence gate is mis-tuned (fires too often)
- The heuristic path is structurally weak on this corpus (which is plausible — it's a backend code corpus and the static keywords skew toward general coding)

Either way: paying for Haiku 30+ times when half should be free is real cost.

---

## 5. Tier 1 — Software-only changes (1–2 weeks each)

No infrastructure changes. No new dependencies. Pure code edits, fully reversible.

### T1.1. Bayesian shrinkage on phase-weight learning *(highest leverage)*

**Status quo:** 11 of 12 active clusters never learn weights (m < 10).

**Change:** Replace the "≥10 samples" gate with a continuous Bayesian update:
```
posterior_weight = (n / (n + κ)) * empirical_mean + (κ / (n + κ)) * task_type_bias
```
where `κ` (prior weight) tunable, e.g. `κ=8`. At m=2, the prior dominates. At m=20, empirical takes over. **No threshold, smooth transition.**

- Effort: 1 file, ~80 LoC in `cluster_lifecycle.py` + `compute_score_correlated_target()`
- Risk: low — backward compatible if `κ` is high
- Expected lift: every cluster contributes signal from m=2; expect 2–3× faster cluster specialization. Whether that translates to score lift depends on §4.3 (variance), but at minimum the *capacity* exists.

### T1.2. Multi-armed bandit on strategy selection *(closes §4.2)*

**Status quo:** Strategy is chosen exploitatively from `strategy_intelligence` rankings.

**Change:** Replace argmax with **Thompson sampling** over a Beta(α, β) per `(task_type, domain, strategy)`:
- α += 1 on score ≥ 8.5
- β += 1 on score < 7.0
- Sample at request time → rare but non-zero exploration of dominated strategies

- Effort: ~120 LoC in `strategy_intelligence.py` + a small `strategy_arms` table (or extend `strategy_affinities`)
- Risk: low if α prior is moderate (e.g. α₀=β₀=2 ensures meaningful sampling early)
- Expected lift: discovery of better strategies for niche (task_type, domain) combos. Will *temporarily lower* avg score (exploration cost) but raise *ceiling* over time.

### T1.3. Pattern-level score attribution *(closes §4.4)*

**Status quo:** Patterns are credited/discredited en bloc.

**Change:** Add a per-pattern `delta_score` field. When an optimization completes:
1. Re-run scoring with one randomly-selected injected pattern *removed* (every Nth optimization, not all)
2. Diff = pattern's contribution to the score
3. Update `OptimizationPattern.contribution` (rolling avg per pattern)
4. Patterns with `contribution < 0` over 5+ samples → demote (lower injection priority)

- Effort: ~200 LoC, +1 column on OP table, +1 score call per N optimizations (~5 % overhead at N=5)
- Risk: medium — adds latency on a fraction of requests, and re-scoring isn't deterministic
- Expected lift: meta-pattern bank curates itself; bad patterns decay, good ones stay. Closes §4.5.

### T1.4. Implicit feedback signals *(closes §4.7)*

**Status quo:** Empty `strategy_affinities`; only signal is LLM score.

**Change:** Add four implicit signals to `OptimizationOutcome`:
- `refine_turns_after`: 0 = first answer accepted; ≥1 = unsatisfied
- `time_to_action_ms`: copy-paste / save latency (frontend-side instrumentation)
- `rolled_back`: bool
- `superseded_by`: optimization_id of a later refined version

Feed into the same Bayesian update as T1.1 with weights:
- `refine_turns_after=0` → strong positive
- `rolled_back=True` → strong negative
- High `time_to_action_ms` → mild negative

- Effort: ~150 LoC backend + minor frontend instrumentation (~50 LoC). One DB migration.
- Risk: low. Frontend instrumentation can be opt-in initially.
- Expected lift: 5–10× more learning signal per optimization (most have at least 1 implicit signal).

### T1.5. Pattern consolidation pass *(closes §4.5)*

**Status quo:** Patterns accumulate forever.

**Change:** When a cluster crosses N patterns (e.g. 15), trigger a Haiku pass that:
1. Embeds all cluster patterns
2. Clusters them via HDBSCAN (the same algorithm we already use)
3. For each sub-cluster of ≥2 patterns: ask Haiku to write a *consolidated* pattern that captures all member intents
4. Replace originals with the consolidated version, retain `source_pattern_ids` for provenance

- Effort: ~250 LoC in `meta_pattern_consolidation.py`, gated by `Phase 4.5` cadence
- Risk: medium — consolidation could lose nuance. Mitigation: keep originals as `state='consolidated'` with FK to the new merged pattern.
- Expected lift: pattern bank stays signal-dense; injection token budget more efficient.

### T1.6. MMR (Maximal Marginal Relevance) on few-shot retrieval *(closes §4.6 partial)*

**Status quo:** Top-2 by max similarity → exemplars often near-duplicates.

**Change:** After top-K candidates, apply MMR:
```
score(d) = λ * sim(query, d) - (1-λ) * max_{d' selected} sim(d, d')
```
λ=0.6 is a strong default. Diverse exemplars > similar exemplars.

- Effort: ~30 LoC in `pattern_injection.py::_few_shot_retrieve()`
- Risk: minimal
- Expected lift: better few-shot diversity → higher optimizer creativity, especially on edge cases.

### T1.7. Tune the A4 Haiku-fallback confidence gate *(closes §4.9)*

**Status quo:** Every classification falls through to Haiku.

**Change:** Audit the static heuristic path (A1 compound keywords + A2 verb-noun + A3 domain signals):
- Run an offline pass on 33 existing optimizations
- For each, log heuristic confidence → check if it would have triggered Haiku
- If >50% trigger Haiku, the gate threshold is mis-tuned OR the static keyword set is sparse for the corpus

Either:
- Raise the confidence threshold (less Haiku, accept more heuristic)
- Or extend `_TASK_TYPE_SIGNALS` with more compound keywords from the actual corpus

- Effort: ~4 hours analysis + small config edits
- Risk: minimal
- Expected lift: 30–60 % reduction in A4 Haiku spend. No quality regression if the heuristic was actually competent on those samples.

---

## 6. Tier 2 — Architectural changes (1–3 months each)

Adds structure or alters the data flow. Possibly new dependencies. Mostly non-breaking with feature flags.

### T2.1. Reflexive self-evaluation loop

After every scored optimization, ask the LLM (Haiku, cheap) two questions:
1. *"What weakness in the original prompt does this fix? What does it still miss?"*
2. *"What would a perfect optimization for this prompt look like?"*

Store the answers as a **synthetic comparison** — used as an additional negative example for refinement, and as a feature in pattern attribution (T1.3). The system learns *what its outputs lack*, not just what they do well.

- Effort: ~3 weeks (new service + schema + integration with refinement)
- Risk: medium (adds Haiku spend; needs token budget caps)
- Expected lift: gives the system a *theory of perfection* per prompt class. High signal for §4.3 (variance).

### T2.2. Online learning of phase weights (continuous, not cluster-gated)

**Status quo:** Weights only update when a cluster matures.

**Change:** Move to streaming online updates:
- After every optimization, compute weight gradient w.r.t. observed score
- Apply EMA update with adaptive learning rate (Adam-style)
- Maintain a global "explore weights" arm + per-cluster "exploit weights" arm — pick at request time via T1.2's bandit

- Effort: ~6 weeks (full rewrite of `cluster_lifecycle.py::compute_score_correlated_target` + new gradient logic)
- Risk: medium-high (needs careful regularization; gradient explosion possible)
- Expected lift: per-prompt adaptation, not per-cluster-batch.

### T2.3. Domain-fine-tuned embedding adapter

**Status quo:** `all-MiniLM-L6-v2` fixed.

**Change:** Train a 16-dim residual adapter on top of MiniLM using contrastive triplets:
- Anchor: a prompt
- Positive: a same-cluster prompt
- Negative: a different-domain prompt

Output: `final_emb = MiniLM(text) + α * Adapter(text)` with α=0.2 default.

- Effort: 4–8 weeks (data pipeline + training script + serving + A/B framework)
- Risk: medium (model swap is fraught; needs shadow-mode verification before cutover)
- Expected lift: cluster boundary sharpening — sub-domain emergence faster, fewer mis-classifications. **Moves us from "general purpose" to "Synthesis-aware" embeddings.**
- Stack delta: adds PyTorch training infra (offline). Serving stays sentence-transformers + small Linear adapter.

### T2.4. Multi-objective optimization frontier

**Status quo:** `overall_score` is single scalar; trade-offs invisible.

**Change:** Track Pareto frontier across `(score, latency, token_cost)`. When the system has 3 candidate strategies that score similarly, pick the cheaper one. When the user wants speed, pick the fastest of equal-score options.

- Effort: 4 weeks (frontier maintenance + cost tracking already partially exists in trace_logger)
- Risk: low
- Expected lift: significant cost reduction on ties; explicit speed/quality user control.

### T2.5. Active learning — synthetic neighbor probing

**Status quo:** System learns only from user prompts.

**Change:** When a cluster has high uncertainty (variance in scores, few samples, ambiguous task type), the warm path generates a **synthetic neighbor prompt** via Haiku ("Write a prompt that's similar to {seed} but tests {edge_case}"), runs it through the pipeline at low priority, scores it, and uses the result to disambiguate.

- Effort: 6–8 weeks (new background service + safety guards against runaway generation)
- Risk: high (spend can balloon; safety boundary is hard)
- Expected lift: fastest path to filling sparse cluster regions. **The system probes its own knowledge gaps.**

---

## 7. Tier 3 — Infrastructure / stack changes (3–6 months)

Touch deployment, persistence, or compute platform. Higher commitment but unlocks scale.

### T3.1. Migrate SQLite → Postgres + pgvector

**Drivers:**
- WAL works but concurrent writes are still serialized
- Vector search is naive numpy in-memory — no persistent ANN
- JSON queries on `cluster_metadata` are full-table scans

**Migration:** Alembic-driven, dual-write phase, cutover. pgvector handles the embedding cluster index natively.

- Effort: 2 months (migration + tests + ops)
- Risk: high (data correctness, rollback, downtime)
- Expected lift: 10–100× cluster scale ceiling, real-time analytics on cluster_metadata, native vector indexing.
- **Required before:** any of §6 changes that depend on >100K cluster scale.

### T3.2. Replace LLM-as-judge with a fine-tuned reward model

**Status quo:** Sonnet/Opus scores every optimization. Cost per score = ~$0.05–0.20.

**Change:** Train a small reward model (e.g. distilled Llama-3.2-1B + LoRA) on collected (prompt, optimization, blended_score) triples. Use it as the primary scorer; reserve LLM judge for periodic ground-truth calibration.

- Effort: 3 months (data collection + training infra + safe rollout)
- Risk: high — reward model can hack itself if not calibrated
- Expected lift: 100× cheaper scoring → enables T1.3 (pattern attribution at every request) + much higher feedback fidelity.
- Stack delta: adds GPU training pipeline; serving: vLLM or similar.

### T3.3. Streaming feature store

**Status quo:** Features (heuristic_analysis, classification_agreement, codebase_context) are computed inline per request, partially cached.

**Change:** Adopt a streaming feature store (Feast / lightweight in-house). Every prompt's features are computed *once*, materialized, served from cache.

- Effort: 2 months (architecture + integration)
- Risk: medium (cache invalidation; consistency)
- Expected lift: 30–50% latency reduction on warm requests; enables real-time A/B (T1.2 at scale).

### T3.4. Distributed warm-path sharding

**Status quo:** Phase 5/6 run single-process per project.

**Change:** Shard by domain. Each domain's lifecycle (Phases 0–6) runs in its own worker. Coordinate global state (GlobalPattern, AdaptiveScheduler) via a shared lock service.

- Effort: 4 months (rearchitecting warm_path)
- Risk: high (consistency of dirty_set across shards)
- Expected lift: enables 10+ concurrent active projects without warm-path congestion.
- **Required before:** any multi-tenant scale beyond ~5 projects.

### T3.5. Dedicated ML platform (MLflow + experiment tracking)

**Status quo:** No experiment registry; A/B is ad-hoc.

**Change:** Adopt MLflow. Every weight profile, strategy bandit posterior, embedding adapter version is a tracked artifact with lineage. Prerequisite for T2.3 / T3.2.

- Effort: 1 month integration
- Risk: low
- Expected lift: experimental hygiene; reproducibility; rollback safety.

---

## 8. Tier 4 — Research-grade / paradigm shifts (6+ months, exploratory)

Speculative; require dedicated research effort. Listed for completeness, **not recommended now**.

| Change | Idea | Why interesting | Why risky |
|---|---|---|---|
| **DPO/PPO policy** | Replace heuristic+LLM hybrid with a trained optimization policy | Learns the entire pipeline end-to-end | Catastrophic forgetting; reward hacking; needs huge data |
| **Causal SCM** | Build a structural causal model: strategy → patterns → score | True counterfactuals; "would X have scored higher if I'd used Y?" | Causal identification is hard with confounded data |
| **Self-modifying strategy templates** | The strategy markdown itself gets learned variations | Strategies become an evolutionary substrate | Drift away from interpretability |
| **World model for prompt engineering** | Train a model that predicts user intent better than any LLM | Eliminates analyzer-LLM bottleneck | Massive data + compute; payoff unclear |
| **Multi-agent debate scoring** | Multiple LLM judges argue, vote | Higher-fidelity feedback | 4× scoring cost |
| **Continual learning with replay buffer** | Brain-replay style consolidation of patterns | Mimics biological memory | Underspecified for prompt-engineering use case |

---

## 9. Recommended sequencing (12-month roadmap)

Sequence by **(leverage / effort)** ratio. Avoid building Tier 3 before Tier 1's signals are saturated.

### Quarter 1 — Signal amplification (Tier 1, all-in)

Goal: 5× learning signal per optimization without infra change.

1. **T1.7** A4 Haiku gate audit (week 1) — quick win, frees token budget
2. **T1.1** Bayesian shrinkage on weights (weeks 2–3) — every cluster learns
3. **T1.2** Strategy bandit (weeks 4–5) — exploration unlocked
4. **T1.4** Implicit feedback (weeks 6–8) — 5–10× signal density
5. **T1.6** MMR few-shot (week 9) — small but free

By end of Q1: every loop produces signal *every prompt*. Then measure §4.3 — has variance widened?

### Quarter 2 — Selective curation (Tier 1 finish + Tier 2 start)

6. **T1.5** Pattern consolidation (weeks 10–13) — keeps the pattern bank signal-dense
7. **T1.3** Pattern attribution (weeks 14–17) — pre-req for T2.1
8. **T2.4** Pareto frontier (weeks 18–21) — explicit cost/quality

End of Q2: pattern bank is healthy, cost is observable, attributions are tracked. **System is now signal-rich.**

### Quarter 3 — Architectural depth (Tier 2)

9. **T2.1** Reflexive self-eval (weeks 22–25) — closes the variance gap
10. **T2.3** Domain-fine-tuned embedding adapter (weeks 26–34) — Synthesis-aware embeddings
11. **T2.2** Online weight learning (weeks 35–38) — streaming adaptation

End of Q3: the system is genuinely learning continuously, with awareness of what it doesn't know yet.

### Quarter 4 — Scale-out (Tier 3, only if Q3 metrics justify)

12. **T3.5** MLflow (week 39) — ops hygiene precondition
13. **T3.1** Postgres + pgvector (weeks 40–48) — cluster scale ceiling
14. **T3.2** Reward model (weeks 49+, spans into Y2) — cost ceiling

**Decision gate before T3:** if Q3 scoreboard shows variance > 0.7 stddev (vs today's 0.37) and per-cluster learned weights stable for >75% of clusters, ship Tier 3. If not, the bottleneck is data quantity, not infra — wait for more usage.

---

## 10. Risk register

| Risk | Mitigation |
|---|---|
| T1.2 bandit explores into cost-runaway | Cap exploration rate at 5%; budget alarm at +20% scoring spend |
| T1.3 attribution adds 5% latency on every Nth request | Make N configurable; default N=10; opt-in per project |
| T2.3 embedding swap breaks cluster boundaries | Shadow-mode for 2 weeks; A/B on coherence metric; rollback if Q_system drops |
| T2.5 active learning generates unsafe / off-topic prompts | Constrained generation (must be near-cluster centroid); manual review for first 100 |
| T3.1 SQLite→Postgres data loss | 2-week dual-write; fingerprint comparison; rollback rehearsed |
| T3.2 reward model is hacked by optimizer | LLM-judge calibration check every 1000 optimizations; auto-rollback if drift > 0.5 stddev |

---

## 11. Open questions for the next cycle

1. **Score variance** — is `stddev=0.37` a corpus artifact (small n) or a structural ceiling (LLM judge saturating)? Test by scoring with multiple judges (Sonnet + Opus + Haiku triangulation) and seeing if disagreement reveals hidden variance.
2. **Cross-cluster vs sub-domain isolation** — should `embedding-health` patterns be *preferred* over backend root-level patterns when matching `embedding-health` clusters? Currently they're equally weighted at the topic-similarity layer.
3. **Pattern-to-score causality** — can we collect enough natural variation in pattern selection to do observational causal inference, or do we need explicit randomized injection (T1.3 with N=2)?
4. **Sub-domain promotion semantics** — when `embedding-health` matures into a top-level domain, do its patterns inherit to a daughter sub-domain or stay at the new top level? Open ADR.
5. **Refinement turn signal** — what's the actual distribution of refinement-turn counts in production? Is `>1` the strong signal or `>3`?

---

## 12. Bottom line

The system has **the right architecture** for self-learning — eight feedback loops, hierarchical clustering with adaptive thresholds, Haiku-driven organic vocabulary, score-correlated weight adaptation. None of that needs replacement.

What it needs is **signal density and exploration**:
- Bayesian shrinkage so every cluster contributes (T1.1)
- A bandit so exploration ever happens (T1.2)
- Implicit feedback so we measure quality without asking (T1.4)
- Pattern attribution so the bank curates itself (T1.3)

Those four changes alone are 4–8 weeks of work, no infrastructure delta, and they fix 80% of the gaps in §4. Everything in Tiers 2–4 is optional ascent on top of a healthy Tier 1 foundation.

Build the foundation first. The fancy stuff comes later.

---

**Document version:** 1.0
**Author:** Synthesis self-audit (post-cycle-5-clean, post-fix bundle)
**Related:** `docs/embedding-stack-audit-2026-04-25.md`, `docs/heuristic-analyzer-audit.md`, `docs/ROADMAP.md`
