"""Tests for ADR-005 B4 — POST /api/github/repos/link response carries
``project_id`` and ``migration_candidates`` for the post-link toast.
"""

from __future__ import annotations

import base64
import hashlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from cryptography.fernet import Fernet
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import GitHubToken, Optimization, PromptCluster


_TEST_SECRET_KEY = "test-secret-key-for-b4"


async def _prime_session(db: AsyncSession, session_id: str = "b4-sess") -> None:
    """Plant a session-scoped encrypted GitHub token.

    Encrypts with the test-only ``_TEST_SECRET_KEY`` — the endpoint test must
    override ``settings.SECRET_KEY`` to the same value before making the call.
    """
    key = hashlib.sha256(_TEST_SECRET_KEY.encode()).digest()
    fernet = Fernet(base64.urlsafe_b64encode(key))
    encrypted = fernet.encrypt(b"ghp_fake_token")
    token = GitHubToken(
        session_id=session_id,
        token_encrypted=encrypted,
        github_login="tester",
        github_user_id="12345",
    )
    db.add(token)
    await db.commit()


async def _seed_legacy_with_opts(db: AsyncSession, n: int = 2) -> str:
    legacy = PromptCluster(
        label="Legacy",
        state="project",
        domain="general",
        task_type="general",
        member_count=0,
    )
    db.add(legacy)
    await db.flush()
    for _ in range(n):
        opt = Optimization(
            raw_prompt="p",
            status="completed",
            project_id=legacy.id,
            repo_full_name=None,
        )
        db.add(opt)
    await db.commit()
    return legacy.id


@pytest.mark.asyncio
async def test_b4_link_response_includes_project_id_and_candidates(
    app_client: AsyncClient, db_session: AsyncSession
):
    """On link, response carries project_id + non-empty migration_candidates."""
    session_id = "b4-sess-1"
    await _prime_session(db_session, session_id)
    legacy_id = await _seed_legacy_with_opts(db_session, n=2)

    # Mock the GitHub client's get_repo so link doesn't touch the network.
    fake_client = MagicMock()
    fake_client.get_repo = AsyncMock(
        return_value={"default_branch": "main", "language": "Python"}
    )

    from app.config import settings

    original_secret = settings.SECRET_KEY
    settings.SECRET_KEY = _TEST_SECRET_KEY
    app_client.cookies.set("session_id", session_id)
    try:
        with patch(
            "app.routers.github_repos.GitHubClient", return_value=fake_client
        ):
            resp = await app_client.post(
                "/api/github/repos/link",
                json={"full_name": "user/b4-repo"},
            )
    finally:
        app_client.cookies.clear()
        settings.SECRET_KEY = original_secret

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["full_name"] == "user/b4-repo"
    assert body["project_id"]  # ADR-005 B4 — new project node id
    # Newly-created project is not Legacy.
    assert body["project_id"] != legacy_id

    candidates = body["migration_candidates"]
    assert candidates is not None
    assert candidates["count"] == 2
    assert candidates["from_project_id"] == legacy_id
    assert candidates["since"]  # ISO-8601 cutoff


@pytest.mark.asyncio
async def test_b4_link_response_candidates_zero_when_legacy_empty(
    app_client: AsyncClient, db_session: AsyncSession
):
    """Candidates count is 0 when Legacy has no recent repo-less opts."""
    session_id = "b4-sess-2"
    await _prime_session(db_session, session_id)
    # Seed Legacy but no optimizations.
    legacy = PromptCluster(
        label="Legacy",
        state="project",
        domain="general",
        task_type="general",
        member_count=0,
    )
    db_session.add(legacy)
    await db_session.commit()

    fake_client = MagicMock()
    fake_client.get_repo = AsyncMock(
        return_value={"default_branch": "main", "language": None}
    )

    from app.config import settings

    original_secret = settings.SECRET_KEY
    settings.SECRET_KEY = _TEST_SECRET_KEY
    app_client.cookies.set("session_id", session_id)
    try:
        with patch(
            "app.routers.github_repos.GitHubClient", return_value=fake_client
        ):
            resp = await app_client.post(
                "/api/github/repos/link",
                json={"full_name": "user/empty-legacy"},
            )
    finally:
        app_client.cookies.clear()
        settings.SECRET_KEY = original_secret

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["project_id"]
    assert body["migration_candidates"]["count"] == 0


@pytest.mark.asyncio
async def test_b4_link_response_no_candidates_field_when_no_legacy(
    app_client: AsyncClient, db_session: AsyncSession
):
    """When Legacy hasn't been provisioned, migration_candidates is None."""
    session_id = "b4-sess-3"
    await _prime_session(db_session, session_id)

    fake_client = MagicMock()
    fake_client.get_repo = AsyncMock(
        return_value={"default_branch": "main", "language": None}
    )

    from app.config import settings

    original_secret = settings.SECRET_KEY
    settings.SECRET_KEY = _TEST_SECRET_KEY
    app_client.cookies.set("session_id", session_id)
    try:
        with patch(
            "app.routers.github_repos.GitHubClient", return_value=fake_client
        ):
            resp = await app_client.post(
                "/api/github/repos/link",
                json={"full_name": "user/no-legacy"},
            )
    finally:
        app_client.cookies.clear()
        settings.SECRET_KEY = original_secret

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["project_id"]
    assert body["migration_candidates"] is None
