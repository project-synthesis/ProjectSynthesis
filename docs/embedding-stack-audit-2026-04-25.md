# Embedding Stack Audit — 2026-04-25

E2E validation cycle on 2026-04-25 prompted a focused audit of the embedding compute path. The user asked: "Focus on our embeddings and vectors and how we compute them, I believe there is a legacy system and we aren't leveraging our tech stack fully for this."

This document maps the full stack, calls out what's legacy / underused, and ranks fixes by leverage. Findings #1 and #2 below are unrelated to embeddings but were caught by the same cycle and shipped with the embedding-relevant fix as PR #55.

---

## Stack inventory

| Layer | File | Role | Backed by |
|-------|------|------|-----------|
| Model + sync wrappers | `backend/app/services/embedding_service.py` | `embed_single` / `embed_texts` / `aembed_single` / `aembed_texts` + naive `cosine_search` | `sentence-transformers 5.3.0`, model: `all-MiniLM-L6-v2` (384-dim) |
| Cluster index | `backend/app/services/taxonomy/embedding_index.py` | In-memory ANN index: numpy default, HNSW at ≥1000 clusters | `hnswlib`, fallback `numpy` |
| Output index | `backend/app/services/taxonomy/optimized_index.py` | Per-cluster centroid of optimized prompts | `numpy` |
| Transform index | `backend/app/services/taxonomy/transformation_index.py` | `optimized − raw` direction-of-improvement vector index | `numpy` |
| Qualifier index | `backend/app/services/taxonomy/qualifier_index.py` | Per-cluster qualifier centroid (sub-domain emergence signal) | `numpy` |
| Fusion | `backend/app/services/taxonomy/fusion.py` | 5-signal `PhaseWeights` blend (raw + optimized + transform + pattern + qualifier), L2-normalized | pure numpy |
| Retrieval | `backend/app/services/repo_index_service.py` | Per-file content + embed pipeline for `RepoFileIndex` | sentence-transformers via `EmbeddingService` |
| Match | `backend/app/services/taxonomy/matching.py` | Hierarchical 2-level cascade (family 0.55, cluster 0.45, candidate 0.65) | `EmbeddingIndex` |
| Domain detection | `backend/app/services/domain_detector.py` | Domain qualifier embedding for cluster scoring | `EmbeddingService.aembed_single` |

## Compute volume per persist (hot path)

`engine.py:599-633` — for every persisted optimization the system embeds **3 separate texts sequentially**:

1. `raw_prompt` → `aembed_single`
2. `optimized_prompt` → `aembed_single`
3. `qualifier_text` (when qualifier present) → `aembed_single`, behind a `loader.get_cached_qualifier_embedding(cache_key)` LRU

Plus the heuristic / B0 / cosine-search paths each call `aembed_single` independently (with no shared cache between the request and the persist phase).

Typical hot-path embed count per optimize: **3–5 forward passes**. On CPU that's ~150–500 ms of pure model inference per optimization.

---

## Findings — ranked by leverage

### 🔥 1. Async/concurrency vocabulary missing from `_TECHNICAL_NOUNS` (FIXED in PR #55)

Code prompts about `asyncio.gather`, `coroutine cancellation`, `mutex acquisition` etc. fell through the heuristic profile selector to `knowledge_work` because none of those nouns matched. The fix added 7 unambiguously-technical tokens (`asyncio`, `coroutine`, `eventloop`, `mutex`, `semaphore`, `deadlock`, `savepoint`) and made the matcher split interior dots/hyphens so `asyncio.gather` produces the sub-token `asyncio`. Live re-fire of the demoted prompt confirmed `code_aware` activation + 30 patterns injected.

Not strictly an embeddings issue but it gates whether codebase-aware embedding signals reach the optimizer at all.

### 🔥 2. `/api/history` was silent on enrichment activation per row (FIXED in PR #55)

Caller had to GET each detail to know whether codebase context fired. New `HistorySummaryEnrichment` shape projects profile + activation booleans + `patterns_injected` + `curated_files` + `repo_relevance_score` from each row's stored `context_sources`. Made the silent demotion above visible at-a-glance.

### 🟡 3. No `normalize_embeddings=True` at encode time

`embedding_service.py:101, 121` — `model.encode(text, convert_to_numpy=True)` produces UN-normalized vectors. Every cosine search downstream re-normalizes (`embedding_service.py:163-164`, `fusion.py:608`, `engine.py:1476`, `engine.py:2106`, `engine.py:2219`). Re-normalization is cheap but redundant on CPU when sentence-transformers can normalize natively at encode time:

```python
return self.model.encode(text, convert_to_numpy=True, normalize_embeddings=True)
```

Effect: storage and downstream search become idempotent under repeated normalize (already are; this just removes duplicate work). Existing persisted embeddings stay unnormalized but cosine search re-normalizes them at read time, so backward compat is automatic.

**Status:** ready to ship as a minor follow-up. Not done in PR #55 because it changes the byte representation of NEW persisted vectors, and we want clean separation from the analyzer fix landing first.

### 🟡 4. No model warmup at startup

First prompt after a `./init.sh restart` always pays the cost of:
- Loading the `~80 MB` `all-MiniLM-L6-v2` weights from disk
- One probe `encode("test")` for dimension auto-detection
- First cold encode (no warm CPU cache)

Total: ~600–1200 ms of latency overhead on the first request.

Fix: schedule a `_warmup_embedding_model()` task in `main.py:lifespan` that calls `EmbeddingService().embed_single("warmup")` once. Ensures the first user request hits a warm model.

**Status:** ready to ship; one-line addition to `lifespan`.

### 🟡 5. Hot-path embeds 3 texts sequentially when batched call would halve latency

`engine.py:599-605` calls `aembed_single` separately for `raw_prompt` and `optimized_prompt`. These can be batched as `aembed_texts([raw, optimized])` for ~50% lower latency on the hot path.

The qualifier text (`engine.py:631`) is gated by `loader.get_cached_qualifier_embedding(cache_key)` — usually cached, so usually not part of the hot path. Skip batching it.

**Status:** ready to ship. Touches one block in `engine.py`. Adds 2 instead of 3 awaits per persist; net ~100–200 ms latency reduction on CPU.

### 🟡 6. Single generic model for code + general text + qualifiers

`all-MiniLM-L6-v2` (May 2022, MTEB ~58) is the universal embedding for:
- Raw prompt text (any domain — coding, marketing, legal, business)
- Optimized prompt text
- Cluster centroids
- Qualifier vocabulary keywords
- Repo file content (code-heavy)

Modern alternatives that perform better on the project's two heaviest workloads:

| Model | Dim | MTEB | Notes |
|-------|-----|------|-------|
| `BAAI/bge-small-en-v1.5` | 384 | ~62 | Drop-in dim replacement; +4 MTEB points |
| `BAAI/bge-base-en-v1.5` | 768 | ~64 | Higher dim — would invalidate existing 384-dim embeddings, requires migration |
| `microsoft/unixcoder-base` | 768 | code-specialized | Higher recall on code; worse on prose |

**Trade-off:** swapping to `bge-small-en-v1.5` gains ~4 MTEB points at zero dim change → no migration cost, no schema change, no index rebuild. The `EMBEDDING_MODEL` env var already supports the swap (`config.py:53`).

**Status:** worth A/B testing but not landed in PR #55. Would need:
- Empirical comparison on the actual prompt distribution
- Cluster-stability check post-swap (centroid drift could trigger cascading splits)
- Coordinated re-embed of the existing corpus OR a transition strategy that lets old + new vectors coexist

This is the heaviest "underutilization" finding. Recommended as a dedicated v0.5.x experiment, not a v0.4.x patch.

### 🟢 7. No GPU device detection

`embedding_service.py:62` instantiates `SentenceTransformer(model_name)` with no `device` argument. `sentence-transformers` auto-detects CUDA when present, so this is non-load-bearing on GPU hosts. The current host has no GPU (`torch.cuda.is_available() == False`). No fix needed; documenting for future hosts.

### 🟢 8. Naive `cosine_search` staticmethod alongside `EmbeddingIndex`

`embedding_service.py:140-167` is a O(N) cosine search staticmethod. Production paths use `EmbeddingIndex` (numpy/HNSW). The staticmethod survives for tests + ad-hoc utilities. Not legacy in a harmful sense — clean separation.

---

## Verdict

The user's instinct was right that the system isn't leveraging its embedding stack fully. But the gap is mostly **opportunity cost** (warmup, batching, native normalization, model upgrade) rather than **legacy code paths** (`cosine_search` staticmethod is the only candidate, and it's intentional).

The biggest behavioural impact came from a **non-embedding** bug — the heuristic profile selector demoting code prompts to `knowledge_work` — which silently disabled the entire embedding-driven enrichment chain (curated retrieval, pattern injection, strategy intelligence). PR #55 closes that gap.

Recommended follow-up sequence:

| Step | Effort | Behaviour change | Migration risk |
|------|--------|------------------|----------------|
| #4 model warmup at startup | trivial | first-request latency only | none |
| #3 `normalize_embeddings=True` at encode | small | byte-rep of new vectors only | none (cosine search re-normalizes existing) |
| #5 hot-path batch (raw + optimized) | small | ~50% hot-path embed latency | none |
| #6 model upgrade to `bge-small-en-v1.5` | medium | corpus-wide re-embed + cluster restability | medium — needs A/B + transition plan |

Steps 3–5 ship together cleanly as a v0.4.5 patch. Step 6 is a v0.5.x experiment with its own design spec.
