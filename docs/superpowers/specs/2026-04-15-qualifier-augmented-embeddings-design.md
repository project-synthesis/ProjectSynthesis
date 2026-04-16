# Qualifier-Augmented Embeddings

**Goal:** Add organic qualifier vocabulary as a fourth embedding signal in the multi-embedding pipeline, enabling qualifier-aware clustering that improves cross-project pattern discovery and sub-domain formation.

**Problem:** The current embedding pipeline uses `all-MiniLM-L6-v2` (384-dim) which encodes general semantic similarity. Prompts about "subscription billing" and "churn win-back campaigns" are both SaaS but embed close together despite being different specializations (revenue vs retention). The organic qualifier vocabulary (Haiku-generated from cluster labels) knows this distinction but only influences Phase 5 sub-domain discovery — it doesn't shape the embedding space itself. Cross-project learning suffers because qualifier-aware clustering would let a new project immediately find mature clusters from another project that share the same specialization.

**Solution:** Embed the matched qualifier's keywords as a standalone 384-dim vector (same model), store as `Optimization.qualifier_embedding`, and add it as a 4th signal in `blend_embeddings()` with a tunable weight. The adaptive weight learning system discovers the optimal qualifier influence per task type and per cluster.

**Semantic amplification (intentional):** The qualifier embedding selectively amplifies qualifier-relevant dimensions in the blended embedding space. Qualifier keywords like "metrics", "kpi" also appear in the raw prompt and are captured by the raw embedding — the qualifier signal is NOT independent information but deliberate emphasis. This is the desired behavior: qualifier terms are the discriminative signal within a domain, so amplifying their influence (from baseline in the raw embedding to baseline + 0.10 qualifier weight) strengthens the boundaries between specializations.

---

## Architecture

### Qualifier Embedding Generation (hot path)

In `process_optimization()`, after computing the three existing embeddings:

1. Parse `domain_raw` via `parse_domain()` → `(primary, qualifier)`
2. Look up keywords from `DomainSignalLoader.get_qualifiers(primary)` → `{qualifier: [keywords]}`
3. Build qualifier text: join the matched qualifier's keywords → `"growth metrics kpi dashboard acquisition cost"`
4. **Cache check:** Look up the qualifier text in a process-level `dict[str, np.ndarray]` cache on `DomainSignalLoader`. If cached, use directly (skip embedding call). If not cached, embed via `aembed_single(qualifier_text)`, cache the result, then store.
5. Store as `Optimization.qualifier_embedding` (LargeBinary, nullable)

**Qualifier embedding cache:** Since qualifier keyword sets are drawn from a fixed vocabulary (~10-30 distinct sets across all domains), the same text produces the same embedding every time. The cache eliminates the ~5-15ms MiniLM embedding call for all but the first occurrence of each qualifier. Cache is invalidated when `refresh_qualifiers()` updates a domain's vocabulary. Keyed by sorted, frozen keyword string for consistency.

When no qualifier is available (cold start, no vocab, plain domain), `qualifier_embedding` stays NULL. Blending functions handle NULL by redistributing weight to other signals.

### Storage

**New column on Optimization:**
```python
qualifier_embedding = Column(LargeBinary, nullable=True)  # 384-dim float32, 1536 bytes
```

**New index:** `QualifierIndex` — same pattern as `TransformationIndex` (in-memory numpy index, pickle persistence via `save_cache`/`load_cache`, NO `cluster_metadata` storage). Running weighted mean of qualifier vectors per cluster. Persisted to `data/qualifier_index.pkl`. Rebuilt from Optimization rows on cold path.

### Downstream Wiring

**Warm/cold path clustering (`blend_embeddings()` in `clustering.py`):**
- New keyword-only parameter: `*, qualifier: np.ndarray | None = None` (keyword-only to avoid breaking existing positional callers)
- New constant: `CLUSTERING_BLEND_W_QUALIFIER = 0.10`
- Adjusted: `CLUSTERING_BLEND_W_RAW` from 0.65 → 0.55 (qualifier absorbs from raw since it refines the topic signal)
- Existing NULL/zero-vector handling in `weighted_blend()` redistributes weights when qualifier is absent

**Fusion (`CompositeQuery` in `fusion.py` — detailed changes):**

The following structures in `fusion.py` all hardcode 4 signals and must be extended to 5:

| Structure | Current | Change |
|-----------|---------|--------|
| `PhaseWeights` dataclass (line 71) | 4 fields: `w_topic, w_transform, w_output, w_pattern` | Add `w_qualifier: float` field |
| `PhaseWeights.total` property | Sums 4 fields | Sum 5 fields |
| `PhaseWeights.enforce_floor()` | `n = 4` | `n = 5` |
| `PhaseWeights.for_phase()` | Indexes 4-tuples | Index 5-tuples |
| `PhaseWeights.from_dict()` | 4 `.get()` calls | Add `w_qualifier=d.get("w_qualifier", 0.0)` — default 0.0 (not 0.25) for old profiles |
| `PhaseWeights.to_dict()` | 4 keys | 5 keys |
| `_DEFAULT_PROFILES` dict | `dict[str, tuple[float, float, float, float]]` | 5-tuples: add qualifier weight per phase |
| `_TASK_TYPE_WEIGHT_BIAS` | 4-key dicts per task type | Add `"w_qualifier"` key per task type |
| `CompositeQuery` dataclass (line 251) | 4 fields: `topic, transformation, output, pattern` | Add `qualifier: np.ndarray \| None = None` |
| `CompositeQuery.fuse()` | 4-signal `weighted_blend()` | 5-signal |
| `build_composite_query()` (line 480) | Builds 4 signals | Build 5th: look up qualifier keywords, embed (with cache), pass as `qualifier=` |
| `compute_score_correlated_target()` (line 443) | 4 accumulators: `w_topic/w_transform/w_output/w_pattern` | Add `w_qualifier` accumulator, pass to `PhaseWeights` constructor |
| `resolve_contextual_weights()` | Returns 4-field `PhaseWeights` | Returns 5-field |

**Hot-path cluster assignment (`assign_cluster()`):**
- No change to cosine search — continues using raw embedding only for speed
- `QualifierIndex` updated after assignment (running weighted mean, same as TransformationIndex)

**Pattern matching (`match_prompt()`):**
- No change — raw embedding only for cross-process consistency
- Qualifier influence enters through improved cluster composition from warm-path re-clustering

**Index infrastructure updates in `process_optimization()`:**
- After existing TransformationIndex and OptimizedEmbeddingIndex updates, add: `QualifierIndex.upsert(cluster_id, qualifier_vec)`

### Backfill Mechanism

Existing optimizations without `qualifier_embedding` get NULL (no data migration). Warm path Phase 4 (refresh) backfills: for each optimization with NULL `qualifier_embedding` where `domain_raw` has a parseable qualifier AND `DomainSignalLoader` has vocab for that domain, generate and store the embedding. **Per-cycle cap: 50 optimizations** — prevents latency spike on first deployment. Full backfill completes over multiple warm cycles.

When `qualifier_stale` flag is set on a cluster (triggered by vocabulary refresh), Phase 4 regenerates qualifier_embeddings for ALL optimizations in that cluster — not just NULLs — because the keyword set has changed and existing embeddings are stale.

Cold path full refit also recomputes all blended embeddings with whatever qualifier data is available.

### Cross-Project Learning Flow

`DomainSignalLoader.load()` queries ALL domain nodes (no project filter), so qualifier vocabulary is inherently cross-project. The flow:

1. Project A builds mature clusters with strong qualifier centroids over time
2. Project B's first prompt gets a qualifier embedding computed on the hot path using Project A's vocabulary (served by `DomainSignalLoader`)
3. Hot-path `assign_cluster()` uses raw embedding only — the qualifier signal does NOT affect immediate cluster assignment
4. On the next warm-path re-clustering cycle (~5 min), `blend_embeddings()` with the qualifier signal reshapes cluster membership — Project B's optimizations migrate toward Project A's clusters that share the same specialization
5. `GlobalPattern` discovery (Phase 4.5, every 10th cycle ~50 min) promotes patterns across these qualifier-aligned clusters
6. From that point forward, Project B gets Project A's proven patterns injected via qualifier-aware pattern retrieval

**Note:** The cross-project benefit is not immediate on "the first prompt" — it takes effect after the first warm-path re-clustering cycle following the prompt's arrival. Hot-path assignment remains raw-embedding-only for speed.

---

## Adaptive Weight Learning Integration

The existing 3-layer weight learning system extends to 5 signals (from 4):

**Layer 1 — Task-type bias (`_TASK_TYPE_WEIGHT_BIAS`):**

| Task Type | Qualifier Bias | Rationale |
|-----------|---------------|-----------|
| coding | 0.15 | Domain specialization highly relevant |
| analysis | 0.12 | Analytical prompts benefit from domain context |
| system | 0.10 | System prompts are domain-specific |
| data | 0.10 | Data tasks have clear specializations |
| writing | 0.05 | Less domain-dependent |
| creative | 0.03 | Domain-agnostic |
| general | 0.05 | Fallback |

**Layer 2 — Score-correlated adaptation:** `compute_score_correlated_target()` must be updated to accumulate `w_qualifier` alongside the other 4 dimensions. The 5th accumulator follows the same EMA pattern. The function constructs `PhaseWeights` with all 5 fields.

**Layer 3 — Per-cluster learned weights:** `cluster_metadata["learned_phase_weights"]` extends from 4 to 5 elements. `PhaseWeights.from_dict()` uses `d.get("w_qualifier", 0.0)` — default 0.0 means "this profile was recorded before qualifier existed, its qualifier weight is unknown." `phase_weights_json` on Optimization also uses the same 0.0 default for old snapshots.

**Cold-start period:** The weight learning system cannot learn optimal qualifier weight from historical data (old profiles all have `w_qualifier=0.0`). Learning begins as new optimizations with real qualifier weights accumulate. During the cold-start period, the system uses the task-type bias defaults from Layer 1. `compute_score_correlated_target()` should skip the qualifier dimension when aggregating profiles where `w_qualifier == 0.0` to avoid treating "no data" as "zero weight is optimal."

---

## Observability

**Events:**

| Path | Op | Decision | Context |
|------|----|----------|---------|
| `hot` | `embed` | `qualifier_embedded` | `{domain, qualifier, keyword_count, embedding_norm, cached: bool}` |
| `hot` | `embed` | `qualifier_skipped` | `{domain, reason: "no_vocab"\|"no_qualifier"\|"cold_start"}` |
| `warm` | `refresh` | `qualifier_backfilled` | `{cluster_id, optimizations_backfilled: int, stale_refresh: bool}` |

**Health endpoint:** Extend `DomainSignalLoader.stats()` with:
```python
"qualifier_embeddings_generated": int,  # total generated this process lifetime
"qualifier_embeddings_skipped": int,    # total skipped (no vocab/qualifier)
"qualifier_cache_size": int,            # distinct cached qualifier embedding vectors
```

---

## Error Handling

| Component | Failure Mode | Handling |
|-----------|-------------|----------|
| `aembed_single()` for qualifier text | Embedding failure | WARNING log, `qualifier_embedding = NULL`, continue. Blending redistributes weight |
| `DomainSignalLoader.get_qualifiers()` | Empty dict (no vocab) | `qualifier_skipped` event with `reason="no_vocab"`, set NULL. Expected during cold start |
| `parse_domain()` | No qualifier parsed | `qualifier_skipped` event with `reason="no_qualifier"`, set NULL |
| `QualifierIndex.upsert()` | Index update failure | WARNING log, continue. Index rebuilds on next warm/cold cycle |
| Phase 4 backfill | Individual optimization failure | Skip, continue with next. Log count at end |

**Principle:** Qualifier embedding is best-effort. Never blocks the hot path, never degrades existing embeddings, never crashes the pipeline. Missing qualifier_embedding = system operates exactly as before this feature.

**Rollback:** Full disable requires THREE changes: (1) `CLUSTERING_BLEND_W_QUALIFIER = 0.0` in `_constants.py`, (2) qualifier element set to 0.0 in all `_DEFAULT_PROFILES` entries, (3) `w_qualifier` bias set to 0.0 in all `_TASK_TYPE_WEIGHT_BIAS` entries. This zeros the qualifier signal across both clustering and fusion paths. No data migration needed — stale `qualifier_embedding` columns are harmless when weight is zero.

---

## Migration and Backward Compatibility

**Schema:** One new nullable column via Alembic migration. No data migration — existing rows get NULL.

**Index:** New `QualifierIndex` class (identical pattern to `TransformationIndex` — pickle persistence only, no `cluster_metadata`). Initialized in `TaxonomyEngine.__init__`, persisted to `data/qualifier_index.pkl`.

**Weight profiles:** Old 4-element `learned_phase_weights` and `phase_weights_json` use `from_dict()` default of `w_qualifier=0.0`. New profiles store all 5 elements. `compute_score_correlated_target()` skips the qualifier dimension for profiles with `w_qualifier == 0.0`.

**Guarantees:**
- Old optimizations (NULL qualifier_embedding): blend weights redistribute — identical behavior to current system
- `match_prompt()` unchanged — raw embedding only
- Hot-path `assign_cluster()` unchanged — raw cosine search

---

## Changes

| File | Change |
|------|--------|
| `backend/app/models.py` | Add `qualifier_embedding = Column(LargeBinary, nullable=True)` |
| `backend/app/services/taxonomy/engine.py` | Generate qualifier embedding in `process_optimization()` with cache lookup, update `QualifierIndex` |
| `backend/app/services/taxonomy/clustering.py` | Add keyword-only `qualifier` param to `blend_embeddings()` |
| `backend/app/services/taxonomy/_constants.py` | Add `CLUSTERING_BLEND_W_QUALIFIER = 0.10`, adjust `CLUSTERING_BLEND_W_RAW` to 0.55 |
| `backend/app/services/taxonomy/qualifier_index.py` | New file — `QualifierIndex` class (same pattern as `TransformationIndex`) |
| `backend/app/services/taxonomy/fusion.py` | Extend `PhaseWeights` (5th field + `total` + `enforce_floor` + `for_phase` + `from_dict` + `to_dict`), extend `CompositeQuery` (5th signal + `fuse()`), update `_DEFAULT_PROFILES` (5-tuples), update `_TASK_TYPE_WEIGHT_BIAS` (add `w_qualifier`), update `compute_score_correlated_target()` (5th accumulator + zero-skip), update `build_composite_query()` (5th signal lookup + embed with cache), update `resolve_contextual_weights()` (5-field return) |
| `backend/app/services/taxonomy/warm_phases.py` | Phase 4 qualifier backfill (capped at 50/cycle), `qualifier_stale` full-cluster regeneration |
| `backend/app/services/taxonomy/cold_path.py` | Load `qualifier_embedding` for blended clustering |
| `backend/app/services/domain_signal_loader.py` | Add qualifier embedding cache (`_qualifier_embedding_cache: dict[str, np.ndarray]`), embedding counters, cache invalidation in `refresh_qualifiers()` |
| `backend/app/routers/health.py` | Wire new stats fields |
| `alembic/versions/` | New migration adding `qualifier_embedding` column |
| `backend/tests/test_blend_embeddings.py` | Update `test_weights_sum_to_one` to include qualifier constant, add qualifier blend tests |
| `backend/tests/test_fusion.py` | Update all `PhaseWeights(...)` constructions to 5-arg, update profile assertions |
| `backend/tests/test_cold_path_adaptive_blend.py` | Verify `blend_embeddings()` keyword-only `qualifier` param |

---

## Testing Strategy

### Unit Tests
- `blend_embeddings()` with qualifier signal — verify weight distribution (0.55/0.20/0.15/0.10)
- `blend_embeddings()` with NULL qualifier — verify weight redistribution to 0.55/0.20/0.15 → renormalized
- `QualifierIndex` upsert and weighted mean computation
- `PhaseWeights` with 5 elements — all methods (total, enforce_floor, for_phase, from_dict, to_dict)
- `PhaseWeights.from_dict()` with 4-element dict — verify `w_qualifier` defaults to 0.0
- `_TASK_TYPE_WEIGHT_BIAS` 5-element dicts for all 7 task types
- Qualifier embedding cache — hit/miss, invalidation on `refresh_qualifiers()`
- Qualifier embedding quality — verify distinct qualifiers within same domain have cosine < 0.50

### Integration Tests
- `process_optimization()` generates qualifier_embedding when vocab available
- `process_optimization()` sets NULL when no vocab (cold start)
- `process_optimization()` uses cached qualifier embedding on second call with same qualifier
- Phase 4 backfill fills qualifier_embedding for existing optimizations (capped at 50)
- Phase 4 backfill regenerates ALL embeddings when `qualifier_stale` is set
- Warm-path re-clustering uses qualifier signal in blending
- `compute_score_correlated_target()` skips qualifier dimension for old profiles (w_qualifier=0.0)

### Regression Tests
- `test_weights_sum_to_one` updated to include `CLUSTERING_BLEND_W_QUALIFIER`
- All `PhaseWeights(...)` constructions updated to 5 args
- `match_prompt()` behavior unchanged
- `assign_cluster()` behavior unchanged (raw embedding search)
