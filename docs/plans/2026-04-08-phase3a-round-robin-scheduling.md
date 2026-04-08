# Phase 3A: Round-Robin Warm Scheduling — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend AdaptiveScheduler from measurement-only to active scheduling. When dirty count exceeds a self-tuning boundary (linear regression), switch to round-robin mode (one project per cycle). Starvation guard prevents project neglect.

**Architecture:** `_dirty_set` changes from `set[str]` to `dict[str, str|None]` for per-project tracking. New `snapshot_dirty_set_with_projects()` method (backward-compatible wrapper preserves old `snapshot_dirty_set()`). `SchedulerDecision` dataclass. `_compute_boundary()` linear regression. `_pick_priority_project()` with starvation guard. Non-processed clusters re-injected after round-robin cycles.

**Tech Stack:** Python 3.12, statistics stdlib, pytest

**Spec:** `docs/specs/2026-04-08-phase3a-round-robin-scheduling.md`

---

### Task 1: Change _dirty_set to dict + backward-compatible snapshot

**Files:**
- Modify: `backend/app/services/taxonomy/engine.py` (_dirty_set, mark_dirty, snapshot methods)
- Modify: `backend/tests/taxonomy/test_warm_path_dirty.py` (fix assertion)
- Test: `backend/tests/taxonomy/test_dirty_set_per_project.py` (create)

- [ ] **Step 1: Write tests for dict-based dirty set**

```python
# backend/tests/taxonomy/test_dirty_set_per_project.py
"""Tests for per-project dirty-set tracking (Phase 3A)."""

import pytest
from unittest.mock import MagicMock

from app.services.taxonomy.engine import TaxonomyEngine


@pytest.fixture
def engine():
    mock_embedding = MagicMock()
    mock_provider = MagicMock()
    return TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)


class TestDirtySetDict:
    def test_mark_dirty_with_project(self, engine):
        engine.mark_dirty("c1", project_id="proj-A")
        assert engine._dirty_set == {"c1": "proj-A"}

    def test_mark_dirty_without_project_defaults_none(self, engine):
        engine.mark_dirty("c1")
        assert engine._dirty_set == {"c1": None}

    def test_snapshot_with_projects(self, engine):
        engine.mark_dirty("c1", project_id="proj-A")
        engine.mark_dirty("c2", project_id="proj-B")
        engine.mark_dirty("c3", project_id="proj-A")

        all_ids, by_project = engine.snapshot_dirty_set_with_projects()
        assert all_ids == {"c1", "c2", "c3"}
        assert by_project["proj-A"] == {"c1", "c3"}
        assert by_project["proj-B"] == {"c2"}
        assert len(engine._dirty_set) == 0  # cleared

    def test_snapshot_none_project_grouped_as_legacy(self, engine):
        engine.mark_dirty("c1")  # no project
        _, by_project = engine.snapshot_dirty_set_with_projects()
        assert "legacy" in by_project
        assert "c1" in by_project["legacy"]

    def test_backward_compat_snapshot(self, engine):
        """Old snapshot_dirty_set() still returns set[str]."""
        engine.mark_dirty("c1", project_id="proj-A")
        engine.mark_dirty("c2", project_id="proj-B")
        result = engine.snapshot_dirty_set()
        assert isinstance(result, set)
        assert result == {"c1", "c2"}
```

- [ ] **Step 2: Change _dirty_set type and methods**

In `engine.py`, change `__init__`:
```python
self._dirty_set: dict[str, str | None] = {}
```

Change `mark_dirty`:
```python
def mark_dirty(self, cluster_id: str, project_id: str | None = None) -> None:
    self._dirty_set[cluster_id] = project_id
```

Add `snapshot_dirty_set_with_projects`:
```python
def snapshot_dirty_set_with_projects(self) -> tuple[set[str], dict[str, set[str]]]:
    snapshot = dict(self._dirty_set)
    self._dirty_set.clear()
    all_ids = set(snapshot.keys())
    by_project: dict[str, set[str]] = {}
    for cid, pid in snapshot.items():
        by_project.setdefault(pid or "legacy", set()).add(cid)
    return all_ids, by_project
```

Update `snapshot_dirty_set` as backward-compatible wrapper:
```python
def snapshot_dirty_set(self) -> set[str]:
    all_ids, _ = self.snapshot_dirty_set_with_projects()
    return all_ids
```

- [ ] **Step 3: Fix test_warm_path_dirty.py assertion**

Change `assert engine._dirty_set == {"cluster-3"}` to `assert set(engine._dirty_set.keys()) == {"cluster-3"}`.

- [ ] **Step 4: Run tests, commit**

```bash
pytest tests/taxonomy/test_dirty_set_per_project.py tests/taxonomy/test_dirty_set.py tests/taxonomy/test_warm_path_dirty.py -v
pytest --tb=short -q
git add backend/app/services/taxonomy/engine.py backend/tests/
git commit -m "feat(taxonomy): Phase 3A per-project dirty tracking (dict-based, backward compatible)"
```

---

### Task 2: SchedulerDecision + _compute_boundary

**Files:**
- Modify: `backend/app/services/taxonomy/engine.py` (AdaptiveScheduler)
- Test: `backend/tests/taxonomy/test_scheduler_regression.py` (create)

- [ ] **Step 1: Write boundary computation tests**

```python
# backend/tests/taxonomy/test_scheduler_regression.py
"""Tests for AdaptiveScheduler boundary computation (Phase 3A)."""

import pytest
from app.services.taxonomy.engine import AdaptiveScheduler


class TestBoundaryComputation:
    def test_bootstrap_returns_default(self):
        scheduler = AdaptiveScheduler()
        assert scheduler._compute_boundary() == 20  # _BOOTSTRAP_BOUNDARY

    def test_boundary_after_bootstrap(self):
        scheduler = AdaptiveScheduler()
        # Feed 10 measurements with linear relationship
        for i in range(10):
            scheduler.record(dirty_count=10 + i * 5, duration_ms=1000 + i * 500)
        boundary = scheduler._compute_boundary()
        assert boundary > 0
        assert boundary < 999

    def test_negative_slope_returns_high(self):
        scheduler = AdaptiveScheduler()
        # Duration decreases as dirty count increases (degenerate)
        for i in range(10):
            scheduler.record(dirty_count=10 + i * 5, duration_ms=5000 - i * 200)
        assert scheduler._compute_boundary() == 999

    def test_degenerate_data_returns_default(self):
        scheduler = AdaptiveScheduler()
        # All same dirty count (zero variance)
        for _ in range(10):
            scheduler.record(dirty_count=50, duration_ms=3000)
        assert scheduler._compute_boundary() == 20  # fallback
```

- [ ] **Step 2: Implement SchedulerDecision + _compute_boundary**

Add `SchedulerDecision` dataclass and `_compute_boundary()`, `decide_mode()`, `_pick_priority_project()` to AdaptiveScheduler. Add `_BOOTSTRAP_BOUNDARY = 20`, `_STARVATION_LIMIT = 3`, `_skip_counts`, `_last_mode`, `_last_project_id`, `_last_dirty_by_project` to `__init__`. Full code in spec Section 1-4.

- [ ] **Step 3: Run tests, commit**

```bash
pytest tests/taxonomy/test_scheduler_regression.py -v
pytest --tb=short -q
git add backend/app/services/taxonomy/engine.py backend/tests/taxonomy/test_scheduler_regression.py
git commit -m "feat(taxonomy): Phase 3A SchedulerDecision + boundary computation"
```

---

### Task 3: Mode decision + priority selection + starvation guard

**Files:**
- Modify: `backend/app/services/taxonomy/engine.py` (decide_mode, _pick_priority_project)
- Test: `backend/tests/taxonomy/test_scheduler_mode_decision.py` (create)
- Test: `backend/tests/taxonomy/test_scheduler_round_robin.py` (create)

- [ ] **Step 1: Write mode decision and round-robin tests**

Tests covering: all-dirty when below boundary, round-robin when above, priority = most dirty clusters, starvation guard at 3 skips, tiebreaker longest-starved.

- [ ] **Step 2: Implement decide_mode + _pick_priority_project**

Per spec Sections 3-4. `decide_mode` takes `dirty_ids` and `dirty_by_project` as explicit parameters (no hidden coupling).

- [ ] **Step 3: Run tests, commit**

```bash
pytest tests/taxonomy/test_scheduler_mode_decision.py tests/taxonomy/test_scheduler_round_robin.py -v
pytest --tb=short -q
git add backend/app/services/taxonomy/engine.py backend/tests/taxonomy/
git commit -m "feat(taxonomy): Phase 3A mode decision + round-robin priority + starvation guard"
```

---

### Task 4: Wire into warm_path.py + re-injection

**Files:**
- Modify: `backend/app/services/taxonomy/warm_path.py` (snapshot, mode decision, re-injection)

- [ ] **Step 1: Replace snapshot logic in execute_warm_path**

Replace the Phase 1 snapshot block with Phase 3A version per spec Section 3 "Warm path integration".

- [ ] **Step 2: Add re-injection after round-robin cycle**

Per spec Section 5 — re-inject non-processed dirty clusters.

- [ ] **Step 3: Update mark_dirty call sites with project_id**

Update the 6 call sites in engine.py and warm_phases.py to pass `project_id` per spec Section 2 table.

- [ ] **Step 4: Run full test suite, commit**

```bash
pytest --tb=short -q
git add backend/app/services/taxonomy/warm_path.py backend/app/services/taxonomy/warm_phases.py backend/app/services/taxonomy/engine.py
git commit -m "feat(taxonomy): Phase 3A wire round-robin into warm path + dirty re-injection"
```

---

### Task 5: Updated scheduler snapshot + observability

**Files:**
- Modify: `backend/app/services/taxonomy/engine.py` (snapshot method)

- [ ] **Step 1: Update snapshot() with Phase 3A fields**

Per spec Section 7 — add boundary, mode, skip_counts, last_project_id, dirty_by_project_counts.

- [ ] **Step 2: Run tests, commit**

```bash
pytest tests/taxonomy/test_adaptive_scheduler.py -v
pytest --tb=short -q
git add backend/app/services/taxonomy/engine.py
git commit -m "feat(taxonomy): Phase 3A scheduler observability snapshot"
```

---

### Task 6: E2E validation

- [ ] **Step 1: Restart, run full tests**

```bash
./init.sh restart
cd backend && source .venv/bin/activate && pytest --tb=short -q
```

- [ ] **Step 2: Verify scheduler in logs**

```bash
sleep 30 && grep -i "round.robin\|all.dirty\|boundary\|scheduler" data/backend.log | tail -10
```

- [ ] **Step 3: Commit if fixes needed**
