# Phase 2A Design Spec: Multi-Project Isolation

**Date:** 2026-04-08
**ADR:** [ADR-005](../adr/ADR-005-taxonomy-scaling-architecture.md) (Phase 2, items 1-3, 5)
**Depends on:** Phase 1 (complete) — EXCLUDED_STRUCTURAL_STATES, Legacy project node, project_id on Optimization, dirty-set, EmbeddingIndex project_filter
**Status:** Superseded — the original "project as tree parent" model was revised to the Hybrid Taxonomy (projects as sibling roots at `parent_id=NULL`) in ADR-005 Amendment 2026-04-19, shipped as v0.4.0 (commit range `ab07fd30…c1ab12f7`). Scope items 1–3 + 5 shipped under the amended model. This spec is retained as the Phase 2A historical record; the live architecture lives in `docs/hybrid-taxonomy-plan.md` and ADR-005 Amendment.

**Scope note:** GlobalPattern lifecycle (ADR-005 Phase 2 item 4) is in a separate spec: Phase 2B.

## Problem

All optimizations share a single namespace. A user working on a backend API and a marketing site sees everything mixed together. The taxonomy's hot-path assignment treats all clusters equally, so a marketing prompt might land in a backend-heavy cluster purely by embedding proximity. Pattern injection pulls techniques from unrelated projects, diluting quality.

## Design Overview

```
GitHub repo link  -->  Project node created (PromptCluster state="project")
                       |
Optimization      -->  repo_full_name on Optimization --> LinkedRepo --> project_node_id
                       |
Hot path search   -->  1. In-project search (project_filter=project_id)
                       2. Cross-project fallback (threshold + 0.15 boost)
                       3. New cluster under project's domain subtree
                       |
Warm path Q gate  -->  Per-project Q metrics (scoped _load_active_nodes)
                       |
Topology UI       -->  Project dropdown + multi-project badge (N of M members)
```

## 1. Project Creation on Repo Link

### LinkedRepo model change

Add `project_node_id` column to `LinkedRepo`:

```python
project_node_id = Column(String(36), ForeignKey("prompt_cluster.id"), nullable=True)
```

### Optimization.repo_full_name (already exists)

`repo_full_name` already exists on the `Optimization` model (`models.py:58`). The pipeline already populates it from the `LinkedRepo` lookup in `pipeline.py` and `sampling_pipeline.py`. **No model change needed.**

Project resolution uses this existing column, NOT `session_id` (which doesn't exist on Optimization).

### Project resolution chain

```
Optimization.repo_full_name
    --> LinkedRepo WHERE full_name = repo_full_name
    --> LinkedRepo.project_node_id
    --> PromptCluster WHERE id = project_node_id AND state = "project"
```

If `repo_full_name` is NULL (no linked repo for this session), fall back to the Legacy project node.

### Creation logic

Project creation happens in a **service function** `ensure_project_for_repo(db, repo_full_name)` called from `github_repos.py` link endpoint. This respects the layer rule (routers -> services -> models).

Logic:
1. Query `LinkedRepo WHERE full_name = repo_full_name`.
2. If `project_node_id` is already set, return it (idempotent).
3. Check if only the Legacy project exists and it has never been renamed (label == "Legacy").
4. **First repo:** Rename Legacy node's label to `repo_full_name`. Set `LinkedRepo.project_node_id = legacy.id`. All existing data stays under this project. This is intentional — the first linked repo inherits the existing taxonomy. The user sees their history under the repo name instead of "Legacy".
5. **Subsequent repos:** Create new `PromptCluster(state="project", label=repo_full_name, domain="general", task_type="general", member_count=0)`. Set `LinkedRepo.project_node_id = new_node.id`.

**Re-linking:** If a repo was unlinked and re-linked, find the existing project node by label match (`PromptCluster WHERE state="project" AND label=repo_full_name`). If found, reattach instead of creating a duplicate.

**Domain bootstrap for new projects:** New projects start empty — no pre-seeded domains. When the first optimization arrives for a new project and no matching domain exists, create a "general" domain node as a child of the project node:

```python
PromptCluster(
    label="general", state="domain", domain="general",
    task_type="general", member_count=0, parent_id=project_node_id,
)
```

The warm path's Phase 5 (Discover) will create additional domains as optimizations accumulate and semantic clusters emerge.

### Unlinking

When `DELETE /api/github/repos/unlink`:
- Do NOT delete the project node or its clusters. Data persists.
- Clear `LinkedRepo.project_node_id` reference.
- Optimizations under the project keep their `project_id` and `repo_full_name`.

### Observability

Emit `project_created` SSE event when a new project node is created. Emit `taxonomy_changed` when Legacy is renamed.

## 2. Hot-Path Project-Scoped Search

### Project resolution in process_optimization()

`process_optimization()` in `engine.py` currently accepts `optimization_id` and `db`. Add `repo_full_name: str | None = None` parameter:

```python
async def process_optimization(
    self, optimization_id: str, db: AsyncSession,
    repo_full_name: str | None = None,  # NEW: from pipeline context
) -> dict:
```

Resolution flow inside `process_optimization()`:

```python
# Resolve project_id from repo
project_id = await _resolve_project_id(db, repo_full_name)
opt.project_id = project_id
opt.repo_full_name = repo_full_name
```

`_resolve_project_id(db, repo_full_name)`:
1. If `repo_full_name` is None → return Legacy project node ID.
2. Query `LinkedRepo WHERE full_name = repo_full_name`.
3. If found and `project_node_id` set → return `project_node_id`.
4. If not found → return Legacy project node ID.

Cache the Legacy project ID on the engine instance (`self._legacy_project_id`) to avoid repeated queries.

### Two-tier search in assign_cluster() (family_ops.py)

The current `assign_cluster()` function loads all non-archived clusters from the DB and computes cosine similarity in Python. It does NOT use `EmbeddingIndex.search()` for the primary code path. The two-tier project search wraps the existing logic:

**Current flow (family_ops.py:281-450):**
```
1. Load all active clusters from DB
2. Compute cosine similarity per cluster
3. Apply multi-signal penalties (coherence, output coherence, task_type, size pressure)
4. Best match above adaptive_merge_threshold -> assign
5. No match -> create new cluster
```

**New flow — add project filtering BEFORE the similarity loop:**

```python
async def assign_cluster(
    db, embedding, label, domain, task_type, overall_score,
    embedding_index=None,
    project_id: str | None = None,  # NEW
):
    # Tier 1: Load only in-project clusters
    if project_id:
        project_domain_ids = await _get_project_domain_ids(db, project_id)
        candidates = await db.execute(
            select(PromptCluster).where(
                PromptCluster.state.notin_(EXCLUDED_STRUCTURAL_STATES),
                PromptCluster.parent_id.in_(project_domain_ids),
            )
        )
    else:
        candidates = await db.execute(
            select(PromptCluster).where(
                PromptCluster.state.notin_(EXCLUDED_STRUCTURAL_STATES),
            )
        )

    # ... existing cosine + multi-signal penalty logic on candidates ...

    if best_match and best_score >= adaptive_merge_threshold(best_match):
        return best_match  # In-project assignment

    # Tier 2: Cross-project fallback (ALL clusters, boosted threshold)
    if project_id:
        all_candidates = await db.execute(
            select(PromptCluster).where(
                PromptCluster.state.notin_(EXCLUDED_STRUCTURAL_STATES),
            )
        )
        # Re-run similarity with boosted threshold
        boosted_threshold = adaptive_merge_threshold(best_global) + CROSS_PROJECT_THRESHOLD_BOOST
        if best_global_score >= boosted_threshold:
            return best_global  # Cross-project assignment (cluster becomes multi-project)

    # Tier 3: Create new cluster under project's domain subtree
    domain_node = await _resolve_or_create_domain(db, project_id, domain)
    return await _create_cluster(db, embedding, label, domain_node, ...)
```

**`_get_project_domain_ids(db, project_id)`**: Query `SELECT id FROM prompt_cluster WHERE parent_id = :project_id AND state = 'domain'`. Returns the set of domain node IDs for this project.

**`_resolve_or_create_domain(db, project_id, domain_label)`**: Find a domain node under this project matching the label. If not found, create one (auto-bootstrap as described in Section 1).

### EmbeddingIndex project_id population

Additionally, use `EmbeddingIndex.search()` with `project_filter` as a **pre-filter optimization** before the full DB-based candidate evaluation. This is an acceleration path — the DB query is the source of truth, the embedding index narrows the candidate set:

```python
# Fast pre-filter via embedding index (optional optimization)
if embedding_index and project_id:
    index_hits = embedding_index.search(embedding, k=10, threshold=0.50, project_filter=project_id)
    if index_hits:
        # Narrow DB candidates to index hits for faster evaluation
        candidate_ids = {cid for cid, _ in index_hits}
        candidates = candidates.where(PromptCluster.id.in_(candidate_ids))
```

In `process_optimization()`, after cluster assignment, populate the embedding index:
```python
await self._embedding_index.upsert(cluster.id, centroid, project_id=project_id)
```

In cold path `rebuild()` (`cold_path.py`), build project_ids from Optimization table:
```python
# For multi-project clusters, use the dominant project (most members)
project_ids = await _resolve_cluster_project_ids(db)
await engine._embedding_index.rebuild(centroids, project_ids=project_ids)
```

`_resolve_cluster_project_ids(db)`: Query `SELECT cluster_id, project_id, COUNT(*) FROM optimizations WHERE cluster_id IS NOT NULL GROUP BY cluster_id, project_id ORDER BY COUNT(*) DESC`. For each cluster_id, take the project_id with the highest count (dominant project).

### ADR risk acknowledgment

The ADR notes the `CROSS_PROJECT_THRESHOLD_BOOST` of 0.15 "may be too aggressive." Validation (Section 8) must verify that cross-project matches at the boosted threshold are semantically meaningful, not spurious. If validation shows false matches, the constant is tunable.

## 3. Per-Project Q Metrics

### _load_active_nodes enhancement

```python
async def _load_active_nodes(
    db: AsyncSession,
    exclude_candidates: bool = False,
    project_id: str | None = None,  # NEW: scope to project's clusters
) -> list[PromptCluster]:
```

When `project_id` is set:
1. Query domain IDs for this project: `SELECT id FROM prompt_cluster WHERE parent_id = :project_id AND state = 'domain'`.
2. Load clusters parented to those domains, plus clusters that have `project_id`-tagged optimizations but live under another project's domain (cross-project clusters).
3. The cross-project inclusion uses a subquery: `SELECT DISTINCT cluster_id FROM optimizations WHERE project_id = :project_id`.

This ensures per-project Q metrics include both the project's own clusters AND any cross-project clusters that contain the project's optimizations.

### Speculative phase scoping

In `_run_speculative_phase()`, determine project scope from dirty_ids:

```python
# Resolve which project(s) the dirty clusters belong to
dirty_project_ids = set()
for cid in dirty_ids:
    # Lookup: cluster -> parent (domain) -> parent (project)
    # Cache this mapping on the engine to avoid per-cluster queries
    pid = engine._cluster_project_cache.get(cid)
    if pid:
        dirty_project_ids.add(pid)

if len(dirty_project_ids) == 1:
    # All dirty clusters from one project -> scope Q to that project
    project_id = dirty_project_ids.pop()
    nodes_before = await _load_active_nodes(db, exclude_candidates=True, project_id=project_id)
else:
    # Mixed or unknown -> global Q
    nodes_before = await _load_active_nodes(db, exclude_candidates=True)
```

**`engine._cluster_project_cache: dict[str, str]`**: Populated during `process_optimization()` and `rebuild()`. Maps `cluster_id -> project_id`. Invalidated on cold path rebuild.

### Audit snapshot

Phase 6 (Audit) continues to compute global Q for the `TaxonomySnapshot`. Per-project Q is used only for speculative phase gates, not persisted (avoids schema change).

## 4. Topology UI Project Filter

> **Note:** The frontend items in this section (project dropdown, multi-project badge) are **deferred** to the hierarchical topology navigation roadmap item (`docs/ROADMAP.md`). That roadmap entry supersedes the flat-topology approach described here with a proper 4-level drill-down (Project → Domain → Cluster → Prompt). Building a flat-topology stopgap would be throwaway code. The backend APIs below are implemented and ready for either approach.

### Backend: tree endpoint — IMPLEMENTED

`GET /api/clusters/tree` gains optional `project_id` query parameter.

When set:
- Return only the subtree rooted at the specified project node.
- Include the project node itself as the root.
- Multi-project clusters (members from multiple projects) are included if they have >= 1 member from the filtered project. Use the subquery: `SELECT DISTINCT cluster_id FROM optimizations WHERE project_id = :project_id`.

When unset:
- Return the full tree (current behavior, backward compatible).

### Backend: cluster detail — IMPLEMENTED

`GET /api/clusters/{id}` response gains:
- `project_ids: list[str]` — distinct project IDs among the cluster's optimizations.
- `member_counts_by_project: dict[str, int]` — per-project member breakdown.

### Backend: project list — IMPLEMENTED

`GET /api/projects` returns all project nodes with id, label, and member_count.

### Frontend: project dropdown — DEFERRED

Superseded by hierarchical topology Level 0 (Project Space). In the hierarchical approach, projects are spatially navigated — no dropdown needed. See `docs/ROADMAP.md` "Hierarchical topology navigation" entry.

### Frontend: multi-project badge — DEFERRED

Superseded by hierarchical topology Level 2 (Cluster View). When viewing a domain's clusters, multi-project clusters will be visually distinguished at that level with the "N of M members" tooltip and cross-project indicators. The data (`member_counts_by_project`, `project_ids`) is already served by the backend.

## 5. New Constants

```python
# _constants.py additions
CROSS_PROJECT_THRESHOLD_BOOST: float = 0.15  # ADR-005 Section 2
```

## 6. Model Changes

```python
# LinkedRepo (models.py) — NEW column
project_node_id = Column(String(36), ForeignKey("prompt_cluster.id"), nullable=True)

# Optimization.repo_full_name — ALREADY EXISTS (models.py:58), no change needed

# TaxonomyEngine (engine.py) — NEW attribute in __init__
self._cluster_project_cache: dict[str, str] = {}  # cluster_id -> project_id
self._legacy_project_id: str | None = None         # cached Legacy project node ID
```

**Constraint on Phase 2A code:** Do NOT add any new direct accesses to `_embedding_index._matrix` or `_embedding_index._ids`. Phase 3B will refactor these internals. Use only the public API (`search()`, `upsert()`, `remove()`, `rebuild()`).

## 7. API Changes

| Endpoint | Change |
|----------|--------|
| `POST /api/github/repos/link` | Creates project node, sets LinkedRepo.project_node_id |
| `DELETE /api/github/repos/unlink` | Clears project_node_id, preserves data |
| `GET /api/clusters/tree` | Optional `project_id` query param |
| `GET /api/clusters/{id}` | Adds `project_ids`, `member_counts_by_project` |
| `GET /api/health` | Adds `project_count` |

## 8. Migration (lifespan startup)

1. ALTER TABLE `linked_repos` ADD COLUMN `project_node_id VARCHAR(36)` (try/except, idempotent).
2. Backfill `project_node_id` on existing `LinkedRepo` rows -> Legacy project ID.

Note: `repo_full_name` already exists on Optimization (models.py:58). No ALTER TABLE needed.

## 9. Validation

### Seed targets
- 500 optimizations across 3 projects: ~200 Legacy/first-repo + ~200 Project A + ~100 Project B.
- Create 2 additional `LinkedRepo` entries pointing to mock repos.

### Assertions
- Each project has its own domain subtree (domains discovered organically via bootstrap).
- In-project search returns only same-project clusters.
- Cross-project fallback works with boosted threshold (verify a strong match across projects is semantically valid).
- New clusters are created under the correct project's domain.
- Per-project Q gates: a regression in Project A doesn't block Project B's warm cycle.
- Topology tree with `project_id` filter returns correct subtree.
- Multi-project clusters show correct "N of M" counts.
- Legacy project renamed to first linked repo name.
- Re-linking a previously unlinked repo reattaches to the existing project node.

### Test files
- `tests/test_project_creation.py` — project node lifecycle (create, rename, unlink, re-link)
- `tests/taxonomy/test_project_scoped_search.py` — two-tier search, cross-project fallback, domain bootstrap
- `tests/taxonomy/test_per_project_q.py` — Q gate isolation across projects, cluster-project cache
- `tests/test_topology_project_filter.py` — tree endpoint with project filter, multi-project badge data
