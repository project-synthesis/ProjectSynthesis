# Audit Report #4: Retrieval Validation & Self-Correction

> **Tier:** 🟠 Significant Effort | **Effort:** 2-3 weeks | **Impact:** High

## Article Finding

> "Agentic RAG must self-correct. When a retrieval doesn't match, the agent should reformulate the query or fall back to broader search — not blindly inject noise."

## Current State: Trust-Without-Verify

The pipeline currently performs single-pass retrieval with **no post-retrieval quality assessment**:

```
pattern_injection → results accepted → injected into optimizer prompt
codebase_retrieval → results accepted → injected into optimizer prompt
taxonomy_matching → results accepted → used for strategy selection
```

There is no mechanism to:
1. Assess whether retrieved results actually answer the query
2. Re-query with different parameters on low confidence
3. Fall back gracefully when retrieval produces noise

### Evidence from Codebase

**Pattern injection (`pattern_injection.py:427-503`):**
```python
matches = embedding_index.search(search_embedding, k=5, threshold=0.45)
# ↑ Search happens
# ↓ Results immediately used — no quality check
for p in patterns:
    injected.append(InjectedPattern(...))
```

**Codebase retrieval (`repo_index_query.py:569-605`):**
```python
for idx, score in selected:
    if not _pack_file(row, score, source):
        budget_skips += 1
        # ↑ Only budget-based rejection, no relevance re-assessment
```

**Context enrichment (`context_enrichment.py`):**
- Enrichment layers execute sequentially
- Each layer's output is accepted
- No cross-layer validation (e.g., "does the codebase context align with the matched patterns?")

## Proposed: RetrievalValidator Service

### Architecture

```
┌─────────────────────────────────────────────────┐
│                  Pipeline Phase                  │
├─────────────────────────────────────────────────┤
│  1. Retrieval (existing)                         │
│     └─ Pattern search, codebase search, etc.    │
│  2. Validation (NEW)                             │
│     ├─ Confidence scoring per result             │
│     ├─ Cross-result coherence check              │
│     └─ Retrieval quality verdict                 │
│  3. Decision gate (NEW)                          │
│     ├─ PASS → inject into optimizer              │
│     ├─ RETRY → re-query with broader threshold   │
│     └─ SKIP → omit this retrieval source         │
└─────────────────────────────────────────────────┘
```

### Implementation

```python
@dataclass
class RetrievalQuality:
    """Assessment of a retrieval result set."""
    confidence: float           # 0.0-1.0 aggregate
    coherence: float            # How well results agree with each other
    coverage: float             # What fraction of query intent is covered
    verdict: Literal["pass", "retry", "skip"]
    retry_params: dict | None   # Modified search params for retry
    diagnostics: dict           # Full observability

class RetrievalValidator:
    """Post-retrieval quality gate for pattern injection and codebase context."""
    
    # Confidence thresholds (tuned against historical data)
    PASS_THRESHOLD = 0.65
    RETRY_THRESHOLD = 0.35
    MAX_RETRIES = 2
    
    def assess_pattern_injection(
        self,
        query: str,
        results: list[InjectedPattern],
        query_embedding: np.ndarray,
    ) -> RetrievalQuality:
        """Assess pattern injection quality."""
        if not results:
            return RetrievalQuality(
                confidence=0.0, coherence=0.0, coverage=0.0,
                verdict="skip", retry_params=None,
                diagnostics={"reason": "empty_results"},
            )
        
        # Signal 1: Score distribution — tight cluster = ambiguous
        scores = [r.similarity for r in results]
        score_spread = max(scores) - min(scores)
        score_mean = sum(scores) / len(scores)
        
        # Signal 2: Top-result gap — big gap = confident top match
        top_gap = scores[0] - scores[1] if len(scores) > 1 else 0.5
        
        # Signal 3: Domain coherence — mixed domains = noisy retrieval
        domains = {r.domain for r in results}
        domain_coherence = 1.0 / len(domains)  # Fewer domains = more coherent
        
        confidence = (
            0.4 * min(1.0, score_mean / 0.7) +   # High scores = good
            0.3 * min(1.0, top_gap / 0.15) +       # Clear winner = good
            0.3 * domain_coherence                   # Domain focus = good
        )
        
        if confidence >= self.PASS_THRESHOLD:
            verdict = "pass"
            retry_params = None
        elif confidence >= self.RETRY_THRESHOLD:
            verdict = "retry"
            retry_params = {
                "threshold": max(0.30, 0.45 - 0.10),  # Broaden
                "k": min(15, 5 * 2),                    # More candidates
            }
        else:
            verdict = "skip"
            retry_params = None
        
        return RetrievalQuality(
            confidence=round(confidence, 3),
            coherence=round(domain_coherence, 3),
            coverage=round(score_mean, 3),
            verdict=verdict,
            retry_params=retry_params,
            diagnostics={
                "score_spread": round(score_spread, 3),
                "top_gap": round(top_gap, 3),
                "n_domains": len(domains),
                "n_results": len(results),
            },
        )
```

### Integration into Pipeline

In `pipeline.py`, wrap the existing enrichment call:

```python
# Before: single-pass enrichment
enrichment = await enrichment_svc.enrich(raw_prompt, ...)

# After: validated enrichment with retry loop
validator = RetrievalValidator()
for attempt in range(validator.MAX_RETRIES + 1):
    enrichment = await enrichment_svc.enrich(raw_prompt, ..., **retry_params)
    quality = validator.assess_pattern_injection(
        raw_prompt, enrichment.injected_patterns, prompt_embedding,
    )
    if quality.verdict == "pass":
        break
    elif quality.verdict == "retry" and attempt < validator.MAX_RETRIES:
        retry_params = quality.retry_params
        logger.info("retrieval_retry attempt=%d confidence=%.3f", attempt, quality.confidence)
        continue
    else:
        logger.warning("retrieval_skip: confidence=%.3f after %d attempts", quality.confidence, attempt)
        enrichment.injected_patterns = []  # Clear noisy results
        break
```

### Adaptive Phase Skipping

When retrieval quality is consistently low for a prompt type, skip the enrichment phase entirely:

```python
# In pipeline.py, before enrichment:
if analysis.confidence < 0.3 and analysis.task_type == "general":
    logger.info("adaptive_skip: low-confidence general prompt, skipping enrichment")
    enrichment = EmptyEnrichment()
```

## Metrics to Track

| Metric | Purpose | Alert Threshold |
|--------|---------|----------------|
| `retrieval_confidence_mean` | Baseline quality | <0.5 over 50 runs |
| `retrieval_retry_rate` | How often retry fires | >30% |
| `retrieval_skip_rate` | How often results are dropped | >15% |
| `retry_improvement_rate` | Does retry actually help? | <50% (retry isn't working) |
| `injected_pattern_count_post_validation` | Effective injection rate | Monitor trend |
