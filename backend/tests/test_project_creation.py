"""Tests for ADR-005 Phase 2A project creation."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import LinkedRepo, PromptCluster


@pytest.mark.asyncio
async def test_linked_repo_has_project_node_id(db_session: AsyncSession):
    """LinkedRepo model has project_node_id column."""
    assert hasattr(LinkedRepo, "project_node_id")


@pytest.mark.asyncio
async def test_cross_project_threshold_boost_constant():
    """CROSS_PROJECT_THRESHOLD_BOOST constant exists."""
    from app.services.taxonomy._constants import CROSS_PROJECT_THRESHOLD_BOOST
    assert CROSS_PROJECT_THRESHOLD_BOOST == 0.15


@pytest.mark.asyncio
async def test_ensure_project_first_repo_creates_new_project(db_session: AsyncSession):
    """First linked repo creates a new project — Legacy is never renamed."""
    from app.services.project_service import ensure_project_for_repo

    legacy = PromptCluster(
        label="Legacy", state="project", domain="general",
        task_type="general", member_count=0,
    )
    db_session.add(legacy)
    await db_session.flush()

    project_id = await ensure_project_for_repo(db_session, "user/backend-api")

    # New project created, Legacy untouched
    assert project_id != legacy.id
    await db_session.refresh(legacy)
    assert legacy.label == "Legacy"  # not renamed

    new_project = await db_session.get(PromptCluster, project_id)
    assert new_project.label == "user/backend-api"
    assert new_project.state == "project"


@pytest.mark.asyncio
async def test_ensure_project_second_repo_creates_new(db_session: AsyncSession):
    """Second linked repo creates a new project node."""
    from app.services.project_service import ensure_project_for_repo

    legacy = PromptCluster(
        label="Legacy", state="project", domain="general",
        task_type="general", member_count=0,
    )
    db_session.add(legacy)
    await db_session.flush()
    await ensure_project_for_repo(db_session, "user/backend-api")
    await db_session.flush()

    project_id = await ensure_project_for_repo(db_session, "user/marketing-site")
    assert project_id != legacy.id

    new_project = await db_session.get(PromptCluster, project_id)
    assert new_project.state == "project"
    assert new_project.label == "user/marketing-site"


@pytest.mark.asyncio
async def test_ensure_project_idempotent(db_session: AsyncSession):
    """Calling twice for same repo returns same project ID."""
    from app.services.project_service import ensure_project_for_repo

    legacy = PromptCluster(
        label="Legacy", state="project", domain="general",
        task_type="general", member_count=0,
    )
    db_session.add(legacy)

    lr = LinkedRepo(
        session_id="sess-1", full_name="user/backend-api",
        branch="main", language="Python",
    )
    db_session.add(lr)
    await db_session.flush()

    pid1 = await ensure_project_for_repo(db_session, "user/backend-api")
    await db_session.flush()
    pid2 = await ensure_project_for_repo(db_session, "user/backend-api")
    assert pid1 == pid2


@pytest.mark.asyncio
async def test_ensure_project_relink_finds_existing(db_session: AsyncSession):
    """Re-linking a previously unlinked repo reattaches to existing project."""
    from app.services.project_service import ensure_project_for_repo

    legacy = PromptCluster(
        label="Legacy", state="project", domain="general",
        task_type="general", member_count=0,
    )
    db_session.add(legacy)
    await db_session.flush()

    await ensure_project_for_repo(db_session, "user/backend-api")
    await db_session.flush()

    # Create second project node directly (simulates a previously linked repo)
    second = PromptCluster(
        label="user/marketing-site", state="project", domain="general",
        task_type="general", member_count=0,
    )
    db_session.add(second)
    await db_session.flush()

    # "Re-link" should find the existing project by label
    pid = await ensure_project_for_repo(db_session, "user/marketing-site")
    assert pid == second.id


@pytest.mark.asyncio
async def test_resolve_project_id_with_repo(db_session: AsyncSession):
    """resolve_project_id returns project_node_id when repo is linked."""
    from app.services.project_service import resolve_project_id

    project = PromptCluster(
        label="user/test-repo", state="project", domain="general",
        task_type="general", member_count=0,
    )
    db_session.add(project)
    await db_session.flush()

    lr = LinkedRepo(
        session_id="sess-1", full_name="user/test-repo",
        branch="main", language="Python",
        project_node_id=project.id,
    )
    db_session.add(lr)
    await db_session.flush()

    result = await resolve_project_id(db_session, "user/test-repo")
    assert result == project.id


@pytest.mark.asyncio
async def test_resolve_project_id_no_repo_returns_legacy(db_session: AsyncSession):
    """resolve_project_id returns legacy ID when repo_full_name is None."""
    from app.services.project_service import resolve_project_id

    result = await resolve_project_id(db_session, None, legacy_project_id="legacy-id")
    assert result == "legacy-id"


@pytest.mark.asyncio
async def test_resolve_project_id_unknown_repo_returns_legacy(db_session: AsyncSession):
    """resolve_project_id returns legacy ID when repo not in LinkedRepo."""
    from app.services.project_service import resolve_project_id

    result = await resolve_project_id(db_session, "unknown/repo", legacy_project_id="legacy-id")
    assert result == "legacy-id"


@pytest.mark.asyncio
async def test_ensure_project_with_explicit_target(db_session: AsyncSession):
    """Explicit target_project_id links repo to existing project."""
    from app.services.project_service import ensure_project_for_repo

    proj = PromptCluster(
        label="existing-project", state="project", domain="general",
        task_type="general", member_count=0,
    )
    db_session.add(proj)
    await db_session.flush()

    result = await ensure_project_for_repo(
        db_session, "user/new-repo", target_project_id=proj.id,
    )
    assert result == proj.id


@pytest.mark.asyncio
async def test_ensure_project_invalid_target_falls_through(db_session: AsyncSession):
    """Invalid target_project_id falls through to auto-creation."""
    from app.services.project_service import ensure_project_for_repo

    legacy = PromptCluster(
        label="Legacy", state="project", domain="general",
        task_type="general", member_count=0,
    )
    db_session.add(legacy)
    await db_session.flush()

    # Pass a non-existent project ID — should fall through and create new project
    result = await ensure_project_for_repo(
        db_session, "user/first-repo", target_project_id="nonexistent-id",
    )
    assert result != legacy.id  # new project, Legacy untouched
    await db_session.refresh(legacy)
    assert legacy.label == "Legacy"


@pytest.mark.asyncio
async def test_two_repos_share_one_project(db_session: AsyncSession):
    """Two repos can be linked to the same project via target_project_id."""
    from app.services.project_service import ensure_project_for_repo

    # First repo creates a new project
    pid1 = await ensure_project_for_repo(db_session, "user/repo-a")
    await db_session.flush()

    # Second repo targets the same project explicitly
    pid2 = await ensure_project_for_repo(
        db_session, "user/repo-b", target_project_id=pid1,
    )
    assert pid1 == pid2  # same project


# ---------------------------------------------------------------------------
# AA1: Auto-associate curl/API callers to linked repo's project
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_effective_repo_explicit_wins(db_session: AsyncSession):
    """Explicit repo_full_name overrides session and global fallbacks."""
    from app.services.project_service import resolve_effective_repo

    # Seed a LinkedRepo that would otherwise be picked by the global fallback
    lr = LinkedRepo(
        session_id="some-session",
        full_name="user/linked-globally",
        branch="main",
        language="Python",
    )
    db_session.add(lr)
    await db_session.flush()

    result = await resolve_effective_repo(
        db_session,
        explicit_repo="user/explicit",
        session_id=None,
    )
    assert result == "user/explicit"


@pytest.mark.asyncio
async def test_resolve_effective_repo_session_cookie_resolves(db_session: AsyncSession):
    """session_id cookie picks the matching LinkedRepo when no explicit repo."""
    from app.services.project_service import resolve_effective_repo

    lr = LinkedRepo(
        session_id="cookie-sess", full_name="user/from-cookie",
        branch="main", language="Python",
    )
    db_session.add(lr)
    await db_session.flush()

    result = await resolve_effective_repo(
        db_session,
        explicit_repo=None,
        session_id="cookie-sess",
    )
    assert result == "user/from-cookie"


@pytest.mark.asyncio
async def test_resolve_effective_repo_falls_back_to_most_recent_linked(
    db_session: AsyncSession,
):
    """AA1: curl without cookies must auto-pick the most recently linked repo.

    This is the fix for the user-reported behaviour where `POST /api/optimize`
    curls landed on Legacy instead of associating to the live LinkedRepo.
    """
    from app.services.project_service import resolve_effective_repo
    from datetime import datetime, timedelta, timezone

    # Seed two LinkedRepos; the most recent should win even though neither
    # matches the (absent) session cookie.
    older = LinkedRepo(
        session_id="s1", full_name="user/older-repo",
        branch="main", language="Python",
        linked_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=5),
    )
    newer = LinkedRepo(
        session_id="s2", full_name="user/newer-repo",
        branch="main", language="Python",
        linked_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    db_session.add(older)
    db_session.add(newer)
    await db_session.flush()

    result = await resolve_effective_repo(
        db_session,
        explicit_repo=None,
        session_id=None,
    )
    assert result == "user/newer-repo"


@pytest.mark.asyncio
async def test_resolve_effective_repo_none_when_no_linked_repos(
    db_session: AsyncSession,
):
    """When no LinkedRepo rows exist, resolve_effective_repo returns None."""
    from app.services.project_service import resolve_effective_repo

    result = await resolve_effective_repo(
        db_session,
        explicit_repo=None,
        session_id=None,
    )
    assert result is None
