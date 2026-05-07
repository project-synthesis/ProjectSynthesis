# Audit Report #9: Semantic Cache Precision

> **Tier:** 🟢 Quick Win | **Effort:** 1-2 days | **Impact:** Medium

## Problem Statement

The `_curated_cache` in `repo_index_query.py` and other caching layers use truncated prompt prefixes as cache keys. Structurally similar but semantically different prompts may collide.

## Current Cache Implementation

**File:** `repo_index_query.py:36-44`

```python
_curated_cache: dict[str, tuple[float, object]] = {}
_CURATED_CACHE_TTL = 300

# Cache key construction (line 373-375):
cache_key = hashlib.sha256(
    f"{repo_full_name}:{branch}:{query[:200]}:{task_type}:{domain}".encode()
).hexdigest()[:16]
```

### Collision Risks

**1. Prompt prefix truncation (200 chars)**

```
Prompt A: "Implement rate limiting for the API endpoint that handles 
           user authentication and session management with Redis backend..."
Prompt B: "Implement rate limiting for the API endpoint that handles 
           user authentication and session management WITHOUT Redis..."
```

Both share the same first 200 characters. Cache key collision → Prompt B gets Prompt A's results.

**2. SHA-256 truncation (16 hex chars = 64 bits)**

Birthday paradox: with 64-bit keys, collision probability reaches 1% at ~600M entries. Not a practical risk for the 100-entry cache cap, but the combination with prefix truncation is.

**3. Task-type sensitivity**

The cache key includes `task_type` and `domain` — but these are derived from the prompt. If two prompts with different intents are classified the same, the cache can't distinguish them.

## Article Connection

The article warns about compositional sensitivity — small semantic changes (negation, scope modifiers) that don't change the prompt surface form. These are exactly the kind of changes that survive a 200-char prefix truncation.

## Proposed Fixes

### Fix 1: Full-Prompt Hashing (Immediate)

```python
# Before:
cache_key = hashlib.sha256(
    f"{repo_full_name}:{branch}:{query[:200]}:{task_type}:{domain}".encode()
).hexdigest()[:16]

# After:
cache_key = hashlib.sha256(
    f"{repo_full_name}:{branch}:{query}:{task_type}:{domain}".encode()
).hexdigest()[:16]
```

**Cost:** SHA-256 on full prompt instead of prefix. Negligible — SHA-256 processes ~500MB/s, prompt is <10KB.

### Fix 2: Embedding-Based Cache Keys (Targeted)

For cases where semantically equivalent but lexically different prompts SHOULD share a cache:

```python
def _semantic_cache_key(
    query: str,
    query_embedding: np.ndarray | None,
    repo_full_name: str,
    branch: str,
) -> str:
    """Cache key using quantized embedding for semantic equivalence."""
    if query_embedding is not None:
        # Quantize embedding to 8-bit for stable hashing
        quantized = (query_embedding * 127).astype(np.int8)
        emb_hash = hashlib.sha256(quantized.tobytes()).hexdigest()[:8]
    else:
        emb_hash = hashlib.sha256(query.encode()).hexdigest()[:8]
    
    repo_hash = hashlib.sha256(f"{repo_full_name}:{branch}".encode()).hexdigest()[:8]
    return f"{repo_hash}:{emb_hash}"
```

**Trade-off:** Embedding quantization introduces its own precision loss. Only use when semantic caching is desired (e.g., "implement auth" and "add authentication" should share cache).

### Fix 3: Cache Validation on Hit

Add a lightweight check on cache hits:

```python
cached = _curated_cache.get(cache_key)
if cached and (time.time() - cached[0]) < _CURATED_CACHE_TTL:
    # NEW: Validate cache hit is for the same query intent
    cached_query = cached[2]  # Store original query with cache entry
    if cached_query == query:  # Exact match
        return cached[1]
    # Prefix match but different suffix — invalidate
    logger.info("cache_invalidation: prefix collision detected")
    del _curated_cache[cache_key]
```

## Other Caching Layers to Audit

| Cache | Location | Key Method | Risk |
|-------|----------|-----------|------|
| `_curated_cache` | `repo_index_query.py` | SHA-256 of prefix | 🟠 Truncation |
| Template cache | `prompt_loader.py` | File path | 🟢 Safe |
| Strategy cache | `strategy_loader.py` | Strategy name | 🟢 Safe |
| Signal cache | `task_type_classifier.py` | In-memory dict | 🟢 Safe (keyed by full data) |
| Embedding cache | `embedding_service.py` | None (no caching) | 🟢 N/A |

## Implementation

1. **Fix 1** — one line change, deploy immediately
2. **Fix 3** — store original query in cache tuple, validate on hit
3. **Fix 2** — evaluate after telemetry shows cache hit/miss patterns
