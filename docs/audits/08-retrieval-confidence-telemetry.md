# Audit Report #8: Retrieval Confidence Telemetry

> **Tier:** 🟢 Quick Win | **Effort:** 2-3 days | **Impact:** Medium — enables all other improvements

## Problem Statement

The system has **zero observability** on retrieval quality. We cannot measure, detect, or alert on the 40% degradation the article warns about because we don't track retrieval confidence metrics.

## Current Observability Gaps

| Metric | Status | Why It Matters |
|--------|--------|---------------|
| Pattern injection precision | ❌ Not tracked | Can't detect compositional failures |
| Retrieval score distribution | ❌ Not tracked | Can't detect embedding degradation |
| Top-k score spread | ❌ Not tracked | Can't detect ambiguous retrieval |
| Retrieval-to-outcome correlation | ❌ Not tracked | Can't measure if retrieval helps |
| Embedding model drift | ❌ Not tracked | Can't detect model quality changes |

### What IS Tracked

- `_injection_provenance_successes/failures` — DB write success, not retrieval quality
- `pattern_useful_count` / `pattern_unused_count` — outcome-based, delayed signal
- Cluster similarity map — logged but not aggregated
- Curated retrieval diagnostics — logged but not persisted

## Proposed Telemetry Schema

### 1. Per-Request Retrieval Metrics

Add to `OptimizationMetrics` or a new `RetrievalMetrics` table:

```python
@dataclass
class RetrievalTelemetry:
    """Retrieval quality metrics for a single pipeline run."""
    trace_id: str
    
    # Pattern injection metrics
    pattern_search_k: int                  # How many candidates searched
    pattern_matches_above_threshold: int   # How many passed threshold
    pattern_top_similarity: float          # Best match score
    pattern_score_spread: float            # max - min of returned scores
    pattern_mean_similarity: float         # Average match score
    pattern_domains_returned: int          # Number of distinct domains
    
    # Codebase retrieval metrics (if applicable)
    codebase_files_searched: int
    codebase_files_above_threshold: int
    codebase_top_relevance: float
    codebase_budget_utilization: float     # chars_used / chars_max
    codebase_diversity_excluded: int
    
    # Few-shot retrieval metrics (if applicable)
    fewshot_candidates_scored: int
    fewshot_above_threshold: int
    fewshot_top_similarity: float
    
    # B0 gate metrics
    b0_cosine: float
    b0_adjusted_score: float
    b0_decision: str                       # "pass" | "skip"
    b0_identifier_match: bool
    
    # Aggregate quality estimate
    retrieval_confidence: float            # Composite 0.0-1.0
    
    created_at: datetime
```

### 2. Integration Points

**Pattern injection (`pattern_injection.py`):**

```python
# After auto_inject_patterns():
telemetry = RetrievalTelemetry(
    trace_id=trace_id,
    pattern_search_k=5,
    pattern_matches_above_threshold=len(matches),
    pattern_top_similarity=matches[0][1] if matches else 0.0,
    pattern_score_spread=(
        matches[0][1] - matches[-1][1] if len(matches) > 1 else 0.0
    ),
    pattern_mean_similarity=(
        sum(m[1] for m in matches) / len(matches) if matches else 0.0
    ),
    pattern_domains_returned=len({cluster_meta.get(m[0], ("", ""))[1] for m in matches}),
    # ... other fields
)
logger.info(
    "retrieval_telemetry: top=%.3f spread=%.3f mean=%.3f domains=%d trace_id=%s",
    telemetry.pattern_top_similarity,
    telemetry.pattern_score_spread,
    telemetry.pattern_mean_similarity,
    telemetry.pattern_domains_returned,
    trace_id,
)
```

**Codebase retrieval (`repo_index_query.py`):**

The `CuratedCodebaseContext` dataclass already has most fields. Extend logging:

```python
# Already exists but add:
logger.info(
    "retrieval_telemetry_codebase: files=%d above_threshold=%d "
    "top=%.3f budget=%.0f%% diversity_excluded=%d",
    result.total_files_indexed,
    result.files_included,
    result.top_relevance_score,
    result.budget_used_chars / max(result.budget_max_chars, 1) * 100,
    result.diversity_excluded_count,
)
```

### 3. Aggregate Dashboard Metrics

Add to health endpoint (`/api/health`):

```python
# In health router:
"retrieval_quality": {
    "last_100_pattern_top_sim_mean": ...,
    "last_100_pattern_top_sim_p10": ...,   # 10th percentile — worst cases
    "last_100_codebase_budget_util_mean": ...,
    "last_100_b0_skip_rate": ...,
    "injection_provenance_success_rate": ...,
}
```

### 4. SSE Events for Real-Time Monitoring

Emit retrieval quality events for frontend observability:

```python
# During pipeline run:
event_bus.publish("retrieval_quality", {
    "trace_id": trace_id,
    "pattern_confidence": telemetry.retrieval_confidence,
    "codebase_confidence": telemetry.codebase_top_relevance,
    "b0_decision": telemetry.b0_decision,
})
```

## Implementation Plan

### Day 1: Structured Logging

Add structured log lines (no schema changes, no DB writes):

```python
# In pattern_injection.py, after search:
logger.info(
    "retrieval_quality pattern_top=%.3f spread=%.3f matches=%d "
    "domains=%d trace_id=%s",
    top_sim, spread, n_matches, n_domains, trace_id,
)
```

### Day 2: Persist to Optimization Row

Add `retrieval_metrics` JSON column to `Optimization` model:

```python
# In models.py:
retrieval_metrics = Column(JSON, nullable=True)

# In pipeline.py persist phase:
opt.retrieval_metrics = {
    "pattern_top": top_sim,
    "pattern_spread": spread,
    "pattern_count": n_matches,
    "codebase_files": n_files,
    "codebase_budget_pct": budget_pct,
    "b0_cosine": b0_cosine,
    "confidence": composite_confidence,
}
```

### Day 3: Health Endpoint Integration

Aggregate metrics in health response for frontend dashboard.

## Alerting Thresholds

| Metric | Warning | Critical | Action |
|--------|---------|----------|--------|
| `pattern_top_sim_p10` | <0.50 | <0.40 | Embedding model may be degrading |
| `b0_skip_rate` | >20% | >40% | Repo link may be stale |
| `codebase_budget_util` | <30% | <15% | Index may need rebuild |
| `injection_success_rate` | <95% | <90% | DB persistence issue |
| `pattern_mean_sim` | <0.45 | <0.35 | Threshold may need adjustment |
