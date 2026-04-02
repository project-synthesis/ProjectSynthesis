# Taxonomy Reclustering Audit — Design Spec

**Date:** 2026-04-01  
**Revision:** 3 (post second review cycle)  
**Scope:** Full restructure of warm path, cold path quality gate, filter/query fixes, module extraction

## Context

The taxonomy reclustering process has 10 critical issues and 6 moderate issues discovered during a comprehensive end-to-end audit. The root cause is that the warm path mixes correctness fixes (reconciliation) with speculative mutations (lifecycle operations) in a single transaction, with a quality gate that compares pre-reconciled Q against post-reconciled Q — producing unreliable results.

### Critical Issues Found

1. **Q_before computed before reconciliation** — Quality gate compares stale coherence (Q_before) against corrected coherence (Q_after). Reconciliation typically *lowers* coherence (hot path never updates it), so Q_after reflects real values while Q_before uses inflated stale values. This makes the gate **incorrectly lenient** — it attributes reconciliation improvements to lifecycle operations, accepting mutations that may have actually caused regression.
2. **Leaf splits bypass Q_system gate** — Committed independently with no quality check
3. **Cold path has NO quality gate** — Always commits regardless of Q regression
4. **Rollback destroys reconciliation** — Q-gate rollback undoes member_count fixes, coherence corrections, score reconciliation alongside lifecycle ops
5. **Cold path loads archived clusters into HDBSCAN** — Dead centroids pollute topology
6. **Cold path excludes mature/template from matching** — These get orphaned while duplicates are created
7. **Emerge finds domain nodes** — `parent_id IS NULL` includes domains, violating guardrails
8. **Embedding index diverges on rollback** — DB rolled back but in-memory index retains post-mutation state
9. **ops_accepted ignores leaf splits** — Deadlock breaker counter increments even when splits succeeded
10. **Warm path active-only query excludes mature/template** — `state == "active"` at line 372 means mature/template clusters skip all lifecycle operations (splits, merges, retires, reconciliation). A mature cluster with 0 members would never be detected for retirement.

### Moderate Issues Found

11. Noise reassignment: N*2 per-point queries instead of batch (embeddings already in `_split_emb_cache`)
12. Manual cosine similarity in 3+ locations instead of using `clustering.cosine_similarity()`
13. `warm_path_age` stalls on idle cycles — epsilon tolerance doesn't decay
14. Cold path doesn't reset `split_failures` metadata after refit
15. Stale label refresh deletes all meta-patterns before re-extraction — no recovery on failure
16. Reconciliation iterates stale `active_nodes` list — nodes archived by splits/retires are still reconciled, while newly created split children are not

## Architecture

### Warm Path Phase Decomposition

The warm path is decomposed into discrete, independently-committed phases. Each speculative phase gets its own `AsyncSession`, per-phase Q gate, and embedding index snapshot/restore.

```
Phase 0: Reconcile + Zombies  → always commits (correctness fixes)
Phase 1: Split + Emerge       → per-phase Q gate, own session
Phase 2: Merge + Same-Domain  → per-phase Q gate, own session  
Phase 3: Retire               → per-phase Q gate, own session
Phase 4: Refresh              → always commits (non-speculative)
Phase 5: Discover + Risk      → always commits (non-speculative)
Phase 6: Audit                → final Q, snapshot, deadlock breaker
```

**Phase contents mapped to current code:**

| Phase | Operations | Current Source Lines |
|-------|-----------|-------------------|
| 0 | Member count, coherence, scores, domain node repairs, zombie cleanup | 870-1061 |
| 1 | Leaf split, family split, k-means fallback, emerge from orphans | 388-794 |
| 2 | Global best-pair merge, same-domain label merge, same-domain embedding merge | 796-853 + 1154-1276 |
| 3 | Retire idle nodes | 854-868 |
| 4 | Stale label/pattern refresh | 1063-1152 |
| 5 | Domain discovery, candidate detection, risk monitoring, signal staleness, tree integrity | 1278-1315 (call sites) + 2347-2992 (helpers) |
| 6 | Q_after computation, snapshot creation, deadlock breaker evaluation, event publishing | 1317-1435 |

### Execution Model

**Strict sequential ordering.** Each speculative phase sees the committed result of previous phases. No parallelism between phases. Each phase loads fresh ORM objects from its own session — no detached object issues.

```
Phase 0 (Reconcile + Zombies) ──commit──→ Q_baseline snapshot

Phase 1 (Split+Emerge)
  └─ fresh session → load non-archived non-domain nodes → compute Q_before
  └─ execute splits + emerges → compute Q_after
  └─ Q_after >= Q_before - ε? → commit : rollback + restore index
  
Phase 2 (Merge + Same-Domain)  
  └─ fresh session → load non-archived non-domain nodes → compute Q_before
  └─ execute global merge + same-domain dedup → compute Q_after
  └─ Q_after >= Q_before - ε? → commit : rollback + restore index

Phase 3 (Retire)
  └─ fresh session → load non-archived non-domain nodes → compute Q_before
  └─ execute retirements → compute Q_after
  └─ Q_after >= Q_before - ε? → commit : rollback + restore index

Phase 4 (Refresh) ──commit──→ (non-speculative, always persists)
Phase 5 (Discover + Risk) ──commit──→ (non-speculative, always persists)
Phase 6 (Audit) ──commit──→ snapshot + deadlock breaker evaluation
```

**Node loading for speculative phases:** Use `state.notin_(["domain", "archived"])` instead of `state == "active"` to include mature/template clusters in lifecycle operations (fixes issue #10).

**Q computation must match node loading:** `compute_q_system()` in `quality.py` currently filters `state == "active"`, excluding mature/template nodes from Q. This must be updated to `state not in ("domain", "archived")` so that lifecycle operations on mature/template nodes are reflected in Q_before/Q_after comparisons. Without this, the Q gate is blind to operations on mature/template clusters.

### Dependency Injection Pattern

All new modules (`warm_path.py`, `warm_phases.py`, `cold_path.py`) receive the `TaxonomyEngine` instance as a parameter. They **never** import `TaxonomyEngine` directly. This avoids circular imports since `engine.py` imports from them.

Helper methods that both warm and cold paths need (`_compute_q_from_nodes`, `_update_per_node_separation`, `_snapshot_metrics`) stay on `TaxonomyEngine` and are called via the `engine` parameter.

### Per-Phase Q Gate Contract

```python
@dataclass
class PhaseResult:
    phase: str                      # "split_emerge", "merge", "retire"
    q_before: float
    q_after: float
    accepted: bool                  # True=committed, False=rolled back
    operations: list[dict]          # Operation log entries
    embedding_index_mutations: int  # Index changes (for diagnostics)
```

### Per-Phase Deadlock Breaker

Track rejections per phase instead of per cycle:

```python
_phase_rejection_counters: dict[str, int]  # {"split_emerge": 0, "merge": 3, ...}
```

After 5 consecutive rejections of the **same phase**, force that specific operation. Prevents a stuck merge from blocking splits.

### Embedding Index Snapshot/Restore

```python
@dataclass
class IndexSnapshot:
    matrix: np.ndarray  # Copy of the centroid matrix
    ids: list[str]      # Copy of the ID list

class EmbeddingIndex:
    def snapshot(self) -> IndexSnapshot:
        """Frozen copy of current index state. Acquires lock."""
        ...
    
    def restore(self, snapshot: IndexSnapshot) -> None:
        """Atomically restore to a previous state. Acquires lock."""
        ...
```

Each speculative phase: `snapshot()` before mutations, `restore()` on rollback. Both methods acquire `_lock` for thread safety.

### Cold Path Quality Gate

The cold path gets a quality gate with wider epsilon (destructive refit tolerance):

```python
COLD_PATH_EPSILON = 0.08  # 8% tolerance (wider than warm path's ~0.1-1%)
```

**Protocol:**
1. Compute `Q_before` from current active nodes (pre-refit)
2. Run HDBSCAN, UMAP, labeling, reconciliation
3. Compute `Q_after` from new topology
4. `Q_after >= Q_before - COLD_PATH_EPSILON` → commit
5. Else → rollback, log warning, return `ColdPathResult(accepted=False)`

No force bypass — even manual API triggers respect the quality gate. 8% tolerance accounts for HDBSCAN non-determinism and UMAP projection noise.

### Warm Path Result Enhancement

```python
@dataclass
class WarmPathResult:
    snapshot_id: str
    q_baseline: float | None        # Post-reconciliation Q
    q_final: float | None           # After all phases
    phase_results: list[PhaseResult] # Per-phase breakdown
    operations_attempted: int        # Sum across phases
    operations_accepted: int         # Sum across phases (committed only)
    deadlock_breaker_used: bool
    deadlock_breaker_phase: str | None  # Which phase was forced
    q_system: float | None = None    # Backward compat — set from q_final in __post_init__

    def __post_init__(self):
        if self.q_system is None and self.q_final is not None:
            self.q_system = self.q_final
```

`q_system` is set automatically in `__post_init__` from `q_final`, preserving all existing callers. (`@property` does not work on `@dataclass` fields — `__post_init__` is the correct pattern.)

### Cold Path Result Enhancement

```python
@dataclass
class ColdPathResult:
    snapshot_id: str
    q_before: float | None
    q_after: float | None
    accepted: bool
    nodes_created: int
    nodes_updated: int
    umap_fitted: bool
    q_system: float | None = None  # Backward compat — set from q_after in __post_init__

    def __post_init__(self):
        if self.q_system is None and self.q_after is not None:
            self.q_system = self.q_after
```

`q_system` is set automatically in `__post_init__` from `q_after`.

## Filter & Query Fixes

### Fix #5 — Exclude archived from cold path HDBSCAN
```python
# cold_path.py (was engine.py line 1764)
PromptCluster.state.notin_(["domain", "archived"])
```

### Fix #6 — Include mature/template in cold path matching
```python
# cold_path.py (was engine.py line 1808)
PromptCluster.state.notin_(["domain", "archived"])
```

### Fix #7 — Emerge excludes domain nodes
```python
PromptCluster.parent_id.is_(None),
PromptCluster.state.notin_(["domain", "archived"]),
```

### Fix #9 — Leaf splits counted in ops_accepted
Leaf splits within Phase 1 (Split+Emerge) properly increment `ops_accepted`.

### Fix #10 — Warm path loads all non-archived, non-domain nodes
```python
# All speculative phases use:
PromptCluster.state.notin_(["domain", "archived"])
# instead of: PromptCluster.state == "active"
```

### Fix #11 — Batch noise reassignment
Replace per-noise-point queries with lookup from `_split_emb_cache` (already pre-fetched). Scores batch-fetched in single query.

### Fix #15 — Safe meta-pattern refresh
Extract new patterns first; only delete old ones if extraction succeeds.

### Fix #12 — Consistent cosine similarity
Replace **scalar** manual `np.dot / np.linalg.norm` calls with `cosine_similarity()` from `clustering.py` (lines 675, 1203, 1241 in engine.py). Do NOT replace the matrix-based pairwise scan at lines 808-817 (global merge) — that uses `mat_norm @ mat_norm.T` which is an intentionally optimized batch operation.

### Fix #13 — warm_path_age always increments
Remove idle-cycle skip. Age represents wall-clock cycles for epsilon decay.

### Fix #14 — Cold path resets split_failures
Clear stale cooldowns after HDBSCAN refit.

### Fix #16 — Reconciliation uses fresh node queries
Each phase re-queries active nodes from its own session. No stale `active_nodes` list.

## File Structure

### New files
- **`warm_path.py`** — Warm path orchestrator: phase sequencing, per-phase Q gates, deadlock breaker
- **`warm_phases.py`** — Individual phase implementations: reconcile, split_emerge, merge, retire, refresh, discover, audit
- **`cold_path.py`** — Cold path: HDBSCAN refit, UMAP, coloring, quality gate

### Modified files
- **`engine.py`** — Reduced to ~900-1100 lines: TaxonomyEngine class, hot path, public API, read APIs, admin ops. Domain discovery helpers stay on TaxonomyEngine (called from warm_phases via engine param). Q computation helpers (`_compute_q_from_nodes`, `_update_per_node_separation`, `_snapshot_metrics`) stay on TaxonomyEngine.
- **`embedding_index.py`** — Add `IndexSnapshot`, `snapshot()`, and `restore()` methods (lock-acquiring)
- **`quality.py`** — Add `COLD_PATH_EPSILON`, `is_cold_path_non_regressive()`. Update `compute_q_system()` to include mature/template nodes (not just active).
- **`__init__.py`** — Re-export `WarmPathResult` from `warm_path.py`, `ColdPathResult` from `cold_path.py` for backward compatibility
- **`main.py`** — Update warm path timer: pass `async_session_factory` to `run_warm_path()`. Lifecycle service calls (`curate()`, `decay_usage()`) get their own session after warm path completes.
- **`routers/clusters.py`** — Update recluster endpoint result schema (q_before, q_after, accepted)

### Unchanged files
- `lifecycle.py`, `clustering.py`, `projection.py`, `coloring.py`, `labeling.py`, `snapshot.py`, `family_ops.py`, `matching.py`, `cluster_meta.py`

## Verification

### Unit Tests
- Per-phase Q gate: verify each phase commits/rolls back independently
- Embedding index snapshot/restore: verify DB↔index consistency after rollback (lock semantics)
- Cold path quality gate: verify regression is rejected, non-regression commits
- Filter fixes: verify archived excluded from HDBSCAN, mature/template included in matching and lifecycle
- Emerge filter: verify domain nodes excluded
- Session isolation: verify ORM objects are fresh per phase (no detached object errors)
- Deadlock breaker: per-phase counter increments and forces correctly

### Integration Tests
- Full warm path cycle: reconcile → split+emerge → merge → retire → refresh → discover → audit
- Warm→cold transition: deadlock breaker triggers cold path, cold path respects quality gate
- Hot→warm consistency: hot path creates cluster → warm path reconciles member_count correctly
- Rollback isolation: phase 2 rollback doesn't affect phase 1 committed changes
- Domain discovery helper calls: verify `_propose_domains`, `_detect_domain_candidates` etc. work via engine parameter
- Lifecycle service calls: `curate()` and `decay_usage()` execute with their own session after warm path

### Manual Verification
- Run `./init.sh restart` → trigger optimizations → observe warm path logs for phase-by-phase execution
- Trigger `POST /api/clusters/recluster` → verify cold path Q gate in response
- Check `GET /api/clusters/stats` sparkline after warm+cold cycles
