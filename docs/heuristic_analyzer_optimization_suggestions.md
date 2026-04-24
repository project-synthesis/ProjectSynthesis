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
**Status:** Partial — telemetry shipped, active-learning pipeline pending. `TaskTypeTelemetry` model + migration `2f3b0645e24d` (v0.4.2) record every heuristic-vs-LLM classification event (`raw_prompt`, `task_type`, `domain`, `source`) for drift analysis + A4 tuning. A `signal_adjuster.py` service that consumes telemetry into a live keyword-weight learning loop is still planned (tracked as C2 in ROADMAP "LLM domain classification — remaining optimizations").
**Optimization (as proposed):** Use the A4 Haiku fallback as an **Active Learning oracle**. Every time Haiku is invoked to resolve an ambiguous prompt (the 15-20% edge cases), pipe that prompt's keywords directly into a high-priority learning queue.

### Semantic Feature Engineering
**Status:** Partial. First-sentence splitting uses `re.split(r"[.?!]", prompt_lower, maxsplit=1)[0]` as of v0.4.0 (fixes the `?`/`!` terminator bug). A code-block / markdown-table stripping preprocessor ahead of first-sentence extraction is still open.
**Current State:** The classifier splits on `.?!` to find the "first sentence" and boost its keywords.
**Remaining work:** Implement a lightweight pre-processor that strips code blocks and markdown tables *before* extracting the first sentence. This prevents a `python` keyword inside a log dump from being falsely weighted as the primary intent of the prompt.
