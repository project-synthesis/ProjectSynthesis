# Warm Path Maintenance Decoupling Implementation Plan

**Status:** Shipped. Two warm-path execution groups: lifecycle (Phases 0–4, dirty-cluster-gated) + maintenance (Phases 5–6, cadence-gated via `MAINTENANCE_CYCLE_INTERVAL=6` + `_maintenance_pending` retry flag). `execute_maintenance_phases()` runs independently of dirty clusters; each sub-step in Phase 4.5 wraps `begin_nested()` so transient failures don't poison the transaction. Historical record.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Decouple maintenance phases (discovery, archival, audit) from the dirty-cluster gate so they run on their own cadence, surviving transient errors and idle periods.

**Architecture:** Split `execute_warm_path()` into two execution groups: **lifecycle** (Phases 0–4, dirty-cluster-gated) and **maintenance** (Phases 5–6, cadence-gated with retry). The lifecycle group keeps the existing early-exit optimization — no dirty clusters means no speculative work. The maintenance group runs independently on a periodic cadence (every Nth warm cycle) or immediately when a prior attempt failed. Phase 5 gains try/except with a retry flag.

**Tech Stack:** Python 3.12, SQLAlchemy async, pytest, existing `TaxonomyEngine` singleton pattern

---

## File Structure

| File | Change | Responsibility |
|------|--------|---------------|
| `backend/app/services/taxonomy/_constants.py` | Modify | Add `MAINTENANCE_CYCLE_INTERVAL` constant |
| `backend/app/services/taxonomy/engine.py` | Modify | Add `_maintenance_pending` flag |
| `backend/app/services/taxonomy/warm_path.py` | Modify | Restructure `execute_warm_path()` into two groups, add `execute_maintenance_phases()` |
| `backend/app/main.py` | Modify | Call maintenance after lifecycle-skipped cycles |
| `backend/tests/taxonomy/test_warm_path.py` | Modify | Update phase-order test, add maintenance-only tests |
| `backend/tests/taxonomy/test_warm_path_dirty.py` | Modify | Add test: idle cycle still runs maintenance |

---

### Task 1: Add MAINTENANCE_CYCLE_INTERVAL constant

**Files:**
- Modify: `backend/app/services/taxonomy/_constants.py:104-121`

- [ ] **Step 1: Add the constant**

Add below the sub-domain discovery constants block (after line 120):

```python
# ---------------------------------------------------------------------------
# Maintenance phase cadence
# ---------------------------------------------------------------------------
# Maintenance phases (discover, archive, audit) run independently of the
# dirty-cluster gate on this cadence.  Every Nth warm cycle, maintenance
# runs even when no clusters were modified.  Retries after transient
# failure are immediate (next cycle), bypassing this cadence.
MAINTENANCE_CYCLE_INTERVAL: int = 6  # ~30 min at default 5-min warm interval
```

- [ ] **Step 2: Verify import works**

Run: `cd backend && source .venv/bin/activate && python -c "from app.services.taxonomy._constants import MAINTENANCE_CYCLE_INTERVAL; print(MAINTENANCE_CYCLE_INTERVAL)"`
Expected: `6`

- [ ] **Step 3: Commit**

```bash
git add backend/app/services/taxonomy/_constants.py
git commit -m "feat(taxonomy): add MAINTENANCE_CYCLE_INTERVAL constant for decoupled discovery cadence"
```

---

### Task 2: Add _maintenance_pending flag to TaxonomyEngine

**Files:**
- Modify: `backend/app/services/taxonomy/engine.py:345-365`

- [ ] **Step 1: Write the failing test**

Create test in `backend/tests/taxonomy/test_warm_path_dirty.py` — append after existing tests:

```python
@pytest.mark.asyncio
async def test_maintenance_pending_flag_lifecycle():
    """Engine._maintenance_pending starts False, can be set and cleared."""
    from app.services.taxonomy.engine import TaxonomyEngine

    mock_embedding = MagicMock()
    mock_provider = MagicMock()
    engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)

    # Starts False
    assert engine._maintenance_pending is False

    # Can be set
    engine._maintenance_pending = True
    assert engine._maintenance_pending is True

    # Can be cleared
    engine._maintenance_pending = False
    assert engine._maintenance_pending is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/taxonomy/test_warm_path_dirty.py::test_maintenance_pending_flag_lifecycle -v`
Expected: FAIL with `AttributeError: 'TaxonomyEngine' object has no attribute '_maintenance_pending'`

- [ ] **Step 3: Add the flag to engine __init__**

In `engine.py`, in the `__init__` method, after line 364 (`self._last_global_pattern_check: float = 0.0`), add:

```python
        # Maintenance retry flag — set True when Phase 5 (discover) fails
        # with a transient error.  Causes the next idle warm cycle to run
        # maintenance phases regardless of the periodic cadence gate.
        self._maintenance_pending: bool = False
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/taxonomy/test_warm_path_dirty.py::test_maintenance_pending_flag_lifecycle -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/taxonomy/engine.py backend/tests/taxonomy/test_warm_path_dirty.py
git commit -m "feat(taxonomy): add _maintenance_pending flag for discovery retry"
```

---

### Task 3: Extract execute_maintenance_phases() function

**Files:**
- Modify: `backend/app/services/taxonomy/warm_path.py:671-727`
- Test: `backend/tests/taxonomy/test_warm_path.py`

- [ ] **Step 1: Write the failing test for the new function**

Add to `backend/tests/taxonomy/test_warm_path.py` after the existing imports:

```python
from app.services.taxonomy.warm_path import execute_maintenance_phases
```

Then add the test:

```python
@pytest.mark.asyncio
async def test_execute_maintenance_phases_calls_discover_and_audit(db, mock_embedding, mock_provider):
    """execute_maintenance_phases runs discover, archive, and audit in order."""
    from app.services.taxonomy.engine import TaxonomyEngine

    engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)
    call_order: list[str] = []

    async def fake_discover(eng, session):
        call_order.append("discover")
        from app.services.taxonomy.warm_phases import DiscoverResult
        return DiscoverResult()

    async def fake_archive(eng, session):
        call_order.append("archive")
        return 0

    async def fake_audit(eng, session, phase_results, q_baseline):
        call_order.append("audit")
        from app.services.taxonomy.warm_phases import AuditResult
        return AuditResult(snapshot_id="maint-snap", q_final=0.5)

    @asynccontextmanager
    async def session_factory():
        yield db

    with (
        patch("app.services.taxonomy.warm_path.phase_discover", fake_discover),
        patch("app.services.taxonomy.warm_path.phase_archive_empty_sub_domains", fake_archive),
        patch("app.services.taxonomy.warm_path.phase_audit", fake_audit),
    ):
        result = await execute_maintenance_phases(engine, session_factory)

    assert call_order == ["discover", "archive", "audit"]
    assert result.snapshot_id == "maint-snap"
    # Maintenance-only cycles have no speculative phases
    assert result.operations_attempted == 0
    assert result.operations_accepted == 0


@pytest.mark.asyncio
async def test_execute_maintenance_phases_sets_retry_on_discover_failure(
    db, mock_embedding, mock_provider
):
    """When phase_discover raises, _maintenance_pending is set for retry."""
    from app.services.taxonomy.engine import TaxonomyEngine

    engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)
    assert engine._maintenance_pending is False

    async def failing_discover(eng, session):
        raise Exception("database is locked")

    async def fake_archive(eng, session):
        return 0

    async def fake_audit(eng, session, phase_results, q_baseline):
        from app.services.taxonomy.warm_phases import AuditResult
        return AuditResult(snapshot_id="maint-fail", q_final=0.5)

    @asynccontextmanager
    async def session_factory():
        yield db

    with (
        patch("app.services.taxonomy.warm_path.phase_discover", failing_discover),
        patch("app.services.taxonomy.warm_path.phase_archive_empty_sub_domains", fake_archive),
        patch("app.services.taxonomy.warm_path.phase_audit", fake_audit),
    ):
        result = await execute_maintenance_phases(engine, session_factory)

    # Retry flag set after discovery failure
    assert engine._maintenance_pending is True
    # Audit still ran despite discover failure
    assert result.snapshot_id == "maint-fail"


@pytest.mark.asyncio
async def test_execute_maintenance_phases_clears_retry_on_success(
    db, mock_embedding, mock_provider
):
    """Successful discovery clears the _maintenance_pending flag."""
    from app.services.taxonomy.engine import TaxonomyEngine

    engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)
    engine._maintenance_pending = True  # Simulate prior failure

    async def ok_discover(eng, session):
        from app.services.taxonomy.warm_phases import DiscoverResult
        return DiscoverResult()

    async def fake_archive(eng, session):
        return 0

    async def fake_audit(eng, session, phase_results, q_baseline):
        from app.services.taxonomy.warm_phases import AuditResult
        return AuditResult(snapshot_id="maint-ok", q_final=0.5)

    @asynccontextmanager
    async def session_factory():
        yield db

    with (
        patch("app.services.taxonomy.warm_path.phase_discover", ok_discover),
        patch("app.services.taxonomy.warm_path.phase_archive_empty_sub_domains", fake_archive),
        patch("app.services.taxonomy.warm_path.phase_audit", fake_audit),
    ):
        result = await execute_maintenance_phases(engine, session_factory)

    assert engine._maintenance_pending is False
    assert result.snapshot_id == "maint-ok"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && pytest tests/taxonomy/test_warm_path.py::test_execute_maintenance_phases_calls_discover_and_audit tests/taxonomy/test_warm_path.py::test_execute_maintenance_phases_sets_retry_on_discover_failure tests/taxonomy/test_warm_path.py::test_execute_maintenance_phases_clears_retry_on_success -v`
Expected: FAIL with `ImportError: cannot import name 'execute_maintenance_phases'`

- [ ] **Step 3: Implement execute_maintenance_phases()**

In `warm_path.py`, first add `phase_archive_empty_sub_domains` to the top-level imports (line 33-44 area) — it's currently imported inline, but we need it at module level so tests can mock it:

```python
from app.services.taxonomy.warm_phases import (
    PhaseResult,
    _record_domain_split_block,
    phase_archive_empty_sub_domains,
    phase_audit,
    phase_discover,
    phase_evaluate_candidates,
    phase_merge,
    phase_reconcile,
    phase_refresh,
    phase_retire,
    phase_split_emerge,
)
```

Then add the new function before `execute_warm_path()` (around line 395):

```python
async def execute_maintenance_phases(
    engine: TaxonomyEngine,
    session_factory: SessionFactory,
    phase_results: list[PhaseResult] | None = None,
    q_baseline: float | None = None,
) -> WarmPathResult:
    """Run maintenance phases independently of the dirty-cluster lifecycle.

    Phases: 5 (Discover), 5.5 (Archive sub-domains), 6 (Audit).
    These phases scan the complete taxonomy state and do not depend on
    dirty clusters.  Phase 5 is wrapped in try/except — on transient
    failure (e.g. SQLite lock), ``engine._maintenance_pending`` is set
    so the next warm cycle retries immediately.

    Args:
        engine: TaxonomyEngine instance.
        session_factory: Async context manager yielding fresh AsyncSession.
        phase_results: Speculative phase results from the lifecycle group
            (empty list if running maintenance-only on an idle cycle).
        q_baseline: Q baseline from lifecycle Phase 0 (None if idle cycle).

    Returns:
        WarmPathResult with audit snapshot.
    """
    if phase_results is None:
        phase_results = []

    # ------------------------------------------------------------------
    # Phase 5: Discover — fresh session, always commits
    # ADR-005: Full scan — domain discovery needs complete cluster state
    # ------------------------------------------------------------------
    try:
        async with session_factory() as db:
            discover_result = await phase_discover(engine, db)
            await db.commit()
            logger.info(
                "Phase 5 (discover): domains=%d candidates=%d",
                discover_result.domains_created,
                discover_result.candidates_detected,
            )
            # Success — clear retry flag
            engine._maintenance_pending = False
    except Exception as discover_exc:
        logger.warning(
            "Phase 5 (discover) failed — will retry next cycle: %s",
            discover_exc,
        )
        engine._maintenance_pending = True
        try:
            get_event_logger().log_decision(
                path="warm", op="discover",
                decision="discover_failed_will_retry",
                context={"error": str(discover_exc)},
            )
        except RuntimeError:
            pass

    # ------------------------------------------------------------------
    # Phase 5.5: Archive empty sub-domains — fresh session, always commits
    # ------------------------------------------------------------------
    try:
        async with session_factory() as db:
            sub_domains_archived = await phase_archive_empty_sub_domains(engine, db)
            await db.commit()
            if sub_domains_archived:
                logger.info(
                    "Phase 5.5 (sub-domain cleanup): archived=%d",
                    sub_domains_archived,
                )
    except Exception as archive_exc:
        logger.warning("Phase 5.5 (archive sub-domains) failed (non-fatal): %s", archive_exc)

    # ------------------------------------------------------------------
    # Phase 6: Audit — fresh session, creates snapshot
    # ADR-005: Full scan — audit/snapshot needs complete cluster state
    # ------------------------------------------------------------------
    async with session_factory() as db:
        audit_result = await phase_audit(
            engine, db, phase_results, q_baseline,
        )
        await db.commit()
        logger.info(
            "Phase 6 (audit): snapshot=%s q_final=%.4f deadlock=%s",
            audit_result.snapshot_id,
            audit_result.q_final or 0.0,
            audit_result.deadlock_breaker_used,
        )

    # ------------------------------------------------------------------
    # Snapshot pruning — tiered retention policy
    # ------------------------------------------------------------------
    try:
        async with session_factory() as db:
            from app.services.taxonomy.snapshot import prune_snapshots
            pruned = await prune_snapshots(db)
            if pruned:
                logger.info("Pruned %d old snapshots via retention policy", pruned)
    except Exception as prune_exc:
        logger.warning("Snapshot pruning failed (non-fatal): %s", prune_exc)

    engine._invalidate_stats_cache()

    return WarmPathResult(
        snapshot_id=audit_result.snapshot_id,
        q_baseline=q_baseline,
        q_final=audit_result.q_final,
        phase_results=phase_results,
        operations_attempted=sum(pr.ops_attempted for pr in phase_results),
        operations_accepted=sum(pr.ops_accepted for pr in phase_results),
        deadlock_breaker_used=audit_result.deadlock_breaker_used,
        deadlock_breaker_phase=audit_result.deadlock_breaker_phase,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && pytest tests/taxonomy/test_warm_path.py::test_execute_maintenance_phases_calls_discover_and_audit tests/taxonomy/test_warm_path.py::test_execute_maintenance_phases_sets_retry_on_discover_failure tests/taxonomy/test_warm_path.py::test_execute_maintenance_phases_clears_retry_on_success -v`
Expected: All 3 PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/taxonomy/warm_path.py backend/tests/taxonomy/test_warm_path.py
git commit -m "feat(taxonomy): extract execute_maintenance_phases() with discovery retry"
```

---

### Task 4: Restructure execute_warm_path() to delegate to maintenance

**Files:**
- Modify: `backend/app/services/taxonomy/warm_path.py:399-790`
- Test: `backend/tests/taxonomy/test_warm_path.py`

- [ ] **Step 1: Update the phase-order test**

Update `test_execute_warm_path_phases_called_in_order` — add the `phase_archive_empty_sub_domains` mock and verify the new delegation:

```python
@pytest.mark.asyncio
async def test_execute_warm_path_phases_called_in_order(db, mock_embedding, mock_provider):
    """execute_warm_path calls phases in the correct order (0→6)."""
    from app.services.taxonomy.engine import TaxonomyEngine

    engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)

    call_order: list[str] = []

    async def fake_reconcile(eng, session):
        call_order.append("reconcile")
        from app.services.taxonomy.warm_phases import ReconcileResult
        return ReconcileResult()

    async def fake_split_emerge(eng, session, split_protected_ids, dirty_ids=None):
        call_order.append("split_emerge")
        return _make_phase_result("split_emerge")

    async def fake_merge(eng, session, split_protected_ids, dirty_ids=None):
        call_order.append("merge")
        return _make_phase_result("merge")

    async def fake_retire(eng, session):
        call_order.append("retire")
        return _make_phase_result("retire")

    async def fake_refresh(eng, session):
        call_order.append("refresh")
        from app.services.taxonomy.warm_phases import RefreshResult
        return RefreshResult()

    async def fake_discover(eng, session):
        call_order.append("discover")
        from app.services.taxonomy.warm_phases import DiscoverResult
        return DiscoverResult()

    async def fake_archive_sub_domains(eng, session):
        call_order.append("archive_sub_domains")
        return 0

    async def fake_audit(eng, session, phase_results, q_baseline):
        call_order.append("audit")
        from app.services.taxonomy.warm_phases import AuditResult
        return AuditResult(snapshot_id="snap-test", q_final=0.5)

    @asynccontextmanager
    async def session_factory():
        yield db

    with (
        patch("app.services.taxonomy.warm_path.phase_reconcile", fake_reconcile),
        patch("app.services.taxonomy.warm_path.phase_split_emerge", fake_split_emerge),
        patch("app.services.taxonomy.warm_path.phase_merge", fake_merge),
        patch("app.services.taxonomy.warm_path.phase_retire", fake_retire),
        patch("app.services.taxonomy.warm_path.phase_refresh", fake_refresh),
        patch("app.services.taxonomy.warm_path.phase_discover", fake_discover),
        patch("app.services.taxonomy.warm_path.phase_archive_empty_sub_domains", fake_archive_sub_domains),
        patch("app.services.taxonomy.warm_path.phase_audit", fake_audit),
    ):
        result = await execute_warm_path(engine, session_factory)

    assert call_order == [
        "reconcile",
        "split_emerge",
        "merge",
        "retire",
        "refresh",
        "discover",
        "archive_sub_domains",
        "audit",
    ], f"Phase order wrong: {call_order}"
    assert result.snapshot_id == "snap-test"
```

- [ ] **Step 2: Run to verify it fails with current code**

Run: `cd backend && pytest tests/taxonomy/test_warm_path.py::test_execute_warm_path_phases_called_in_order -v`
Expected: FAIL — the current code doesn't mock `phase_archive_empty_sub_domains` at the top level (it's imported inline), and the order assertion now includes `archive_sub_domains`.

- [ ] **Step 3: Refactor execute_warm_path()**

Replace the Phase 5, 5.5, 6, and snapshot pruning blocks (lines 671–734) AND the finalize/return block (lines 734–790) in `execute_warm_path()` with a delegation to `execute_maintenance_phases()`. The function should end like this after the Phase 4.75 block:

```python
    # ------------------------------------------------------------------
    # Maintenance group: Phases 5, 5.5, 6 + snapshot pruning
    # Delegated to execute_maintenance_phases() which handles discovery
    # retry and error isolation independently.
    # ------------------------------------------------------------------
    maint_result = await execute_maintenance_phases(
        engine, session_factory,
        phase_results=all_phase_results,
        q_baseline=q_baseline,
    )

    # Merge audit-level deadlock info with per-phase deadlock info
    final_deadlock_used = deadlock_used or maint_result.deadlock_breaker_used
    final_deadlock_phase = deadlock_phase or maint_result.deadlock_breaker_phase

    # ADR-005: Record cycle measurement for adaptive scheduling.
    _cycle_duration_ms = int((_time.monotonic() - _cycle_start) * 1000)
    if _total_dirty_count is not None:
        engine._scheduler.record(
            dirty_count=_total_dirty_count,
            duration_ms=_cycle_duration_ms,
        )
    logger.debug(
        "Warm cycle measurement recorded: duration_ms=%d dirty_count=%s scheduler=%s",
        _cycle_duration_ms,
        len(dirty_ids) if dirty_ids is not None else "all",
        engine._scheduler.snapshot(),
    )

    # ADR-005 Phase 3A: re-inject non-processed dirty clusters after budget allocation
    if mode.is_round_robin and dirty_by_project:
        _processed = mode.scoped_dirty_ids or set()
        _reinjected = 0
        _reinjected_projects = 0
        for pid, cids in dirty_by_project.items():
            remaining = cids - _processed
            if remaining:
                _reinjected_projects += 1
                raw_pid = None if pid == "legacy" else pid
                for cid in remaining:
                    engine.mark_dirty(cid, project_id=raw_pid)
                _reinjected += len(remaining)
        if _reinjected:
            logger.info(
                "Warm path: re-injected %d dirty clusters from %d projects",
                _reinjected,
                _reinjected_projects,
            )

    return WarmPathResult(
        snapshot_id=maint_result.snapshot_id,
        q_baseline=q_baseline,
        q_final=maint_result.q_final,
        phase_results=all_phase_results,
        operations_attempted=sum(pr.ops_attempted for pr in all_phase_results),
        operations_accepted=sum(pr.ops_accepted for pr in all_phase_results),
        deadlock_breaker_used=final_deadlock_used,
        deadlock_breaker_phase=final_deadlock_phase,
    )
```

The top-level import for `phase_archive_empty_sub_domains` was already added in Task 3.

- [ ] **Step 4: Run updated test**

Run: `cd backend && pytest tests/taxonomy/test_warm_path.py::test_execute_warm_path_phases_called_in_order -v`
Expected: PASS

- [ ] **Step 5: Run all warm path tests**

Run: `cd backend && pytest tests/taxonomy/test_warm_path.py tests/taxonomy/test_warm_path_dirty.py -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/taxonomy/warm_path.py backend/tests/taxonomy/test_warm_path.py
git commit -m "refactor(taxonomy): delegate maintenance phases from execute_warm_path()"
```

---

### Task 5: Add maintenance-on-idle to the no-dirty-clusters path

**Files:**
- Modify: `backend/app/services/taxonomy/warm_path.py:428-451`
- Test: `backend/tests/taxonomy/test_warm_path_dirty.py`

This is the core fix: when no dirty clusters exist, the warm path now checks whether maintenance should run (cadence gate OR retry flag).

- [ ] **Step 1: Write the failing test — idle cycle runs maintenance on cadence**

Add these imports to the top of `backend/tests/taxonomy/test_warm_path_dirty.py`, merging with the existing `from unittest.mock import MagicMock` line:

```python
from contextlib import asynccontextmanager
from unittest.mock import MagicMock, patch  # add 'patch' to existing MagicMock import
```

Then append the tests:

```python
@pytest.mark.asyncio
async def test_idle_cycle_runs_maintenance_on_cadence(db):
    """When no dirty clusters exist, maintenance runs every MAINTENANCE_CYCLE_INTERVAL cycles."""
    from app.services.taxonomy._constants import MAINTENANCE_CYCLE_INTERVAL
    from app.services.taxonomy.engine import TaxonomyEngine
    from app.services.taxonomy.warm_path import execute_warm_path

    mock_embedding = MagicMock()
    mock_provider = MagicMock()
    engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)

    # Simulate age at the cadence boundary
    engine._warm_path_age = MAINTENANCE_CYCLE_INTERVAL

    maintenance_called = []

    async def fake_maintenance(eng, sf, phase_results=None, q_baseline=None):
        maintenance_called.append(True)
        from app.services.taxonomy.warm_path import WarmPathResult
        return WarmPathResult(
            snapshot_id="maint-idle",
            q_baseline=None, q_final=0.5,
            phase_results=[], operations_attempted=0,
            operations_accepted=0, deadlock_breaker_used=False,
            deadlock_breaker_phase=None,
        )

    @asynccontextmanager
    async def session_factory():
        yield db

    with patch(
        "app.services.taxonomy.warm_path.execute_maintenance_phases",
        fake_maintenance,
    ):
        result = await execute_warm_path(engine, session_factory)

    assert len(maintenance_called) == 1
    assert result.snapshot_id == "maint-idle"


@pytest.mark.asyncio
async def test_idle_cycle_skips_maintenance_off_cadence(db):
    """When no dirty clusters and not on cadence, maintenance is skipped."""
    from app.services.taxonomy._constants import MAINTENANCE_CYCLE_INTERVAL
    from app.services.taxonomy.engine import TaxonomyEngine
    from app.services.taxonomy.warm_path import execute_warm_path

    mock_embedding = MagicMock()
    mock_provider = MagicMock()
    engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)

    # Off-cadence age (not a multiple of interval)
    engine._warm_path_age = MAINTENANCE_CYCLE_INTERVAL + 1

    maintenance_called = []

    async def fake_maintenance(eng, sf, phase_results=None, q_baseline=None):
        maintenance_called.append(True)
        from app.services.taxonomy.warm_path import WarmPathResult
        return WarmPathResult(
            snapshot_id="should-not-run",
            q_baseline=None, q_final=None,
            phase_results=[], operations_attempted=0,
            operations_accepted=0, deadlock_breaker_used=False,
            deadlock_breaker_phase=None,
        )

    @asynccontextmanager
    async def session_factory():
        yield db

    with patch(
        "app.services.taxonomy.warm_path.execute_maintenance_phases",
        fake_maintenance,
    ):
        result = await execute_warm_path(engine, session_factory)

    assert len(maintenance_called) == 0
    assert result.snapshot_id == "skipped"


@pytest.mark.asyncio
async def test_idle_cycle_runs_maintenance_on_retry(db):
    """When _maintenance_pending is True, idle cycle runs maintenance regardless of cadence."""
    from app.services.taxonomy._constants import MAINTENANCE_CYCLE_INTERVAL
    from app.services.taxonomy.engine import TaxonomyEngine
    from app.services.taxonomy.warm_path import execute_warm_path

    mock_embedding = MagicMock()
    mock_provider = MagicMock()
    engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)

    # Off-cadence, but pending retry
    engine._warm_path_age = MAINTENANCE_CYCLE_INTERVAL + 1
    engine._maintenance_pending = True

    maintenance_called = []

    async def fake_maintenance(eng, sf, phase_results=None, q_baseline=None):
        maintenance_called.append(True)
        # Simulate successful discovery clearing the flag
        eng._maintenance_pending = False
        from app.services.taxonomy.warm_path import WarmPathResult
        return WarmPathResult(
            snapshot_id="maint-retry",
            q_baseline=None, q_final=0.5,
            phase_results=[], operations_attempted=0,
            operations_accepted=0, deadlock_breaker_used=False,
            deadlock_breaker_phase=None,
        )

    @asynccontextmanager
    async def session_factory():
        yield db

    with patch(
        "app.services.taxonomy.warm_path.execute_maintenance_phases",
        fake_maintenance,
    ):
        result = await execute_warm_path(engine, session_factory)

    assert len(maintenance_called) == 1
    assert result.snapshot_id == "maint-retry"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && pytest tests/taxonomy/test_warm_path_dirty.py::test_idle_cycle_runs_maintenance_on_cadence tests/taxonomy/test_warm_path_dirty.py::test_idle_cycle_skips_maintenance_off_cadence tests/taxonomy/test_warm_path_dirty.py::test_idle_cycle_runs_maintenance_on_retry -v`
Expected: FAIL — the current no-dirty-clusters path returns immediately

- [ ] **Step 3: Modify the no-dirty-clusters early-exit**

Replace the block at lines 428-451 in `execute_warm_path()` with:

```python
        if not dirty_ids:
            # Nothing changed since last cycle — skip speculative phases.
            # But maintenance phases (discover, archive, audit) run on
            # their own cadence or when retrying after a transient failure.
            from app.services.taxonomy._constants import MAINTENANCE_CYCLE_INTERVAL

            cadence_gate = (engine._warm_path_age % MAINTENANCE_CYCLE_INTERVAL == 0)
            should_maintain = cadence_gate or engine._maintenance_pending

            if should_maintain:
                logger.info(
                    "Warm path: no dirty clusters but running maintenance "
                    "(cadence=%s pending=%s age=%d)",
                    cadence_gate, engine._maintenance_pending, engine._warm_path_age,
                )
                try:
                    get_event_logger().log_decision(
                        path="warm", op="maintenance",
                        decision="maintenance_on_idle",
                        context={
                            "warm_path_age": engine._warm_path_age,
                            "cadence_gate": cadence_gate,
                            "retry_pending": engine._maintenance_pending,
                        },
                    )
                except RuntimeError:
                    pass

                # NOTE: do NOT increment _warm_path_age here — phase_audit()
                # inside execute_maintenance_phases() does it unconditionally.
                return await execute_maintenance_phases(engine, session_factory)

            # Neither cadence nor retry — skip entirely
            logger.debug("Warm path skipped — no dirty clusters (age=%d)", engine._warm_path_age)
            try:
                get_event_logger().log_decision(
                    path="warm", op="skip", decision="no_dirty_clusters",
                    context={"warm_path_age": engine._warm_path_age},
                )
            except RuntimeError:
                pass
            engine._warm_path_age += 1
            return WarmPathResult(
                snapshot_id="skipped",
                q_baseline=None,
                q_final=None,
                phase_results=[],
                operations_attempted=0,
                operations_accepted=0,
                deadlock_breaker_used=False,
                deadlock_breaker_phase=None,
            )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && pytest tests/taxonomy/test_warm_path_dirty.py::test_idle_cycle_runs_maintenance_on_cadence tests/taxonomy/test_warm_path_dirty.py::test_idle_cycle_skips_maintenance_off_cadence tests/taxonomy/test_warm_path_dirty.py::test_idle_cycle_runs_maintenance_on_retry -v`
Expected: All 3 PASS

- [ ] **Step 5: Run full warm path test suite**

Run: `cd backend && pytest tests/taxonomy/test_warm_path.py tests/taxonomy/test_warm_path_dirty.py -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/taxonomy/warm_path.py backend/tests/taxonomy/test_warm_path_dirty.py
git commit -m "feat(taxonomy): run maintenance phases on idle cycles via cadence gate + retry flag"
```

---

### Task 6: Update main.py warm path timer for maintenance-aware logging

**Files:**
- Modify: `backend/app/main.py:1188-1191`

- [ ] **Step 1: Update the skip log message**

In `main.py`, the warm path timer currently logs "skipped — no dirty clusters" when `result.snapshot_id == "skipped"`. Now maintenance-only cycles will return a real snapshot_id. Update the logging to distinguish between the three cases. Replace lines 1188-1199 with:

```python
                        result = await engine.run_warm_path(async_session_factory)
                        if result is None:
                            logger.debug("Warm path skipped — lock held")
                        elif result.snapshot_id == "skipped":
                            logger.debug("Warm path skipped — no dirty clusters, maintenance off-cadence")
                        elif result.q_baseline is None and result.snapshot_id != "skipped":
                            # Maintenance-only cycle (no Phase 0, so no q_baseline)
                            logger.info(
                                "Warm path maintenance-only: q=%.4f snapshot=%s",
                                result.q_system or 0.0,
                                result.snapshot_id,
                            )
                        else:
                            logger.info(
                                "Warm path completed: q=%.4f baseline=%.4f ops=%d/%d snapshot=%s",
                                result.q_system or 0.0,
                                result.q_baseline or 0.0,
                                result.operations_accepted,
                                result.operations_attempted,
                                result.snapshot_id,
                            )
```

- [ ] **Step 2: Verify syntax**

Run: `cd backend && python -c "import app.main"`
Expected: No import errors

- [ ] **Step 3: Commit**

```bash
git add backend/app/main.py
git commit -m "fix(main): distinguish maintenance-only warm path cycles in log output"
```

---

### Task 7: Update docstrings and module header

**Files:**
- Modify: `backend/app/services/taxonomy/warm_path.py:1-14`

- [ ] **Step 1: Update the module docstring**

Replace the module docstring (lines 1-16) with:

```python
"""Warm-path orchestrator — sequential phase execution with per-phase Q gates.

Receives ``engine`` and ``session_factory`` as parameters.  Never imports
TaxonomyEngine at runtime (uses TYPE_CHECKING only).

Two execution groups:

**Lifecycle group** (dirty-cluster-gated):
  0.   Reconcile    — fresh session, always commits, then compute Q_baseline
  0.5  Evaluate     — candidate promotion/rejection
  1.   Split/Emerge — speculative (Q gate)
  2.   Merge        — speculative (Q gate)
  3.   Retire       — speculative (Q gate)
  4.   Refresh      — fresh session, always commits
  4.25 Sub-domain pattern aggregation
  4.5  Global pattern promotion/validation (periodic gate)
  4.75 Task-type signal refresh

**Maintenance group** (cadence-gated, independent of dirty clusters):
  5.  Discover     — fresh session, try/except with retry flag
  5.5 Archive      — sub-domain garbage collection
  6.  Audit        — fresh session, creates snapshot

Maintenance runs every ``MAINTENANCE_CYCLE_INTERVAL`` warm cycles (default 6,
~30 min at 5-min interval), or immediately when ``engine._maintenance_pending``
is set after a transient failure.

Copyright 2025-2026 Project Synthesis contributors.
"""
```

- [ ] **Step 2: Update execute_warm_path docstring**

Replace the `execute_warm_path` docstring to reflect the new structure:

```python
    """Orchestrate the complete warm path: lifecycle + maintenance groups.

    The lifecycle group (Phases 0–4) is gated by dirty clusters — when no
    clusters have been modified since the last cycle, these phases are skipped.
    The maintenance group (Phases 5–6) runs via ``execute_maintenance_phases()``
    on its own cadence or when retrying after a transient failure.

    Args:
        engine: TaxonomyEngine instance.
        session_factory: Async context manager yielding fresh AsyncSession.

    Returns:
        WarmPathResult aggregating all phase outcomes.
    """
```

- [ ] **Step 3: Commit**

```bash
git add backend/app/services/taxonomy/warm_path.py
git commit -m "docs(taxonomy): update warm_path docstrings for maintenance decoupling"
```

---

### Task 8: Verify full test suite passes

**Files:**
- None (verification only)

- [ ] **Step 1: Run all warm path tests**

Run: `cd backend && pytest tests/taxonomy/test_warm_path.py tests/taxonomy/test_warm_path_dirty.py tests/taxonomy/test_warm_phases.py -v`
Expected: All PASS

- [ ] **Step 2: Run full backend test suite**

Run: `cd backend && pytest --tb=short -q`
Expected: All PASS (1932+ tests)

- [ ] **Step 3: Verify ruff lint**

Run: `cd backend && ruff check app/services/taxonomy/warm_path.py app/services/taxonomy/engine.py app/services/taxonomy/_constants.py app/main.py`
Expected: No errors

- [ ] **Step 4: Final commit if any lint fixes**

```bash
git add -u
git commit -m "style: fix lint in warm path maintenance decoupling"
```
