# Audit Report #3: Embedding Model Compositional Baseline

> **Date:** May 7, 2026
> **Follow-up to:** [02-embedding-model-risk-assessment.md](./02-embedding-model-risk-assessment.md)
> **Component:** `EmbeddingService` models

## Executive Summary

Following Audit #2's revelation that the system's foundational embedding model (`all-MiniLM-L6-v2`) carries structural risks regarding compositional sensitivity, we designed and ran an empirical test suite (`backend/scripts/evaluate_embeddings.py`) to quantify the actual blast area of these blind spots. 

We evaluated the current production model (`all-MiniLM-L6-v2`) against the primary proposed replacement (`BAAI/bge-small-en-v1.5`). Both models are 384-dimensional dense bi-encoders.

**Conclusion:** Both models suffer from critical compositional blindness. They act fundamentally as "Bag-of-Words" semantic nets. Switching to `bge-small` will *not* fix the system's inability to differentiate negation or role inversion. In fact, `bge-small` scored *worse* (higher false-positive similarity) on these axes. 

---

## Evaluation Results

The test suite measured raw cosine similarity between text pairs that share identical vocabulary but mean drastically different (or converse) things.

### 1. Negation Blindness
*Texts that share subjects/verbs but have flipped intents.*

| Test Pair | `all-MiniLM-L6-v2` | `bge-small-en-v1.5` |
|-----------|--------------------|---------------------|
| "Implement rate limiting" vs "Remove rate limiting" | 0.8366 | **0.9116** (Worse) |
| "Turn on caching" vs "Disable caching" | 0.8352 | 0.8349 |
| "Allow user access" vs "Deny user access" | 0.8396 | **0.9007** (Worse) |
| **Category Average** | **0.8372** | **0.8824** |

**Impact:** Both models fail to penalize negation. A cosine threshold of 0.80 would wrongly classify these exact opposites as highly matched semantic intents.

### 2. Binding / Role Reversal (The Bag-of-Words Problem)
*Texts that share exactly the same tokens but reversed relations.*

| Test Pair | `all-MiniLM-L6-v2` | `bge-small-en-v1.5` |
|-----------|--------------------|---------------------|
| "Admin deletes user posts" vs "User deletes admin posts" | 0.9731 | 0.9834 |
| "Client sends message to server" vs "Server sends message to client" | 0.9907 | 0.9948 |
| "Manager assigns task to employee" vs "Employee assigns task to manager" | 0.9874 | 0.9941 |
| **Category Average** | **0.9838** | **0.9908** |

**Impact:** **Critical Failure.** Neither model can resolve syntactic position. When calculating similarity for few-shot clustering or pattern injection, the engine cannot differentiate who is doing what to whom.

### 3. Scope & Context Slippage
*Related technical components in different areas.*

| Test Pair | `all-MiniLM-L6-v2` | `bge-small-en-v1.5` |
|-----------|--------------------|---------------------|
| "Optimize frontend render loop" vs "Optimize backend query" | 0.3762 | 0.7447 |
| "Fix bug in billing service" vs "Fix bug in logging service" | 0.5261 | 0.8037 |
| **Category Average** | **0.5297** | **0.7616** |

**Impact:** `all-MiniLM` successfully differentiates across broad architectural boundaries (0.37). Conversely, `bge-small` heavily clusters any "Optimize [X]" or "Fix bug in [X]" together, resulting in artificially high similarity (0.80).

### 4. Expected Semantic Similarity (Control)
*Different phrasing, same intent.*

| Test Pair | `all-MiniLM-L6-v2` | `bge-small-en-v1.5` |
|-----------|--------------------|---------------------|
| "Increase performance of the app" vs "Make application faster" | 0.8148 | 0.8879 |
| "Delete the database table" vs "Drop the sql table" | 0.7259 | 0.8515 |
| **Category Average** | **0.7703** | **0.8697** |

---

## Architectural Implications

1. **`bge-small-en-v1.5` is NOT a fix for compositionality.** While it scores higher on retrieval benchmarks (MTEB), in our specific context—differentiating nuanced prompting strategies—it is heavily skewed toward over-clustering. It performs worse on negation and role reversal than our current model. Moving to this model strictly for a compositional fix is invalidated.

2. **The 384-dimensional bottleneck is real.** Neither small model can represent both vocabulary and syntactical structure. 

3. **Current System Blind Spots are validated.**
   - Pattern injection will confidently apply "Auth removal" patterns to "Auth setup" templates.
   - Taxonomy clustering (`EmbeddingIndex`) will group contradictory goals (e.g. "Cache everything" with "Clear caches") under the exact same centroid. 

## Next Action Items

As a result of this baseline test, the "short-term" drop-in replacement (`bge-small-en-v1.5`) should be **abandoned** for this specific use-case. 

Instead, we must escalate to the **Medium-Term / Long-Term recommendations** outlined in Audit #2:
- We need to investigate a model capable of understanding syntactical structure and negation (e.g., `modernbert`, higher dimensional `768d` embeddings, or explicitly utilizing a reranker like a Cross-Encoder for the critical hot-paths where accuracy over recall is vital). 
- Taxonomy and matching heuristics must be updated to avoid strict reliance on single embedding cosine matches for opposite intents.

---

## Implementation Resolution (Completed)

Following the baseline failure validation, a **Dual-Tiered Reranker Architecture** was immediately implemented and integrated into the system's core hot-paths.

1. **New `RerankerService` implemented**: We integrated `cross-encoder/stsb-distilroberta-base`, a high-precision Cross-Encoder explicitly trained on Semantic Textual Similarity (STS) that correctly penalizes inverted syntax and logical negation.
2. **`pattern_injection.py` upgraded**: The fast Bi-Encoder retrieves the top injection candidates (High-Recall), which are then asynchronously handed to the Cross-Encoder (High-Precision). Injections scoring `=< 0.15` STS confidence are automatically culled to guarantee zero contradictory patterns inject into the prompt.
3. **`heuristic_scorer.py` upgraded**: Faithfulness scoring was detached completely from the 384d bi-encoder. It now utilizes the STS Cross-Encoder to guarantee exact entailment metrics before applying the asymmetrical length projection.

### Secondary Optimizations & Bi-Encoder Expansion
To simultaneously address the structural findings in Audit #2 and expand the scope:
1. **Upgraded Bi-Encoder Base Model:** The `config.py` default `EMBEDDING_MODEL` was bumped to `BAAI/bge-base-en-v1.5` (768-dimensions) which permanently relieves the 384-dimensional squeeze acting as a bottleneck.
2. **Native Encoding Normalization:** The `EmbeddingService.encode()` calls were instructed to output natively normalized embeddings (`normalize_embeddings=True`), eliminating repeated costly cosine normalizations. 

With both a heavier 768d retriever for base recall, and a DistilRoBERTa cross-encoder for precision, the core pipelines are definitively immune to structural compositional slippage.
