# Heuristic Analyzer Hardening & Optimization Suggestions

_Authored 2026-04-22. Some items have since shipped — status markers added 2026-04-24._

Based on the audit of the `HeuristicAnalyzer` and its sub-components, here are several architectural and algorithmic optimizations to address its current blind spots while maintaining its Zero-LLM performance profile.

## 1. Algorithmic Upgrades (Zero-LLM)

### Naive Bayes Classification over Additive Weights
**Status:** Open.
**Current State:** The task type classifier relies on an additive weight system with a 2x positional boost for the first sentence.
**Optimization:** Replace or augment the hardcoded weighting with a local **Multinomial Naive Bayes (NB)** classifier. NB models are extremely lightweight (pure math, no neural networks), can be updated incrementally (online learning), and handle the statistical co-occurrence of words far better than independent regex weights. It would eliminate the need for manual `_TECHNICAL_NOUNS` disambiguation patching.

### Asymmetrical Faithfulness Scoring
**Status:** Shipped — v0.4.2 (fix I-5). `heuristic_faithfulness()` in `backend/app/services/heuristic_scorer.py` now uses an asymmetrical log-length projection: `projection = cosine(original, optimized) * log(max(l1, l2)) / log(l1)` with both lengths floored at 40 chars to prevent log-underflow and projection capped at 1.0. Expansions recover their cosine penalty organically via the log-ratio boost; contractions (`l2 ≤ l1`) fall through to raw cosine because `max` collapses to `l1`. A piecewise score map projects `[0,1] → [1,10]` with a 9–10 band at projection ≥ 0.85. Supersedes the strategy-kwarg + `EXPANSION_STRATEGIES` dampener approach originally scoped under I-5 — no `strategy_used` arg has to thread through `score_prompt()` / `pipeline_phases` / `sampling_pipeline` / `batch_pipeline` / `refinement_service`.

## 2. Weakness Detection Hardening

### Negation Awareness in Regex
**Status:** Shipped — v0.4.2. `_is_negated()` helper in `weakness_detector.py` checks for `not`, `no`, `without`, `avoid`, `never`, `don't`, `doesn't`, `won't`, `shouldn't`, `cannot`, `can't` preceding weakness keywords in a small window and suppresses the positive signal.
**Current State:** `weakness_detector.py` checks for the presence of words like "must" or "return" to assign strengths.
**Original proposal retained as reference:** Introduce a lightweight negation look-behind in the regex patterns (e.g., checking for `not`, `no`, `without`, `skip` within a 3-word window preceding the constraint keyword).

### Context-Aware Density Scoring
**Status:** Shipped — v0.4.2. `_compute_structural_density()` in `weakness_detector.py` lets structured prompts under 50 words with high structural density skip the `underspecified` flag.
**Current State (pre-fix):** The system flagged prompts as "underspecified" if they were under 50 words for complex tasks (`coding`, `system`).
**Original proposal retained as reference:** Structure should influence the density check. A 30-word prompt structured entirely as a YAML schema might be perfectly specified. The weakness detector should cross-reference the `HeuristicScorer`'s structure metrics to suppress length-based warnings when information density is highly structured.

## 3. Telemetry and Learning Pipelines

### Proactive Signal Mining from Haiku
**Status:** Shipped — v0.4.4. `TaskTypeTelemetry` model + migration `2f3b0645e24d` (v0.4.2) record every heuristic-vs-LLM classification event. `signal_adjuster.py` (v0.4.4) consumes telemetry via warm Phase 4.76, merging high-confidence tokens into `_TASK_TYPE_SIGNALS` at weight 0.5. Closes the active-learning loop — what was C2 Open in `enrichment-consolidation-action-items.md` is now wired end-to-end.
**Implementation note:** The A4 Haiku fallback fires on ~15-20% of edge-case prompts; each invocation persists a telemetry row, and the warm-path adjuster mines those rows into live signal tweaks every cycle.

### Semantic Feature Engineering
**Status:** Shipped — v0.4.5 (PR #55). `extract_first_sentence()` in `task_type_classifier.py` tightens the regex from `re.split(r"[.?!]")` to `re.split(r"[.?!](?=\s|$)")` so module-method dots (`asyncio.gather`) no longer truncate the boundary mid-token. Pre-strip of code fences + markdown tables remains a follow-up — but the most acute boundary leak (interior dots in identifier names) is now closed via this regex tightening + the `has_technical_nouns()` interior-token split that decomposes `backend/app/services/...` into its constituent vocabulary tokens.
