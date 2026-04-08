# ADR-005: Taxonomy Scaling Architecture — Project Partitions, Adaptive Warm Path, and Cross-Project Pattern Sharing

**Status:** Accepted (design phase)
**Date:** 2026-04-08
**Authors:** Human + Claude Opus 4.6

## Context

### Problem

Project Synthesis's taxonomy engine manages all prompt optimizations through an evolutionary clustering system (hot/warm/cold paths). At 161 optimizations, the taxonomy has 67 clusters — **45 of which are singletons** (1 member each, 67% of all clusters). Key findings:

- **Zero mergeable singleton pairs**: No two singletons have cosine similarity above the merge threshold (0.59). The warm path's merge phase will never consolidate them.
- **Singletons are valuable long-tail**: Each represents a unique prompt type. They're not noise — they're specialization waiting for recurrence.
- **Current scale is toy**: 14.6 prompts/day. A true vibe coder using the tool across multiple projects will generate 100-500 prompts/day (3K-15K/month).
- **No manual management**: The taxonomy IS the management layer — no delete, no folders, no tags. Every optimization lives in the taxonomy forever.
- **The singleton problem solves itself at scale**: At 5K+ prompts, the same semantic regions recur across projects, naturally growing singletons into multi-member clusters.

### Real Scaling Concerns (at 5K-50K prompts)

1. **Warm path wall-clock time**: Currently scans ALL active clusters every cycle. At 500 clusters, warm cycles could take minutes.
2. **SQLite single-writer contention**: Hot-path writes (per-optimization) and warm-path writes (burst during flush) contend. Seed batches (30 concurrent pipelines) amplify this.
3. **Embedding index search**: O(N) brute-force numpy. At 5,000 clusters, ~10ms per search — still fast, but needs a path to O(log N).
4. **Multi-project isolation**: One project's singletons shouldn't dilute another project's pattern injection quality.

### Decision Drivers

- The app is designed for vibe coders running 100-500 prompts/day across multiple projects
- Cross-project pattern transfer is a core value proposition (patterns learned in Project A help Project B)
- The system must be self-tuning — no manual configuration knobs for scaling behavior
- Backward compatible with existing single-project taxonomy (161 optimizations, 67 clusters)

## Decision

### 1. Data Model: Project as Tree Parent

**Choice:** Project is a `PromptCluster` node with `state='project'` in the existing tree hierarchy.

**Hierarchy:** project → domain → cluster → (optimizations via cluster_id)

**Rejected alternative:** Flat `project_id` column. Would create two parallel filtering concepts (parent_id for hierarchy, project_id for partition) and require adding `WHERE project_id = ?` to every query. The tree parent approach keeps one unified hierarchy where breadcrumb, topology, and inspector work naturally.

**Migration:** Create a "Legacy" project node (`state='project'`). Re-parent all existing domain nodes under it. All existing clusters inherit the Legacy project through their domain parent. Optimizations get `project_id` via their cluster's project ancestry. One-time, reversible.

**Schema addition to PromptCluster:**
- `state='project'` added to the state enum
- No new columns — the existing `parent_id` tree handles the hierarchy

**New fields on Optimization:**
- `project_id: str | null` — denormalized for fast filtering. Set from the linked repo at optimization time.

### 2. Hot Path: Project-Scoped Assignment with Cross-Project Merge

**In-project search (primary):** Embedding index searches only the active project's partition first. O(partition_size) instead of O(total).

**Cross-project assignment (fallback):** If no in-project match but a strong cross-project match exists (cosine > 0.7), assign to the cross-project cluster directly. This accelerates singleton absorption — a prompt in Project B can join a semantically identical cluster in Project A. The cluster naturally becomes multi-project.

**New cluster creation:** When no match is found anywhere, create a new singleton under the active project's domain subtree.

**Embedding index:** Add `project_id` tag to each vector. `search()` gains an optional `project_filter` parameter. In-project search filters before cosine; cross-project search removes the filter.

### 3. Warm Path: Fully Adaptive Scheduler

**No static mode selection.** The warm path self-tunes based on measured performance:

**Signals (per cycle):**
- `dirty_count`: Number of clusters marked dirty by hot path since last cycle
- `last_cycle_duration_ms`: Wall-clock time of previous cycle

**Rolling window:** Last 10 cycles' measurements.

**Derived thresholds (self-tuning):**
- `target_cycle_time`: p75 of recent cycle durations (the "comfortable" operating point)
- `dirty_count_boundary`: Derived via linear regression of (dirty_count, duration) pairs — the dirty count at which estimated duration would exceed the target

**Mode decision (every cycle):**
- `dirty_count ≤ boundary` → **All-dirty mode**: Process all dirty clusters across all projects
- `dirty_count > boundary` → **Round-robin mode**: Process only the highest-priority project (most dirty clusters). Others wait for next cycle.

**Bootstrap (first 10 cycles):** Static fallbacks (boundary=20, target=10s) until enough data is collected.

**Dirty tracking:** Hot path marks clusters as dirty when members are added/removed. `pattern_stale=True` (already exists) serves as the dirty flag for pattern extraction. New `_dirty_set: set[str]` on the engine for merge/split/retire scope.

**Per-project Q metrics:** Quality metrics computed per-project partition. A bad merge in Project A doesn't block Project B's warm cycle.

### 4. SQLite Contention: No Architectural Change Needed

**Current WAL mode + busy_timeout=30000 handles 50K scale.** Analysis:
- Hot path at 200/hour (vibe coder pace) = one write every 18 seconds. WAL handles this.
- Seed batches use `bulk_persist()` (one transaction) + `batch_taxonomy_assign()` (sequential). The busy_timeout absorbs contention.
- Warm path's speculative transaction (rollback on Q regression) is the expensive case but only runs during split/merge, not every cycle.

**Monitoring gate:** If the `/api/monitoring` endpoint's LLM latency p95 exceeds 30s consistently, evaluate PostgreSQL migration. Data-driven, not premature.

### 5. Embedding Index: Numpy Now, HNSW Later

**Phase 1:** Keep numpy brute-force with project-filter parameter. At 500 clusters, search is ~1ms.

**Phase 2 trigger:** When `/api/monitoring` shows embedding search exceeding 50ms (estimated at ~3,000 clusters). Swap to hnswlib via the existing `search()` abstraction — single-file change.

**Project scoping:** Vectors tagged with project_id. `search(embedding, k, threshold, project_filter=None)` filters before computation.

### 6. Global Pattern Promotion (Cross-Project Learning)

**Criteria:** A MetaPattern is promoted to global tier when:
- `global_source_count >= 5` (present in 5+ distinct clusters)
- Those clusters span `>= 2` distinct projects
- The pattern's embedding has cosine similarity > 0.82 with patterns in other projects (already computed by the `global_source_count` pipeline)

**Frequency:** Computed every warm cycle as part of Phase 4 (refresh). The existing `global_source_count` computation already joins across all clusters — extending it with project-count is one additional GROUP BY.

**Injection:** Global patterns are injected into all projects' optimizations. The existing cross-cluster injection pipeline (pattern_injection.py) already searches patterns with high `global_source_count`. No change needed — the injection is already cross-project by design.

**Observability:** New event `op="promote", decision="global_pattern"` logged by TaxonomyEventLogger.

## Implementation Phases

### Phase 1: Foundation (can be built now)
- Add `state='project'` to PromptCluster state enum
- Create "Legacy" project node, migrate existing domains
- Add `project_id` to Optimization (denormalized)
- Dirty-set tracking on TaxonomyEngine
- Warm path: dirty-only processing (skip unchanged clusters)
- Embedding index: project_filter parameter
- Adaptive warm path scheduler with rolling window

### Phase 2: Multi-Project (when second project is linked)
- Project creation on GitHub repo link
- Hot-path project-scoped search + cross-project assignment
- Per-project Q metrics and warm path scoping
- Global pattern promotion with cross-project counting
- Topology UI: project filter dropdown

### Phase 3: Performance (driven by monitoring data)
- HNSW embedding index (if search > 50ms)
- PostgreSQL migration (if SQLite contention > 30s p95)
- Round-robin warm path scheduling (if cycle time > 30s consistently)

## Consequences

### Positive
- Taxonomy scales to 50K+ prompts without configuration
- Cross-project pattern sharing preserves the core value proposition
- Self-tuning scheduler adapts to any hardware/workload combination
- Backward compatible — existing single-project users see no change
- Each phase is independently valuable and deployable

### Negative
- Tree parent migration adds complexity to the existing tree traversal code
- Every tree query that filters by state needs `state='project'` exclusion
- Cross-project assignment (cosine > 0.7) means clusters can contain optimizations from multiple projects — the topology view needs to handle this
- The adaptive scheduler needs 10 cycles of data before self-tuning activates

### Risks
- SQLite may not handle seed batches of 100+ prompts without contention. Mitigation: monitoring + PostgreSQL escape hatch.
- Cross-project assignment at 0.7 threshold may be too aggressive, merging unrelated prompts from different project contexts. Mitigation: the threshold is a constant that can be tuned, and the merge is reversible (split path).
- The "Legacy" project node is a migration artifact that may confuse users. Mitigation: rename to the first linked repo when one is connected.

## References

- Taxonomy architecture: `backend/CLAUDE.md` (Taxonomy Engine section)
- Current constants: `backend/app/services/taxonomy/_constants.py`
- Warm path implementation: `backend/app/services/taxonomy/warm_phases.py`
- Hot path: `backend/app/services/taxonomy/engine.py` (`process_optimization`)
- Embedding index: `backend/app/services/taxonomy/embedding_index.py`
- Quality metrics: `backend/app/services/taxonomy/quality.py`
- UMAP projection (separated from cold path): `backend/app/services/taxonomy/cold_path.py` (`execute_umap_projection`)
