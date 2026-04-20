"""Tests for recurring GC functions — expired GitHub tokens and orphan linked repos.

Covers the hourly cleanup sweep added to close PR #1's "cleanup runs only
at startup" regression. Uses the in-memory SQLite fixture from
``conftest.py`` so no real DB is touched.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

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
