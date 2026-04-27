# Multinomial Naive Bayes Migration Assessment

_Authored 2026-04-27. Source: `docs/heuristic_analyzer_optimization_suggestions.md` §1 (only Open item)._

## Context

`docs/heuristic_analyzer_optimization_suggestions.md` (item §1, "Naive Bayes Classification over Additive Weights") is the only **Open** item in that file — every other suggestion (asymmetrical faithfulness, negation awareness, structural density, telemetry mining, semantic feature engineering) has shipped between v0.4.2 and v0.4.5. The remaining gap is the additive-weight task type classifier in `backend/app/services/task_type_classifier.py`, which currently:

- Sums hardcoded `_TASK_TYPE_SIGNALS` weights with a 2× first-sentence boost (`score_category`, lines 480–512).
- Requires manual curation of `_TECHNICAL_NOUNS` (~120 tokens, lines 188–263) and `_STATIC_COMPOUND_SIGNALS` (lines 149–152) to patch blind spots.
- Confidence is `min(1.0, max_score)` — not a real probability — so the A4 Haiku fallback gates (`_LLM_CLASSIFICATION_CONFIDENCE_GATE=0.40`, `_LLM_CLASSIFICATION_MARGIN_GATE=0.10`) fire on raw weight magnitudes rather than calibrated posteriors.
- Cannot exploit the active-learning oracle that already exists: `TaskTypeTelemetry` (migration `2f3b0645e24d`) records every heuristic-vs-LLM disagreement, but `signal_adjuster.py` only mines new tokens at a fixed weight=0.5 — no co-occurrence learning, no class-prior updates.

A Multinomial NB classifier closes this loop: it consumes the same telemetry already being collected, produces calibrated `P(class | tokens)` posteriors, and updates incrementally without retraining a neural model.

---

## What Becomes Obsolete or Redundant

### Fully replaceable
| Component | File / lines | Why obsolete |
|---|---|---|
| Additive scoring core | `task_type_classifier.py:score_category` (480–512) | NB log-posterior sum supersedes weighted regex hits with a true probability output. |
| Manual weight tuning of `_TASK_TYPE_SIGNALS` single-keyword entries | `task_type_classifier.py` (25–143) | NB learns weights from `TaskTypeTelemetry` corpus per-class. Bootstrap dict survives only as a cold-start prior. |
| `_STATIC_SINGLE_SIGNALS` snapshot + B6 fallback merge logic | `task_type_classifier.py:162–165, 389–450` (`set_task_type_signals`) | Only needed because dynamic TF-IDF could erase bootstrap baselines. NB stores per-class token likelihoods directly; no merge dance. |
| TF-IDF extractor as a signal source | `task_type_signal_extractor.py` (full file) | Discriminative-token mining duplicates what NB's likelihood ratio already captures. Could be deleted entirely or repurposed for vocabulary auditing. |
| `signal_adjuster.adjust_signals_from_telemetry` | `signal_adjuster.py` (50–217) | Replaced by NB partial-fit on the same telemetry rows. The `signal_adjusted` taxonomy event becomes `nb_likelihood_updated`. |
| 2× first-sentence positional boost | `task_type_classifier.py:510–511` | NB learns positional importance via two parallel feature vocabularies (first-sentence tokens vs. body tokens) — no hand-coded multiplier. |

### Reduced in scope (kept as features, not standalone logic)
| Component | New role |
|---|---|
| A1 compound signals (`_STATIC_COMPOUND_SIGNALS`) | Bigram/trigram features fed to NB. Manual curation no longer needed; the table seeds the feature vocabulary. |
| A2 technical verb+noun disambiguation (`check_technical_disambiguation`) | Single binary feature `tech_verb_noun_pair_in_first_sentence`. The flip logic in `heuristic_analyzer.py:288–310` deletes; NB handles the `creative→coding` shift natively when the feature fires. |
| B2 `has_technical_nouns()` rescue | Two binary features: `first_sentence_has_tech_noun`, `body_has_tech_noun`. Rescue logic stays only as a profile-selection bridge in `context_enrichment.py:141`. |
| B5+ task-type lock (lead-verb writing rescue) | Becomes a single rule-feature `first_word_is_writing_lead_verb`. `pipeline_phases.resolve_post_analyze_state` keeps the lock as a tie-breaker only when NB margin < 0.1. |
| `rescue_task_type_via_structural_evidence` | Features: `has_snake_case`, `has_pascal_with_separator`, `has_tech_noun`. The tuple-returning rescue becomes redundant once NB consumes them directly. |

### Stays untouched (orthogonal)
- `weakness_detector.py` (`_is_negated`, `_compute_structural_density`) — operates on prompt structure, not class membership.
- `DomainSignalLoader` / domain classification — domains use organic vocabulary scoring; only task type migrates.
- `EmbeddingIndex` / `QualifierIndex` — vector-space pipeline, distinct concern.
- A4 Haiku fallback — keeps firing, but on calibrated NB posterior probability instead of weight magnitude.

---

## What Can Augment Synthesis / Taxonomy / Embeddings

### 1. Calibrated confidence flowing into routing decisions
Real `P(class | prompt)` lets the system reason about uncertainty instead of magnitude. Specific consumers that gain:

- **A4 fallback gating** (`task_type_classifier.py:312–346`): replace dual-gate (`confidence < 0.40` AND `margin < 0.10`) with a single posterior-entropy gate. Higher-quality fallback selection → fewer wasted Haiku calls (the 15–20% rate cited in CLAUDE.md likely drops to ~8–10%).
- **Context enrichment profile selection** (`context_enrichment.py:102–143`): currently boolean (`code_aware`/`knowledge_work`/`cold_start`). With posteriors, profile can become a soft mixture — e.g. when `P(coding)=0.55, P(analysis)=0.40`, blend codebase context retrieval with knowledge-work patterns instead of choosing one.
- **Few-shot retrieval** (`FEW_SHOT_MMR_LAMBDA=0.6`): MMR diversification can weight by class-posterior overlap, surfacing examples that match the prompt's class distribution rather than its argmax.

### 2. Embedding-augmented features (hybrid NB)
The codebase already runs `EmbeddingService` (MiniLM, 384-dim, CPU). An NB feature set that combines:
- **Token features** (multinomial term counts from `extract_first_sentence` + body)
- **Quantized embedding features** (k-means cluster IDs over the 384-dim vector, or sign bits per dimension → a 384-bit hash)

…gives NB semantic generalization without abandoning its lightweight profile. This is structurally the same as the existing 5-signal composite fusion in `PhaseWeights` — the NB classifier becomes another well-typed signal feeding `compute_score_correlated_target()`.

### 3. Telemetry → live model updates (closed loop)
- `TaskTypeTelemetry` rows already carry `(raw_prompt, task_type, domain, source)`. Warm Phase 4.76 currently feeds `signal_adjuster`; redirect to `nb_classifier.partial_fit(prompt_tokens, task_type)` on rows where `source='haiku_fallback'` (the high-confidence oracle).
- Add a `nb_classifier.disagreement_score` field to `TaxonomyEventLogger` so drift becomes observable in the Taxonomy Observatory (`TaxonomyObservatory.svelte`).
- The existing `ClassificationAgreement` singleton already tracks heuristic-vs-LLM hit rate — this metric becomes the NB classifier's accuracy proxy without new instrumentation.

### 4. Taxonomy benefits
- **Sub-domain emergence vocabulary** (`compute_qualifier_cascade` in `sub_domain_readiness.py`): the cascade's third source (TF-IDF `signal_keywords`) can be replaced with NB's per-class top likelihood ratios — better discriminators because NB normalizes by class prior automatically.
- **Cluster-level task-type purity**: each `PromptCluster` could store an aggregated NB posterior over its members. Phase 5 sub-domain creation could gate on posterior entropy (low entropy = coherent task type → eligible for sub-domain split). Replaces some of the heuristic consistency math in `domain_resolver`.
- **`set_task_type_signals` warm path** simplifies: instead of merging dynamic TF-IDF into a global dict, the NB model holds the parameters; the warm path just calls `partial_fit` and emits `nb_updated`.

### 5. Embeddings benefits
- **`EmbeddingIndex` filter functions** can take an NB-derived task-type filter natively (predicate over posterior threshold) without a separate task-type column lookup.
- **Multi-embedding blending weights** (raw 0.55, optimized 0.20, transformation 0.15, qualifier 0.10) could be NB-modulated per task type — coding prompts weight `qualifier` higher (technical vocabulary is discriminative), creative weights `raw` higher.
- **Repo relevance gate (B0)**: `compute_repo_relevance` returns `(cosine, info_dict)`; NB posterior over `coding|system|data` becomes a second gate that complements the cosine floor — high-NB-coding + low-cosine-to-anchor is a stronger "wrong project" signal than either alone.

---

## Recommended Migration Approach

### Phase 1 — Shadow mode (zero behavior change)
1. Add `backend/app/services/nb_classifier.py` with `MultinomialNB` (scikit-learn or hand-rolled — pure NumPy keeps the no-heavy-deps profile).
2. Train initial model from `TaskTypeTelemetry` rows where `source='haiku_fallback'` (high-quality labels). Bootstrap with `_TASK_TYPE_SIGNALS` as Laplace-smoothed pseudo-counts so cold-start works.
3. In `heuristic_analyzer.py:265–276`, run NB **alongside** existing classifier; record both into a new `NbShadowAgreement` table or extend `TaskTypeTelemetry` with `nb_prediction` + `nb_confidence` columns.
4. Add `nb_*` events to `TaxonomyEventLogger`.
5. Surface accuracy delta on the Taxonomy Observatory for 2–3 weeks of real traffic.

### Phase 2 — Cutover (NB primary, heuristic fallback)
1. Promote NB to primary classifier; current additive scorer becomes the fallback when NB is uninitialized (cold-start under `MIN_TRAINING_SAMPLES=50`).
2. Replace A4 dual-gate with `posterior_entropy > 0.7` (~40% threshold equivalent on uniform posterior).
3. Delete `task_type_signal_extractor.py` and the dynamic-merge path in `set_task_type_signals` (lines 389–450). Keep `_STATIC_COMPOUND_SIGNALS` as feature vocabulary seed only.
4. Wire `signal_adjuster.py` → `nb_classifier.partial_fit()` on Phase 4.76.

### Phase 3 — Augmentation (downstream consumers)
1. `context_enrichment.py:102–143` — soft-mixture profile selection when top-2 posterior gap < 0.2.
2. `sub_domain_readiness.py:compute_qualifier_cascade` — swap third source (TF-IDF) for NB top-likelihood-ratio tokens.
3. `EmbeddingIndex.search` — accept optional `task_type_posterior_filter` callable.
4. `repo_relevance.compute_repo_relevance` — return `nb_task_type_posterior` as third tuple element.

---

## Critical Files

| File | Role | Phase touched |
|---|---|---|
| `backend/app/services/nb_classifier.py` | New: model, partial-fit, persistence (pickle to `data/nb_model.pkl`) | 1 |
| `backend/app/services/task_type_classifier.py` | Demote `score_category`, keep `extract_first_sentence` + `has_technical_nouns` as feature extractors | 2 |
| `backend/app/services/heuristic_analyzer.py:265–346` | Layer 1 calls NB; Layer 1c uses entropy gate | 2 |
| `backend/app/services/signal_adjuster.py` | Pivot to NB `partial_fit` | 2 |
| `backend/app/services/task_type_signal_extractor.py` | Delete | 2 |
| `backend/app/services/context_enrichment.py:102–143` | Soft-mixture profile | 3 |
| `backend/app/services/taxonomy/sub_domain_readiness.py` | NB-derived discriminators in cascade | 3 |
| `backend/app/models.py` (`TaskTypeTelemetry`) | Add `nb_prediction`, `nb_confidence` columns + Alembic migration | 1 |
| `backend/app/services/observability/taxonomy_event_logger.py` | New event types `nb_classified`, `nb_updated`, `nb_disagreement` | 1 |
| `frontend/.../TaxonomyObservatory.svelte` | Surface NB accuracy + drift panel | 1 |

## Existing Utilities to Reuse (No Reinvention)

- `extract_first_sentence()` in `task_type_classifier.py:348–363` — already handles code fences, markdown tables, interior-dot regex. Becomes the NB feature extractor's tokenization entry point.
- `has_technical_nouns()` (619–662) — interior-token splitting for `asyncio.gather`-style identifiers; reuse as a binary feature encoder.
- `EmbeddingService.embed_single()` (`embedding_service.py`) — provides 384-dim vectors if Phase-4 hybrid features are pursued.
- `TaxonomyEventLogger` — JSONL + ring buffer dual-write infrastructure; new event types slot in directly.
- `_apply_cross_process_dirty_marks` SSE bridge — same pattern reused for `nb_updated` events from MCP/CLI processes.

## Verification

- **Phase 1 shadow**: query `TaskTypeTelemetry` for `nb_prediction = task_type` agreement rate; target ≥85% before cutover. Acceptance threshold visible in `/api/health` extension.
- **Phase 2 cutover regression**: re-run `cd backend && pytest tests/services/test_task_type_classifier.py -v` (existing fixture should pass with NB primary). Add `tests/services/test_nb_classifier.py` covering cold-start, partial-fit idempotency, and prior-only fallback.
- **End-to-end**: synthesize 10 prompts representing all 7 task types via `synthesis_create` MCP tool, verify task_type assignments + confidence values via `/api/optimizations` listing.
- **Drift dashboard**: confirm `nb_disagreement` events flow into `ActivityPanel` and `TaxonomyObservatory` PatternDensityHeatmap.
- **A4 fallback rate**: compare Haiku invocation count over 7-day windows pre/post Phase 2; expect 30–50% reduction.

---

## Out of Scope (explicit)

- Domain classifier migration (separate concern, organic vocabulary already works well).
- Replacing `weakness_detector.py` heuristics (orthogonal axis).
- Multi-label classification (NB is single-label; soft mixture handled at consumer level).
- Neural classifier upgrade (DistilBERT etc.) — NB's incremental update + zero-LLM cost is the explicit constraint from the source doc.
