# ADR-005 Phases 2-3 — Implementation Decomposition

> **Spec:** `docs/specs/2026-04-08-phase2a-multi-project-isolation.md`, `phase2b-global-pattern-tier.md`, `phase3a-round-robin-scheduling.md`, `phase3b-hnsw-embedding-index.md`
> **ADR:** `docs/adr/ADR-005-taxonomy-scaling-architecture.md`
> **Depends on:** Phase 1 (complete, merged to main)

Phases 2-3 decompose into 4 sequential plans. Each produces working, testable software.

## Sub-plan 2A: Multi-Project Isolation (tasks 1-8)

**Goal:** Project creation on repo link, hot-path project-scoped search with cross-project fallback, per-project Q metrics, topology UI project filter.

**Dependencies:** Phase 1 (EXCLUDED_STRUCTURAL_STATES, Legacy project node, project_id on Optimization, EmbeddingIndex project_filter)

**Files to modify:**
- `backend/app/models.py` — add project_node_id to LinkedRepo
- `backend/app/services/taxonomy/_constants.py` — CROSS_PROJECT_THRESHOLD_BOOST
- `backend/app/services/taxonomy/engine.py` — process_optimization project resolution, caches
- `backend/app/services/taxonomy/family_ops.py` — two-tier assign_cluster
- `backend/app/services/taxonomy/warm_path.py` — _load_active_nodes project_id, Q scoping
- `backend/app/services/taxonomy/cold_path.py` — rebuild with project_ids
- `backend/app/routers/github_repos.py` — project creation on link
- `backend/app/routers/clusters.py` — tree endpoint project filter, cluster detail
- `backend/app/routers/health.py` — project_count
- `backend/app/main.py` — migration (ALTER TABLE linked_repos)
- `frontend/src/` — ProjectFilter component, multi-project badge

**Estimated tasks:** 8

## Sub-plan 2B: Global Pattern Tier (tasks 1-7)

**Goal:** GlobalPattern promotion from MetaPattern, injection alongside MetaPatterns with 1.3x boost, validation with demotion/re-promotion hysteresis, retention cap.

**Dependencies:** Phase 2A (project nodes, project_id on Optimization)

**Files to modify:**
- `backend/app/services/taxonomy/_constants.py` — 9 new GLOBAL_PATTERN_* constants
- `backend/app/services/taxonomy/engine.py` — _last_global_pattern_check
- `backend/app/services/taxonomy/warm_path.py` — Phase 4.5 orchestration
- `backend/app/services/taxonomy/warm_phases.py` — phase_global_patterns function
- `backend/app/services/pattern_injection.py` — InjectedPattern dataclass, GlobalPattern injection
- `backend/app/models.py` — global_pattern_id on OptimizationPattern
- `backend/app/routers/health.py` — global_patterns stats
- `backend/app/main.py` — migration (ALTER TABLE optimization_patterns)

**Estimated tasks:** 7

## Sub-plan 3A: Round-Robin Warm Scheduling (tasks 1-6)

**Goal:** Extend AdaptiveScheduler with linear regression boundary, round-robin mode when dirty count exceeds boundary, starvation guard, per-project dirty tracking.

**Dependencies:** Phase 2A (_cluster_project_cache, per-project Q metrics)

**Files to modify:**
- `backend/app/services/taxonomy/engine.py` — _dirty_set dict, mark_dirty project_id, snapshot_dirty_set_with_projects, SchedulerDecision, decide_mode, _compute_boundary, _pick_priority_project
- `backend/app/services/taxonomy/warm_path.py` — snapshot change, mode decision, re-injection

**Estimated tasks:** 6

## Sub-plan 3B: HNSW Embedding Index (tasks 1-8)

**Goal:** Dual-backend EmbeddingIndex (numpy + hnswlib) with stable label mapping, auto-selection on rebuild, backward-compatible cache.

**Dependencies:** Phase 1 (EmbeddingIndex _project_ids, project_filter), Phase 2A (project_ids populated)

**Files to modify:**
- `backend/app/services/taxonomy/embedding_index.py` — NumpyBackend, HnswBackend, stable label mapping, adapter layer, cache, snapshot/restore
- `backend/app/services/taxonomy/engine.py` — replace direct _matrix/_ids access with public reset()
- `requirements.txt` — hnswlib dependency

**Estimated tasks:** 8

## Execution Order — MANDATORY: 2A → 2B → 3A → 3B

**2A first** because:
- 2A creates project nodes and populates project_id on Optimization — 2B needs this for cross-project promotion
- 2A adds _cluster_project_cache — 3A needs this for per-project dirty tracking
- 2A modifies family_ops.py, warm_path.py, engine.py — later phases build on these changes

**2B before 3A** because:
- 2B adds Phase 4.5 to warm_path.py — 3A also modifies warm_path.py (snapshot change)
- Running 3A first would cause merge conflicts in warm_path.py

**3A before 3B** because:
- 3A changes _dirty_set from set to dict — independent of 3B
- 3B is the most self-contained phase (single file + dependency)
- Either order works but 3A is more critical for multi-project performance

## Validation Strategy

Each phase includes seed-based E2E validation:
- 2A: 500 opts across 3 projects — verify isolation, cross-project fallback, Q scoping
- 2B: 1K+ opts, 200+ clusters — verify promotion, injection, validation lifecycle
- 3A: 2K+ opts — verify scheduler transitions, round-robin, starvation guard
- 3B: 5K+ opts, 1K+ clusters — benchmark numpy vs HNSW, verify identical results

## Next Step

Start with Sub-plan 2A using `superpowers:subagent-driven-development`.
