# Taxonomy Reclustering Audit — Design Spec

**Date:** 2026-04-01
**Scope:** Full restructure of warm path, cold path quality gate, filter/query fixes, module extraction

## Context

The taxonomy reclustering process has 9 critical issues and 6 moderate issues discovered during a comprehensive end-to-end audit. The root cause is that the warm path mixes correctness fixes (reconciliation) with speculative mutations (lifecycle operations) in a single transaction, with a quality gate that compares pre-reconciled Q against post-reconciled Q — producing unreliable results.

### Critical Issues Found

1. **Q_before computed before reconciliation** — Quality gate compares stale coherence (Q_before) against corrected coherence (Q_after), incorrectly rejecting valid operations
2. **Leaf splits bypass Q_system gate** — Committed independently with no quality check
3. **Cold path has NO quality gate** — Always commits regardless of Q regression
4. **Rollback destroys reconciliation** — Q-gate rollback undoes member_count fixes, coherence corrections, score reconciliation alongside lifecycle ops
5. **Cold path loads archived clusters into HDBSCAN** — Dead centroids pollute topology
6. **Cold path excludes mature/template from matching** — These get orphaned while duplicates are created
7. **Emerge finds domain nodes** — `parent_id IS NULL` includes domains, violating guardrails
8. **Embedding index diverges on rollback** — DB rolled back but in-memory index retains post-mutation state
9. **ops_accepted ignores leaf splits** — Deadlock breaker counter increments even when splits succeeded

### Moderate Issues Found

10. Noise reassignment: N*2 per-point queries instead of batch
11. Manual cosine similarity in 3+ locations instead of using `clustering.cosine_similarity()`
12. `warm_path_age` stalls on idle cycles — epsilon tolerance doesn't decay
13. Cold path doesn't reset `split_failures` metadata after refit
14. Stale label refresh deletes all meta-patterns before re-extraction — no recovery on failure
15. Split candidate pre-fetch too broad — fetches for all >=6 member clusters

## Architecture

### Warm Path Phase Decomposition

The warm path is decomposed into discrete, independently-committed phases. Each speculative phase gets its own `AsyncSession`, per-phase Q gate, and embedding index snapshot/restore.

```
Phase 0: Reconcile         → always commits (correctness fixes)
Phase 1: Split + Emerge    → per-phase Q gate, own session
Phase 2: Merge             → per-phase Q gate, own session  
Phase 3: Retire            → per-phase Q gate, own session
Phase 4: Refresh           → always commits (non-speculative)
Phase 5: Discover          → always commits (non-speculative)
Phase 6: Audit             → final Q, snapshot, deadlock breaker
```

### Execution Model

**Strict sequential ordering.** Each speculative phase sees the committed result of previous phases. No parallelism between phases.

```
Phase 0 (Reconcile) ──commit──→ Q_baseline snapshot

Phase 1 (Split+Emerge)
  └─ fresh session → load active nodes → compute Q_before
  └─ execute splits + emerges → compute Q_after
  └─ Q_after >= Q_before - ε? → commit : rollback + restore index
  
Phase 2 (Merge)  
  └─ fresh session → load active nodes → compute Q_before (sees Phase 1 result)
  └─ execute global merge + same-domain dedup → compute Q_after
  └─ Q_after >= Q_before - ε? → commit : rollback + restore index

Phase 3 (Retire)
  └─ fresh session → load active nodes → compute Q_before (sees Phases 1-2)
  └─ execute retirements → compute Q_after
  └─ Q_after >= Q_before - ε? → commit : rollback + restore index

Phase 4 (Refresh) ──commit──→ (non-speculative, always persists)
Phase 5 (Discover) ──commit──→ (non-speculative, always persists)
Phase 6 (Audit) ──commit──→ snapshot + deadlock breaker evaluation
```

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
        """Frozen copy of current index state."""
        ...
    
    def restore(self, snapshot: IndexSnapshot) -> None:
        """Atomically restore to a previous state."""
        ...
```

Each speculative phase: `snapshot()` before mutations, `restore()` on rollback.

### Cold Path Quality Gate

The cold path gets a quality gate with wider epsilon (destructive refit tolerance):

```python
COLD_PATH_EPSILON = 0.05  # 5% tolerance
```

**Protocol:**
1. Compute `Q_before` from current active nodes (pre-refit)
2. Run HDBSCAN, UMAP, labeling, reconciliation
3. Compute `Q_after` from new topology
4. `Q_after >= Q_before - COLD_PATH_EPSILON` → commit
5. Else → rollback, log warning, return `ColdPathResult(accepted=False)`

No force bypass — even manual API triggers respect the quality gate.

### Warm Path Result Enhancement

```python
@dataclass
class WarmPathResult:
    snapshot_id: str
    q_baseline: float | None       # Post-reconciliation Q (was q_system)
    q_final: float | None          # After all phases
    phase_results: list[PhaseResult]  # NEW — per-phase breakdown
    operations_attempted: int       # Sum across phases
    operations_accepted: int        # Sum across phases (committed only)
    deadlock_breaker_used: bool
    deadlock_breaker_phase: str | None  # NEW — which phase was forced
```

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
```

## Filter & Query Fixes

### Fix #5 — Exclude archived from cold path HDBSCAN
```python
# engine.py line 1764
PromptCluster.state.notin_(["domain", "archived"])
```

### Fix #6 — Include mature/template in cold path matching
```python
# engine.py line 1808
PromptCluster.state.notin_(["domain", "archived"])
```

### Fix #7 — Emerge excludes domain nodes
```python
PromptCluster.parent_id.is_(None),
PromptCluster.state.notin_(["domain", "archived"]),
```

### Fix #9 — Leaf splits counted in ops_accepted
Leaf splits within Phase 1 (Split+Emerge) properly increment `ops_accepted`.

### Fix #11 — Batch noise reassignment
Replace per-noise-point queries with single batch fetch.

### Fix #13 — Safe meta-pattern refresh
Extract new patterns first; only delete old ones if extraction succeeds.

### Fix #14 — Targeted split candidate pre-fetch
Only fetch embeddings for clusters with coherence below dynamic floor or NULL.

### Fix #15 — Consistent cosine similarity
Replace all manual `np.dot / np.linalg.norm` with `cosine_similarity()` from `clustering.py`.

### Fix #16 — warm_path_age always increments
Remove idle-cycle skip. Age represents wall-clock cycles.

### Fix #17 — Cold path resets split_failures
Clear stale cooldowns after HDBSCAN refit.

### Fix #18 — Delete stale comment
Remove incorrect comment at engine.py:1011-1014.

## File Structure

### New files
- **`warm_path.py`** — Warm path orchestrator: phase sequencing, per-phase Q gates, deadlock breaker
- **`warm_phases.py`** — Individual phase implementations: reconcile, split_emerge, merge, retire, refresh, discover, audit
- **`cold_path.py`** — Cold path: HDBSCAN refit, UMAP, coloring, quality gate

### Modified files
- **`engine.py`** — Reduced to ~500 lines: TaxonomyEngine class, hot path, public API. Warm/cold path delegated to new modules
- **`embedding_index.py`** — Add `snapshot()` and `restore()` methods
- **`quality.py`** — Add `COLD_PATH_EPSILON`, update `is_non_regressive()` for cold path variant
- **`main.py`** — Update warm path timer to pass `async_session_factory` instead of single session
- **`routers/clusters.py`** — Update recluster endpoint result schema

### Unchanged files
- `lifecycle.py`, `clustering.py`, `projection.py`, `coloring.py`, `labeling.py`, `snapshot.py`, `family_ops.py`, `matching.py`, `cluster_meta.py`

## Verification

### Unit Tests
- Per-phase Q gate: verify each phase commits/rolls back independently
- Embedding index snapshot/restore: verify DB↔index consistency after rollback
- Cold path quality gate: verify regression is rejected, non-regression commits
- Filter fixes: verify archived excluded from HDBSCAN, mature/template included in matching
- Emerge filter: verify domain nodes excluded

### Integration Tests
- Full warm path cycle: reconcile → split+emerge → merge → retire → refresh → discover → audit
- Warm→cold transition: deadlock breaker triggers cold path, cold path respects quality gate
- Hot→warm consistency: hot path creates cluster → warm path reconciles member_count correctly
- Rollback isolation: phase 2 rollback doesn't affect phase 1 committed changes

### Manual Verification
- Run `./init.sh restart` → trigger optimizations → observe warm path logs for phase-by-phase execution
- Trigger `POST /api/clusters/recluster` → verify cold path Q gate in response
- Check `GET /api/clusters/stats` sparkline after warm+cold cycles
