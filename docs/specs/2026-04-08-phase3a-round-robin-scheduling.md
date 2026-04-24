# Phase 3A Design Spec: Round-Robin Warm Scheduling

**Date:** 2026-04-08
**ADR:** [ADR-005](../adr/ADR-005-taxonomy-scaling-architecture.md) (Phase 3, item 1 — Section 3)
**Depends on:** Phase 1 (dirty-set, AdaptiveScheduler measurement), Phase 2A (multi-project, per-project Q metrics, `_cluster_project_cache`)
**Status:** Shipped (core) — `AdaptiveScheduler` shipped as part of the B-layer work in v0.4.0 with linear regression boundary + all-dirty vs per-project budget modes + proportional quotas + `_MIN_QUOTA=3` floor + per-project starvation guard (3-cycle counter). Large-corpus stress validation (≥1000 clusters sustained across cycles) is Deferred on the ROADMAP, but the scheduler's self-tuning behavior is live.

## Problem

The warm path processes all dirty clusters every cycle regardless of how many there are. At scale (300+ dirty clusters across 3+ projects), cycle duration exceeds the comfortable target (p75 of recent durations). The system needs to shed load gracefully by processing one project at a time when overloaded, while still processing all dirty clusters when the load is manageable.

## Design Overview

```
AdaptiveScheduler (engine.py)
    |
    +-- Rolling window: last 10 (dirty_count, duration_ms) measurements
    +-- target_cycle_ms: p75 of recent durations (self-tuning)
    +-- dirty_count_boundary: linear regression prediction
    |
    v Mode decision (each warm cycle)
    |
    +-- dirty_count <= boundary  ->  All-dirty mode (process all projects)
    +-- dirty_count >  boundary  ->  Round-robin mode (one project per cycle)
                                     |
                                     +-- Priority: most dirty clusters
                                     +-- Starvation guard: max 3 skipped cycles
```

## 1. Linear Regression for Boundary Computation

### Input

Rolling window of `WarmCycleMeasurement(dirty_count, duration_ms)` (already collected by Phase 1). Only dirty-only cycles are recorded (full-scan cycles skipped — Phase 1 decision).

### Computation

After bootstrap (10 cycles), compute slope and intercept using stdlib math (no scipy):

```python
def _compute_boundary(self) -> int:
    """Dirty count at which predicted duration equals target."""
    if len(self._window) < self._WINDOW_SIZE:
        return self._BOOTSTRAP_BOUNDARY  # 20

    xs = [m.dirty_count for m in self._window]
    ys = [m.duration_ms for m in self._window]
    n = len(xs)
    sum_x = sum(xs)
    sum_y = sum(ys)
    sum_xy = sum(x * y for x, y in zip(xs, ys))
    sum_xx = sum(x * x for x in xs)

    denom = n * sum_xx - sum_x * sum_x
    if abs(denom) < 1e-9:
        return self._BOOTSTRAP_BOUNDARY  # degenerate case

    slope = (n * sum_xy - sum_x * sum_y) / denom
    intercept = (sum_y - slope * sum_x) / n

    if slope <= 0:
        # Duration doesn't grow with dirty count — never switch to round-robin
        return 999

    boundary = (self._target_cycle_ms - intercept) / slope
    result = max(1, int(boundary))

    # Log warning when boundary clamps to 1 (degenerate regression)
    if result == 1:
        logger.warning(
            "AdaptiveScheduler: boundary clamped to 1 (slope=%.2f intercept=%.0f target=%d)",
            slope, intercept, self._target_cycle_ms,
        )

    return result
```

### New constants

```python
_BOOTSTRAP_BOUNDARY: int = 20  # dirty count fallback during bootstrap
```

## 2. Per-Project Dirty Tracking

### Data structure change

Phase 1's `_dirty_set: set[str]` becomes `_dirty_set: dict[str, str | None]` mapping `cluster_id -> project_id`.

### mark_dirty() change

```python
def mark_dirty(self, cluster_id: str, project_id: str | None = None) -> None:
    """Mark a cluster as needing warm-path processing."""
    self._dirty_set[cluster_id] = project_id
```

The `project_id` parameter has a default of `None`, making this **backward compatible**. All existing Phase 1 and Phase 2A call sites that pass only `cluster_id` will store `None` as the project.

### Call site project_id resolution

All `mark_dirty()` call sites updated with project_id:

| Call site | File:line (approx) | How to get project_id |
|-----------|--------------------|-----------------------|
| Old cluster lost member | engine.py:370 | `opt.project_id` (available in scope) |
| New cluster gained member | engine.py:378 | `opt.project_id` (available in scope) |
| Merge survivor (global) | warm_phases.py:2227 | `engine._cluster_project_cache.get(merged.id)` |
| Merge survivor (label) | warm_phases.py:2364 | `engine._cluster_project_cache.get(merged.id)` |
| Merge survivor (embedding) | warm_phases.py:2475 | `engine._cluster_project_cache.get(merged.id)` |
| Dissolution targets | warm_phases.py:2615 | `_ra_info["cluster_id"]` -> cache lookup |

The `_cluster_project_cache` (introduced in Phase 2A) maps cluster_id -> project_id. For merge survivors, the project is the dominant project of the surviving cluster.

### snapshot_dirty_set() return type change

**Breaking change management:** Phase 1's `snapshot_dirty_set()` returns `set[str]`. Phase 3A changes the return type. To manage this cleanly:

**Option chosen: new method.** Keep `snapshot_dirty_set() -> set[str]` unchanged. Add a new method:

```python
def snapshot_dirty_set_with_projects(self) -> tuple[set[str], dict[str, set[str]]]:
    """Snapshot dirty set with per-project breakdown.

    Returns (all_ids, per_project_ids) where per_project_ids maps
    project_id -> set of cluster_ids. Clusters with project_id=None
    are grouped under "legacy".
    """
    snapshot = dict(self._dirty_set)
    self._dirty_set.clear()
    all_ids = set(snapshot.keys())
    by_project: dict[str, set[str]] = {}
    for cid, pid in snapshot.items():
        by_project.setdefault(pid or "legacy", set()).add(cid)
    return all_ids, by_project
```

The warm path in Phase 3A switches to calling `snapshot_dirty_set_with_projects()`. The old `snapshot_dirty_set()` remains available for backward compatibility and tests. It delegates:

```python
def snapshot_dirty_set(self) -> set[str]:
    """Snapshot and clear. Returns cluster IDs only (no project breakdown)."""
    all_ids, _ = self.snapshot_dirty_set_with_projects()
    return all_ids
```

**This preserves all existing Phase 1 tests** and Phase 2A code that calls `snapshot_dirty_set()`.

### Existing test compatibility

Phase 1 tests assert against `_dirty_set` internals:
- `assert len(engine._dirty_set) == 0` — works with dict (len of dict)
- `assert "cluster-1" in engine._dirty_set` — works with dict (checks keys)
- `assert engine._dirty_set == {"cluster-3"}` — **BREAKS** (dict != set)

Fix: Update `test_warm_path_dirty.py` to use `set(engine._dirty_set.keys())` for the one assertion that compares against a set literal. All other assertions use `len()` or `in` which work unchanged.

`test_dirty_set.py` line 40: `assert snapshot == {"cluster-1", "cluster-2"}` — still works because `snapshot_dirty_set()` returns `set[str]` (backward-compatible wrapper).

## 3. Mode Decision

### SchedulerDecision dataclass

```python
@dataclass
class SchedulerDecision:
    mode: str  # "all_dirty" | "round_robin"
    project_id: str | None = None  # which project to process (round-robin only)
    scoped_dirty_ids: set[str] | None = None  # filtered dirty_ids (round-robin only)

    @property
    def is_round_robin(self) -> bool:
        return self.mode == "round_robin"
```

Note: `is_round_robin` is a **property only**, not a dataclass field. No duplication.

### decide_mode()

```python
def decide_mode(
    self, dirty_ids: set[str] | None, dirty_by_project: dict[str, set[str]] | None = None,
) -> SchedulerDecision:
    """Decide scheduling mode for this warm cycle.

    Args:
        dirty_ids: All dirty cluster IDs (None = full scan).
        dirty_by_project: Per-project breakdown (None = no project info).
    """
    if dirty_ids is None:
        return SchedulerDecision("all_dirty")

    boundary = self._compute_boundary()
    if len(dirty_ids) <= boundary:
        return SchedulerDecision("all_dirty")

    if not dirty_by_project:
        return SchedulerDecision("all_dirty")  # no project info, can't round-robin

    # Round-robin: pick highest-priority project
    project_id, scoped = self._pick_priority_project(dirty_by_project)
    return SchedulerDecision("round_robin", project_id, scoped)
```

`dirty_by_project` is passed as an explicit parameter, not set via attribute assignment. This eliminates the hidden coupling issue.

### Warm path integration

```python
# In execute_warm_path(), replace Phase 1 snapshot logic:
if engine.is_first_warm_cycle():
    dirty_ids = None
    dirty_by_project = None
else:
    dirty_ids, dirty_by_project = engine.snapshot_dirty_set_with_projects()
    if not dirty_ids:
        dirty_ids = None
        dirty_by_project = None

# Phase 3A: scheduling mode decision
mode = engine._scheduler.decide_mode(dirty_ids, dirty_by_project)
if mode.is_round_robin:
    dirty_ids = mode.scoped_dirty_ids
    logger.info(
        "Warm path: round-robin mode, project='%s' (%d dirty)",
        mode.project_id, len(dirty_ids) if dirty_ids else 0,
    )
else:
    logger.info(
        "Warm path: all-dirty mode (%s dirty)",
        len(dirty_ids) if dirty_ids is not None else "all",
    )
```

## 4. Priority Selection with Starvation Guard

Single implementation (no duplicate function definitions):

```python
def _pick_priority_project(
    self, dirty_by_project: dict[str, set[str]],
) -> tuple[str, set[str]]:
    """Pick the project to process. Starved projects get priority."""
    # Check for starved projects first (skipped >= 3 consecutive cycles)
    for pid, count in self._skip_counts.items():
        if count >= self._STARVATION_LIMIT and pid in dirty_by_project:
            self._skip_counts[pid] = 0
            # Update skip counts for others
            for other_pid in dirty_by_project:
                if other_pid != pid:
                    self._skip_counts[other_pid] = self._skip_counts.get(other_pid, 0) + 1
            return (pid, dirty_by_project[pid])

    # Normal priority: most dirty clusters
    ranked = sorted(
        dirty_by_project.items(),
        key=lambda kv: len(kv[1]),
        reverse=True,
    )
    chosen_pid = ranked[0][0]

    # Update skip counts
    for pid in dirty_by_project:
        if pid == chosen_pid:
            self._skip_counts[pid] = 0
        else:
            self._skip_counts[pid] = self._skip_counts.get(pid, 0) + 1

    return (chosen_pid, dirty_by_project[chosen_pid])
```

**Tiebreaker for multiple starved projects:** `self._skip_counts` is iterated in insertion order. The first starved project found wins. This is deterministic but arbitrary. For better fairness, sort starved projects by skip count descending (longest-starved first):

```python
starved = [(pid, self._skip_counts[pid]) for pid in dirty_by_project
           if self._skip_counts.get(pid, 0) >= self._STARVATION_LIMIT]
if starved:
    starved.sort(key=lambda x: -x[1])  # longest-starved first
    pid = starved[0][0]
    ...
```

### New constant

```python
_STARVATION_LIMIT: int = 3  # max consecutive skipped cycles before force-include
```

## 5. Non-Processed Dirty Clusters

Clusters not processed in round-robin mode stay dirty for the next cycle:

```python
# After warm cycle completes in round-robin mode
if mode.is_round_robin and dirty_by_project:
    for pid, cids in dirty_by_project.items():
        if pid != mode.project_id:
            for cid in cids:
                engine.mark_dirty(cid, project_id=pid)
```

This re-injects unprocessed clusters. New clusters marked dirty during the cycle (from concurrent hot-path activity) are already in the live `_dirty_set` — the re-injection simply adds the skipped ones alongside them. Dict semantics prevent duplicates (same key = last write wins, but the value is the same project_id).

## 6. Relationship to Existing Systems

### pattern_stale flag

The per-project dirty tracking (`dict[str, str|None]`) is separate from the existing `pattern_stale` flag in `cluster_metadata`. `pattern_stale` controls pattern extraction in Phase 4 (Refresh). The dirty set controls split/merge/retire scoping in Phases 1-3. These are independent systems.

### Error recovery

If a round-robin cycle crashes midway, the speculative transaction rollback restores DB state (existing behavior). The processed project's clusters are no longer in the dirty set (snapshot already cleared them). They will be re-dirtied naturally by subsequent hot-path activity. Non-processed projects' clusters are re-injected in the finally block.

## 7. Observability

### Scheduler snapshot

```python
def snapshot(self) -> dict:
    return {
        "target_cycle_ms": self._target_cycle_ms,
        "boundary": self._compute_boundary(),
        "window_size": len(self._window),
        "mode": self._last_mode,  # "all_dirty" | "round_robin"
        "bootstrapping": len(self._window) < self._WINDOW_SIZE,
        "skip_counts": dict(self._skip_counts),
        "last_project_id": self._last_project_id,
        "dirty_by_project_counts": {
            pid: len(cids) for pid, cids in (self._last_dirty_by_project or {}).items()
        },
    }
```

Add `self._last_mode`, `self._last_project_id`, `self._last_dirty_by_project` to `AdaptiveScheduler.__init__()`.

### Warm path log

Each cycle logs: mode decision, chosen project (if round-robin), dirty counts per project, boundary value.

## 8. AdaptiveScheduler __init__ additions

```python
def __init__(self) -> None:
    self._window: list[WarmCycleMeasurement] = []
    self._target_cycle_ms: int = self._BOOTSTRAP_TARGET_MS
    self._skip_counts: dict[str, int] = {}
    self._last_mode: str = "all_dirty"
    self._last_project_id: str | None = None
    self._last_dirty_by_project: dict[str, set[str]] | None = None
```

## 9. Validation

### Seed targets
- 2K+ optimizations across 3 projects.
- Run enough warm cycles to exit bootstrap (10+).
- Inflate dirty set beyond boundary to trigger round-robin.

### Assertions
- Bootstrap phase: all-dirty mode for first 10 cycles.
- After bootstrap: boundary computed from regression (verify reasonable value, not clamped to 1).
- When dirty_count <= boundary: all-dirty mode.
- When dirty_count > boundary: round-robin mode, processes highest-priority project.
- Starvation guard: project skipped 3 times gets forced in on cycle 4.
- Tiebreaker: longest-starved project wins among multiple starved.
- Non-processed dirty clusters persist to next cycle (no silent drops).
- Per-project Q metrics: regression in processed project doesn't affect others.
- `snapshot_dirty_set()` backward compat: returns `set[str]`, existing tests pass.
- `snapshot_dirty_set_with_projects()` returns correct tuple.

### Test files
- `tests/taxonomy/test_scheduler_regression.py` — boundary computation, edge cases, degenerate data
- `tests/taxonomy/test_scheduler_mode_decision.py` — mode selection, boundary crossing
- `tests/taxonomy/test_scheduler_round_robin.py` — priority selection, starvation guard, tiebreaker
- `tests/taxonomy/test_dirty_set_per_project.py` — dict-based tracking, snapshot compat, re-injection
