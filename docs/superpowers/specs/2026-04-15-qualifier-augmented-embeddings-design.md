# Qualifier-Augmented Embeddings

**Goal:** Add organic qualifier vocabulary as a fourth embedding signal in the multi-embedding pipeline, enabling qualifier-aware clustering that improves cross-project pattern discovery and sub-domain formation.

**Problem:** The current embedding pipeline uses `all-MiniLM-L6-v2` (384-dim) which encodes general semantic similarity. Prompts about "subscription billing" and "churn win-back campaigns" are both SaaS but embed close together despite being different specializations (revenue vs retention). The organic qualifier vocabulary (Haiku-generated from cluster labels) knows this distinction but only influences Phase 5 sub-domain discovery — it doesn't shape the embedding space itself. Cross-project learning suffers because Project B's first "billing" prompt has no specialization signal to find Project A's mature "revenue" clusters.

**Solution:** Embed the matched qualifier's keywords as a standalone 384-dim vector (same model), store as `Optimization.qualifier_embedding`, and add it as a 4th signal in `blend_embeddings()` with a tunable weight. The existing adaptive weight learning system discovers the optimal qualifier influence per task type and per cluster.

---

## Architecture

### Qualifier Embedding Generation (hot path)

In `process_optimization()`, after computing the three existing embeddings:

1. Parse `domain_raw` via `parse_domain()` → `(primary, qualifier)`
2. Look up keywords from `DomainSignalLoader.get_qualifiers(primary)` → `{qualifier: [keywords]}`
3. Build qualifier text: join the matched qualifier's keywords → `"growth metrics kpi dashboard acquisition cost"`
4. Embed via `aembed_single(qualifier_text)` → 384-dim float32 vector
5. Store as `Optimization.qualifier_embedding` (LargeBinary, nullable)

When no qualifier is available (cold start, no vocab, plain domain), `qualifier_embedding` stays NULL. Blending functions handle NULL by redistributing weight to other signals.

### Storage

**New column on Optimization:**
```python
qualifier_embedding = Column(LargeBinary, nullable=True)  # 384-dim float32, 1536 bytes
```

**New index:** `QualifierIndex` — same pattern as `TransformationIndex` and `OptimizedEmbeddingIndex`. Running weighted mean of qualifier vectors per cluster. Stored in `cluster_metadata["qualifier_centroid"]` (no schema change on PromptCluster). Persisted to `data/qualifier_index.pkl`.

### Downstream Wiring

**Warm/cold path clustering (`blend_embeddings()`):**
- New parameter: `qualifier: np.ndarray | None`
- New constant: `CLUSTERING_BLEND_W_QUALIFIER = 0.10`
- Adjusted: `CLUSTERING_BLEND_W_RAW` from 0.65 → 0.55 (qualifier absorbs from raw since it refines the topic signal)
- Existing NULL handling redistributes weights when qualifier is absent

**Fusion for pattern injection (`CompositeQuery` in `fusion.py`):**
- Add qualifier as 5th signal in `build_composite_query()`
- Source: look up qualifier keywords from `DomainSignalLoader`, embed them
- New weight slot in `_DEFAULT_PROFILES` and `PhaseWeights`
- Adaptive weight learning (`resolve_contextual_weights()`) discovers optimal qualifier weight per task type and per cluster

**Hot-path cluster assignment (`assign_cluster()`):**
- No change to cosine search — continues using raw embedding only for speed
- `QualifierIndex` updated after assignment (running weighted mean, same as TransformationIndex)

**Pattern matching (`match_prompt()`):**
- No change — raw embedding only for cross-process consistency
- Qualifier influence enters through improved cluster composition from warm-path re-clustering

**Index infrastructure updates in `process_optimization()`:**
- After existing TransformationIndex and OptimizedEmbeddingIndex updates, add: `QualifierIndex.upsert(cluster_id, qualifier_vec)`

### Backfill Mechanism

Existing optimizations without `qualifier_embedding` get NULL (no data migration). Warm path Phase 4 (refresh) backfills: for each optimization with NULL `qualifier_embedding` where `domain_raw` has a parseable qualifier AND `DomainSignalLoader` has vocab for that domain, generate and store the embedding. Cold path full refit also recomputes all blended embeddings with whatever qualifier data is available.

Cluster-level `qualifier_stale` flag (in `cluster_metadata`) triggers backfill on next Phase 4 refresh cycle when qualifier vocabulary changes.

### Cross-Project Learning Flow

1. Project A builds mature clusters with strong qualifier centroids over time
2. Project B's first prompt gets a qualifier embedding from the organic vocab
3. Warm path `blend_embeddings()` with qualifier signal places Project B's optimization near Project A's clusters sharing the same specialization
4. `GlobalPattern` discovery (Phase 4.5) promotes patterns across these qualifier-aligned clusters
5. Project B gets Project A's proven patterns injected from the first prompt — zero friction

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

**Layer 2 — Score-correlated adaptation:** `compute_score_correlated_target()` operates on weight vectors of arbitrary length — adding the 5th dimension requires no algorithmic change. The system discovers whether qualifier weight helps or hurts score lift per cluster.

**Layer 3 — Per-cluster learned weights:** `cluster_metadata["learned_phase_weights"]` extends from 4 to 5 elements. Old 4-element profiles padded with default qualifier weight on read. `phase_weights_json` on Optimization also pads old snapshots.

---

## Observability

**Events:**

| Path | Op | Decision | Context |
|------|----|----------|---------|
| `hot` | `embed` | `qualifier_embedded` | `{domain, qualifier, keyword_count, embedding_norm}` |
| `hot` | `embed` | `qualifier_skipped` | `{domain, reason: "no_vocab"\|"no_qualifier"\|"cold_start"}` |
| `warm` | `refresh` | `qualifier_backfilled` | `{cluster_id, optimizations_backfilled: int}` |

**Health endpoint:** Extend `DomainSignalLoader.stats()` with:
```python
"qualifier_embeddings_generated": int,  # total generated this process lifetime
"qualifier_embeddings_skipped": int,    # total skipped (no vocab/qualifier)
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

**Rollback:** Set `CLUSTERING_BLEND_W_QUALIFIER = 0.0` — blending treats zero-weight signals as absent. No data migration needed.

---

## Migration and Backward Compatibility

**Schema:** One new nullable column via Alembic migration. No data migration — existing rows get NULL.

**Index:** New `QualifierIndex` class (identical pattern to `TransformationIndex`). Initialized in `TaxonomyEngine.__init__`, persisted to `data/qualifier_index.pkl`.

**Weight profiles:** Old 4-element `learned_phase_weights` and `phase_weights_json` padded with default qualifier weight on read. New profiles store all 5 elements.

**Guarantees:**
- Old optimizations (NULL qualifier_embedding): blend weights redistribute — identical behavior to current system
- `match_prompt()` unchanged — raw embedding only
- Hot-path `assign_cluster()` unchanged — raw cosine search
- All existing tests pass without modification

---

## Changes

| File | Change |
|------|--------|
| `backend/app/models.py` | Add `qualifier_embedding = Column(LargeBinary, nullable=True)` |
| `backend/app/services/taxonomy/engine.py` | Generate qualifier embedding in `process_optimization()`, update `QualifierIndex` |
| `backend/app/services/taxonomy/clustering.py` | Add `qualifier` param to `blend_embeddings()` |
| `backend/app/services/taxonomy/_constants.py` | Add `CLUSTERING_BLEND_W_QUALIFIER = 0.10`, adjust `CLUSTERING_BLEND_W_RAW` to 0.55 |
| `backend/app/services/taxonomy/qualifier_index.py` | New file — `QualifierIndex` class (same pattern as `TransformationIndex`) |
| `backend/app/services/taxonomy/fusion.py` | Add qualifier as 5th signal in `CompositeQuery`, `PhaseWeights`, `_DEFAULT_PROFILES`, `_TASK_TYPE_WEIGHT_BIAS` |
| `backend/app/services/taxonomy/warm_phases.py` | Phase 4 qualifier backfill for NULL `qualifier_embedding` optimizations |
| `backend/app/services/taxonomy/cold_path.py` | Load `qualifier_embedding` for blended clustering |
| `backend/app/services/domain_signal_loader.py` | Add embedding generation counters to `stats()` |
| `backend/app/routers/health.py` | Wire new stats fields |
| `alembic/versions/` | New migration adding `qualifier_embedding` column |

---

## Testing Strategy

### Unit Tests
- `blend_embeddings()` with qualifier signal — verify weight distribution
- `blend_embeddings()` with NULL qualifier — verify weight redistribution
- `QualifierIndex` upsert and weighted mean computation
- `PhaseWeights` with 5 elements — backward compat with 4-element profiles
- `_TASK_TYPE_WEIGHT_BIAS` 5-element tuples

### Integration Tests
- `process_optimization()` generates qualifier_embedding when vocab available
- `process_optimization()` sets NULL when no vocab (cold start)
- Phase 4 backfill fills qualifier_embedding for existing optimizations
- Warm-path re-clustering uses qualifier signal in blending
- Cross-project assignment: prompt with qualifier finds qualifier-aligned cluster from another project

### Regression Tests
- All existing embedding tests pass with qualifier_embedding=NULL
- `match_prompt()` behavior unchanged
- `assign_cluster()` behavior unchanged (raw embedding search)
- Hot-path latency within acceptable bounds (+1-2ms for qualifier lookup + embed)
