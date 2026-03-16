# SHA-Based Explore Caching — Spec

## Problem

Every optimization with a linked repo runs the full explore pipeline (GitHub API calls + embedding + Haiku synthesis) even when the repo hasn't changed. This wastes API calls, tokens, and adds ~5-10s latency.

## Solution

Cache explore results keyed by `(repo_full_name, branch, head_sha, prompt_hash)`. A new push changes HEAD SHA, causing automatic cache miss. Same prompt + same repo state = instant cache hit.

### Cache Design

- **Key**: `f"{repo_full_name}:{branch}:{head_sha}:{prompt_hash}"` where `prompt_hash = hashlib.sha256(raw_prompt.encode()).hexdigest()[:16]`
- **Value**: The synthesized context string (output of explore)
- **Storage**: In-memory dict with TTL (default: `EXPLORE_RESULT_CACHE_TTL` = 3600s / 1 hour)
- **Max entries**: 100 (LRU eviction when full)
- **Invalidation**: Automatic via SHA — new push = new SHA = cache miss. Manual via `invalidate(repo_full_name)`.

### Flow

```
explore(raw_prompt, repo, branch, token)
  → get HEAD SHA via lightweight API call
  → compute cache key from (repo, branch, sha, prompt_hash)
  → if cache hit → return cached context immediately
  → if cache miss → run full explore pipeline → cache result → return
```

### Staleness Detection

Before returning a cached result, the explorer should already have the current HEAD SHA (fetched as part of the cache key computation). If the SHA differs from the indexed SHA in `repo_index_meta`, trigger a background re-index (non-blocking) so the semantic index catches up for future requests.

## Scope

- New class: `ExploreCache` — in-memory TTL cache with LRU eviction
- Modified: `CodebaseExplorer.explore()` — check cache before running pipeline, cache after
- New config: `EXPLORE_RESULT_CACHE_TTL` (default 3600) in `config.py`
- Tests: cache hit, cache miss, SHA invalidation, TTL expiry, LRU eviction

## Non-Goals

- No persistent (disk/Redis) caching — in-memory is sufficient for single-user tool
- No background re-index triggering (would need task queue — noted for future)
