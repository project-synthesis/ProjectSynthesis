# Evolutionary Taxonomy Engine — Design Specification

**Date:** 2026-03-20
**Status:** Draft
**Scope:** Replace hardcoded domain classification with a fully organic, uncapped hierarchical taxonomy system driven by HDBSCAN clustering, adaptive quality gates, and a 3D generative visualization.

---

## 1. Motivation

Domain classification in Project Synthesis is currently hardcoded in three locations that must stay in sync:

1. `DomainType` Literal in `pipeline_contracts.py` — 7 values
2. `DOMAIN_COLORS` in `frontend/src/lib/constants/patterns.ts` — 7 color mappings
3. `VALID_DOMAINS` in `pattern_extractor.py` — 7-value sanitization set

Strategies are fully adaptive (discovered from `prompts/strategies/*.md`). Domains are not. This inconsistency contradicts the product's core premise — a system intelligent enough to optimize prompts should be intelligent enough to discover its own taxonomy.

**Goals:**
- **Architectural purity (B):** Consistent extensibility model — no hardcoded classification values anywhere in the codebase.
- **Future-proofing (D):** The system evolves its own domain understanding as usage patterns emerge, without human configuration.

**Design principles:**
- Fully organic — no seed data, no bootstrap. The taxonomy emerges from zero.
- Fully autonomous with guardrails — lifecycle operations (emerge, merge, split, retire) happen without human intervention but are gated by non-regression quality checks.
- Uncapped hierarchy depth — the data decides how many levels exist. No fixed "domain → family → meta-pattern" layering.
- 3D semantic space — the visualization is a navigable 3D topology, not a 2D ring layout.

---

## 2. Core Architecture

### 2.1 The Taxonomy Engine

A single `TaxonomyEngine` service owns the entire hierarchy. It replaces the domain layer of `PatternExtractorService`, the hardcoded `DomainType`/`VALID_DOMAINS`/`DOMAIN_COLORS`, and domain-grouping logic in `KnowledgeGraphService`.

### 2.2 Data Structure: HDBSCAN Condensed Tree

The hierarchy is not a fixed-depth tree with named levels. It is an HDBSCAN condensed tree where clusters exist at different density thresholds:

- **Persistence** — how long a cluster survives across density thresholds. High persistence = fundamental grouping. Low persistence = fine-grained cluster.
- **Parent** — the next enclosing cluster in the condensed tree.
- **Lambda birth/death** — the density range where the cluster exists. Clusters are born when density increases, die when they merge into something larger or fragment.

There are no fixed "levels." The hierarchy is as deep or shallow as the data warrants. The same clustering algorithm operates at every scale.

```
               +-------------+
               |  Root (l=0)  |    <- everything
               +------+------+
            +---------+---------+
      +-----+-----+       +----+----+
      | l=0.3     |       | l=0.25  |    <- high-persistence nodes
      | (infra)   |       | (exp)   |
      +-----+-----+       +----+----+
    +-------+-------+       +--+--+
  +-+-+  +-+-+  +--++   +-+-+ +-+-+
  |BE |  |DB |  |ops|   |FE | |FS |    <- mid-persistence
  +-+-+  +-+-+  +--++   +-+-+ +-+-+
   ...    ...    ...     ...   ...
    |      |      |       |     |       <- families, meta-patterns...
   ...    ...    ...     ...   ...         emerge at finer density
```

Every node is the same type. The UI decides how to present the hierarchy based on persistence and zoom level.

### 2.3 Clustering Algorithm

**HDBSCAN with hierarchical cut.** No pre-specified k. Noise-aware — points that don't belong to any cluster remain "uncategorized."

**Three execution tiers:**

| Tier | When | What | Latency |
|------|------|------|---------|
| **Hot path** | Per-optimization | Nearest-centroid assignment. If cosine >= adaptive threshold, assign. Else create singleton candidate. | <500ms |
| **Warm path** | Every N optimizations (default 10) or 5-minute timer | Incremental re-clustering on candidates + recently changed nodes. Lifecycle operations (emerge/merge/split/retire) execute here. | <5s at 1000 nodes |
| **Cold path** | Quality degradation below threshold, or manual trigger | Full batch HDBSCAN + UMAP recomputation. The "defrag" operation. | 10-30s |

### 2.4 Quality Gate Framework

Every lifecycle operation passes through a speculative simulation:

```python
class QualityGate:
    def evaluate(self, operation: Operation) -> GateResult:
        # 1. Snapshot current metrics
        # 2. Apply operation to in-memory copy
        # 3. Compute new metrics
        # 4. Compare: new >= old across all dimensions
        # 5. Return approve/reject with detailed reasoning
```

**Metrics (hierarchical, adaptive):**

| Metric | Description | Adaptation |
|--------|-------------|-----------|
| **Coherence** | Mean pairwise cosine similarity of direct children | Threshold scales with persistence — high-persistence nodes allowed lower coherence (broad groupings) |
| **Separation** | Min cosine distance between sibling cluster centroids | Threshold scales inversely with depth — top-level siblings must be very distinct |
| **Coverage** | % of optimizations reachable through the tree | Must be monotonically non-decreasing system-wide |
| **Stability** | Centroid EMA drift over recent observations | Threshold scales with age — young clusters may drift, old ones must be stable |
| **DBCV** | Density-Based Cluster Validation score for the full tree | System-wide tree health metric |

**Adaptive threshold formula:**

```
threshold(persistence, population) = base * (1 + alpha * log(1 + population))
```

Where `alpha = 0.15` (tunable). Concrete examples:
- Population 3: threshold ~= base * 1.21 (lenient — let it form)
- Population 30: threshold ~= base * 1.51 (moderate)
- Population 100: threshold ~= base * 1.69 (strict — well-defined by now)

Base thresholds calibrated from the current system's empirical values (0.78 family merge, 0.82 pattern merge).

### 2.5 Non-Regression Invariant

**Cardinal rule: system-wide quality must be monotonically non-decreasing, with epsilon tolerance and compound operation support to prevent deadlock.**

```
Q_system = w_c * mean(coherence_i) + w_s * mean(separation_ij) + w_v * coverage + w_d * DBCV
```

**Weight management — constant-sum normalization:**

Weights always sum to 1.0. When DBCV activates (tree >= 5 confirmed nodes), other weights scale down proportionally to make room:

```
w_d_target = 0.15
ramp_progress = min(1.0, observations_since_activation / 20)
w_d = w_d_target * ramp_progress

# Scale remaining weights to fill 1.0 - w_d
remaining = 1.0 - w_d
w_c = 0.4 * remaining    # 0.4 at w_d=0, 0.34 at w_d=0.15
w_s = 0.35 * remaining   # 0.35 at w_d=0, 0.2975 at w_d=0.15
w_v = 0.25 * remaining   # 0.25 at w_d=0, 0.2125 at w_d=0.15
```

This ensures `total_weight == 1.0` always. DBCV ramp-in is mathematically smooth — no discontinuity in Q_system when DBCV activates.

**Epsilon tolerance and compound operations:**

Strict `Q_after >= Q_before` can deadlock the system when embedding distributions drift naturally. Two escape hatches:

1. **Epsilon tolerance:** `Q_after >= Q_before - epsilon`, where `epsilon = max(0.001, 0.01 * exp(-age / 50))`. Young taxonomies (age < 20 warm-path cycles) get epsilon ~= 0.007, mature taxonomies (~100 cycles) get epsilon ~= 0.001. This allows minor temporary dips that correct themselves.

2. **Compound operation batching:** Within a single warm-path cycle, all operations of the same priority level are evaluated as a batch: `Q_before` is measured once before the first operation, `Q_after` once after the last. A split that temporarily dips Q (by creating small fragments) followed by an emerge (consolidating fragments) can pass as a unit even if neither would pass individually.

3. **Deadlock breaker:** If 5 consecutive warm-path cycles reject ALL attempted operations (system is stuck), force the operation with the largest positive delta on any single quality dimension through regardless of composite Q_system impact, log a warning, and schedule a cold-path recomputation via `asyncio.create_task(self.run_cold_path())` (deferred to the next event loop iteration, after the current warm-path releases its lock). This prevents permanent stagnation.

**Edge case hardening for Q_system computation:**

```python
def compute_q_system(nodes: list[TaxonomyNode], weights: QWeights) -> float:
    """Compute composite system quality score.

    Edge cases:
    - Empty tree (0 confirmed nodes): returns 0.0 (no quality to measure)
    - Single node: coherence=1.0 (trivially coherent), separation=1.0
      (no siblings to conflict with), coverage from member assignment
    - All nodes retired: returns 0.0 (system needs cold-path rebuild)
    - NaN/Inf protection: any dimension that computes NaN is replaced
      with 0.0 and flagged in warnings
    """
    confirmed = [n for n in nodes if n.state == "confirmed"]
    if not confirmed:
        return 0.0

    coherences = [n.coherence for n in confirmed if math.isfinite(n.coherence)]
    separations = _pairwise_separations(confirmed)
    coverage = _compute_coverage(confirmed, nodes)

    mean_c = statistics.mean(coherences) if coherences else 0.0
    mean_s = statistics.mean(separations) if separations else 1.0  # no siblings = perfect separation
    dbcv = _compute_dbcv(confirmed) if len(confirmed) >= 5 and weights.w_d > 0 else 0.0

    # Clamp all components to [0.0, 1.0]
    mean_c = max(0.0, min(1.0, mean_c))
    mean_s = max(0.0, min(1.0, mean_s))
    coverage = max(0.0, min(1.0, coverage))
    dbcv = max(0.0, min(1.0, dbcv))

    # Weights always sum to 1.0 (constant-sum normalization)
    raw = weights.w_c * mean_c + weights.w_s * mean_s + weights.w_v * coverage + weights.w_d * dbcv

    # Sanity check — should not happen with constant-sum weights, but defensive
    total_weight = weights.w_c + weights.w_s + weights.w_v + weights.w_d
    if total_weight < 1e-9:
        return 0.0
    if abs(total_weight - 1.0) > 1e-6:
        raw /= total_weight  # Self-heal if weights drift

    return max(0.0, min(1.0, raw))
```

### 2.6 Concurrency Model

The hot path (per-optimization) and warm path (periodic) can run concurrently. This requires explicit coordination:

```python
class TaxonomyEngine:
    _warm_path_lock: asyncio.Lock   # Serializes warm/cold-path execution
```

**Hot path:** Lock-free. Does nearest-centroid assignment using a read-consistent snapshot of cluster centroids (cached in memory, refreshed after each warm-path cycle). If the hot path assigns an optimization to a cluster that the concurrent warm-path retires mid-flight, the optimization's `taxonomy_node_id` becomes stale. On the next warm-path cycle, stale assignments are detected (node is retired) and re-assigned.

**Warm path:** Checks `_warm_path_lock.locked()` first as a fast-path deduplication check — if the lock is already held, the invocation is skipped and logged (no separate boolean needed, since `asyncio.Lock.locked()` is atomic in the single-threaded event loop). If not held, acquires the lock before starting. This prevents both the timer trigger and the optimization-count trigger from running concurrently.

**Cold path:** Acquires `_warm_path_lock` (same lock — cold path is a superset of warm path). Blocks warm-path invocations for the duration. Hot-path continues with stale cached centroids; refreshed when cold-path completes.

---

## 3. Lifecycle Operations

Four operations govern taxonomy evolution. Each is triggered organically, evaluated speculatively, and committed only when non-regressive.

### 3.1 Emerge

A new cluster crystallizes from uncategorized or singleton nodes.

**Trigger (warm path):** HDBSCAN identifies a new dense region. Minimum conditions:
- >= 3 members
- Persistence above `min_persistence` threshold
- Silhouette score > 0

**Algorithm:**

1. HDBSCAN identifies candidate cluster `C_new`.
2. Compute `coherence(C_new)` and `separation(C_new)`.
3. Speculative gate: compute `Q_system` before and after assigning members to `C_new`. Commit only if `Q_after >= Q_before - epsilon` (see Section 2.5 for epsilon formula).
4. On commit: state = `candidate`. Generate label via Haiku. Generate color from UMAP position in OKLab. Insert parent edge. Publish `taxonomy_changed` event.
5. Promotion: `candidate` -> `confirmed` when stability sustains below drift threshold for 5 consecutive warm-path cycles.

**Non-regression proof:** Members only move to `C_new` if the system improves. If they were better classified where they were, emergence is rejected.

### 3.2 Merge

Two clusters converge semantically.

**Trigger (warm path):** Cosine similarity between sibling centroids exceeds `merge_proximity_threshold`, OR one cluster's members have higher mean similarity to the other cluster's centroid than to their own.

**Algorithm:**

1. Identify merge candidates: sibling pairs `(A, B)` ordered by similarity (highest first).
2. For each pair:
   a. Compute merged cluster `M = A U B`.
   b. Verify: `coherence(M) >= min(coherence(A), coherence(B))` — merged cluster not less coherent than the weaker parent.
   c. Speculative gate: `Q_system` must be non-regressive (epsilon-tolerant, see Section 2.5).
   d. Meta-pattern preservation: all meta-patterns from both A and B retained. Duplicates (cosine >= 0.82) enriched, never dropped.
   e. If all gates pass, commit.
3. On commit: M inherits label of higher-persistence parent. Centroid = weighted mean by member count. Color regenerated. A and B transition to `retired` (soft delete, preserved for audit). Publish `taxonomy_changed`.

**Non-regression proof:** Coherence floor (step 2b) prevents dilution. System Q gate (step 2c) prevents global regression. Meta-pattern check (step 2d) prevents knowledge loss.

### 3.3 Split

A cluster has accumulated enough internal sub-structure to warrant separation.

**Trigger (warm path):** Coherence drops below adaptive threshold AND internal HDBSCAN finds >= 2 sub-clusters with individual coherence above the child-level threshold.

**Algorithm:**

1. Run HDBSCAN on parent P's member embeddings.
2. Extract candidate children `C_1, ..., C_k`.
3. Verify: `coherence(C_i) > coherence(P)` for ALL children — every child more coherent than parent.
4. Verify: no orphans — every member of P assigned to some `C_i` or reassigned to nearest sibling. Orphan count < 10%.
5. Speculative gate: `Q_system` must be non-regressive (epsilon-tolerant, see Section 2.5).
6. Meta-pattern redistribution: each pattern assigned to child whose centroid is most similar. No patterns dropped.
7. On commit: generate labels and colors for each child. P transitions to `retired`. Children inherit P's parent edge. Publish `taxonomy_changed`.

**Non-regression proof:** Step 3 ensures every child is more precise. Step 4 prevents orphans. Step 5 validates globally. Step 6 preserves knowledge.

### 3.4 Retire

A cluster loses relevance.

**Trigger (warm path):** Member count < 2 for >= 3 consecutive warm-path cycles, OR zero new members for an adaptive observation count. The idle threshold adapts with age: `retire_after_idle = max(20, 3 * age_in_days)`. A confirmed cluster less than 7 days old is never eligible for retirement (minimum age gate). This prevents premature retirement of seasonal or periodic-use clusters.

**Algorithm:**

1. For each remaining member: find nearest sibling by centroid similarity. Verify member's similarity to target > similarity to current cluster. If no target, member becomes uncategorized.
2. Meta-pattern absorption: each pattern assigned to absorbing cluster. Merge with existing patterns if cosine >= 0.82. Patterns with no target preserved as uncategorized (never deleted).
3. Speculative gate: `Q_system` after redistribution must be non-regressive.
4. On commit: `state = 'retired'`, `retired_at = now()`. Node preserved in DB (audit trail). Parent-child edges updated. Publish `taxonomy_changed`.

**Non-regression proof:** Each member verified to be better served by new home (step 1). No meta-patterns lost (step 2). Global quality maintained (step 3).

**Key guarantee:** Retired nodes are never deleted. Full audit trail preserved. If usage patterns shift back, a new cluster re-emerges organically.

### 3.5 Operation Ordering and Conflict Resolution

Priority within a single warm-path cycle:

```
Split > Emerge > Merge > Retire
```

Rationale:
1. Split first — don't merge or retire what needs splitting.
2. Emerge — new structure before consolidation.
3. Merge — consolidate after new structure settles.
4. Retire — clean up after all constructive operations.

Operations within a cycle are sequential. Each operation sees results of all preceding operations. If two operations affect overlapping clusters, the higher-priority one executes first, then the lower-priority one is re-evaluated.

---

## 4. Analyzer Integration & Cold-Start Graceful Degradation

### 4.1 Analyzer Prompt Transformation

The `analyze.md` prompt changes from a fixed 7-domain menu to free-text:

```markdown
**domain**: Describe the development domain in 1-3 words. Be specific --
"REST API design" is better than "backend". Use your judgment about what
technical area this prompt primarily targets. If the prompt is not
development-related, use "general".
```

`DomainType` Literal is deleted from `pipeline_contracts.py`. The `domain` field on `AnalysisResult` becomes `str`. Validation shifts from schema-level to semantic-level (taxonomy engine mapping).

### 4.2 Post-Analysis Domain Mapping

New pipeline step between analysis and optimization:

```
analyze -> domain_mapping -> optimize -> score
```

1. Embed the free-text domain string using `all-MiniLM-L6-v2`.
2. Search taxonomy tree for nearest confirmed cluster centroid.
3. If best match >= `assignment_threshold`, assign.
4. If below threshold, mark as "unmapped" — stored in unmapped pool for warm-path consideration.

Both values persisted: `domain_raw` (LLM free-text) and `taxonomy_node_id` (mapped cluster or null). Raw text always available for re-mapping as taxonomy evolves.

### 4.3 Cold-Start Phases

| Phase | Optimizations | Taxonomy state | Visualization | Navigator |
|-------|--------------|----------------|---------------|-----------|
| **0: Empty** | 0 | Root only | Pulsing seed node: "Your taxonomy will emerge as you optimize prompts" | Empty state |
| **1: Accumulating** | 1-9 | Root + uncategorized leaves | 3D scatter of points. Colors from hash of free-text domain. No clusters. | Flat list, sorted by recency |
| **2: Crystallizing** | 10-29 | First warm-path runs, 2-4 candidate clusters | Translucent cluster volumes appear. Dashed boundaries. Provisional labels. Muted colors. | Begins grouping. "Emerging" section for unmapped. |
| **3: Stabilizing** | 30-49 | Candidates promote to confirmed. Lifecycle begins. | Confirmed clusters get solid boundaries, full-saturation colors, permanent labels. | Stable domain groups. |
| **4: Mature** | 50+ | Full lifecycle active. Splits and merges. | Rich 3D topology with navigable hierarchy. | Full hierarchical grouping. |

### 4.4 Sampling Pipeline Alignment

Low-confidence (< 0.7) fallback writes `domain_raw = "general"`. Mapping still runs — "general" embeds to a point in taxonomy space. If a catch-all cluster exists, it maps there. Otherwise enters unmapped pool. Confidence threshold preserved — low-confidence results don't pollute taxonomy.

---

## 5. Data Model

### 5.1 New Table: `taxonomy_nodes`

```python
class TaxonomyNode(Base):
    __tablename__ = "taxonomy_nodes"

    id: str                          # UUID primary key
    parent_id: str | None            # FK -> taxonomy_nodes.id (null = root)

    # Cluster identity
    label: str                       # LLM-generated ("API Architecture")
    label_generated_at: datetime

    # Embedding state
    centroid_embedding: bytes         # 384-dim float32, running mean
    member_count: int                 # Direct children count

    # Quality metrics (updated each warm-path cycle)
    coherence: float
    separation: float
    stability: float
    persistence: float               # HDBSCAN persistence

    # Lifecycle
    state: str                       # 'candidate' | 'confirmed' | 'retired'
    created_at: datetime
    confirmed_at: datetime | None
    retired_at: datetime | None
    observations: int                # Warm-path cycles survived

    # UMAP projection (cached)
    umap_x: float | None
    umap_y: float | None
    umap_z: float | None

    # Generated color
    color_hex: str                   # OKLab-derived

    # Relationships
    parent = relationship("TaxonomyNode", remote_side=[id], backref="children")
    families = relationship("PatternFamily", back_populates="taxonomy_node")
```

Indexes: `ix_taxonomy_parent` (parent_id), `ix_taxonomy_state` (state), `ix_taxonomy_persistence` (persistence DESC).

### 5.2 New Table: `taxonomy_snapshots`

```python
class TaxonomySnapshot(Base):
    __tablename__ = "taxonomy_snapshots"

    id: str
    created_at: datetime
    trigger: str                     # 'warm_path' | 'cold_path' | 'manual'

    # System-wide metrics
    q_system: float
    q_coherence: float
    q_separation: float
    q_coverage: float
    q_dbcv: float

    # What changed
    operations: str                  # JSON list
    nodes_created: int
    nodes_retired: int
    nodes_merged: int
    nodes_split: int

    # Recovery
    tree_state: str                  # JSON: node IDs + parent edges
```

**Snapshot retention policy:** Snapshots accumulate rapidly (one per warm-path cycle, ~every 5 minutes). Pruning runs after each snapshot write:
- Last 24 hours: keep all snapshots
- 1-30 days: keep one per day (highest Q_system that day)
- 30+ days: keep one per week
- `tree_state` JSON is stored only on daily/weekly retained snapshots; warm-path snapshots within the 24-hour window store operations-only diffs to minimize storage.

### 5.3 Modified: `PatternFamily`

```python
# Removed
domain: str

# Added
taxonomy_node_id: str | None      # FK -> taxonomy_nodes.id
domain_raw: str                    # Free-text from analyzer
```

### 5.4 Modified: `Optimization`

```python
# Removed
domain: str | None

# Added
taxonomy_node_id: str | None      # FK -> taxonomy_nodes.id
domain_raw: str | None             # Free-text from analyzer
```

### 5.5 Migration

Single Alembic revision. Clean slate — database is empty. Drop old `domain` columns, add new columns and tables. No backfill, no compatibility shims.

---

## 6. Service Architecture & File Layout

### 6.1 Replacement Map

| Current file | Outcome |
|---|---|
| `services/pattern_extractor.py` | **Deleted.** All logic moves to taxonomy engine. |
| `services/knowledge_graph.py` | **Trimmed.** Domain grouping moves out. Becomes thin read/serialization layer over taxonomy tree. |
| `services/pattern_matcher.py` | **Trimmed.** Domain references replaced by `taxonomy_node_id`. |
| `schemas/pipeline_contracts.py` | **Modified.** `DomainType` deleted. `domain` becomes `str`. |
| `services/sampling_pipeline.py` | **Modified.** Low-confidence fallback writes `domain_raw`. Domain mapping step added. |
| `services/pipeline.py` | **Modified.** New domain-mapping step between analysis and optimization. |
| `routers/patterns.py` | **Modified.** Domain filter queries taxonomy tree. New endpoints. |
| `prompts/analyze.md` | **Modified.** Free-text domain instructions. |
| `frontend/.../constants/patterns.ts` | **Deleted.** `DOMAIN_COLORS`/`domainColor()` gone. Colors from API. `scoreColor()` moves to `utils/colors.ts`. |

### 6.2 New Package: `backend/app/services/taxonomy/`

```
taxonomy/
    __init__.py              # Public API: TaxonomyEngine
    engine.py                # Hot/warm/cold path orchestration
    clustering.py            # HDBSCAN wrapper, incremental updates, condensed tree
    quality.py               # QualityGate, metrics, speculative simulation
    lifecycle.py             # Emerge, merge, split, retire
    projection.py            # UMAP 3D, incremental transform
    labeling.py              # Haiku label generation
    coloring.py              # OKLab color generation from UMAP position
    snapshot.py              # Snapshot CRUD, audit trail, recovery
```

Each file ~200-400 lines. Total package ~2000-2800 lines.

### 6.3 TaxonomyEngine Public API

```python
class TaxonomyEngine:
    """Unified hierarchical taxonomy management."""

    def __init__(
        self,
        embedding_service: EmbeddingService,
        provider: LLMProvider | None = None,
    ) -> None: ...

    # --- Hot path (per-optimization) ---
    async def process_optimization(self, optimization_id: str) -> None
    async def map_domain(self, domain_raw: str) -> TaxonomyMapping

    # --- Warm path (periodic) ---
    async def run_warm_path(self) -> WarmPathResult

    # --- Cold path (full recomputation) ---
    async def run_cold_path(self) -> ColdPathResult

    # --- Read API ---
    async def get_tree(self, min_persistence: float = 0.0) -> list[TaxonomyNodeDTO]
    async def get_node(self, node_id: str) -> TaxonomyNodeDTO
    async def get_stats(self) -> TaxonomyStats
    async def search(self, query: str, top_k: int = 5) -> list[TaxonomySearchResult]

    # --- Manual overrides ---
    async def rename_node(self, node_id: str, label: str) -> TaxonomyNodeDTO
    async def reassign_family(self, family_id: str, target_node_id: str) -> bool
```

### 6.4 Pipeline Integration

```
analyze --> domain_mapping --> optimize --> score
               |                              |
               v                              v
        TaxonomyEngine                 TaxonomyEngine
        .map_domain()                  .process_optimization()
        (synchronous)                  (background task)
```

Warm-path triggered every `warm_path_interval` (default 10) optimizations, plus a 5-minute background timer. Both triggers are deduplicated via `_warm_path_running` flag (see Section 2.6).

**`process_optimization()` responsibilities (replaces `PatternExtractorService.process()`):**

The taxonomy engine's hot-path method must handle all work previously done by `pattern_extractor.py`:

1. Embed the raw prompt and store on `Optimization.embedding`
2. Assign to nearest family (or create candidate) — taxonomy-aware clustering
3. Extract meta-patterns via Haiku LLM call
4. Merge meta-patterns into the family (cosine >= 0.82 enrichment)
5. Write `OptimizationPattern` join record (relationship="source")
6. Publish `taxonomy_changed` event (replaces `pattern_updated`)

All existing functionality is preserved; the only change is that family assignment uses the taxonomy tree instead of hardcoded domain strings.

### 6.5 Event Bus

| Event | Publisher | Subscribers |
|---|---|---|
| `optimization_created` | pipeline.py | TaxonomyEngine.process_optimization() |
| `taxonomy_changed` | TaxonomyEngine (lifecycle ops) | Frontend (SSE -> 3D refresh) |
| `taxonomy_quality` | TaxonomyEngine (warm/cold path) | StatusBar (health indicator) |

Replaces current `pattern_updated` event.

### 6.6 New API Endpoints

```
GET  /api/taxonomy/tree       # Full tree for 3D visualization
GET  /api/taxonomy/node/{id}  # Single node with children, parent chain, metrics
GET  /api/taxonomy/stats      # System quality metrics + snapshot history
POST /api/taxonomy/recluster  # Manual cold-path trigger
```

Existing pattern endpoints modified to query taxonomy tree for domain filtering.

### 6.7 MCP Server Integration

The MCP server (`mcp_server.py`) runs as a separate process. It needs taxonomy capabilities for:
- `synthesis_optimize`: domain mapping during sampling pipeline
- `synthesis_analyze`: domain_raw on analysis results

The MCP server instantiates its own `TaxonomyEngine` (same pattern as its own `RoutingManager`). The engine reads/writes the same SQLite database. Warm-path and cold-path timers run only in the FastAPI process (the MCP server's engine operates in hot-path-only mode to avoid conflicting warm-path cycles). The MCP server publishes `taxonomy_changed` events via HTTP POST to `/api/events/_publish` (same cross-process notification pattern used for other events).

**Centroid cache invalidation:** The MCP server's hot-path-only engine maintains a cached centroid snapshot for nearest-centroid assignment. This cache must refresh when the FastAPI process completes warm-path cycles that modify the taxonomy. The MCP server subscribes to `taxonomy_changed` SSE events from the FastAPI process (via its existing event polling mechanism). On receiving `taxonomy_changed`, the engine reloads confirmed node centroids from the database into its in-memory cache. Between invalidation events, hot-path assignments use the cached snapshot (identical to the FastAPI engine's behavior between warm-path cycles).

### 6.8 PatternMatcher Response Schema

The `/api/patterns/match` endpoint currently returns `family.domain` as a string. After migration:

```python
# Response includes both taxonomy node reference and resolved label
{
    "family_id": "...",
    "family_label": "REST API patterns",
    "taxonomy_node_id": "...",           # FK into taxonomy tree
    "taxonomy_label": "API Architecture", # Resolved cluster label
    "taxonomy_color": "#a855f7",          # Generated color from node
    "taxonomy_breadcrumb": ["Infrastructure", "API Architecture"],
    "similarity": 0.85,
    "meta_patterns": [...]
}
```

The frontend `PatternSuggestion` component displays `taxonomy_label` with `taxonomy_color` where it previously showed the hardcoded domain name with `DOMAIN_COLORS`.

### 6.9 Frontend File Changes

```
# Deleted
frontend/src/lib/constants/patterns.ts
frontend/src/lib/components/patterns/utils/layout.ts

# Moved
scoreColor() -> frontend/src/lib/utils/colors.ts

# Modified
frontend/src/lib/api/patterns.ts
frontend/src/lib/stores/patterns.svelte.ts
frontend/src/lib/components/layout/PatternNavigator.svelte
frontend/src/lib/components/layout/Inspector.svelte

# Replaced
frontend/src/lib/components/patterns/RadialMindmap.svelte -> deleted

# New
frontend/src/lib/components/taxonomy/SemanticTopology.svelte
frontend/src/lib/components/taxonomy/TopologyControls.svelte
frontend/src/lib/components/taxonomy/TopologyRenderer.ts
frontend/src/lib/components/taxonomy/TopologyData.ts
frontend/src/lib/components/taxonomy/TopologyInteraction.ts
frontend/src/lib/components/taxonomy/TopologyLabels.ts
frontend/src/lib/components/taxonomy/TopologyWorker.ts
frontend/src/lib/api/taxonomy.ts
frontend/src/lib/utils/colors.ts
```

---

## 7. 3D Visualization Engine

### 7.1 Component Architecture

```
SemanticTopology.svelte (orchestrator)
    TopologyRenderer.ts        -- Three.js scene, camera, render loop
    TopologyData.ts            -- API data -> scene graph
    TopologyInteraction.ts     -- raycasting, selection, navigation
    TopologyLabels.ts          -- billboard text sprites, LOD
    TopologyControls.svelte    -- UI overlay: search, filter, zoom, health
    TopologyWorker.ts          -- Web Worker: force settling
```

Replaces `RadialMindmap.svelte`. Same editor tab slot (`activity_type: 'patterns'`), renders `<canvas>` instead of `<svg>`.

### 7.2 Scene Graph Layers

| Layer | Content | Material | Performance |
|-------|---------|----------|-------------|
| **Cluster volumes** | Semi-transparent convex hulls | `MeshBasicMaterial` (no lighting) | Opacity: confirmed=0.12, candidate=0.06 |
| **Nodes** | `InstancedMesh` with 3 LOD tiers | Per-instance color | Far: 4-vert quad. Mid: 8-seg octahedron. Near: 16-seg icosphere. |
| **Edges** | `LineSegments` | `LineBasicMaterial` | Hierarchical: always visible. Similarity (>=0.55): mid/near LOD only. |
| **Labels** | `Sprite` with canvas textures | `SpriteMaterial` | Near LOD only. 64px monospace on power-of-2 canvas. |

### 7.3 Semantic Zoom

Zooming is hierarchy navigation, not magnification:

- **Far** (camera > 50 units): only high-persistence nodes visible. Large cluster volumes, few labels.
- **Mid** (15-50 units): mid-persistence nodes appear. Parent volumes fade, child volumes emerge.
- **Near** (< 15 units): leaf nodes visible. Full labels, similarity edges, individual patterns.

`persistence` visibility threshold scales linearly with camera distance.

### 7.4 Navigation

| Action | Input | Behavior |
|---|---|---|
| Orbit | Left drag | Rotate around focus |
| Pan | Right drag / Shift+Left | Move focus point |
| Zoom | Scroll | Dolly. LOD tiers update. |
| Focus | Click node | Animate to center on node. Children expand. |
| Ascend | Click volume / Escape | Up one level. Focus parent. |
| Search | Ctrl+F | Highlight matches, animate to best. |

### 7.5 UMAP Projection

Backend: `umap.UMAP(n_components=3, metric="cosine", low_memory=True, random_state=42)`. Runs in thread pool executor. Incremental `transform()` for new points (O(k), not O(n) refit). Positions cached on `TaxonomyNode.umap_x/y/z`.

**Cold-path projection stability (Procrustes alignment):** A full UMAP refit on a changed dataset produces a completely different projection, destroying the user's spatial mental model. After every cold-path refit, apply Procrustes analysis (scipy.spatial.procrustes) between old and new projections of confirmed nodes. This finds the optimal rotation + scaling + translation that minimizes displacement of stable nodes. The aligned positions preserve the user's spatial memory ("security stuff is upper-left") while allowing the global structure to evolve. Implementation: use `scipy.linalg.orthogonal_procrustes(new_confirmed_centered, old_confirmed_centered)` which returns the rotation matrix `R` directly (rather than `scipy.spatial.procrustes` which returns standardized matrices). Center both point sets, compute `R`, then apply `new_all_centered @ R + old_mean` to all new positions including candidates. This preserves relative positions since Procrustes alignment is a rigid transformation.

Frontend: Web Worker applies 50-iteration force settling for local collision resolution. Scene renders immediately with UMAP positions, then animates to settled positions (~50-100ms for 500 nodes).

### 7.6 Generative Color System

Colors derived from 3D UMAP position in OKLab perceptual space:

```python
def generate_color(umap_x: float, umap_y: float, umap_z: float) -> str:
    """Generate perceptually distinct color from 3D UMAP position.

    Uses OKLab with extended gamut (a/b +/-0.20) to support 50+ clusters
    with discriminable colors. Z-axis modulates chroma.
    """
    # Normalize UMAP coordinates to [-1, 1]
    a = normalize(umap_x, -1.0, 1.0) * 0.20   # green-red axis (extended)
    b = normalize(umap_y, -1.0, 1.0) * 0.20   # blue-yellow axis (extended)
    chroma_scale = 0.7 + 0.3 * normalize(umap_z, 0.0, 1.0)
    a *= chroma_scale
    b *= chroma_scale
    L = 0.72  # Fixed lightness for dark-background readability
    return oklab_to_hex(L, a, b)


def enforce_minimum_delta_e(
    colors: list[tuple[str, str]],  # [(node_id, hex_color), ...]
    min_delta_e: float = 0.04,
) -> list[tuple[str, str]]:
    """Post-processing pass: ensure sibling clusters are visually distinct.

    For any pair where deltaE(OKLab) < min_delta_e, apply incremental
    hue rotation to one of them until the minimum distance is met.
    Only applied to sibling clusters (same parent in the tree).
    """
    # Convert to OKLab, check pairwise, rotate as needed
    ...
```

Properties:
- Semantically close = visually similar (UMAP proximity -> OKLab proximity)
- **Minimum perceptual distance:** Post-processing guarantees deltaE >= 0.04 between siblings
- **Extended gamut:** a/b ranges +/-0.20 support 50+ discriminable clusters (vs. prior +/-0.15)
- Deterministic (same position = same color, modulo sibling-delta enforcement)
- Dark-background optimized (L=0.72 guarantees WCAG AA contrast against `#06060c`)
- Neon register alignment (a/b ranges favor cyan/magenta/amber)

### 7.7 Performance Budget

Target: 60fps on integrated graphics with up to 2000 nodes in the taxonomy tree, with up to 500 visible at any given zoom level (semantic zoom culls by persistence).

| Component | Budget | Strategy |
|---|---|---|
| Draw calls | <= 20/frame | InstancedMesh, batched volumes |
| Triangles | <= 50K visible | LOD: far=4, mid=64, near=256 per node |
| Textures | <= 32MB | Label sprites LRU-cached, 256x64, max 500 |
| Worker | <= 100ms | 500 nodes x 50 iterations |
| API payload | <= 200KB | Positions (3 floats), colors, labels, metrics |
| JS heap | <= 64MB | Shared geometries via InstancedMesh |

Progressive fallback: if frame time > 20ms consistently, reduce LOD thresholds -> disable similarity edges -> reduce volume tessellation -> cap visible nodes at 500.

### 7.8 Animated Transitions

| Event | Animation |
|---|---|
| Emerge | Fade in 0->1 (400ms), grow from point |
| Retire | Fade out 1->0 (600ms), shrink to point |
| Merge | Two nodes animate to midpoint, flash, resolve to one |
| Split | Pulse, split into children animating to new positions |
| Re-label | Cross-fade old->new text |
| Re-color | OKLab interpolation (300ms) |
| Reclassify | Detach from parent edge, arc-animate to new position |

Ease-out cubic timing. Concurrent operations staggered with 200ms gaps.

---

## 8. Testing Strategy

### 8.1 Four Testing Layers

**Layer 1: Unit Tests — Deterministic Algorithms**

```
tests/taxonomy/
    test_clustering.py       -- HDBSCAN wrapper, distance matrix
    test_quality.py          -- QualityGate, adaptive thresholds, Q_system
    test_lifecycle.py        -- Each operation in isolation
    test_projection.py       -- UMAP fit/transform
    test_coloring.py         -- OKLab, contrast, readability
    test_labeling.py         -- Prompt construction (mock LLM)
    test_snapshot.py         -- Serialization, recovery
```

Key verification: adaptive threshold scaling, non-regression on merge (coherence floor), WCAG AA contrast for all generated colors.

**Layer 2: Integration Tests — Engine Pipelines**

```
tests/taxonomy/
    test_engine_hot_path.py   -- process_optimization end-to-end
    test_engine_warm_path.py  -- re-clustering with lifecycle
    test_engine_cold_path.py  -- full recomputation
    test_domain_mapping.py    -- map_domain() free-text inputs
    test_cold_start.py        -- 0->50 optimization phase transitions
```

Key verification: Q_system monotonically non-decreasing across all warm-path cycles.

**Layer 3: Behavioral Tests — Emergent Properties**

```
tests/taxonomy/
    test_emergence.py          -- synthetic distributions -> known clusters
    test_evolution.py          -- 500+ optimization simulations
    test_edge_cases.py         -- adversarial/degenerate inputs
```

Key verification: distinct prompt domains produce distinct clusters (purity >= 80%). Broad domains split when sub-specializations arrive. Identical prompts converge, not proliferate.

**Layer 4: Performance Tests**

```
tests/taxonomy/
    test_performance.py        -- latency assertions per execution tier
```

Key verification: hot path < 500ms, warm path < 5s at 1000 nodes, incremental UMAP > 10x faster than refit.

### 8.2 Test Data Generators

Embedding-space cluster generators for realistic distributions:

```python
def make_cluster_distribution(center_text: str, n_samples: int, spread: float = 0.1):
    """Generate n embeddings clustered around the embedding of center_text.
    Real embedding model + Gaussian noise + L2 normalization."""
```

### 8.3 Frontend Tests

- `TopologyData.ts`: unit tests for API -> scene graph transforms, LOD assignment, edge generation.
- `TopologyInteraction.ts`: mock scene raycasting, zoom-to-persistence mapping.
- `TopologyWorker.ts`: force settling convergence, no NaN, runtime budget.
- `SemanticTopology.svelte`: integration with `@testing-library/svelte`, canvas mount, scene graph node count, zoom behavior.

---

## 9. Quality Metrics Dashboard

### 9.1 Q_system Sparkline — Hardened Mathematics

The sparkline displays `Q_system` over the last N snapshots. The computation must be robust against all edge cases.

**Data pipeline:**

```python
def compute_sparkline_data(
    snapshots: list[TaxonomySnapshot],
    max_points: int = 30,
) -> SparklineData:
    """Transform raw snapshots into sparkline-ready data.

    Args:
        snapshots: ordered by created_at ascending.
        max_points: maximum data points (downsample if exceeded).

    Returns:
        SparklineData with normalized points, min, max, current, trend.

    Edge cases:
        - 0 snapshots: returns empty SparklineData (no render)
        - 1 snapshot: single point, no trend computable
        - All identical values: flat line, trend = 0.0
        - NaN/Inf in q_system: filtered out with warning
        - Negative values: clamped to 0.0
        - Values > 1.0: clamped to 1.0 (should not happen but defensive)
    """
    # 1. Filter invalid
    valid = [s for s in snapshots if math.isfinite(s.q_system)]
    if not valid:
        return SparklineData.empty()

    values = [max(0.0, min(1.0, s.q_system)) for s in valid]

    # 2. Downsample if needed (LTTB — Largest Triangle Three Buckets)
    if len(values) > max_points:
        values = lttb_downsample(values, max_points)

    # 3. Compute statistics
    v_min = min(values)
    v_max = max(values)
    v_current = values[-1]
    v_range = v_max - v_min

    # 4. Normalize to [0, 1] for SVG Y coordinates
    #    Guard against zero range (all values identical)
    if v_range < 1e-9:
        normalized = [0.5] * len(values)  # Flat line at midpoint
    else:
        normalized = [(v - v_min) / v_range for v in values]

    # 5. Compute trend via linear regression (least squares)
    trend = _compute_trend(values)

    return SparklineData(
        points=normalized,
        raw_values=values,
        min=v_min,
        max=v_max,
        current=v_current,
        trend=trend,
        point_count=len(values),
    )
```

**LTTB downsampling (Largest Triangle Three Buckets):**

Preserves visual shape when reducing data points. Better than naive every-Nth sampling because it keeps peaks and valleys.

```python
def lttb_downsample(values: list[float], target: int) -> list[float]:
    """Largest Triangle Three Buckets downsampling.

    Reduces N points to `target` points while preserving visual shape.
    Always keeps first and last points. Interior points selected by
    maximizing the triangle area formed with neighbors.

    Preconditions:
        - len(values) > target > 2
        - All values are finite floats

    Time complexity: O(N) single pass.
    """
    n = len(values)
    if n <= target:
        return values[:]

    result = [values[0]]  # Always keep first
    bucket_size = (n - 2) / (target - 2)

    prev_idx = 0
    for i in range(1, target - 1):
        # Bucket boundaries
        bucket_start = int(1 + (i - 1) * bucket_size)
        bucket_end = int(1 + i * bucket_size)
        bucket_end = min(bucket_end, n - 1)

        # Next bucket average (for triangle area calculation)
        next_start = int(1 + i * bucket_size)
        next_end = int(1 + (i + 1) * bucket_size)
        next_end = min(next_end, n)

        if next_start >= n:
            next_avg = values[-1]
        else:
            next_slice = values[next_start:next_end]
            next_avg = sum(next_slice) / len(next_slice) if next_slice else values[-1]

        # Next bucket average x-coordinate (midpoint of next bucket)
        next_x = (next_start + min(next_end, n) - 1) / 2.0

        # Find point in bucket that maximizes triangle area
        best_area = -1.0
        best_idx = bucket_start
        prev_val = values[prev_idx]

        for j in range(bucket_start, bucket_end):
            # Triangle area = 0.5 * |x1(y2-y3) + x2(y3-y1) + x3(y1-y2)|
            # Three points: (prev_idx, prev_val), (j, values[j]), (next_x, next_avg)
            area = abs(
                prev_idx * (values[j] - next_avg)
                + j * (next_avg - prev_val)
                + next_x * (prev_val - values[j])
            )
            if area > best_area:
                best_area = area
                best_idx = j

        result.append(values[best_idx])
        prev_idx = best_idx

    result.append(values[-1])  # Always keep last
    return result
```

**Trend computation via ordinary least squares:**

```python
def _compute_trend(values: list[float]) -> float:
    """Compute trend as slope of OLS linear regression.

    Returns:
        Slope normalized to [-1.0, 1.0] where:
        - +1.0 = strongly improving
        -  0.0 = flat
        - -1.0 = strongly degrading

    Edge cases:
        - 0 or 1 values: return 0.0 (no trend computable)
        - All identical: return 0.0
        - Numerical instability (denominator near zero): return 0.0
    """
    n = len(values)
    if n < 2:
        return 0.0

    # OLS: slope = (n * sum(xy) - sum(x) * sum(y)) / (n * sum(x^2) - sum(x)^2)
    sum_x = 0.0
    sum_y = 0.0
    sum_xy = 0.0
    sum_x2 = 0.0

    for i, v in enumerate(values):
        x = float(i)
        sum_x += x
        sum_y += v
        sum_xy += x * v
        sum_x2 += x * x

    denominator = n * sum_x2 - sum_x * sum_x
    if abs(denominator) < 1e-12:
        return 0.0

    slope = (n * sum_xy - sum_x * sum_y) / denominator

    # Normalize as percentage of mean — makes trend meaningful regardless
    # of absolute Q_system level or window size.
    # A slope that changes Q by 10% of its mean over the window = trend +/-0.5
    mean_y = sum_y / n
    if abs(mean_y) < 1e-9:
        return 0.0

    total_change = slope * (n - 1)
    # Normalize: total_change / mean_y gives fractional change over window.
    # Scale by 2.0 so that a 50% change = trend 1.0 (full scale).
    trend = (total_change / mean_y) * 2.0
    return max(-1.0, min(1.0, trend))
```

### 9.2 StatusBar Indicator

Single `Q_system` badge with color derived from score:

```
>= 0.8: var(--color-neon-green)   (healthy)
>= 0.6: var(--color-neon-cyan)    (good)
>= 0.4: var(--color-neon-yellow)  (attention needed)
<  0.4: var(--color-neon-red)     (degraded — cold-path recommended)
```

Tooltip: last snapshot timestamp + operation summary. Pulses on warm-path completion.

### 9.3 Inspector Health Panel

Displayed when no family is selected:

```
TAXONOMY HEALTH

Q_system    0.847  ||||||||||||..  ^0.02
Coherence   0.812  ||||||||||||...
Separation  0.891  |||||||||||||..
Coverage    0.940  ||||||||||||||.
Tree Health 0.783  |||||||||||....

[Q_system sparkline over 30 cycles]

Nodes: 47 confirmed . 3 candidate
Depth: 4 levels . 8 leaf clusters
Last cycle: 12s ago (2 emerges)

[Run Full Recomputation]
```

### 9.4 API Endpoint

```
GET /api/taxonomy/stats
{
    "q_system": 0.847,
    "q_coherence": 0.812,
    "q_separation": 0.891,
    "q_coverage": 0.940,
    "q_dbcv": 0.783,
    "nodes": {
        "confirmed": 47,
        "candidate": 3,
        "retired": 12,
        "max_depth": 4,
        "leaf_count": 8
    },
    "last_warm_path": "2026-03-20T14:32:00Z",
    "last_cold_path": null,
    "q_history": [
        {"timestamp": "...", "q_system": 0.823, "operations": 2},
        ...
    ]
}
```

### 9.5 Monitoring & Alerting

Structured log entries:

```python
logger.info("taxonomy.warm_path.complete", extra={
    "q_system_before": 0.831,
    "q_system_after": 0.847,
    "operations_attempted": 4,
    "operations_committed": 2,
    "operations_rejected": 2,
    "reject_reasons": ["merge_coherence_drop", "retire_coverage_loss"],
    "duration_ms": 1234,
    "node_count": 50,
})
```

Auto-trigger cold-path when `Q_system < 0.4`.

---

## 10. Files Affected (Complete List)

### Backend — Deleted
- `backend/app/services/pattern_extractor.py`

### Backend — New
- `backend/app/services/taxonomy/__init__.py`
- `backend/app/services/taxonomy/engine.py`
- `backend/app/services/taxonomy/clustering.py`
- `backend/app/services/taxonomy/quality.py`
- `backend/app/services/taxonomy/lifecycle.py`
- `backend/app/services/taxonomy/projection.py`
- `backend/app/services/taxonomy/labeling.py`
- `backend/app/services/taxonomy/coloring.py`
- `backend/app/services/taxonomy/snapshot.py`
- `backend/app/routers/taxonomy.py`

### Backend — Modified
- `backend/app/schemas/pipeline_contracts.py` (delete `DomainType`, `domain` -> `str`)
- `backend/app/services/pipeline.py` (add domain mapping step)
- `backend/app/services/sampling_pipeline.py` (free-text domain, mapping)
- `backend/app/services/knowledge_graph.py` (trim domain logic, read from taxonomy)
- `backend/app/services/pattern_matcher.py` (taxonomy_node_id instead of domain string)
- `backend/app/routers/patterns.py` (taxonomy-based filtering, modified endpoints)
- `backend/app/models.py` (new TaxonomyNode/TaxonomySnapshot models, modify PatternFamily/Optimization)
- `backend/app/main.py` (TaxonomyEngine startup, event subscriptions)
- `prompts/analyze.md` (free-text domain instructions)

### Backend — Tests (New)
- `backend/tests/taxonomy/test_clustering.py`
- `backend/tests/taxonomy/test_quality.py`
- `backend/tests/taxonomy/test_lifecycle.py`
- `backend/tests/taxonomy/test_projection.py`
- `backend/tests/taxonomy/test_coloring.py`
- `backend/tests/taxonomy/test_labeling.py`
- `backend/tests/taxonomy/test_snapshot.py`
- `backend/tests/taxonomy/test_engine_hot_path.py`
- `backend/tests/taxonomy/test_engine_warm_path.py`
- `backend/tests/taxonomy/test_engine_cold_path.py`
- `backend/tests/taxonomy/test_domain_mapping.py`
- `backend/tests/taxonomy/test_cold_start.py`
- `backend/tests/taxonomy/test_emergence.py`
- `backend/tests/taxonomy/test_evolution.py`
- `backend/tests/taxonomy/test_edge_cases.py`
- `backend/tests/taxonomy/test_performance.py`
- `backend/tests/taxonomy/conftest.py`

### Backend — Tests (Modified/Deleted)
- `backend/tests/test_pattern_extractor.py` (deleted — replaced by taxonomy tests)
- `backend/tests/test_patterns_router.py` (modified — taxonomy-based assertions)
- `backend/tests/test_pipeline.py` (modified — domain mapping step)

### Frontend — Deleted
- `frontend/src/lib/constants/patterns.ts`
- `frontend/src/lib/components/patterns/RadialMindmap.svelte`
- `frontend/src/lib/components/patterns/utils/layout.ts`
- `frontend/src/lib/constants/patterns.test.ts`
- `frontend/src/lib/components/patterns/utils/layout.test.ts`
- `frontend/src/lib/components/patterns/RadialMindmap.test.ts`

### Frontend — New
- `frontend/src/lib/components/taxonomy/SemanticTopology.svelte`
- `frontend/src/lib/components/taxonomy/TopologyRenderer.ts`
- `frontend/src/lib/components/taxonomy/TopologyData.ts`
- `frontend/src/lib/components/taxonomy/TopologyInteraction.ts`
- `frontend/src/lib/components/taxonomy/TopologyLabels.ts`
- `frontend/src/lib/components/taxonomy/TopologyControls.svelte`
- `frontend/src/lib/components/taxonomy/TopologyWorker.ts`
- `frontend/src/lib/api/taxonomy.ts`
- `frontend/src/lib/utils/colors.ts`

### Frontend — Modified
- `frontend/src/lib/api/patterns.ts` (taxonomy node references)
- `frontend/src/lib/stores/patterns.svelte.ts` (taxonomy tree state, `taxonomy_changed` SSE)
- `frontend/src/lib/components/layout/PatternNavigator.svelte` (group by taxonomy node)
- `frontend/src/lib/components/layout/Inspector.svelte` (hierarchy breadcrumb, health panel)
- `frontend/src/lib/components/layout/Inspector.test.ts`
- `frontend/src/lib/components/layout/PatternNavigator.test.ts`
- `frontend/src/lib/components/layout/StatusBar` (if it exists — Q_system badge)
- `frontend/src/app.css` (any domain-specific styles)

### Frontend — Tests (New)
- `frontend/src/lib/components/taxonomy/SemanticTopology.test.ts`
- `frontend/src/lib/components/taxonomy/TopologyData.test.ts`
- `frontend/src/lib/components/taxonomy/TopologyWorker.test.ts`
- `frontend/src/lib/utils/colors.test.ts`

---

## 11. Dependencies

### Backend (new pip packages)
- `hdbscan` >= 0.8.38 (or `scikit-learn` >= 1.3 which includes HDBSCAN)
- `umap-learn` >= 0.5.5
- `scipy` >= 1.11 (required by HDBSCAN/UMAP, likely already present)

### Frontend (new npm packages)
- `three` >= 0.170
- `@types/three` (dev dependency)

---

## 12. Open Questions

1. **Warm-path interval tuning:** Default 10 optimizations. Should this adapt based on system maturity (longer intervals for mature taxonomies)?
2. ~~**UMAP stability across cold-path refits:**~~ **Resolved:** Procrustes alignment applied after every cold-path refit (see Section 7.5).
3. **Label regeneration frequency:** Haiku calls on every label change vs. batched regeneration on warm-path cycles.
4. **Memory footprint:** HDBSCAN on 10K+ embeddings may need chunked processing. Monitor and add streaming support if needed.

## 13. Review Findings — Addressed

Issues identified by spec review and resolved in this document:

| ID | Severity | Issue | Resolution |
|---|---|---|---|
| C1 | Critical | Non-regression invariant can deadlock | Epsilon tolerance + compound ops + deadlock breaker (Section 2.5) |
| C2 | Critical | Warm/hot path race condition | asyncio.Lock + centroid snapshot + stale reassignment (Section 2.6) |
| C3 | Critical | DBCV weight ramp creates Q_system discontinuity | Constant-sum weight normalization (Section 2.5) |
| I1 | Important | LTTB triangle area uses wrong x-coordinate | Fixed to use next-bucket midpoint (Section 9.1) |
| I2 | Important | OKLab gamut insufficient for 30+ clusters | Extended to +/-0.20, added deltaE enforcement (Section 7.6) |
| I3 | Important | Missing warm-path deduplication | _warm_path_running flag (Section 2.6) |
| I4 | Important | Retire threshold too aggressive for low-activity | Adaptive: max(20, 3*age_days), 7-day minimum age (Section 3.4) |
| I5 | Important | Cold-path UMAP refit destroys spatial model | Procrustes alignment (Section 7.5) |
| I6 | Important | PatternMatcher response schema unspecified | Explicit schema with breadcrumb (Section 6.8) |
| S1 | Suggestion | Performance "2000 nodes" misleading | Clarified: 2000 in data, 500 visible (Section 7.7) |
| S2 | Suggestion | Unbounded snapshot growth | Retention policy: 24h/daily/weekly pruning (Section 5.2) |
| S4 | Suggestion | MCP server integration unspecified | Hot-path-only engine instance (Section 6.7) |
| S5 | Suggestion | process_optimization() responsibilities unclear | Explicit 6-step list (Section 6.4) |
| S6 | Suggestion | Trend normalization clusters near 0 | Percentage-of-mean normalization (Section 9.1) |

**Second-pass findings (all addressed):**

| ID | Severity | Issue | Resolution |
|---|---|---|---|
| N4 | Important | MCP server centroid cache never refreshed | Explicit invalidation via taxonomy_changed SSE (Section 6.7) |
| N3 | Suggestion | Deadlock breaker scheduling mechanism unspecified | asyncio.create_task after lock release (Section 2.5) |
| N5 | Suggestion | _warm_path_running redundant with Lock.locked() | Eliminated separate boolean (Section 2.6) |
| I5+ | Suggestion | scipy.spatial.procrustes returns standardized matrices | Use scipy.linalg.orthogonal_procrustes for direct R matrix (Section 7.5) |
