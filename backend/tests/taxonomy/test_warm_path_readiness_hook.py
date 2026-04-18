"""Integration test: warm-path Phase 5 persists readiness snapshots.

Cycle 4 (RED): the hook inside ``execute_maintenance_phases`` that calls
``record_snapshot()`` for each ``DomainReadinessReport`` is not yet wired —
this test asserts the call happens and must fail until GREEN implements it.

We patch ``record_snapshot`` and ``compute_all_domain_readiness`` at their
*source* modules (``app.services.taxonomy.readiness_history`` and
``app.services.taxonomy.sub_domain_readiness``) rather than at
``warm_path``.  That makes the test robust regardless of whether the
eventual GREEN implementation imports them at the top of ``warm_path`` or
lazily inside the Phase 5 try-block — ``patch`` replaces the symbol inside
the home module, so both import styles pick up the mock.

Copyright 2025-2026 Project Synthesis contributors.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest

from app.services.taxonomy.sub_domain_readiness import DomainReadinessReport
from app.services.taxonomy.warm_path import execute_maintenance_phases


def _make_report(domain_id: str = "dom-x") -> DomainReadinessReport:
    """Construct a minimally-valid DomainReadinessReport for mocking."""
    # Use model_construct so we do not have to fabricate every nested field;
    # the hook only needs to pass the report object through to record_snapshot.
    return DomainReadinessReport.model_construct(
        domain_id=domain_id,
        domain_label=f"label-{domain_id}",
        member_count=10,
    )


@pytest.mark.asyncio
async def test_warm_path_phase5_records_readiness_snapshots(
    db, mock_embedding, mock_provider
):
    """After Phase 5 succeeds, record_snapshot must fire once per domain report.

    Drives ``execute_maintenance_phases`` with patched discover/archive/audit
    (minimal stand-ins copied from existing warm-path tests) and a stubbed
    ``compute_all_domain_readiness`` returning two reports.  Asserts
    ``record_snapshot`` was awaited exactly twice.

    This test FAILS until GREEN wires the hook inside the Phase 5 try-block
    (awaiting success — ``engine._maintenance_pending = False`` — before
    persisting snapshots, so transient failures skip history writes).
    """
    from app.services.taxonomy.engine import TaxonomyEngine

    engine = TaxonomyEngine(
        embedding_service=mock_embedding, provider=mock_provider,
    )

    async def fake_discover(eng, session):
        from app.services.taxonomy.warm_phases import DiscoverResult
        return DiscoverResult()

    async def fake_archive(eng, session):
        return 0

    async def fake_audit(eng, session, phase_results, q_baseline):
        from app.services.taxonomy.warm_phases import AuditResult
        return AuditResult(snapshot_id="maint-hook", q_final=0.5)

    reports = [_make_report("dom-a"), _make_report("dom-b")]

    @asynccontextmanager
    async def session_factory():
        yield db

    with (
        patch(
            "app.services.taxonomy.warm_path.phase_discover", fake_discover,
        ),
        patch(
            "app.services.taxonomy.warm_path.phase_archive_empty_sub_domains",
            fake_archive,
        ),
        patch(
            "app.services.taxonomy.warm_path.phase_audit", fake_audit,
        ),
        patch(
            "app.services.taxonomy.sub_domain_readiness."
            "compute_all_domain_readiness",
            new=AsyncMock(return_value=reports),
        ),
        patch(
            "app.services.taxonomy.readiness_history.record_snapshot",
            new_callable=AsyncMock,
        ) as mock_record,
    ):
        result = await execute_maintenance_phases(engine, session_factory)

    # Phase 5 succeeded — retry flag cleared and audit ran.
    assert engine._maintenance_pending is False
    assert result.snapshot_id == "maint-hook"

    # THE RED ASSERTION: hook should persist one row per domain report.
    assert mock_record.await_count == len(reports), (
        f"expected record_snapshot called {len(reports)} times "
        f"(one per domain report), got {mock_record.await_count} — "
        f"Phase 5 readiness snapshot hook is not yet wired"
    )


@pytest.mark.asyncio
async def test_warm_path_phase5_skips_snapshots_on_discover_failure(
    db, mock_embedding, mock_provider
):
    """When Phase 5 fails, readiness snapshots must NOT be recorded.

    The hook must sit inside the success branch (after
    ``engine._maintenance_pending = False``), not before the try-block.
    This guards against writing misleading observability rows when the
    domain tree is in a transient inconsistent state.
    """
    from app.services.taxonomy.engine import TaxonomyEngine

    engine = TaxonomyEngine(
        embedding_service=mock_embedding, provider=mock_provider,
    )

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
        patch(
            "app.services.taxonomy.warm_path.phase_discover",
            failing_discover,
        ),
        patch(
            "app.services.taxonomy.warm_path.phase_archive_empty_sub_domains",
            fake_archive,
        ),
        patch(
            "app.services.taxonomy.warm_path.phase_audit", fake_audit,
        ),
        patch(
            "app.services.taxonomy.sub_domain_readiness."
            "compute_all_domain_readiness",
            new=AsyncMock(return_value=[_make_report("dom-a")]),
        ),
        patch(
            "app.services.taxonomy.readiness_history.record_snapshot",
            new_callable=AsyncMock,
        ) as mock_record,
    ):
        await execute_maintenance_phases(engine, session_factory)

    assert engine._maintenance_pending is True
    assert mock_record.await_count == 0, (
        "record_snapshot must not fire when Phase 5 discover fails"
    )
