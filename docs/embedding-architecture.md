# Embedding Architecture

How embeddings are produced, stored, indexed, and consumed across the taxonomy engine.

## Embedding Types

| Column | Dim | Produced By | Description |
|--------|-----|-------------|-------------|
| `Optimization.embedding` | 384 | Hot path (`engine.py`) | `embed(raw_prompt)` — what the user asked |
| `Optimization.optimized_embedding` | 384 | Hot path (`engine.py`) | `embed(optimized_prompt)` — what the system produced |
| `Optimization.transformation_embedding` | 384 | Hot path (`engine.py`) | `L2_norm(optimized_emb - raw_emb)` — direction of improvement |
| `Optimization.qualifier_embedding` | 384 | Hot path (`engine.py`) | `embed(qualifier_text)` — organic vocabulary keywords derived from intent_label + domain qualifier + cluster-level enriched vocabulary (`DomainSignalLoader.generated_qualifiers`) |
| `PromptCluster.centroid_embedding` | 384 | Hot path running mean + warm/cold reconciliation | Score-weighted mean of member raw embeddings (`max(0.1, score/10)`) |
| `MetaPattern.embedding` | 384 | Warm-path pattern extraction (Haiku) | `embed(pattern_text)` — reusable technique |

## In-Memory Indices

| Index | Contents | Consumers |
|-------|----------|-----------|
| `EmbeddingIndex` | Raw centroid per cluster. Dual-backend: `_NumpyBackend` default + `_HnswBackend` at ≥`HNSW_CLUSTER_THRESHOLD=1000` clusters (fallback to numpy on HNSW failure). Stable `_id_to_label` mapping + tombstones. `_project_ids` parallel array for per-project filtering. | Hot-path assignment, pattern injection cluster search, composite fusion cluster lookup, warm merge pair finding |
| `OptimizedEmbeddingIndex` | Mean optimized embedding per cluster | Composite fusion Signal 3 (output direction), output-similar few-shot retrieval |
| `TransformationIndex` | Mean transformation vector per cluster | Composite fusion Signal 2 (technique direction) |
| `QualifierIndex` | Per-cluster qualifier centroids | Composite fusion Signal 5 (domain specialization), sub-domain discovery (`compute_qualifier_cascade()`) |

All four indices are maintained across the full lifecycle: hot path (running mean upsert), warm path (reconciliation, merge/remove, retire/remove, split/remove), cold path (full rebuild), startup (cache load + DB backfill).

## Consumption Paths

### HDBSCAN Clustering (warm split, cold refit, all merge paths)

```
blend_embeddings(raw=0.55, optimized=0.20, transformation=0.15, qualifier=0.10)
    │
    ├─ raw: PromptCluster.centroid_embedding (cold/merge) or Optimization.embedding (warm split)
    ├─ optimized: OptimizedEmbeddingIndex.get_vector(cluster_id)
    ├─ transformation: TransformationIndex.get_vector(cluster_id)
    └─ qualifier: QualifierIndex.get_vector(cluster_id)
    │
    └─▶ batch_cluster(blended_embeddings) → HDBSCAN labels
         └─ Raw centroids stored on nodes (not blended)
```

Weights configurable via `CLUSTERING_BLEND_W_*` constants in `_constants.py` (`RAW`, `OPTIMIZED`, `TRANSFORM`, `QUALIFIER`). All merge paths use blended centroids: global best-pair, same-domain label merge, and same-domain embedding merge.

**Hot-path assignment stays raw-only** — avoids circular dependency (optimized/transformation embeddings are computed from the current optimization, which depends on cluster assignment) and bootstraps cleanly before warm-path qualifier vocabulary populates.

### Shared Blend Core

Both `blend_embeddings()` (HDBSCAN) and `CompositeQuery.fuse()` (composite fusion) delegate to `weighted_blend()` in `clustering.py`. This centralizes zero-vector detection (threshold 1e-9), weight redistribution, and L2-normalization to prevent algorithmic drift between the two paths.

### Composite Fusion (pattern injection, matching)

```
resolve_fused_embedding(raw_prompt, phase)
    │
    ├─ Signal 1 (topic):         embed(raw_prompt)
    ├─ Signal 2 (transformation): TransformationIndex.get_vector(matched_cluster)
    ├─ Signal 3 (output):         OptimizedEmbeddingIndex.get_vector(matched_cluster)
    ├─ Signal 4 (pattern):        avg(top 3 MetaPattern.embedding WHERE global_source_count >= 3)
    └─ Signal 5 (qualifier):      QualifierIndex.get_vector(matched_cluster)
    │
    └─▶ PhaseWeights.fuse() → L2-normalized 384-dim query vector
```

Per-phase default profiles (`_DEFAULT_PROFILES` in `fusion.py`), tuples of `(w_topic, w_transform, w_output, w_pattern, w_qualifier)`:

| Phase | topic | transform | output | pattern | qualifier |
|-------|-------|-----------|--------|---------|-----------|
| `analysis` | 0.55 | 0.15 | 0.10 | 0.15 | 0.05 |
| `optimization` | 0.18 | 0.30 | 0.22 | 0.20 | 0.10 |
| `pattern_injection` | 0.22 | 0.22 | 0.18 | 0.28 | 0.10 |
| `scoring` | 0.13 | 0.18 | 0.42 | 0.20 | 0.07 |

Task-type biases (`_TASK_TYPE_WEIGHT_BIAS`) add small directional offsets per task type (7 types: coding, writing, analysis, creative, data, system, general). `w_qualifier` bias is non-zero across all types because domain specialization signal is consistently valuable. `PhaseWeights.from_dict()` defaults `w_qualifier=0.0` for backward-compat on pre-qualifier stored profiles.

### Few-Shot Retrieval

```
retrieve_few_shot_examples(raw_prompt)
    │
    ├─ input_sim:  cosine(embed(raw_prompt), Optimization.embedding)        threshold 0.50
    └─ output_sim: cosine(embed(raw_prompt), Optimization.optimized_embedding)  threshold 0.40
    │
    └─▶ Qualify if either passes → re-rank by max(input_sim, output_sim) * overall_score
```

### Cross-Cluster Pattern Injection

```
auto_inject_patterns(raw_prompt)
    │
    ├─ Topic-matched patterns:  top-5 clusters via EmbeddingIndex.search()
    └─ Universal patterns:      MetaPattern WHERE global_source_count >= 3
    │
    └─▶ Relevance = cosine_sim * log2(1 + global_source_count) * cluster_score_factor
```

## Weight Learning Flow

```
Layer 1: resolve_contextual_weights(task_type, cluster_learned_weights)
    │
    ├─ Phase defaults (4 profiles × 4 signals)
    ├─ + task-type bias (_TASK_TYPE_WEIGHT_BIAS: 7 types)
    └─ + cluster learned weights blend (alpha=0.3)
    │
    └─▶ Stored on Optimization.phase_weights_json

Layer 2: compute_score_correlated_target(scored_profiles)  [warm path, min 10 samples]
    │
    ├─ z-score above median → contribution weight
    └─ Weighted mean of above-median profiles
    │
    ├─▶ Global: adapt preferences (EMA alpha=0.05)
    └─▶ Per-cluster: store in cluster_metadata["learned_phase_weights"]

Layer 3: decay_toward_target(current, phase, target)  [warm path, rate=0.01]
    │
    └─ Drift toward cluster learned weights (or defaults if no learning yet)
    │
    └─▶ Runs BEFORE Layer 2 so adaptation (0.05) dominates decay (0.01)
```

## Design Decisions

**Blend vs. concatenation for HDBSCAN**: Weighted blend (384-dim) chosen over concatenation (1152-dim). HDBSCAN uses Euclidean distance on L2-normalized vectors — concatenating three different embedding spaces makes normalization semantically incoherent. HDBSCAN also degrades in high dimensions.

**Hot-path raw-only**: Cluster assignment at ingestion time uses raw embedding only. The optimization's output embedding is computed from the current optimization, creating a chicken-and-egg problem if used for assignment. Warm/cold paths correct misassignments using blended HDBSCAN.

**Default blend weights (0.55/0.20/0.15/0.10)**: Raw dominates because topic similarity is the primary clustering signal. Optimized adds output-quality signal (clusters producing similar outputs). Transformation adds technique-direction signal (clusters using similar improvement strategies). Qualifier adds domain specialization signal (sub-domain alignment via organic vocabulary). Configurable via `CLUSTERING_BLEND_W_{RAW,OPTIMIZED,TRANSFORM,QUALIFIER}` constants in `_constants.py`.

**Score-weighted centroids**: `max(0.1, score/10)` gives 9:1 weight ratio between score-9 and score-1 optimizations, shifting centroids toward high-quality members without completely ignoring low-scoring ones.
