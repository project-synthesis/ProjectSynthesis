# Agentic RAG Pipeline Audit — Part 2: Deep-Dive Component Analysis

> **Date:** 2026-05-07
> **Companion:** [Part 1 — Framework Mapping & Tiered Recommendations](./agentic-rag-audit-part1.md)

---

## Table of Contents

1. [Retrieval Pipeline Deep-Dive](#1-retrieval-pipeline-deep-dive)
2. [Knowledge Graph (Taxonomy Engine) Deep-Dive](#2-knowledge-graph-taxonomy-engine-deep-dive)
3. [Scoring & Validation Deep-Dive](#3-scoring--validation-deep-dive)
4. [Learning & Adaptation Deep-Dive](#4-learning--adaptation-deep-dive)
5. [Enrichment Orchestration Deep-Dive](#5-enrichment-orchestration-deep-dive)
6. [Implementation Guidance Per Tier](#6-implementation-guidance-per-tier)
7. [Risk Assessment & Production Tradeoffs](#7-risk-assessment--production-tradeoffs)
8. [Conclusion & Prioritized Action Items](#8-conclusion--prioritized-action-items)

---

## 1. Retrieval Pipeline Deep-Dive

### 1.1 Codebase Retrieval (`repo_index_query.py`)

The codebase retrieval system implements a sophisticated multi-stage search:

```
embed(prompt) → semantic search → source-type balancing → diversity filter
    → dependency graph expansion (imports + markdown refs) → budget packing
```

**Strengths against Agentic RAG principles:**

- **Source-type balancing**: Max 3 results per source type prevents any single file type from dominating. This is a form of diversity-aware retrieval that the article's Level 2 recommends.
- **Dependency graph expansion**: After initial semantic results, the system follows import statements and markdown references to include related files. This is a primitive form of **multi-hop retrieval** — retrieved file A's imports lead to file B.
- **Budget-constrained packing**: Character budget prevents context window overflow. The system packs highest-relevance files first, truncating lower-ranked ones.

**Gaps mapped to Agentic RAG levels:**

| Gap | Article Level | Current State | Recommendation |
|---|---|---|---|
| No per-chunk relevance validation | Level 2 | All retrieved chunks injected blindly | Add post-retrieval cosine floor filter (Tier 4.3) |
| Uniform budget allocation | Level 2 | Same char budget regardless of task type | Scale codebase budget by task_type (Tier 4.2) |
| No re-query on empty results | Level 2 | Returns empty context silently | Add broader-threshold retry on zero results (Tier 3.1) |
| Single embedding query | Level 2 | One query vector for all sources | Decompose into sub-queries per source (Tier 2.1) |

### 1.2 Pattern Injection (`pattern_injection.py`)

Pattern injection uses the composite fusion system to find and inject relevant historical patterns:

```
build_composite_query(5 signals) → fuse(phase_weights) → search taxonomy index
    → rank by relevance × log2(source_count) × cluster_score_factor
    → inject top-N patterns with provenance tracking
```

**Strengths:**
- **Composite query fusion** is the system's most advanced retrieval feature. The 5-signal blend (topic, transformation, output, pattern, qualifier) with per-phase weight profiles is more sophisticated than anything the article describes at Level 2.
- **Provenance tracking** via `OptimizationPattern` records which patterns contributed to each optimization — this enables the feedback loop (thumbs_up → source_count boost).
- **Cross-cluster patterns** (global_source_count ≥ threshold) inject proven techniques from unrelated domains.

**Gaps:**
- **No retrieval confidence signal**: After injection, the system doesn't assess whether the injected patterns are actually useful for this specific prompt. A pattern may have high global_source_count but low relevance to the current prompt.
- **No adaptive injection depth**: The number of patterns injected is fixed. A prompt about a well-covered topic should get more patterns; a novel prompt should get fewer (or none) to avoid noise injection.

### 1.3 Relevance Gating (`repo_relevance.py`)

The B0 relevance gate is a pre-retrieval quality check:

```
cosine_similarity(prompt_embedding, repo_embedding)
    + domain_vocab_overlap_score
    + identifier_matching_score
    → combined_score > threshold → allow codebase injection
```

**Assessment:** This is a **Level 2 self-correction** primitive — it validates whether retrieval *should happen* before executing it. The multi-signal approach (embedding + vocabulary + identifiers) is robust against single-signal failures. However:

- It's binary (inject or don't) rather than graduated (inject with confidence weighting)
- It doesn't re-route to alternative sources on rejection — a failed B0 gate means no codebase context, period

---

## 2. Knowledge Graph (Taxonomy Engine) Deep-Dive

### 2.1 Structure vs. Graph RAG

The article describes **Graph RAG** as building knowledge graphs with typed relationships for multi-hop reasoning. Our taxonomy engine has:

**What we have (structural knowledge base):**
```
Domain → Cluster → Family → MetaPattern
                           → GlobalPattern (cross-cluster promoted)
```

- **Hierarchical parent-child relationships** via `PromptCluster.parent_id`
- **Cluster membership** via `Optimization.cluster_id`
- **Cross-cluster promotion** via `GlobalPattern` with cross-project/cross-cluster gates
- **Learned cluster metadata**: coherence, avg_score, member_count, UMAP 3D projection
- **Lifecycle management**: candidate → active → mature → retired (warm path phases)

**What Graph RAG would add:**
- **Typed edges**: `SIMILAR_TO(weight=0.85)`, `DERIVED_FROM`, `CONTRADICTS`, `SUPERSEDES`
- **Edge traversal queries**: "Find all patterns related to 'authentication' within 2 hops that have avg_score > 7"
- **Relationship reasoning**: "Pattern A contradicts Pattern B — which one applies to this prompt?"
- **Temporal edges**: "Pattern A was superseded by Pattern B after feedback cycle N"

### 2.2 Taxonomy Matching (`matching.py`)

The hierarchical cascade search implements a 2-level multi-hop pattern:

```
Level 1: Search leaf families (FAMILY_MATCH_THRESHOLD=0.55)
    → On miss: Level 2: Search parent clusters (CLUSTER_MATCH_THRESHOLD=0.45)
        → Aggregate top-3 child patterns ranked by cosine to query
    → Cross-cluster patterns: Always search global patterns (CROSS_CLUSTER_MIN_SOURCE_COUNT)
```

**Adaptive thresholds** per cluster coherence via `suggestion_threshold()` — high-coherence clusters use tighter thresholds, low-coherence clusters are more permissive. Cold-start candidates use the strictest `CANDIDATE_THRESHOLD=0.65`.

**This is the closest thing to multi-hop retrieval in the system.** Level 1 → Level 2 fallback is a form of broadening the search scope on failure, and the cosine-ranked aggregation of child patterns from the matched parent is a form of result synthesis.

**Gap:** The hop is structural (parent-child), not semantic (content-derived). True multi-hop would be: "Retrieved Pattern A mentions 'rate limiting' → generate new query for 'rate limiting patterns' → retrieve again."

### 2.3 Warm Path Lifecycle

The warm path runs 7+ phases on a cadence (every ~5 minutes):

```
Phase 0:   Reconcile (member counts, avg scores, centroid updates)
Phase 0.5: Evaluate candidates (promote or reject)
Phase 1:   Split/Emerge (speculative, Q-gated)
Phase 2:   Merge (speculative, Q-gated)
Phase 3:   Retire (speculative, Q-gated)
Phase 4:   Refresh (index rebuild, signal refresh)
Phase 4.25: Sub-domain pattern aggregation
Phase 4.5:  Global pattern promotion/validation
Phase 4.75: Task-type signal refresh (TF-IDF)
Phase 5:   Discover (new domain detection)
Phase 5.5: Archive (sub-domain GC)
Phase 6:   Audit (snapshot creation)
```

**This is a sophisticated self-organizing knowledge graph** that the article would classify as Level 3 infrastructure. The Q-gate system (quality-gated speculative operations with rollback) ensures the taxonomy never degrades — every structural change must be non-regressive.

**Key Agentic RAG insight:** The warm path's `compute_score_correlated_target()` function implements **score-correlated weight adaptation** — it identifies which embedding weight profiles correlate with high optimization scores and moves the system's weights toward them. This is a form of **long-term memory** where the system learns which retrieval signals produce the best outcomes.

### 2.4 Cold Path (HDBSCAN Refit)

The cold path (`cold_path.py`) is the "defrag" operation — full reclustering with HDBSCAN + UMAP 3D projection + OKLab coloring. It runs with:

- **Concurrent invocation serialization** via `_COLD_PATH_LOCK`
- **Peer-writer quiescing** via `refit_in_progress_until` metadata flag
- **4-phase chunked execution** with per-phase Q-gates
- **Typed exceptions** distinguishing Q-regression (controlled) from phase failure (unexpected)

**Agentic RAG assessment:** This is production-grade knowledge graph maintenance that ensures the taxonomy remains useful for retrieval. The Q-gate system prevents bad refits from corrupting the graph — this is a form of **self-correction at the infrastructure level**.

---

## 3. Scoring & Validation Deep-Dive

### 3.1 Hybrid Scoring (`score_blender.py`)

The scoring system blends LLM judgments with model-independent heuristics:

```
For each dimension (clarity, specificity, structure, faithfulness, conciseness):
    1. Get LLM score (from dedicated scorer LLM call)
    2. Get heuristic score (regex/embedding-based, zero-LLM)
    3. Z-score normalize LLM component (when historical stats available)
    4. Weighted blend: score = (1-w_h) × LLM_normalized + w_h × heuristic
    5. Detect divergence (|LLM - heuristic| > 2.5)
```

**Dimension-specific heuristic weights:**
- Structure: 0.50 (heuristic very reliable — regex detects headers/lists/tags)
- Clarity: 0.40 (precision signals + ambiguity detection)
- Specificity: 0.40 (constraint counting)
- Conciseness: 0.20 (TTR penalizes domain-term repetition; bumped to 0.35 for technical prompts via C3)
- Faithfulness: 0.20 (embedding similarity is rough)

**Agentic RAG assessment:** This is a sophisticated **self-correction** mechanism at the scoring level. The z-score normalization breaks LLM score clustering (same-family bias per Wataoka et al. 2024), and the divergence detection flags dimensions where the two scoring sources disagree — enabling human review of contested scores.

**Gap:** Divergence is *detected and logged* but not *acted upon*. A Level 2 self-correction system would re-query or re-score when divergence exceeds a threshold.

### 3.2 Heuristic Scorer (`heuristic_scorer.py`)

Five independent scoring heuristics, each using regex/embedding analysis:

| Dimension | Method | Key Feature |
|---|---|---|
| Structure | Markdown headers + XML sections + lists + format mentions | Additive scoring with tiered bonuses |
| Conciseness | Type-Token Ratio + structural density + filler penalty | Technical-prompt calibration (TTR multiplier for domain-dense text) |
| Specificity | 11 constraint categories with graduated density scoring | Density bonus for concentrated specificity in shorter prompts |
| Clarity | Precision signals + ambiguity penalty | Context-aware ambiguity detection (skips identifiers) |
| Faithfulness | Asymmetrical projection metric (cosine + log length ratio) | Handles expansion/contraction asymmetry without penalizing length increase |

**Agentic RAG insight:** The faithfulness heuristic's asymmetrical projection is notable — it mathematically compensates for the fact that optimized prompts are usually longer than originals. `projection = similarity × (log(max(l1,l2)) / log(l1))` boosts the cosine similarity for expansions while preserving penalties for contractions. This is the kind of domain-specific scoring that a generic LLM judge would miss.

### 3.3 Heuristic Analyzer (`heuristic_analyzer.py`)

The zero-LLM classification pipeline:

```
Layer 1:  Keyword classification (compound keywords, TF-IDF signals)
Layer 1b: Technical verb disambiguation (A2 — coding reclassification)
Layer 1c: Confidence-gated LLM fallback (A4 — Haiku call for ambiguous ~15-20%)
Layer 2:  Structural signals (code blocks, lists, question form)
Layer 3:  Weakness/strength detection (negation-aware)
Layer 4:  Strategy selection (historical learning → adaptation → static fallback)
Layer 5:  Intent label generation (verb + noun phrase extraction)
```

**Agentic RAG assessment:** The A4 confidence-gated LLM fallback is a **cost-aware self-correction** pattern. Instead of always calling the LLM for classification (expensive), it uses heuristics first and only falls back to Haiku when confidence is low AND top categories are close (margin < gate). This balances the article's concern about "higher latency, cost, and complexity" at Level 3.

---

## 4. Learning & Adaptation Deep-Dive

### 4.1 Fusion Weight Adaptation (`fusion.py`)

The system learns optimal retrieval signal weights through a multi-layered adaptation system:

**Bootstrap → Context → Cluster Learning → Score Correlation:**

1. **Phase defaults**: Hardcoded per-phase weight profiles (e.g., analysis phase weights topic at 0.55)
2. **Task-type bias**: Directional offsets per task type (coding boosts transform by +0.15)
3. **Cluster learned weights**: Blended at alpha=0.3 from cluster's historical profile
4. **Score-correlated adaptation**: `compute_score_correlated_target()` uses z-score weighted mean of historical profiles, selecting weights that correlate with high optimization scores

**Bayesian shrinkage** (T1.1): `posterior = (n/(n+κ)) × empirical + (κ/(n+κ)) × prior` with κ=8. At n=2 (minimum), prior carries 80%; at n=8, equal contribution; at n=24, empirical dominates 75/25. This eliminates the previous ≥10 hard threshold that blocked small clusters from learning.

**Agentic RAG insight:** This is the system's most sophisticated **long-term memory** mechanism. It directly maps to the article's description of Level 3 memory: "learning from past successes for future efficiency." The Bayesian shrinkage ensures the system learns even from sparse data without overfitting.

### 4.2 Strategy Adaptation (`adaptation_tracker.py`)

User feedback drives strategy selection:

```
feedback(thumbs_up/down) → AdaptationTracker.update_affinity()
    → approval_rate recomputation
    → degenerate detection (>90% same rating, 10+ feedbacks)
    → blocked strategy gate (approval_rate < 0.3, 5+ feedbacks)
```

The `HeuristicAnalyzer._select_strategy()` uses a 3-level priority chain:
1. Historical learning (avg_score ≥ 6.0 across ≥ 3 completed optimizations)
2. Adaptation tracker affinities (approval_rate > 0.6, not blocked)
3. Static fallback map (task_type → default strategy)

### 4.3 Feedback-Driven Pattern Reinforcement (`feedback_service.py`)

Positive feedback cascades through the system:

```
thumbs_up → MetaPattern.source_count += 1
          → AdaptationTracker.update_affinity()
          → GlobalPattern promotion eligibility check
          → SSE event for real-time UI update
```

**Quiesce-aware**: Feedback writes check the host cluster's `refit_in_progress_until` flag and skip pattern updates during cold-path refits to prevent concurrent modification.

---

## 5. Enrichment Orchestration Deep-Dive

### 5.1 Context Enrichment (`context_enrichment.py`)

The orchestrator uses profile-based gating to decide which context layers to activate:

| Profile | Trigger | Layers Activated |
|---|---|---|
| `code_aware` | B0 gate passes (codebase relevant) | Codebase + strategy + patterns |
| `knowledge_work` | B0 fails, patterns available | Strategy + patterns (no codebase) |
| `cold_start` | No patterns, no codebase relevance | Strategy only (minimal context) |

**Agentic RAG assessment:** This is a form of **adaptive source routing** — the system decides which retrieval sources to query based on prompt characteristics. However, it's a static 3-profile system rather than a dynamic per-query routing decision.

### 5.2 Strategy Intelligence (`strategy_intelligence.py`)

Merges three signal sources into a single strategy advisory:

1. **Score-based rankings**: Top/bottom strategies by (task_type, domain) from Optimization history
2. **User feedback affinities**: Approval rates per strategy from AdaptationTracker
3. **Domain vocabulary**: Keyword signals from DomainSignalLoader

**C1 domain-relaxed fallback**: When exact (domain + task_type) match returns nothing, falls back to task_type-only across all domains. This is a form of **retrieval broadening on miss** — a Level 2 pattern.

---

## 6. Implementation Guidance Per Tier

### Tier 4.1 — Retrieval Confidence Scoring

**What:** After `auto_inject_patterns()` completes, compute a confidence score for the injected set.

**How:** In `pattern_injection.py`, after the patterns are selected:

```python
# After ranking and selecting top-N patterns
if injected_patterns:
    avg_relevance = sum(p.relevance for p in injected_patterns) / len(injected_patterns)
    min_relevance = min(p.relevance for p in injected_patterns)
    confidence = avg_relevance * (1 - 0.3 * (avg_relevance - min_relevance))  # penalize high variance
    
    if confidence < RETRIEVAL_CONFIDENCE_FLOOR:
        # Inject signal into optimizer context
        context_parts.append(
            "⚠️ Pattern retrieval confidence is low — the injected patterns may not be "
            "directly relevant. Prioritize the user's original intent over pattern suggestions."
        )
```

**Why it's high ROI:** Zero architectural change. One function, one constant, one conditional string injection. Immediately improves optimization quality for prompts where pattern retrieval is noisy.

### Tier 3.1 — Retrieval Validation Gate

**What:** Add a post-retrieval validation step that checks whether retrieved context is actually useful.

**How:** Create `retrieval_validator.py`:

```python
class RetrievalValidator:
    def validate(self, prompt_embedding, retrieved_chunks, threshold=0.25):
        """Filter retrieved chunks by cosine similarity to prompt."""
        valid = []
        for chunk in retrieved_chunks:
            chunk_emb = self.embed(chunk.content)
            sim = cosine_similarity(prompt_embedding, chunk_emb)
            if sim >= threshold:
                valid.append((chunk, sim))
            else:
                logger.info("Dropped low-relevance chunk: sim=%.3f < %.3f", sim, threshold)
        
        if len(valid) < len(retrieved_chunks) * 0.5:
            # More than half dropped — signal low retrieval quality
            return valid, "low_confidence"
        return valid, "normal"
```

Wire into `context_enrichment.py` between the retrieval and injection steps.

### Tier 2.1 — Query Decomposition Engine

**What:** Before enrichment, analyze the prompt for multiple intents and decompose into sub-queries.

**Architecture:**

```python
class QueryDecomposer:
    async def decompose(self, prompt: str, analysis: HeuristicAnalysis) -> list[SubQuery]:
        """Decompose a multi-intent prompt into sub-queries.
        
        Uses heuristic detection first (sentence splitting, intent verb counting).
        Falls back to Haiku LLM decomposition for complex cases.
        """
        # Heuristic: count distinct intent verbs in different sentences
        sentences = split_sentences(prompt)
        intent_clusters = cluster_by_intent_verb(sentences)
        
        if len(intent_clusters) <= 1:
            return [SubQuery(text=prompt, intent="primary")]
        
        # Multiple intents detected — generate sub-queries
        sub_queries = []
        for cluster in intent_clusters:
            sq = SubQuery(
                text=" ".join(cluster.sentences),
                intent=cluster.primary_verb,
                target_sources=self._route_sources(cluster, analysis),
            )
            sub_queries.append(sq)
        return sub_queries
```

**Integration point:** `PipelineOrchestrator.run()` would call decomposition after analysis, run enrichment per sub-query, then merge context sets before optimization.

### Tier 2.2 — Formal Reflection Phase

**Architecture:**

```python
class ReflectionService:
    async def reflect(self, original_prompt, optimized_prompt, injected_context) -> ReflectionResult:
        """Post-optimization reflection check.
        
        Returns:
            ReflectionResult with:
                - gaps: list of intents from original not addressed in optimized
                - contradictions: conflicts between optimized and injected context
                - confidence: overall quality assessment
                - re_retrieve: whether to loop back to enrichment
        """
```

Wire into `pipeline.py` as an optional phase between optimize and score. Gate by preference (`pipeline.enable_reflection`).

---

## 7. Risk Assessment & Production Tradeoffs

### Latency Impact per Tier

| Tier | Added Latency | Mitigation |
|---|---|---|
| 4.x (Quick Wins) | < 50ms | Pure computation, no LLM calls |
| 3.1 (Validation Gate) | 50-200ms | Embedding computation only |
| 3.2 (Phase Skipping) | **-200ms to -2s** | Reduces latency by skipping unnecessary phases |
| 3.3 (Graph-Aware Retrieval) | 100-500ms | DB queries + cosine computation |
| 2.1 (Query Decomposition) | 200-500ms heuristic, 2-5s with LLM | Gate LLM decomposition by prompt complexity |
| 2.2 (Reflection) | 3-10s | Full LLM call; gate by preference |
| 1.x (Architectural) | Variable | Complete pipeline redesign required |

### Backward Compatibility

All Tier 4 and Tier 3 changes are **fully backward-compatible** — they add new capabilities without changing existing interfaces. Tier 2 changes require new pipeline phases but can be gated by preferences. Tier 1 changes are breaking and require a migration plan.

### Cost Impact

| Tier | LLM Cost Impact | Rationale |
|---|---|---|
| 4.x | None | Zero LLM calls added |
| 3.1 | None | Heuristic validation only |
| 3.2 | **Reduced** | Skipping phases saves LLM calls |
| 3.3 | None | DB/embedding queries only |
| 2.1 | +1 Haiku call (conditional) | Only for complex multi-intent prompts |
| 2.2 | +1 Haiku/Sonnet call | Gated by preference; can use cheaper model |
| 1.1 | +1-3 calls per loop iteration | Agent loop may iterate 2-4 times |

---

## 8. Conclusion & Prioritized Action Items

### Where We Stand

Project Synthesis operates at **Level 2 Agentic RAG** with significant Level 3 infrastructure. The composite fusion system, taxonomy engine, and score-correlated weight adaptation are more sophisticated than what the article describes at any level. The primary gaps are in **query decomposition**, **formal self-correction loops**, and **session memory**.

### Recommended Priority Sequence

```
Phase 1 (Week 1-2):   Tier 4 quick wins — retrieval confidence, budget rebalancing, 
                       post-retrieval filter, enrichment telemetry
                       
Phase 2 (Week 3-4):   Tier 3.1 + 3.2 — retrieval validation gate + adaptive phase 
                       skipping (reduces cost while improving quality)
                       
Phase 3 (Week 5-8):   Tier 3.3 + 3.4 — graph-aware retrieval + session context 
                       (biggest retrieval quality improvement without architectural change)
                       
Phase 4 (Month 3-4):  Tier 2.1 + 2.2 — query decomposition + reflection phase 
                       (requires careful integration testing)
                       
Phase 5 (Month 5+):   Tier 1.x architectural shifts — only if Tier 2 results show 
                       diminishing returns from the current linear pipeline
```

### Key Metrics to Track

| Metric | Purpose | Source |
|---|---|---|
| Retrieval confidence (avg, p10) | Validate Tier 4.1 | New: `pattern_injection.py` |
| Phase skip rate | Validate Tier 3.2 | `pipeline.py` telemetry |
| Sibling pattern hit rate | Validate Tier 3.3 | `taxonomy/matching.py` |
| Multi-intent detection rate | Size Tier 2.1 need | New: `query_decomposer.py` |
| Reflection loop-back rate | Validate Tier 2.2 value | New: `reflection_service.py` |
| Overall score improvement Δ | All tiers | `score_blender.py` existing |

### Bottom Line

> The system's greatest strength is its **long-term memory infrastructure** (fusion weight adaptation, taxonomy lifecycle, feedback-driven reinforcement). The greatest gap is the **absence of a closed-loop retrieval cycle** — the system retrieves once and trusts the result. Adding retrieval validation (Tier 3.1) and graph-aware pattern retrieval (Tier 3.3) would close the most impactful gaps with moderate effort, while the quick wins (Tier 4) provide immediate value with minimal risk.
