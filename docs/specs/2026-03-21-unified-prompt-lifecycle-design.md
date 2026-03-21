# Unified Prompt Lifecycle — Design Spec

**Date:** 2026-03-21
**Status:** Draft
**Approach:** Evolutionary Convergence (Approach C)

## Problem

Project Synthesis has three partially-overlapping subsystems — taxonomy engine, pattern knowledge graph, and history navigation — that evolved independently. At 500+ prompts, critical gaps emerge:

1. **O(n) cosine search** in `match_prompt()` loads all families into memory
2. **Missing DB indices** on PatternFamily.taxonomy_node_id, MetaPattern.family_id, Optimization.created_at
3. **Identity mismatch** — frontend assumes taxonomy nodeId == familyId (structurally wrong)
4. **No auto-management** — no archival, dedup detection, quality pruning, or temporal decay
5. **No retroactive linking** — pre-pattern optimizations have null family_id
6. **Stale suggestions** — no dedup of already-applied patterns, no session context

The prompt library is a passive archive. It needs to become an active, self-managing system.

## Design Principles

1. **Unified entity** — merge PatternFamily + TaxonomyNode into `PromptCluster`
2. **High autonomy** — system acts silently, user observes and overrides
3. **Active library** — auto-injection of learned patterns + template spawning from mature clusters
4. **Scale target** — 500+ prompts, no performance degradation
5. **Evolutionary convergence** — evolve existing engine, don't rewrite

## Section 1: Unified Data Model — PromptCluster

A single entity that can be both a leaf (directly owns prompt members) and a branch (has child clusters). Replaces both `PatternFamily` and `TaxonomyNode`.

### Schema

```
PromptCluster
├── id: UUID (PK)
├── parent_id: UUID (FK self, nullable)
├── label: str
├── state: candidate | active | mature | template | archived
├── domain: str
├── task_type: str
├── centroid_embedding: bytes (384-dim, L2-normalized running mean)
├── member_count: int
├── usage_count: int
├── avg_score: float
├── coherence: float
├── separation: float
├── stability: float
├── persistence: float
├── umap_x/y/z: float
├── color_hex: str (OKLab-derived)
├── preferred_strategy: str (nullable)
├── prune_flag_count: int (default 0)
├── last_used_at: datetime (nullable)
├── promoted_at: datetime (nullable)
├── archived_at: datetime (nullable)
├── created_at: datetime
├── updated_at: datetime
```

### State Machine

```
candidate → active → mature → template
    ↓          ↓        ↓         ↓
  archived  archived  archived  archived
```

**Transitions:**
- **candidate → active**: warm path confirms cluster (Q_system non-regression passes)
- **active → mature**: coherence >= 0.75 AND member_count >= 5 AND avg_score >= 7.0
- **mature → template**: usage_count >= 3 AND avg_score >= 7.5 (sets promoted_at)
- **any → archived**: no new members for 90 days AND usage_count = 0 in last 30 days (sets archived_at)

### Indices

- `(parent_id)` — hierarchy traversal
- `(state)` — lifecycle queries
- `(domain, state)` — filtered browsing
- `(persistence)` — LOD visibility
- `(created_at DESC)` — recency ordering
- `meta_pattern(cluster_id)` — pattern lookup
- `optimization_pattern(cluster_id)` — member lookup

### Dropped Fields from TaxonomyNode

| TaxonomyNode field | Fate | Rationale |
|-------------------|------|-----------|
| `label_generated_at` | Dropped | Subsumed by `updated_at` on the cluster — label regeneration is an update. |
| `confirmed_at` | Mapped to `updated_at` | The candidate → active transition updates `updated_at`. No separate timestamp needed — `state` itself encodes the confirmation. |
| `retired_at` | Mapped to `archived_at` | The `retired` state maps to `archived`. |
| `observations` | Dropped | Redundant with `member_count`. The `observations` counter tracked the same accumulation. |

### State Mapping (existing → new)

| Old TaxonomyNode.state | New PromptCluster.state | Condition |
|------------------------|------------------------|-----------|
| `candidate` | `candidate` | Direct mapping |
| `confirmed` | `active` (default) | Standard mapping |
| `confirmed` | `mature` | If `member_count >= 5 AND coherence >= 0.75 AND avg_score >= 7.0` at migration time |
| `retired` | `archived` | Direct mapping, sets `archived_at = retired_at` |

Existing `PatternFamily` rows without a linked `TaxonomyNode` default to `state = 'active'`.

### Migration Strategy

**Pre-migration safety:** Before running, create a backup: `cp data/synthesis.db data/synthesis.db.pre-migration`. SQLite has limited transactional DDL — a backup ensures safe rollback.

**Note on SQLite ALTER TABLE limitations:** SQLite does not support `RENAME TABLE` reliably across all operations. The migration uses a create-copy-swap pattern: (1) create `prompt_cluster` with full schema, (2) copy data, (3) swap FK references, (4) drop old tables.

Single Alembic migration:

1. Create new `prompt_cluster` table with full schema (all PromptCluster columns)
2. Copy all `pattern_family` rows into `prompt_cluster`, setting `state = 'active'` as default
3. For each `PatternFamily` with `taxonomy_node_id IS NOT NULL`: copy matching `TaxonomyNode` fields (umap_x/y/z, color_hex, persistence, stability, coherence, separation, parent_id) into the corresponding `prompt_cluster` row. Apply state mapping (see table above).
4. Create `prompt_cluster` rows for hierarchy-only `TaxonomyNode` entries (nodes that have children but no linked PatternFamily — they become parent clusters with `member_count = 0`)
5. Drop `taxonomy_node_id` column from `prompt_cluster` (migration-only matching key, not in final schema)
6. Update FK references: `OptimizationPattern.family_id` → `cluster_id`, `MetaPattern.family_id` → `cluster_id`
7. Update `Optimization.taxonomy_node_id` FK: remap to `prompt_cluster.id` where a matching cluster exists, set to NULL otherwise. Rename column to `cluster_id`.
8. Preserve `TaxonomySnapshot` records as frozen audit trail — historical `tree_state` JSON retains old `taxonomy_node` IDs for provenance. No re-keying. Add `legacy: bool` column to distinguish pre-migration snapshots.
9. Drop `taxonomy_nodes` table. Drop `pattern_family` table.
10. Create all indices (see Indices section above)
11. Add `similarity: float` column to `optimization_pattern` (nullable, not backfilled — future writes populate it)

## Section 2: Embedding Index

Replaces O(n) full-table cosine scan with in-memory numpy matrix search.

### Why Numpy Over FAISS/hnswlib

At 500-2000 clusters with 384-dim embeddings:
- Matrix multiply is ~2-3ms (exact results)
- Embedding matrix fits in L2 cache (~3MB for 2000 clusters)
- Zero new dependencies
- FAISS only necessary at 10,000+ clusters — YAGNI until then

### EmbeddingIndex Service

Singleton, warm-loaded at startup alongside the taxonomy engine.

**Thread safety:** All mutating operations (`upsert`, `remove`, `rebuild`) are gated by `self._lock: asyncio.Lock`. Read-only `search()` operates on an immutable snapshot — mutations create a new matrix/ID-map pair and swap the reference atomically (copy-on-write pattern). This allows concurrent reads during writes without locking.

```python
class EmbeddingIndex:
    """In-memory embedding search index for PromptCluster centroids."""

    _lock: asyncio.Lock  # Gates all mutations
    _matrix: np.ndarray  # (n, 384) contiguous float32, immutable between swaps
    _ids: list[str]      # Parallel ID array, same length as matrix rows

    def warm_load(self, db: AsyncSession) -> None:
        """Load all non-archived cluster centroids into contiguous numpy matrix."""

    def search(self, embedding: np.ndarray, k: int = 5,
               threshold: float = 0.72) -> list[tuple[str, float]]:
        """Top-k cosine search. Returns [(cluster_id, score), ...].
        Lock-free — reads current snapshot."""

    async def upsert(self, cluster_id: str, embedding: np.ndarray) -> None:
        """Insert or update a single centroid. Acquires _lock, creates new snapshot."""

    async def remove(self, cluster_id: str) -> None:
        """Remove a centroid from the index. Acquires _lock, creates new snapshot."""

    async def rebuild(self, centroids: dict[str, np.ndarray]) -> None:
        """Full rebuild from scratch (cold path). Acquires _lock."""
```

### Integration Points

- **Hot path**: `index.search()` replaces full-table cosine scan in `_assign_family()`. `index.upsert()` on new/merged family.
- **Warm path**: `index.upsert()` after merge, `index.remove()` after retire. No full rebuild.
- **Cold path**: `index.rebuild()` after HDBSCAN refit.
- **Pattern match**: `index.search()` for both on-paste suggestion and auto-injection.

### Performance

| Scale | Current (full scan) | Proposed (index) |
|-------|-------------------|-----------------|
| 50 clusters | ~5ms | ~1ms |
| 500 clusters | ~50ms | ~2ms |
| 2000 clusters | ~200ms (full-table fetch + Python object overhead) | ~3ms |

## Section 3: Prompt Lifecycle Service

New `services/prompt_lifecycle.py` (~400 LOC). Composable service layered on top of existing engine paths.

### 3a. Auto-Curation (periodic, after warm path)

`lifecycle.curate()` runs sequentially after `engine.run_warm_path()` completes and commits, within the same timer callback but outside the warm path lock. This ensures warm path results are visible to curation queries.

- **Dedup detection**: scan all active cluster pairs. Pairs with centroid cosine >= 0.90 are auto-merged (smaller into larger). This catches clusters that diverged below the 0.78 hot-path threshold during centroid drift but have since converged.
- **Stale detection**: clusters where `updated_at < now - 90 days` AND `usage_count = 0` in last 30 days. Transition to `archived`. Excluded from search index and navigation by default.
- **Quality pruning**: clusters with `avg_score < 4.0` AND `member_count >= 3`. Tracked via `prune_flag_count: int` column on PromptCluster (default 0, added in P1 migration). Incremented each warm cycle when conditions are met, reset to 0 when conditions no longer hold. After `prune_flag_count >= 2`: auto-archive.
- **Orphan recovery**: optimizations with null cluster_id batch-processed — embed raw_prompt, search index, assign to best match if cosine >= 0.72. Runs once at startup, then incrementally.

### 3b. Auto-Evolution (event-driven, after hot path)

- **State promotion**: check transition conditions after hot path assigns a cluster (candidate → active → mature → template per state machine rules).
- **Strategy affinity tracking**: when optimization scores >= 7.0, record strategy per cluster. Accumulates `preferred_strategy` — the strategy that statistically produces best results for that domain/task_type.
- **Temporal decay**: runs once per warm cycle. For each cluster where `last_used_at < now - 30 days`, applies `usage_count = floor(usage_count * 0.9)` and then sets `last_used_at = now()` to prevent re-decay for another 30 days. Minimum usage_count after decay: 0. This prevents ancient high-usage clusters from dominating suggestions while ensuring decay is applied at most once per 30-day window per cluster.

### 3c. Template Spawning (user-facing)

When a cluster reaches `template` state:
- Surfaces in "Proven Templates" section of ClusterNavigator, sorted by avg_score
- "Use template" pre-fills prompt editor with cluster's highest-scoring member's `optimized_prompt` + auto-selects preferred_strategy
- Templates carry meta-patterns, auto-injected into optimizer context when spawned

### Integration (additive, no modification to existing paths)

```
optimization_created event
  → [existing] engine.process_optimization()       (hot path)
  → [new]      lifecycle.post_process()             (state promotion, affinity)

warm path timer (300s)
  → [existing] engine.run_warm_path()
  → [new]      lifecycle.curate()                   (dedup, stale, quality, orphan)

startup
  → [existing] engine singleton init
  → [new]      lifecycle.backfill_orphans()          (one-time retroactive linking)
  → [new]      embedding_index.warm_load()           (build search matrix)
```

## Section 4: Frontend Navigation Redesign

### Unified Model

Every surface operates on `PromptCluster`. One ID space, one `selectCluster(id)` call, no identity mismatch.

**Three-level drill-down:**
1. **Domain groups** (topology far view) — backend, frontend, database, etc.
2. **Clusters** (topology mid view, ClusterNavigator, Inspector) — labeled groups with state badges
3. **Member prompts** (topology near view, History) — individual optimizations within a cluster

### Component Changes

**PatternNavigator → ClusterNavigator:**
- State filter tabs: active | mature | template | archived (0px border-radius, sharp)
- "Proven Templates" section at top (state=template clusters, sorted by avg_score)
- "Use Template" button: `.btn-outline-primary`, 20px height, text-[10px], hover Recipe B
- Domain filter and search preserved

**Inspector updates:**
- State badge per cluster (2px rounded-sm, chromatic state colors)
- Preferred strategy display
- Child cluster expansion for hierarchy nodes
- Manual "Promote to template" and "Unarchive" overrides
- Rename/domain edit preserved, linked optimizations become "members"

**SemanticTopology updates (Three.js):**
- State encoding via opacity + size + color (NOT geometry shapes):
  - candidate: OKLab color @ 40% opacity
  - active: OKLab color @ 100%
  - mature: OKLab color @ 100% + 1.2x size multiplier
  - template: neon-cyan (#00e5ff) override + 1.5x size + persistent label
  - archived: hidden by default (filterable via controls)
- Click always resolves to `selectCluster()` — no identity mismatch
- LOD tiers, force layout, OKLab coloring preserved

**Store: patterns.svelte.ts → clusters.svelte.ts:**
- `selectedClusterId` replaces `selectedFamilyId`
- `clusterDetail` replaces `familyDetail`
- New `templates: PromptCluster[]` (state=template subset)
- New `spawnTemplate(clusterId)` method → pre-fill editor
- taxonomyTree/Stats, paste detection, SSE invalidation preserved

**Bug fix: topology tooltip** uses non-brand CSS tokens (`--color-surface`, `--color-contour`). Corrected to `--color-bg-card`, `--color-border-subtle`, `--color-text-secondary`. Padding 8px → 6px.

### State Color Mapping

| State | Color | Token | Semantic |
|-------|-------|-------|----------|
| candidate | #7a7a9e | text-dim | Unconfirmed |
| active | #4d8eff | neon-blue | Information |
| mature | #a855f7 | neon-purple | Elevated |
| template | #00e5ff | neon-cyan | Primary identity |
| archived | #2a2a3e | border-subtle | Suppressed |

### Auto-Injection Flow

1. User submits prompt
2. Pipeline pre-phase: `embeddingIndex.search(prompt, k=3)` finds relevant clusters
3. Meta-patterns + preferred strategy hint injected into optimizer context
4. Pipeline runs with enriched context
5. SSE event: `context: API error handling · 3 patterns injected` (instrument-panel voice, mono font)

No paste detection needed for auto-injection — happens automatically. Paste detection preserved as preview mechanism before submission.

### Brand Compliance

All frontend changes verified against brand guidelines:
- Zero-effects directive: no glow, shadow, bloom in any component
- Ultra-compact density: 20px buttons, 10px text, 6px max sidebar padding
- Chromatic encoding: color is data (state colors, domain colors, score colors)
- Flat geometry: 0px default radius, 2px for status chips, 9999px for pill tokens
- Neon tube model: 1px borders, uniform width, no gradients within borders
- Voice/tone: technical, instrument-panel, subject + metric

## Section 5: Engine Refactor

### Module Decomposition

Current `engine.py` (2,098 LOC) decomposed into 4 focused modules:

```
services/taxonomy/
├── __init__.py          (84 LOC)   — singleton, public API (unchanged)
├── engine.py            (~500 LOC) — orchestrator: entry points only
├── family_ops.py        (~400 LOC) — _assign_family, _merge_meta_pattern, _extract_meta_patterns
├── matching.py          (~350 LOC) — match_prompt, domain_mapping, cascade search
├── embedding_index.py   (~200 LOC) — numpy search index
├── clustering.py        (294 LOC)  — unchanged
├── lifecycle.py         (418 LOC)  — unchanged
├── quality.py           (186 LOC)  — unchanged
├── projection.py        (165 LOC)  — unchanged
├── coloring.py          (214 LOC)  — unchanged
├── labeling.py          (66 LOC)   — unchanged
├── snapshot.py          (224 LOC)  — unchanged
└── sparkline.py         (161 LOC)  — unchanged
```

New top-level service:
```
services/prompt_lifecycle.py  (~400 LOC)
```

### What Moves Where

- **engine.py** keeps: `process_optimization()`, `run_warm_path()`, `run_cold_path()` as entry points that delegate to extracted modules
- **family_ops.py** gets: family assignment (cosine search → merge/create), meta-pattern extraction (Haiku structured output), meta-pattern merging (dedup at 0.82)
- **matching.py** gets: `match_prompt()` (2-level cascade), `map_domain()`, adaptive threshold computation. Uses `embedding_index` for search.
- **embedding_index.py**: new module, numpy-based search index

### API Changes

```
# Renamed endpoints (old paths redirect 301 for backward compat)
# Note: no SSE streaming endpoints exist on taxonomy.py or patterns.py — all REST.
# 301 redirects are safe for all affected paths.
GET  /api/clusters/tree          (was /api/taxonomy/tree)
GET  /api/clusters/{id}          (was /api/taxonomy/node/{id} + /api/patterns/families/{id})
GET  /api/clusters/stats         (was /api/taxonomy/stats)
POST /api/clusters/recluster     (was /api/taxonomy/recluster)
GET  /api/clusters/templates     (NEW — state=template, sorted by avg_score)
PATCH /api/clusters/{id}         (was /api/patterns/families/{id})
POST  /api/clusters/match        (was /api/patterns/match)
GET   /api/clusters/search       (was /api/patterns/search)

# Pipeline addition
POST /api/optimize               (unchanged URL, new SSE event: context_injected)
```

Merged into single router: `clusters.py` (replaces `taxonomy.py` + `patterns.py`).

**Redirect deprecation:** 301 redirects from old paths (`/api/taxonomy/*`, `/api/patterns/families/*`) removed in a follow-up minor version after confirming no external consumers. Monitor redirect hit counts via access logs for 2 release cycles before removal.

## Phased Delivery

| Phase | Scope | Risk | Prerequisite |
|-------|-------|------|-------------|
| **P1** | DB migration + PromptCluster model + indices + API rename | Medium | None |
| **P2** | engine.py decomposition + embedding_index.py | Low | P1 |
| **P3** | prompt_lifecycle.py (auto-curation + state promotion) | Low | P2 |
| **P4** | Frontend: clusters.svelte.ts + ClusterNavigator + Inspector state badges | Medium | P1 |
| **P5** | Auto-injection pipeline pre-phase + template spawning | Low | P2, P4 |
| **P6** | Orphan backfill + temporal decay + strategy affinity | Low | P2 |

Each phase is independently shippable. P1-P2 are prerequisites. P3-P6 can proceed in any order after P2. P4 can run in parallel with P2 (frontend only depends on P1 model changes).

## Acceptance Criteria

### P1: Data Migration + Model + Indices + API Rename

- `SELECT count(*) FROM prompt_cluster` equals the sum of existing PatternFamily rows + hierarchy-only TaxonomyNode rows
- All existing `GET /api/taxonomy/tree`, `GET /api/patterns/families` endpoints return 200 via 301 redirect
- New `GET /api/clusters/tree` returns the same data shape as the old taxonomy tree endpoint
- All prompt_cluster rows have a valid `state` value (no nulls, no unmapped states)
- Indices verified via `EXPLAIN QUERY PLAN` on critical queries (cluster_id lookups, state filters, parent_id traversal)
- `data/synthesis.db.pre-migration` backup exists

### P2: Engine Decomposition + Embedding Index

- After startup, `EmbeddingIndex` contains N centroids matching `SELECT count(*) FROM prompt_cluster WHERE state NOT IN ('archived')`
- `match_prompt()` returns equivalent results to pre-refactor (same top match for a test set of 10 prompts)
- `match_prompt()` latency < 5ms for 500 clusters (measured via trace log)
- `engine.py` is < 600 LOC. `family_ops.py`, `matching.py` each < 500 LOC
- All existing tests pass without modification (behavior preserved)

### P3: Prompt Lifecycle Service

- A cluster with `member_count >= 5, coherence >= 0.75, avg_score >= 7.0` is promoted to `mature` within one warm path cycle
- A cluster with `usage_count >= 3, avg_score >= 7.5` in `mature` state is promoted to `template` within one warm path cycle
- A cluster with no new members for 90 days and usage_count = 0 in last 30 days transitions to `archived`
- Archived clusters are excluded from `EmbeddingIndex.search()` results
- Dedup detection merges cluster pairs with cosine >= 0.90

### P4: Frontend — ClusterNavigator + Inspector + Topology

- ClusterNavigator shows state filter tabs (active/mature/template/archived)
- Clicking a state tab filters the displayed clusters
- Selecting a cluster in any surface (Navigator, ClusterNavigator, Topology) populates Inspector with state badge and cluster detail
- State badges use correct chromatic mapping (candidate=text-dim, active=neon-blue, mature=neon-purple, template=neon-cyan, archived=border-subtle)
- Topology tooltip uses brand tokens (--color-bg-card, --color-border-subtle, --color-text-secondary, padding 4px 6px)
- No glow, shadow, or bloom effects introduced in any component

### P5: Auto-Injection + Template Spawning

- Submitting a prompt auto-injects meta-patterns from top-3 matching clusters (verified via SSE `context_injected` event)
- SSE stream includes context notification in instrument-panel voice format
- "Use template" button in ClusterNavigator pre-fills editor with highest-scoring member's optimized_prompt
- Template spawning auto-selects the cluster's preferred_strategy

### P6: Orphan Backfill + Temporal Decay + Strategy Affinity

- After startup backfill, `SELECT count(*) FROM optimization WHERE cluster_id IS NULL` decreases (optimizations matched to clusters)
- Optimizations with truly no match remain null (cosine < 0.72 to all clusters)
- After 30 days with no activity, cluster usage_count is reduced by 10%
- `preferred_strategy` is set on clusters after 3+ optimizations with score >= 7.0 use the same strategy

## Out of Scope

- Multi-user support / auth-gated clusters
- Export/import of prompt libraries
- Cross-instance cluster federation
- FAISS/hnswlib (deferred until 10,000+ clusters)
- Real-time collaborative editing of clusters
