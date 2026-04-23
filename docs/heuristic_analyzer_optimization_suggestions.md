# Heuristic Analyzer Hardening & Optimization Suggestions

Based on the audit of the `HeuristicAnalyzer` and its sub-components, here are several architectural and algorithmic optimizations to address its current blind spots while maintaining its Zero-LLM performance profile.

## 1. Algorithmic Upgrades (Zero-LLM)

### Naive Bayes Classification over Additive Weights
**Current State:** The task type classifier relies on an additive weight system with a 2x positional boost for the first sentence. 
**Optimization:** Replace or augment the hardcoded weighting with a local **Multinomial Naive Bayes (NB)** classifier. NB models are extremely lightweight (pure math, no neural networks), can be updated incrementally (online learning), and handle the statistical co-occurrence of words far better than independent regex weights. It would eliminate the need for manual `_TECHNICAL_NOUNS` disambiguation patching.

### Asymmetrical Faithfulness Scoring
**Current State:** Faithfulness relies on Cosine Similarity between embeddings. Because cosine similarity measures exact vector alignment, expansion strategies (like `chain-of-thought` or `meta-prompting`) artificially score lower because they add substantial "framing" text. The system currently uses a "strategy-aware dampener" to hack the score back up.
**Optimization:** Implement an **asymmetrical inclusion metric**. Instead of calculating bidirectional similarity, calculate how much of the original prompt's semantic vector is preserved *within* the expanded prompt's embedding space (e.g., using projection or token-level overlap). This removes the need for hardcoded strategy exemptions.

## 2. Weakness Detection Hardening

### Negation Awareness in Regex
**Current State:** `weakness_detector.py` checks for the presence of words like "must" or "return" to assign strengths. A prompt saying "You do not need to return anything specific and there are no constraints" will trigger positive constraint and outcome flags.
**Optimization:** Introduce a lightweight negation look-behind in the regex patterns (e.g., checking for `not`, `no`, `without`, `skip` within a 3-word window preceding the constraint keyword). 

### Context-Aware Density Scoring
**Current State:** The system flags prompts as "underspecified" if they are under 50 words for complex tasks (`coding`, `system`).
**Optimization:** Structure should influence the density check. A 30-word prompt structured entirely as a YAML schema might be perfectly specified. The weakness detector should cross-reference the `HeuristicScorer`'s structure metrics (like `n_xml_sections`) to suppress length-based warnings when information density is highly structured.

## 3. Telemetry and Learning Pipelines

### Proactive Signal Mining from Haiku
**Current State:** The TF-IDF dynamic signal loader waits for 30 organic samples of a task type to update the single-word keyword list. 
**Optimization:** Use the A4 Haiku fallback as an **Active Learning oracle**. Every time Haiku is invoked to resolve an ambiguous prompt (the 15-20% edge cases), pipe that prompt's keywords directly into a high-priority learning queue. This allows the heuristic classifier to rapidly adapt to new technical jargon (e.g., a new framework that initially confuses the heuristic) without waiting for 30 passive occurrences.

### Semantic Feature Engineering
**Current State:** The classifier splits on `.?!` to find the "first sentence" and boost its keywords.
**Optimization:** Code snippets and markdown often break basic sentence splitting. Implement a lightweight pre-processor that strips code blocks and markdown tables *before* extracting the first sentence. This prevents a `python` keyword inside a log dump from being falsely weighted as the primary intent of the prompt.
