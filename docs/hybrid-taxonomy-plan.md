# Hybrid Taxonomy Architecture — Implementation Plan

_Date: 2026-04-19 — decisions locked, execution shipped in v0.4.0 (2026-04-19)_

**Status:** Implemented. This doc is retained as the canonical locked-decision record linked from ADR-005 Amendment (2026-04-19). See `backend/alembic/versions/d9e0f1a2b3c4_add_dominant_project_id_to_prompt_cluster.py` for the schema migration, `VISIBILITY_THRESHOLD_FRACTION` in `pipeline_constants.py:52`, `scripts/taxonomy-reset.sh`, and the CHANGELOG entry for v0.4.0 "ADR-005 Hybrid Taxonomy — projects as sibling roots" for the full shipped surface.

## Context

Current DB state (5 prompts, 2 projects):
```
Legacy (project)
  ├── backend (domain)
  └── frontend (domain)
project-synthesis/ProjectSynthesis (project)
  └── general (domain)
```

This exposes the architectural contradiction between ADR-005 (projects as tree parents) and the user's Hybrid vision:
- Taxonomy should be **global** (cross-project learning is the core product)
- Projects should be **views** over the global taxonomy (each earns its own visible domains via evidence)
- The `.first()` bug in `_propose_domains()` is a symptom of split `general` nodes, not the root cause

Decisions (locked 2026-04-19):
1. **Visibility threshold** — adaptive (scales with per-project optimization count)
2. **Pattern visibility** — defer to meta-pattern injection vision: GlobalPatterns always visible across projects, in-project MetaPatterns filtered per project
3. **Migration** — not required. User has 5 prompts backed up locally; fresh-start is acceptable

## Target shape

**Layer 1 — Global data substrate.** One `general` domain, one `backend`, one `frontend`, etc. All domain nodes live at the taxonomy root (`parent_id IS NULL`). Clusters parent under their domain. `Optimization.project_id` stays denormalized for attribution/filtering.

**Layer 2 — Per-project view filter.** Endpoints that surface domains/clusters accept `project_id`. A domain is *visible* for project X when at least `N` of its clusters contain optimizations owned by X, where `N = max(1, ceil(0.05 * project_optimization_count))` floored to 1 for fresh projects.

**Layer 3 — UI surfaces.** Topology Level 0 shows projects as orbs. Level 1 (per project) shows only that project's visible domains. Pattern graph shows GlobalPatterns always + in-project MetaPatterns filtered by `project_id`. Discovery (Changes A/B/C) runs globally against the one `general`.

**Project nodes** move to `parent_id IS NULL` — they are metadata/ownership entities, siblings of domain nodes in the DB but never traversed as tree parents by taxonomy discovery.

## Steps

1. **Single-general invariant** (`engine.py`, `family_ops.py`). `_propose_domains()` queries the one `general` with deterministic order (`parent_id IS NULL` preferred, oldest first). `find_or_create_domain_for_project()` drops the per-project branch — returns the global `general`, creating it with `parent_id=None` if absent.

2. **Warm Phase 0 reconciliation.** Collapse pre-existing per-project generals into one global `general`:
   - Promote the oldest `general` to `parent_id=None` (canonical).
   - Reparent clusters under other generals to the canonical one.
   - Archive orphaned per-project generals (`state="archived"`, `cluster_metadata.reason="collapsed_to_global_general"`).
   - Idempotent — no-op when invariant already holds.

3. **Project nodes detached from domain tree.** New project nodes created with `parent_id=None` (sibling of domains). Existing code assuming `cluster.parent_id → project` chain is updated: domain reparenting uses `Optimization.project_id` lookups, not `cluster.parent_id` traversal.

4. **`GET /api/domains?project_id=X` filter.** When `project_id` present, return only domains with `visible_in_project(X) = True`. Visibility computed via a subquery: `COUNT(DISTINCT cluster.id) WHERE cluster.parent_id = domain.id AND EXISTS (SELECT 1 FROM optimization WHERE cluster_id = cluster.id AND project_id = X) >= threshold`.

5. **Adaptive visibility threshold.** `compute_visibility_threshold(project_total_opts) = max(1, ceil(0.05 * project_total_opts))`. For fresh projects (<20 opts), threshold stays at 1. Scales linearly thereafter. Constant `VISIBILITY_THRESHOLD_FRACTION = 0.05` in `pipeline_constants.py`.

6. **Pattern graph per-project view.** `GET /api/patterns?project_id=X` joins `OptimizationPattern → Optimization.project_id` for MetaPattern visibility and UNIONs with all `GlobalPattern` rows (cross-project by definition). No `project_id` filter → legacy behavior (all MetaPatterns, all GlobalPatterns).

7. **Fresh-start data wipe script.** `scripts/taxonomy-reset.sh` wipes `PromptCluster`, `MetaPattern`, `GlobalPattern`, `OptimizationPattern`, `TaxonomySnapshot` while preserving `Optimization`, `Feedback`, `LinkedRepo`, `GitHubToken`. Optimization `cluster_id`/`domain`/`project_id` cleared so hot-path re-assigns on next write. User-opt-in (requires `--confirm`).

## Out of scope for this plan

- Sub-domain per-project visibility (follow-up — piggyback on step 4 once cluster tree filter ships)
- Cross-project cluster merge (requires embedding-space alignment — separate ADR)
- Per-project Q metrics (already exists in `AdaptiveScheduler` — no change needed)
- Pattern graph UI changes (backend endpoint first; frontend lands on topology navigation roadmap entry)

## Integration with existing roadmap

- **Hierarchical Topology Navigation** (`docs/ROADMAP.md:123`) — Level 1 drill-down consumes step 4's endpoint directly. No additional work.
- **Integration Store** (`docs/ROADMAP.md:76`) — generalized project creation still creates project nodes; step 3 just moves them to `parent_id=None`.
- **Taxonomy Observatory** (`docs/ROADMAP.md:15`) — no change; domain evolution events stay global.

## Verification

- Unit: existing domain-discovery tests continue to pass with unified `general`.
- New: `test_find_or_create_returns_global_general`, `test_warm_phase0_collapses_per_project_generals`, `test_domains_endpoint_filters_by_project_id`, `test_visibility_threshold_scales_with_project_size`.
- Integration: seed 2 backend + 2 frontend prompts under two distinct projects; assert both projects see their own visible domains via `/api/domains?project_id=X` while the underlying `backend`/`frontend` domain nodes are shared.
- Manual: restart, re-run the 3-prompt scenario from the prior plan — domains emerge with correct brand colors, projects each see only their own visible set.
