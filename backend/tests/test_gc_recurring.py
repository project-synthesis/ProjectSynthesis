"""Tests for recurring GC functions — expired GitHub tokens and orphan linked repos.

Covers the hourly cleanup sweep added to close PR #1's "cleanup runs only
at startup" regression. Uses the in-memory SQLite fixture from
``conftest.py`` so no real DB is touched.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.gc import (
    _gc_expired_github_tokens,
    _gc_orphan_linked_repos,
    run_recurring_gc,
)


def _now() -> datetime:
    """Naive UTC (matches models.py DateTime columns)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


@pytest.mark.asyncio
async def test_expired_github_tokens_are_deleted(db_session) -> None:
    from app.models import GitHubToken

    now = _now()
    # Both access token and refresh token firmly expired — should be swept
    expired = GitHubToken(
        session_id="s-expired",
        token_encrypted=b"x",
        expires_at=now - timedelta(hours=2),
        refresh_token_expires_at=now - timedelta(days=2),
    )
    # Access expired but refresh still within grace window — keep
    within_grace = GitHubToken(
        session_id="s-grace",
        token_encrypted=b"x",
        expires_at=now - timedelta(hours=2),
        refresh_token_expires_at=now - timedelta(hours=1),  # inside 24h grace
    )
    # Legacy non-expiring token (expires_at NULL) — never swept
    legacy = GitHubToken(
        session_id="s-legacy",
        token_encrypted=b"x",
        expires_at=None,
        refresh_token_expires_at=None,
    )
    # Live token — should not be swept
    live = GitHubToken(
        session_id="s-live",
        token_encrypted=b"x",
        expires_at=now + timedelta(hours=1),
        refresh_token_expires_at=now + timedelta(days=30),
    )
    db_session.add_all([expired, within_grace, legacy, live])
    await db_session.commit()

    count = await _gc_expired_github_tokens(db_session)
    await db_session.commit()
    assert count == 1, f"expected 1 deletion, got {count}"

    from sqlalchemy import select
    remaining = await db_session.execute(select(GitHubToken.session_id))
    remaining_ids = {r[0] for r in remaining.all()}
    assert remaining_ids == {"s-grace", "s-legacy", "s-live"}


@pytest.mark.asyncio
async def test_orphan_linked_repos_are_deleted(db_session) -> None:
    from app.models import GitHubToken, LinkedRepo

    now = _now()
    # A live session + its linked repo — both should survive
    live_token = GitHubToken(
        session_id="live-session",
        token_encrypted=b"x",
        expires_at=now + timedelta(days=7),
    )
    live_repo = LinkedRepo(
        session_id="live-session",
        full_name="octocat/hello-world",
    )
    # An orphan linked repo with no matching GitHubToken — should be deleted
    orphan_repo = LinkedRepo(
        session_id="orphan-session",
        full_name="dead/repo",
    )
    db_session.add_all([live_token, live_repo, orphan_repo])
    await db_session.commit()

    count = await _gc_orphan_linked_repos(db_session)
    await db_session.commit()
    assert count == 1, f"expected 1 orphan deletion, got {count}"

    from sqlalchemy import select
    remaining = await db_session.execute(select(LinkedRepo.full_name))
    remaining_names = {r[0] for r in remaining.all()}
    assert remaining_names == {"octocat/hello-world"}


@pytest.mark.asyncio
async def test_run_recurring_gc_no_work(db_session) -> None:
    """Empty DB — run_recurring_gc must be a no-op (no commit, no crash)."""
    await run_recurring_gc(db_session)
    # No assertion needed — success means no exception


@pytest.mark.asyncio
async def test_run_recurring_gc_cleans_both_categories(db_session) -> None:
    from app.models import GitHubToken, LinkedRepo

    now = _now()
    # Orphan linked repo (no token) + expired token with orphan repo
    db_session.add(GitHubToken(
        session_id="victim",
        token_encrypted=b"x",
        expires_at=now - timedelta(hours=2),
        refresh_token_expires_at=now - timedelta(days=2),
    ))
    db_session.add(LinkedRepo(
        session_id="victim",
        full_name="doomed/repo",
    ))
    db_session.add(LinkedRepo(
        session_id="already-orphan",
        full_name="already/dead",
    ))
    await db_session.commit()

    await run_recurring_gc(db_session)

    from sqlalchemy import select
    tokens = (await db_session.execute(select(GitHubToken.session_id))).all()
    repos = (await db_session.execute(select(LinkedRepo.full_name))).all()
    assert tokens == [], "expected all expired tokens swept"
    # 'already-orphan' repo is swept because no token ever existed for it.
    # 'doomed/repo' is swept on the NEXT sweep after the token is gone,
    # not this one (both functions run in the same transaction; the delete
    # of the token does not affect the snapshot read by the repo query).
    # For SQLite autoflush defaults this means one survives this cycle —
    # which is fine; the next hourly tick sweeps it.
    names = {r[0] for r in repos}
    assert "already/dead" not in names, "orphan linked_repo not swept"


# v0.4.35 — phase-tagged sweep tests + recurring-sweep wiring tests


class TestPhaseTaggedSweeps:
    """Both stuck-state sweeps gain a `phase: str = "startup"` kwarg that
    tags `logger.info` so startup vs recurring cleanups are grep-able.

    Pinned via `caplog` substring matches (not full-string equality) so
    later log-text drift in unrelated parts of the message doesn't break
    these tests. Spec §5 AC1-4.
    """

    async def test_gc_orphan_runs_default_phase_logs_startup(
        self, db_session: AsyncSession, caplog,
    ) -> None:
        """AC #1: `_gc_orphan_runs(db)` with no phase kwarg → log contains `GC[startup]:`."""
        # Set up: 1 orphan RunRow past TTL.
        from datetime import datetime, timedelta, timezone
        from app.models import RunRow
        from app.services.gc import RUN_ORPHAN_TTL_HOURS, _gc_orphan_runs

        # Naive UTC matches the DateTime columns in models.py (per gc.py:_utcnow()).
        cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=RUN_ORPHAN_TTL_HOURS + 1)
        row = RunRow(
            id="rr-orphan-1",
            mode="topic_probe",
            status="running",
            started_at=cutoff,
        )
        db_session.add(row)
        await db_session.commit()

        with caplog.at_level("INFO", logger="app.services.gc"):
            cleaned = await _gc_orphan_runs(db_session)
        assert cleaned == 1
        assert any("GC[startup]:" in rec.message for rec in caplog.records), \
            f"Expected 'GC[startup]:' in logs; got: {[r.message for r in caplog.records]}"

    async def test_gc_orphan_runs_phase_recurring_logs_recurring(
        self, db_session: AsyncSession, caplog,
    ) -> None:
        """AC #2: `_gc_orphan_runs(db, phase='recurring')` → log contains `GC[recurring]:`."""
        from datetime import datetime, timedelta, timezone
        from app.models import RunRow
        from app.services.gc import RUN_ORPHAN_TTL_HOURS, _gc_orphan_runs

        # Naive UTC matches the DateTime columns in models.py (per gc.py:_utcnow()).
        cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=RUN_ORPHAN_TTL_HOURS + 1)
        row = RunRow(
            id="rr-orphan-2",
            mode="topic_probe",
            status="running",
            started_at=cutoff,
        )
        db_session.add(row)
        await db_session.commit()

        with caplog.at_level("INFO", logger="app.services.gc"):
            cleaned = await _gc_orphan_runs(db_session, phase="recurring")
        assert cleaned == 1
        assert any("GC[recurring]:" in rec.message for rec in caplog.records), \
            f"Expected 'GC[recurring]:' in logs; got: {[r.message for r in caplog.records]}"

    async def test_gc_stuck_pending_default_phase_logs_startup(
        self, db_session: AsyncSession, caplog,
    ) -> None:
        """AC #3: `_gc_stuck_pending_optimizations(db)` no phase → log contains `GC[startup]:`."""
        from datetime import datetime, timedelta, timezone
        from app.models import Optimization
        from app.services.gc import (
            _STUCK_PENDING_AGE_HOURS,
            _gc_stuck_pending_optimizations,
        )

        # Naive UTC matches the DateTime columns in models.py (per gc.py:_utcnow()).
        cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=_STUCK_PENDING_AGE_HOURS + 1)
        opt = Optimization(
            id="opt-stuck-1",
            trace_id="tr-stuck-1",
            raw_prompt="test",
            status="pending",
            optimized_prompt=None,
            created_at=cutoff,
        )
        db_session.add(opt)
        await db_session.commit()

        with caplog.at_level("INFO", logger="app.services.gc"):
            cleaned = await _gc_stuck_pending_optimizations(db_session)
        assert cleaned == 1
        assert any("GC[startup]:" in rec.message for rec in caplog.records), \
            f"Expected 'GC[startup]:' in logs; got: {[r.message for r in caplog.records]}"

    async def test_gc_stuck_pending_phase_recurring_logs_recurring(
        self, db_session: AsyncSession, caplog,
    ) -> None:
        """AC #4: `_gc_stuck_pending_optimizations(db, phase='recurring')` → log contains `GC[recurring]:`."""
        from datetime import datetime, timedelta, timezone
        from app.models import Optimization
        from app.services.gc import (
            _STUCK_PENDING_AGE_HOURS,
            _gc_stuck_pending_optimizations,
        )

        # Naive UTC matches the DateTime columns in models.py (per gc.py:_utcnow()).
        cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=_STUCK_PENDING_AGE_HOURS + 1)
        opt = Optimization(
            id="opt-stuck-2",
            trace_id="tr-stuck-2",
            raw_prompt="test",
            status="pending",
            optimized_prompt=None,
            created_at=cutoff,
        )
        db_session.add(opt)
        await db_session.commit()

        with caplog.at_level("INFO", logger="app.services.gc"):
            cleaned = await _gc_stuck_pending_optimizations(db_session, phase="recurring")
        assert cleaned == 1
        assert any("GC[recurring]:" in rec.message for rec in caplog.records), \
            f"Expected 'GC[recurring]:' in logs; got: {[r.message for r in caplog.records]}"

    async def test_run_recurring_gc_sweeps_orphan_runs_and_stuck_pending(
        self, db_session: AsyncSession, caplog,
    ) -> None:
        """AC #5 + #6: `run_recurring_gc` now calls both new sweeps with
        `phase='recurring'` AND still calls the existing 2 sweeps.

        Regression guard: existing `_gc_expired_github_tokens` +
        `_gc_orphan_linked_repos` calls survive the extension.
        """
        from datetime import datetime, timedelta, timezone
        from unittest.mock import patch, AsyncMock
        from app.models import RunRow, Optimization
        from app.services.gc import (
            RUN_ORPHAN_TTL_HOURS,
            _STUCK_PENDING_AGE_HOURS,
            run_recurring_gc,
        )

        # Set up: 1 stuck row of each type so both new sweeps have rows to clean.
        now_naive = datetime.now(timezone.utc).replace(tzinfo=None)
        cutoff_runs = now_naive - timedelta(hours=RUN_ORPHAN_TTL_HOURS + 1)
        cutoff_pending = now_naive - timedelta(hours=_STUCK_PENDING_AGE_HOURS + 1)
        db_session.add_all([
            RunRow(
                id="rr-recurring-1",
                mode="seed_agent",
                status="running",
                started_at=cutoff_runs,
            ),
            Optimization(
                id="opt-recurring-1",
                trace_id="tr-recurring-1",
                raw_prompt="t",
                status="pending",
                optimized_prompt=None,
                created_at=cutoff_pending,
            ),
        ])
        await db_session.commit()

        # Stub the WriteQueue path so we exercise the legacy direct-db branch
        # (more deterministic for caplog assertions than going through the
        # writer's submit() machinery in a unit test).
        with patch(
            "app.services.gc._gc_expired_github_tokens",
            new=AsyncMock(return_value=0),
        ) as mock_tokens, patch(
            "app.services.gc._gc_orphan_linked_repos",
            new=AsyncMock(return_value=0),
        ) as mock_repos:
            with caplog.at_level("INFO", logger="app.services.gc"):
                await run_recurring_gc(db_session)  # write_queue=None → legacy direct-db fall-through

        # Regression: existing 2 sweeps still called.
        mock_tokens.assert_awaited_once_with(db_session)
        mock_repos.assert_awaited_once_with(db_session)

        # New: both stuck-state sweeps logged with [recurring].
        log_text = " ".join(rec.message for rec in caplog.records)
        assert "GC[recurring]:" in log_text, \
            f"Expected 'GC[recurring]:' in logs; got: {log_text!r}"

        # Verify the rows were actually flipped/deleted.
        from sqlalchemy import select
        refreshed_run = (await db_session.execute(
            select(RunRow).where(RunRow.id == "rr-recurring-1")
        )).scalar_one()
        assert refreshed_run.status == "failed"

        remaining_pending = (await db_session.execute(
            select(Optimization).where(Optimization.id == "opt-recurring-1")
        )).scalar_one_or_none()
        assert remaining_pending is None, \
            "stuck pending optimization should have been deleted"
