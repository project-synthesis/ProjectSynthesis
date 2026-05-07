# Audit Report #7: Taxonomy Matching Precision

> **Tier:** 🟡 Medium Effort | **Effort:** 1-2 weeks | **Impact:** Medium

## Problem Statement

Taxonomy matching uses a hierarchical cascade of threshold-only filters. No stage verifies that matched clusters are semantically appropriate — just that they exceed a cosine similarity floor. This is the article's "single-stage retrieval" problem applied to the taxonomy layer.

## Current Matching Flow

From `taxonomy/matching.py` and `pattern_injection.py`:

```
1. Embed prompt via composite fusion (5-signal blend)
2. Search embedding_index: threshold 0.45, k=5
3. Fetch MetaPatterns from matched cluster IDs
4. Include sub-domain parent patterns
5. Cross-cluster injection (global_source_count >= N)
6. GlobalPattern injection (1.3x relevance boost)
```

### Threshold-Only Gates

| Stage | Threshold | Verification | Risk |
|-------|-----------|-------------|------|
| Topic search | 0.45 | None | 🔴 Low threshold admits noise |
| Cross-cluster | `CROSS_CLUSTER_RELEVANCE_FLOOR` | Relevance formula | 🟡 Formula uses same embeddings |
| GlobalPattern | `CROSS_CLUSTER_RELEVANCE_FLOOR` | 1.3x boost | 🟡 Boost amplifies imprecision |
| Few-shot input | 0.50 | None | 🟠 Medium threshold |
| Few-shot output | 0.40 | None | 🔴 Low threshold |

### Specific Risks

**1. Centroid Averaging Dilutes Precision**

Cluster embeddings are centroids (averaged from member embeddings). Averaging smooths out compositional signals:

```
Cluster contains:
  - "implement caching with Redis"
  - "remove existing cache layer"
  - "cache invalidation strategies"
Centroid: average of all three → loses the implement/remove distinction
```

A query about "implement caching" matches the cluster at 0.55 similarity, but the cluster also contains "remove caching" patterns.

**2. Sub-Domain Parent Escalation**

```python
# pattern_injection.py:456-476
extended_ids = list(cluster_ids)
# ... fetch parent_ids ...
sub_domain_ids = [r[0] for r in sub_q.all()]
extended_ids.extend(sub_domain_ids)
```

Parent domains aggregate child patterns. This deliberately broadens the search — but without precision verification, broader search = more noise.

**3. Cross-Cluster Relevance Formula**

```python
# pattern_injection.py:553-554
cluster_score_factor = max(0.1, (cluster_avg_score or 5.0) / 10.0)
relevance = sim * math.log2(1 + mp.global_source_count) * cluster_score_factor
```

The formula multiplies cosine similarity × frequency × quality. **Frequency (global_source_count) can override low similarity.** A pattern used 8 times with cosine 0.35 scores `0.35 × log2(9) × 0.8 = 0.89` — above the floor despite low semantic relevance.

## Proposed Improvements

### 1. Pattern-Level Relevance Scoring (Quick Win)

Score individual patterns against the query, not just the cluster:

```python
# After fetching patterns from matched clusters:
for p in patterns:
    if p.embedding is not None:
        pat_emb = np.frombuffer(p.embedding, dtype=np.float32)
        pattern_sim = float(np.dot(prompt_embedding, pat_emb) / (
            np.linalg.norm(prompt_embedding) * np.linalg.norm(pat_emb) + 1e-9
        ))
        if pattern_sim < 0.35:  # Pattern-level floor
            logger.debug("Skipping low-relevance pattern: sim=%.3f", pattern_sim)
            continue
    injected.append(InjectedPattern(..., similarity=pattern_sim))
```

**Cost:** Each pattern already has an embedding. Cost is O(N) dot products where N = number of patterns in matched clusters (~5-20).

### 2. Intent Consistency Filter

After pattern selection, check that patterns don't contradict the query:

```python
from app.services.compositional_checker import detect_intent_inversion

# Filter patterns with inverted intent
verified_patterns = []
for p in injected_patterns:
    if detect_intent_inversion(raw_prompt, p.pattern_text):
        logger.info("intent_inversion_filtered: pattern='%s'", p.pattern_text[:80])
        continue
    verified_patterns.append(p)
```

### 3. Frequency Cap on Cross-Cluster

Cap the frequency multiplier to prevent popular-but-irrelevant patterns:

```python
# Current:
relevance = sim * math.log2(1 + mp.global_source_count) * cluster_score_factor

# Proposed: Cap log factor at 3.0 (~8 sources)
freq_factor = min(3.0, math.log2(1 + mp.global_source_count))
relevance = sim * freq_factor * cluster_score_factor
```

### 4. Cluster Quality Metadata

Track per-cluster precision metrics over time:

```python
# When pattern usefulness is recorded (T1.3-lite):
# Also update cluster-level precision estimate
if quality.verdict == "negation_flip":
    cluster.precision_issues += 1
    if cluster.precision_issues > 5:
        cluster.requires_manual_review = True
```

## Implementation Priority

1. **Pattern-level relevance scoring** — highest ROI, zero new dependencies
2. **Frequency cap** — one-line change, prevents quality-blind popularity inflation
3. **Intent consistency filter** — depends on compositional checker from Report #3
4. **Cluster quality metadata** — schema change, longer-term tracking
