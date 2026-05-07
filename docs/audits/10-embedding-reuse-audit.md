# Audit Report #10: Embedding Reuse Audit

> **Tier:** 🟢 Quick Win | **Effort:** 1-2 days | **Impact:** Low-Medium (latency + consistency)

## Problem Statement

The same prompt embedding is computed multiple times per pipeline run across different services. This wastes CPU cycles and creates a consistency risk — if the embedding model were swapped mid-run (edge case), different services would use different representations.

## Current Embedding Computation Flow

For a single pipeline run (internal tier, full enrichment):

| # | Service | Method | Input | Purpose |
|---|---------|--------|-------|---------|
| 1 | `pattern_injection.py` | `aembed_single(raw_prompt)` | Raw prompt | Pattern search |
| 2 | `taxonomy/fusion.py` | `aembed_single(raw_prompt)` | Raw prompt | Topic signal |
| 3 | `taxonomy/fusion.py` | `aembed_single(transformation_desc)` | Derived text | Transformation signal |
| 4 | `taxonomy/fusion.py` | `aembed_single(output_desc)` | Derived text | Output signal |
| 5 | `taxonomy/fusion.py` | `aembed_single(pattern_desc)` | Derived text | Pattern signal |
| 6 | `taxonomy/fusion.py` | `aembed_single(qualifier)` | Derived text | Qualifier signal |
| 7 | `repo_relevance.py` | `aembed_single(raw_prompt)` | Raw prompt | B0 gate |
| 8 | `repo_relevance.py` | `aembed_single(anchor)` | Repo synthesis | B0 gate |
| 9 | `repo_index_query.py` | `aembed_single(query)` | Query text | Codebase search |
| 10 | `heuristic_scorer.py` | `embed_single(original)` | Original prompt | Faithfulness |
| 11 | `heuristic_scorer.py` | `embed_single(optimized)` | Optimized prompt | Faithfulness |

**Total:** 11 embedding calls per pipeline run.

### Duplicate Computations

| Embedding Input | Computed By | Times |
|----------------|-------------|-------|
| `raw_prompt` | pattern_injection, fusion (topic), repo_relevance | **3×** |
| `raw_prompt` (as query) | repo_index_query | Often same text = **4×** |

**Waste:** 3 redundant embeddings × ~15ms each = ~45ms wasted per pipeline run.

## Proposed: Embedding Context Object

Create a per-request embedding cache that flows through the pipeline:

```python
@dataclass
class EmbeddingContext:
    """Per-request embedding cache to prevent redundant computation."""
    
    _cache: dict[str, np.ndarray] = field(default_factory=dict)
    _es: EmbeddingService = field(default_factory=EmbeddingService)
    _hit_count: int = 0
    _miss_count: int = 0
    
    def _cache_key(self, text: str) -> str:
        """Hash text for cache lookup."""
        return hashlib.sha256(text.encode()).hexdigest()[:16]
    
    async def embed(self, text: str) -> np.ndarray:
        """Embed text with caching."""
        key = self._cache_key(text)
        if key in self._cache:
            self._hit_count += 1
            return self._cache[key]
        
        self._miss_count += 1
        vec = await self._es.aembed_single(text)
        self._cache[key] = vec
        return vec
    
    def embed_sync(self, text: str) -> np.ndarray:
        """Synchronous embed with caching."""
        key = self._cache_key(text)
        if key in self._cache:
            self._hit_count += 1
            return self._cache[key]
        
        self._miss_count += 1
        vec = self._es.embed_single(text)
        self._cache[key] = vec
        return vec
    
    @property
    def stats(self) -> dict:
        total = self._hit_count + self._miss_count
        return {
            "hits": self._hit_count,
            "misses": self._miss_count,
            "hit_rate": self._hit_count / total if total else 0,
            "cache_size": len(self._cache),
        }
```

### Integration

In `pipeline.py`:

```python
async def run(self, ...):
    emb_ctx = EmbeddingContext()
    
    # Pass to pattern injection
    injected, cluster_ids = await auto_inject_patterns(
        raw_prompt, taxonomy_engine, db, trace_id,
        embedding_context=emb_ctx,
    )
    
    # Pass to enrichment (which passes to B0, codebase retrieval)
    enrichment = await enrichment_svc.enrich(
        raw_prompt, ..., embedding_context=emb_ctx,
    )
    
    # Pass to scorer
    scores = scorer.score_prompt(
        optimized_prompt, original=raw_prompt,
        embedding_context=emb_ctx,
    )
    
    # Log embedding efficiency
    logger.info(
        "embedding_reuse: %s trace_id=%s",
        emb_ctx.stats, trace_id,
    )
```

### Service Changes

Each service that creates an `EmbeddingService()` instance adds an optional `embedding_context` parameter:

```python
# In pattern_injection.py:
async def auto_inject_patterns(
    raw_prompt, taxonomy_engine, db, trace_id,
    ...,
    embedding_context: EmbeddingContext | None = None,
):
    if embedding_context:
        prompt_embedding = await embedding_context.embed(raw_prompt)
    else:
        embedding_svc = EmbeddingService()
        prompt_embedding = await embedding_svc.aembed_single(raw_prompt)
```

**Backward compatible:** All callers without `embedding_context` continue to work unchanged.

## Expected Impact

| Metric | Before | After |
|--------|--------|-------|
| Embedding calls per pipeline run | 11 | 8 (3 cached) |
| Embedding latency | ~165ms | ~120ms |
| Consistency risk | Multiple independent computations | Single source of truth |
| Cache hit rate | N/A | ~27% (3/11) |

## Implementation

1. Create `EmbeddingContext` in `backend/app/services/embedding_context.py`
2. Thread through `pipeline.py` → `pattern_injection.py` → `context_enrichment.py`
3. Add `embedding_context` optional param to all embedding consumers
4. Log stats per request for validation
