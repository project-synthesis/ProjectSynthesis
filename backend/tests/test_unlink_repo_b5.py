"""Tests for ADR-005 B5 — DELETE /api/github/repos/unlink supports
``?mode=keep|rehome`` and preserves project nodes on unlink.
"""

from __future__ import annotations

import base64
import hashlib

import pytest
from cryptography.fernet import Fernet
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import GitHubToken, LinkedRepo, Optimization, PromptCluster

_TEST_SECRET_KEY = "test-secret-key-for-b5"


async def _prime_session_with_linked_repo(
    db: AsyncSession,
    *,
    session_id: str,
    repo_full_name: str,
) -> tuple[str, str]:
    """Plant a session + encrypted token + Legacy + project + LinkedRepo.

    Returns ``(legacy_id, project_id)``.
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

    legacy = PromptCluster(
        label="Legacy", state="project", domain="general",
        task_type="general", member_count=0,
    )
    db.add(legacy)
    await db.flush()

    project = PromptCluster(
        label=repo_full_name, state="project", domain="general",
        task_type="general", member_count=0,
    )
    db.add(project)
    await db.flush()

    linked = LinkedRepo(
        session_id=session_id,
        full_name=repo_full_name,
        default_branch="main",
        project_node_id=project.id,
    )
    db.add(linked)
    await db.commit()
    return legacy.id, project.id


async def _seed_opts_on_project(db: AsyncSession, *, project_id: str, n: int = 2):
    for _ in range(n):
        opt = Optimization(
            raw_prompt="p",
            status="completed",
            project_id=project_id,
            repo_full_name="user/b5-repo",
        )
        db.add(opt)
    await db.commit()


async def _call_unlink(app_client: AsyncClient, session_id: str, mode: str | None = None):
    from app.config import settings

    original_secret = settings.SECRET_KEY
    settings.SECRET_KEY = _TEST_SECRET_KEY
    app_client.cookies.set("session_id", session_id)
    try:
        path = "/api/github/repos/unlink"
        if mode is not None:
            path = f"{path}?mode={mode}"
        return await app_client.delete(path)
    finally:
        app_client.cookies.clear()
        settings.SECRET_KEY = original_secret


@pytest.mark.asyncio
async def test_b5_unlink_default_mode_is_keep(
    app_client: AsyncClient, db_session: AsyncSession
):
    """Default mode is 'keep' — opts stay on the project."""
    session_id = "b5-sess-1"
    _legacy_id, project_id = await _prime_session_with_linked_repo(
        db_session, session_id=session_id, repo_full_name="user/b5-repo",
    )
    await _seed_opts_on_project(db_session, project_id=project_id, n=2)

    resp = await _call_unlink(app_client, session_id)
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["mode"] == "keep"
    assert body["project_id"] == project_id
    assert body["rehomed_count"] == 0

    # Opts remain attributed to the project.
    opts = (await db_session.execute(select(Optimization))).scalars().all()
    assert all(o.project_id == project_id for o in opts)

    # Project node survives — unlink NEVER deletes the project.
    proj = await db_session.get(PromptCluster, project_id)
    assert proj is not None
    assert proj.state == "project"


@pytest.mark.asyncio
async def test_b5_unlink_rehome_mode_migrates_to_legacy(
    app_client: AsyncClient, db_session: AsyncSession
):
    """mode=rehome migrates last-7-day opts from the project back to Legacy."""
    session_id = "b5-sess-2"
    legacy_id, project_id = await _prime_session_with_linked_repo(
        db_session, session_id=session_id, repo_full_name="user/b5-repo",
    )
    await _seed_opts_on_project(db_session, project_id=project_id, n=3)

    resp = await _call_unlink(app_client, session_id, mode="rehome")
    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] == "rehome"
    assert body["project_id"] == project_id
    assert body["rehomed_count"] == 3

    # Opts now live on Legacy.
    opts = (await db_session.execute(select(Optimization))).scalars().all()
    assert all(o.project_id == legacy_id for o in opts)

    # Project node still survives (projects are forever).
    proj = await db_session.get(PromptCluster, project_id)
    assert proj is not None


@pytest.mark.asyncio
async def test_b5_unlink_invalid_mode_returns_422(
    app_client: AsyncClient, db_session: AsyncSession
):
    """Unknown mode values are rejected by the pattern validator."""
    session_id = "b5-sess-3"
    await _prime_session_with_linked_repo(
        db_session, session_id=session_id, repo_full_name="user/b5-repo",
    )

    resp = await _call_unlink(app_client, session_id, mode="nuke")
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_b5_unlink_emits_repo_unlinked_event(
    app_client: AsyncClient, db_session: AsyncSession
):
    import asyncio

    from app.services.event_bus import event_bus

    session_id = "b5-sess-4"
    _legacy_id, project_id = await _prime_session_with_linked_repo(
        db_session, session_id=session_id, repo_full_name="user/b5-repo",
    )

    queue: asyncio.Queue = asyncio.Queue()
    event_bus._subscribers.add(queue)
    try:
        resp = await _call_unlink(app_client, session_id, mode="keep")
        assert resp.status_code == 200

        found = None
        while not queue.empty():
            evt = queue.get_nowait()
            if evt.get("event") == "repo_unlinked":
                found = evt
                break
        assert found is not None
        assert found["data"]["project_id"] == project_id
        assert found["data"]["mode"] == "keep"
    finally:
        event_bus._subscribers.discard(queue)


@pytest.mark.asyncio
async def test_b5_unlink_idempotent_when_nothing_linked(
    app_client: AsyncClient, db_session: AsyncSession
):
    """Unlink without a LinkedRepo returns 200 ok with zero side effects."""
    # Session token but no LinkedRepo row.
    key = hashlib.sha256(_TEST_SECRET_KEY.encode()).digest()
    fernet = Fernet(base64.urlsafe_b64encode(key))
    encrypted = fernet.encrypt(b"ghp_fake_token")
    session_id = "b5-sess-5"
    db_session.add(GitHubToken(
        session_id=session_id,
        token_encrypted=encrypted,
        github_login="tester",
        github_user_id="99999",
    ))
    await db_session.commit()

    resp = await _call_unlink(app_client, session_id, mode="rehome")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["project_id"] is None
    assert body["rehomed_count"] == 0
