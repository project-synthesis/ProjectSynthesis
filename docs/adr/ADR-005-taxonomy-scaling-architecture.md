# ADR-005: Taxonomy Scaling Architecture — Project Partitions, Adaptive Warm Path, and Cross-Project Pattern Sharing

**Status:** Amended 2026-04-19 — hybrid taxonomy supersedes the "project as tree parent" data model. Original design body preserved below for historical context; see `## Amendment 2026-04-19 — Hybrid Taxonomy` at the end.
**Date:** 2026-04-08 (original) · Amended 2026-04-19
**Authors:** Human + Claude Opus 4.6 (original) · Human + Claude Opus 4.7 (amendment)

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

**Cross-project assignment (fallback):** If no in-project match, search globally with a boosted threshold: `adaptive_merge_threshold() + 0.15` (~0.70 for singletons, ~0.74 for larger clusters). If a strong cross-project match passes this boosted gate, assign directly. This accelerates singleton absorption — a prompt in Project B can join a semantically identical cluster in Project A. The cluster naturally becomes multi-project.

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

### 6. Global Pattern Tier — Durable Cross-Project Techniques

**New model: `GlobalPattern`** — independent table, survives cluster archival.

| Field | Type | Purpose |
|-------|------|---------|
| id | UUID | PK |
| pattern_text | str | The technique description |
| embedding | bytes | 384-dim for injection search |
| source_cluster_ids | JSON list[str] | Contributing clusters |
| source_project_ids | JSON list[str] | Contributing projects |
| cross_project_count | int | Distinct projects |
| global_source_count | int | Distinct clusters |
| avg_cluster_score | float | Mean avg_score of source clusters |
| promoted_at | datetime | Graduation timestamp |
| last_validated_at | datetime | Last warm-path validation |
| state | str | `active` / `demoted` / `retired` |

**Promotion criteria** (warm path Phase 4):
- MetaPattern's pairwise matches span >= 2 distinct projects
- `global_source_count >= 5`
- Source clusters' `avg_score >= 6.0`
- Deduplicated: cosine >= 0.90 against existing GlobalPatterns updates rather than creates

**Injection:** Cross-cluster injection query searches BOTH MetaPattern (cluster-level) AND GlobalPattern (global tier). GlobalPatterns with `state='active'` get 1.3x relevance multiplier.

**Validation** (every 10th warm cycle, min 30-min wall-clock gate):
- Check source clusters still active with decent scores
- `avg_cluster_score < 5.0` → demoted (multiplier removed, pattern kept)
- Re-promote at `avg_cluster_score >= 6.0` (1.0-point hysteresis gap prevents oscillation)
- All source clusters archived AND >30 days since last validation → retired

**Retention policy:**
- Hard cap: 500 GlobalPatterns with `state IN ('active', 'demoted')`
- Eviction order when cap hit: (1) demoted LRU by `last_validated_at`, (2) active LRU
- Retired patterns excluded from cap count, kept for audit trail

## Implementation Phases

### Phase 1: Foundation (dirty tracking + adaptive measurement)
- `EXCLUDED_STRUCTURAL_STATES` constant replacing 37+ inline state lists
- `state='project'` code convention (no DDL — state is String(20))
- "Legacy" project node migration with rollback support
- `project_id` on Optimization (denormalized) + backfill
- Dirty-set tracking on TaxonomyEngine (with restart full-scan)
- Warm path: dirty-only processing with phase-specific scoping
- Adaptive scheduler: rolling window + p75 target (all-dirty mode only)
- Embedding index: project_filter parameter + _project_ids array

### Phase 2: Multi-Project + Global Tier
- Project creation on GitHub repo link
- Hot-path project-scoped search + cross-project assignment (boosted threshold)
- Per-project Q metrics via _load_active_nodes(project_id)
- GlobalPattern model + promotion + injection + validation + retention
- Topology UI: project filter dropdown

### Phase 3: Performance (driven by /api/monitoring data)
- Round-robin warm scheduling branch (when cycle time > target)
- HNSW embedding index (when search > 50ms)
- PostgreSQL migration (when SQLite contention > 30s p95)

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
- Cross-project assignment (adaptive threshold + 0.15 boost) means clusters can contain optimizations from multiple projects — the topology view needs to handle this
- The adaptive scheduler needs 10 cycles of data before self-tuning activates

### Risks
- SQLite may not handle seed batches of 100+ prompts without contention. Mitigation: monitoring + PostgreSQL escape hatch.
- Cross-project assignment boost (+0.15 over adaptive threshold) may be too aggressive, merging unrelated prompts from different project contexts. Mitigation: the boost is a constant that can be tuned, and the merge is reversible (split path).
- The "Legacy" project node is a migration artifact that may confuse users. Mitigation: rename to the first linked repo when one is connected.

## References

- Taxonomy architecture: `backend/CLAUDE.md` (Taxonomy Engine section)
- Current constants: `backend/app/services/taxonomy/_constants.py`
- Warm path implementation: `backend/app/services/taxonomy/warm_phases.py`
- Hot path: `backend/app/services/taxonomy/engine.py` (`process_optimization`)
- Embedding index: `backend/app/services/taxonomy/embedding_index.py`
- Quality metrics: `backend/app/services/taxonomy/quality.py`
- UMAP projection (separated from cold path): `backend/app/services/taxonomy/cold_path.py` (`execute_umap_projection`)

---

## Amendment 2026-04-19 — Hybrid Taxonomy

### Why the tree-parent model was revisited

The original ADR placed projects as tree parents (`project → domain → cluster`). Six months of operation exposed four structural problems:

1. **Duplicate filtering surfaces.** Every endpoint that surfaced clusters had to walk the parent chain to filter by project. `/api/clusters/tree?project_id=X` was a dead code path: it returned only the project node and its bare child clusters, because clusters parent under *domains*, not projects. Stats, topology, similarity-edges all had the same dead-path bug.
2. **Mega-Legacy domain split.** With every existing optimization re-parented under `Legacy`, the Legacy project's `general` domain ballooned. Warm-path merge/split decisions had to repeatedly traverse project→domain→cluster ancestry, which HDBSCAN/spectral were never designed to respect.
3. **Frontend couldn't compose views.** "Show me project X's patterns" and "show me project X's domains" needed two different query shapes. The cluster store and pattern store had no shared filter primitive — projects were a tree axis but patterns were not.
4. **Cross-project learning is the product.** Taxonomy should be global; *view* is per-project. The tree-parent model inverted this.

### New data model

- **Projects live at `parent_id = NULL`** with `state = "project"`. They are sibling roots.
- **Domains also live at `parent_id = NULL`** with `state = "domain"` (unchanged from ADR-004).
- **Clusters parent to domains** via `parent_id`. They do *not* parent to projects.
- **`PromptCluster.dominant_project_id`** — new nullable FK column (S1 migration). Populated by warm Phase 0 and cold path from majority-project-by-member count. Ties resolve non-Legacy preferred. Index-backed view filtering: `WHERE dominant_project_id = X OR state IN ('project','domain') OR id = X`.
- **`Optimization.project_id`** remains the authoritative per-prompt attribution (denormalized FK). B1 freezes it at pipeline entry, eliminating the persist-time race.

### Contract changes shipped (B1–B8, C1a–C1c)

- **B1** — Explicit `project_id` in all optimize/refine/sampling/batch pipelines. Resolution order: explicit request → `resolve_project_id(repo_full_name)` → cached `legacy_project_id`. Legacy cache primed in both backend and MCP lifespans.
- **B2** — `migrate_optimizations()` service (bulk re-attribution with `since` / `repo_full_name_is_null` filters, emits `optimizations_migrated` event).
- **B3** — `POST /api/projects/migrate` rate-limited router.
- **B4** — `link_repo` response now carries `migration_candidates: {count, from_project_id, since}` for UI toast. No auto-migration.
- **B5** — `unlink_repo` accepts `?mode=keep|rehome`. "keep" preserves attribution; "rehome" migrates recent opts back to Legacy. Emits `repo_unlinked` event.
- **B6** — `/api/clusters/tree`, `/api/clusters/stats`, `/api/clusters/similarity-edges` accept `?project_id=X` + `?scope=all`. Hybrid filter clause ensures structural skeleton always visible. `engine._stats_cache` keyed per-project.
- **B7** — `auto_inject_patterns(db, query_embedding, project_id=None)` threads `project_filter` into `embedding_index.search()`. GlobalPatterns unchanged (cross-project is the point). `enable_cross_project_injection` preference flag (default false).
- **B8** — `GlobalPattern` promotion tightened with `distinct_project_count >= GLOBAL_PATTERN_MIN_PROJECTS` gate. Legacy-only patterns can no longer reach the Global tier.
- **C1a** — Warm Phase 0 recomputes `dominant_project_id` alongside `member_count`.
- **C1b** — Cold path writes `dominant_project_id` on each cluster after the members sweep.
- **C1c** — `_reassign_to_active()` accepts `preferred_project_id` kwarg and prefers same-project targets with per-opt default; cross-project wins only when cosine margin ≥ `CROSS_PROJECT_REASSIGN_MARGIN` (0.10) over the best same-project option. Mixed-cluster dissolution no longer silently leaks members into Legacy.

### Frontend delta (F1–F5)

- **F1** — `frontend/src/lib/stores/project.svelte.ts` (Svelte 5 rune store). `currentProjectId` persisted to `localStorage`; survives unlink and no-GitHub sessions. `eligibleForLegacyMigration()` returns the count from the last `linkRepo` response.
- **F2** — Project selector dropdown in Navigator header with live prompt/cluster counts. Hidden when only Legacy exists.
- **F3** — All `optimize`/`refine`/`match` calls carry `project_id` from the store. Explicit "Legacy" selection overrides repo-link inference, enabling Legacy-only prompts while a repo is linked.
- **F4** — Tree, topology, and pattern match consumers re-fetch on store change via `$effect`. "All projects" omits the param.
- **F5** — Transition toasts: link offers Legacy migration (Move/Keep via `addWithActions`); unlink offers Stay/Switch; Inspector renders per-project `member_counts_by_project` breakdown; empty-state panel on scoped empty views.

### Locked-decision record

The full discovery + design exchange that led to this amendment is captured in `docs/hybrid-taxonomy-plan.md` (locked 2026-04-19). It carries the complete problem matrix (pipeline / repo-link / repo-unlink / repo-switch / tree filter / pattern injection / cross-project merges / UI state model), the intermittent-prompting handling matrix, and the verification plan.

### Evidence of shipment

- **Commit range on `main`:** `ab07fd30 … c1ab12f7` (8 sequential commits — S1 schema, B1, B2–B5, B6, B7–B8, C1a–C1c, F1–F5, ops utilities)
- **Schema migration:** `backend/alembic/versions/d9e0f1a2b3c4_add_dominant_project_id_to_prompt_cluster.py`
- **Key files:**
  - `backend/app/services/project_service.py` — `ensure_project_for_repo()`, `resolve_project_id()`, `migrate_optimizations()`, Legacy cache primitives
  - `backend/app/routers/projects.py` — `GET /api/projects`, `POST /api/projects/migrate`
  - `backend/app/routers/clusters.py` — B6 hybrid scope filter on tree/stats/similarity-edges
  - `backend/app/routers/github_repos.py` — B4/B5 link/unlink contract
  - `backend/app/services/taxonomy/engine.py` — `_stats_scope_clause()`, per-project `_stats_cache` keying
  - `backend/app/services/taxonomy/warm_phases.py` — C1a Phase 0 `dominant_project_id` reconciliation, C1c same-project preference in `_reassign_to_active()`
  - `backend/app/services/taxonomy/cold_path.py` — C1b write-through of `dominant_project_id`
  - `backend/app/services/pattern_injection.py` — B7 project-scoped injection
  - `backend/app/services/taxonomy/global_patterns.py` — B8 distinct-project gate
  - `frontend/src/lib/stores/project.svelte.ts` + `project.test.ts`
  - `frontend/src/lib/components/layout/Navigator.svelte` — F2 dropdown + F5 toasts
  - `frontend/src/lib/stores/toast.svelte.ts` — `addWithActions()` primitive
- **New tests:** `backend/tests/test_link_repo_b4.py`, `backend/tests/test_unlink_repo_b5.py`, `backend/tests/test_projects_router.py`, `backend/tests/test_pattern_injection_project_scope.py`, `backend/tests/test_patterns_router.py`, `backend/tests/taxonomy/test_dissolve_same_project_pref.py`, plus extensions to `test_project_migration.py`, `test_engine_read_api.py` (per-scope cache isolation), `test_global_pattern_promotion.py`, `test_global_pattern_validation.py`.
- **Ops:** `scripts/taxonomy-reset.sh` — fresh-start utility preserving optimizations/feedback/linked-repos while wiping derived taxonomy graph.

### Still deferred from the 2026-04-08 design

- **Phase 3 HNSW embedding index** — code path present (`_HnswBackend` in `backend/app/services/taxonomy/embedding_index.py`, activated at `HNSW_CLUSTER_THRESHOLD=1000`), currently dormant with numpy backend primary. Trigger condition (≥ 1000 clusters for multiple warm cycles) has not been reached.
- **Phase 3 round-robin warm scheduling** — the adaptive scheduler (linear regression boundary, all-dirty vs per-project budget modes, starvation guard) shipped as part of B-layer work; the deferred piece is large-DB stress validation, which awaits real 5K+ prompt corpora.
