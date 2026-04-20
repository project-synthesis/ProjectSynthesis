"""Tests for ADR-005 / Hybrid migration (Legacy bootstrap + domain detach)."""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import LinkedRepo, Optimization, PromptCluster


@pytest.mark.asyncio
async def test_legacy_project_node_created(db_session: AsyncSession):
    """Migration creates a Legacy project node if none exists."""
    from app.main import _run_adr005_migration

    await _run_adr005_migration(db_session)
    await db_session.commit()

    result = await db_session.execute(
        select(PromptCluster).where(PromptCluster.state == "project")
    )
    project = result.scalar_one_or_none()
    assert project is not None
    assert project.label == "Legacy"
    assert project.state == "project"
    # Hybrid: Legacy itself lives at the root.
    assert project.parent_id is None


@pytest.mark.asyncio
async def test_root_domains_remain_detached(db_session: AsyncSession):
    """Hybrid: domains already at root (parent_id=None) stay at root."""
    domain = PromptCluster(
        label="test-domain",
        state="domain",
        domain="test",
        task_type="general",
        member_count=0,
    )
    db_session.add(domain)
    await db_session.flush()
    assert domain.parent_id is None

    from app.main import _run_adr005_migration

    await _run_adr005_migration(db_session)
    await db_session.commit()

    await db_session.refresh(domain)
    # Hybrid: domains MUST remain at root (parent_id IS NULL).
    assert domain.parent_id is None


@pytest.mark.asyncio
async def test_project_parented_domains_are_detached(db_session: AsyncSession):
    """Hybrid: legacy domains parented under a project are detached to root."""
    # Pre-existing project node.
    existing_project = PromptCluster(
        label="LegacyProject",
        state="project",
        domain="general",
        task_type="general",
        member_count=0,
    )
    db_session.add(existing_project)
    await db_session.flush()

    # Legacy-style data: domain parented under a project.
    parented_domain = PromptCluster(
        label="backend",
        state="domain",
        domain="backend",
        task_type="general",
        member_count=0,
        parent_id=existing_project.id,
    )
    db_session.add(parented_domain)
    await db_session.flush()
    assert parented_domain.parent_id == existing_project.id

    from app.main import _run_adr005_migration

    await _run_adr005_migration(db_session)
    await db_session.commit()

    await db_session.refresh(parented_domain)
    # Hybrid: the project→domain parent link is broken.
    assert parented_domain.parent_id is None


@pytest.mark.asyncio
async def test_migration_is_idempotent(db_session: AsyncSession):
    """Running migration twice doesn't create duplicate Legacy nodes."""
    from app.main import _run_adr005_migration

    await _run_adr005_migration(db_session)
    await db_session.commit()
    await _run_adr005_migration(db_session)
    await db_session.commit()

    result = await db_session.execute(
        select(PromptCluster).where(
            PromptCluster.state == "project",
            PromptCluster.label == "Legacy",
        )
    )
    projects = result.scalars().all()
    assert len(projects) == 1


@pytest.mark.asyncio
async def test_repo_linked_optimization_backfills_via_linkedrepo(
    db_session: AsyncSession,
):
    """Hybrid: Optimization.project_id resolves via repo_full_name → LinkedRepo."""
    from app.main import _run_adr005_migration

    project = PromptCluster(
        label="user/api-repo",
        state="project",
        domain="general",
        task_type="general",
        member_count=0,
    )
    db_session.add(project)
    await db_session.flush()

    lr = LinkedRepo(
        session_id="test-session",
        full_name="user/api-repo",
        default_branch="main",
        project_node_id=project.id,
    )
    db_session.add(lr)
    await db_session.flush()

    opt = Optimization(
        raw_prompt="test",
        status="completed",
        repo_full_name="user/api-repo",
    )
    db_session.add(opt)
    await db_session.flush()
    assert opt.project_id is None

    await _run_adr005_migration(db_session)
    await db_session.commit()

    await db_session.refresh(opt)
    assert opt.project_id == project.id


@pytest.mark.asyncio
async def test_repoless_optimization_falls_back_to_legacy(
    db_session: AsyncSession,
):
    """Hybrid: repo-less optimizations fall back to the Legacy project."""
    from app.main import _run_adr005_migration

    opt = Optimization(raw_prompt="test", status="completed")
    db_session.add(opt)
    await db_session.flush()
    assert opt.repo_full_name is None
    assert opt.project_id is None

    await _run_adr005_migration(db_session)
    await db_session.commit()

    legacy = (await db_session.execute(
        select(PromptCluster).where(
            PromptCluster.state == "project",
            PromptCluster.label == "Legacy",
        )
    )).scalar_one()

    await db_session.refresh(opt)
    assert opt.project_id == legacy.id


@pytest.mark.asyncio
async def test_unlinked_repo_gets_its_own_project_node(
    db_session: AsyncSession,
):
    """Hybrid Step 2.5: a LinkedRepo with NULL project_node_id earns a new
    project node matching its ``full_name`` — NOT bulk-assigned to Legacy.

    This is the invariant that makes "each project learns its own domain"
    work end-to-end on startup after resets or pre-ADR-005 migrations.
    """
    from app.main import _run_adr005_migration

    lr = LinkedRepo(
        session_id="test-session",
        full_name="alice/search-service",
        default_branch="main",
        project_node_id=None,  # the unlinked case
    )
    db_session.add(lr)
    await db_session.flush()

    opt = Optimization(
        raw_prompt="test",
        status="completed",
        repo_full_name="alice/search-service",
    )
    db_session.add(opt)
    await db_session.flush()

    await _run_adr005_migration(db_session)
    await db_session.commit()

    # A new project node was created for this repo.
    repo_project = (await db_session.execute(
        select(PromptCluster).where(
            PromptCluster.state == "project",
            PromptCluster.label == "alice/search-service",
        )
    )).scalar_one()
    assert repo_project.parent_id is None

    # LinkedRepo now points to the new project (not Legacy).
    await db_session.refresh(lr)
    assert lr.project_node_id == repo_project.id

    # The optimization is attributed to the new repo project, not Legacy.
    legacy = (await db_session.execute(
        select(PromptCluster).where(
            PromptCluster.state == "project",
            PromptCluster.label == "Legacy",
        )
    )).scalar_one()
    await db_session.refresh(opt)
    assert opt.project_id == repo_project.id
    assert opt.project_id != legacy.id


@pytest.mark.asyncio
async def test_unlinked_repo_without_full_name_is_skipped(
    db_session: AsyncSession,
):
    """Step 2.5 skips LinkedRepo rows with empty full_name (defensive)."""
    from app.main import _run_adr005_migration

    lr = LinkedRepo(
        session_id="test-session",
        full_name="",  # malformed / placeholder row
        default_branch="main",
        project_node_id=None,
    )
    db_session.add(lr)
    await db_session.flush()

    await _run_adr005_migration(db_session)
    await db_session.commit()

    # Only Legacy exists (no empty-label project got created).
    projects = (await db_session.execute(
        select(PromptCluster).where(PromptCluster.state == "project")
    )).scalars().all()
    labels = {p.label for p in projects}
    assert labels == {"Legacy"}


# =============================================================================
# ADR-005 B2 — migrate_optimizations() service tests
# =============================================================================


async def _make_projects(
    db: AsyncSession,
    *,
    labels: tuple[str, ...] = ("Legacy", "user/repo"),
) -> dict[str, str]:
    """Helper: create project nodes, return ``{label: id}`` mapping."""
    ids: dict[str, str] = {}
    for label in labels:
        node = PromptCluster(
            label=label,
            state="project",
            domain="general",
            task_type="general",
            member_count=0,
        )
        db.add(node)
        await db.flush()
        ids[label] = node.id
    return ids


async def _make_opt(
    db: AsyncSession,
    *,
    project_id: str | None,
    repo_full_name: str | None = None,
    prompt: str = "test prompt",
) -> Optimization:
    opt = Optimization(
        raw_prompt=prompt,
        status="completed",
        project_id=project_id,
        repo_full_name=repo_full_name,
    )
    db.add(opt)
    await db.flush()
    return opt


@pytest.mark.asyncio
async def test_b2_basic_migration_moves_all_matching_rows(
    db_session: AsyncSession,
):
    """B2: base case — every row on the source project is reassigned."""
    from app.services.project_service import migrate_optimizations

    ids = await _make_projects(db_session)
    for _ in range(3):
        await _make_opt(db_session, project_id=ids["Legacy"])

    moved = await migrate_optimizations(
        db_session,
        from_project_id=ids["Legacy"],
        to_project_id=ids["user/repo"],
    )
    await db_session.commit()
    assert moved == 3

    rows = (await db_session.execute(select(Optimization))).scalars().all()
    assert all(o.project_id == ids["user/repo"] for o in rows)


@pytest.mark.asyncio
async def test_b2_noop_when_from_equals_to(db_session: AsyncSession):
    """B2: from==to is a safe no-op and never touches the DB."""
    from app.services.project_service import migrate_optimizations

    ids = await _make_projects(db_session, labels=("Legacy",))
    await _make_opt(db_session, project_id=ids["Legacy"])

    moved = await migrate_optimizations(
        db_session,
        from_project_id=ids["Legacy"],
        to_project_id=ids["Legacy"],
    )
    assert moved == 0


@pytest.mark.asyncio
async def test_b2_invalid_destination_raises(db_session: AsyncSession):
    """B2: unknown or non-project destination raises ValueError."""
    from app.services.project_service import migrate_optimizations

    ids = await _make_projects(db_session, labels=("Legacy",))

    with pytest.raises(ValueError, match="not a valid project node"):
        await migrate_optimizations(
            db_session,
            from_project_id=ids["Legacy"],
            to_project_id="missing-id",
        )


@pytest.mark.asyncio
async def test_b2_invalid_destination_if_not_project_state(
    db_session: AsyncSession,
):
    """B2: destination must have state='project' — domain nodes rejected."""
    from app.services.project_service import migrate_optimizations

    ids = await _make_projects(db_session, labels=("Legacy",))
    domain = PromptCluster(
        label="backend",
        state="domain",
        domain="backend",
        task_type="general",
        member_count=0,
    )
    db_session.add(domain)
    await db_session.flush()

    with pytest.raises(ValueError, match="not a valid project node"):
        await migrate_optimizations(
            db_session,
            from_project_id=ids["Legacy"],
            to_project_id=domain.id,
        )


@pytest.mark.asyncio
async def test_b2_dry_run_returns_count_without_mutating(
    db_session: AsyncSession,
):
    """B2: dry_run returns candidate count but does not update any row."""
    from app.services.project_service import migrate_optimizations

    ids = await _make_projects(db_session)
    for _ in range(2):
        await _make_opt(db_session, project_id=ids["Legacy"])

    candidates = await migrate_optimizations(
        db_session,
        from_project_id=ids["Legacy"],
        to_project_id=ids["user/repo"],
        dry_run=True,
    )
    assert candidates == 2

    rows = (await db_session.execute(select(Optimization))).scalars().all()
    assert all(o.project_id == ids["Legacy"] for o in rows)


@pytest.mark.asyncio
async def test_b2_since_filter_scopes_by_created_at(db_session: AsyncSession):
    """B2: `since` timestamp only migrates rows created on/after the cutoff."""
    from datetime import datetime, timedelta, timezone

    from app.services.project_service import migrate_optimizations

    ids = await _make_projects(db_session)

    # Old row.
    old_opt = await _make_opt(db_session, project_id=ids["Legacy"])
    old_opt.created_at = datetime.utcnow() - timedelta(days=30)
    # New row (default created_at is "now").
    await _make_opt(db_session, project_id=ids["Legacy"])
    await db_session.flush()

    since = datetime.now(timezone.utc) - timedelta(days=7)
    moved = await migrate_optimizations(
        db_session,
        from_project_id=ids["Legacy"],
        to_project_id=ids["user/repo"],
        since=since,
    )
    await db_session.commit()
    assert moved == 1

    # Old row stays on Legacy; new row moves.
    await db_session.refresh(old_opt)
    assert old_opt.project_id == ids["Legacy"]


@pytest.mark.asyncio
async def test_b2_repo_full_name_is_null_guard(db_session: AsyncSession):
    """B2: `repo_full_name_is_null=True` skips opts tagged with another repo."""
    from app.services.project_service import migrate_optimizations

    ids = await _make_projects(db_session)
    # Repo-less Legacy opt — eligible.
    await _make_opt(db_session, project_id=ids["Legacy"], repo_full_name=None)
    # Legacy opt tagged with a different repo — NOT eligible under the guard.
    await _make_opt(
        db_session, project_id=ids["Legacy"], repo_full_name="some/other-repo"
    )

    moved = await migrate_optimizations(
        db_session,
        from_project_id=ids["Legacy"],
        to_project_id=ids["user/repo"],
        repo_full_name_is_null=True,
    )
    await db_session.commit()
    assert moved == 1

    # Tagged-repo row stays.
    rows = (await db_session.execute(
        select(Optimization).where(Optimization.repo_full_name == "some/other-repo")
    )).scalars().all()
    assert rows[0].project_id == ids["Legacy"]


@pytest.mark.asyncio
async def test_b2_zero_candidates_returns_zero(db_session: AsyncSession):
    """B2: empty source returns 0 without raising."""
    from app.services.project_service import migrate_optimizations

    ids = await _make_projects(db_session)
    moved = await migrate_optimizations(
        db_session,
        from_project_id=ids["Legacy"],
        to_project_id=ids["user/repo"],
    )
    assert moved == 0


@pytest.mark.asyncio
async def test_b2_emits_optimizations_migrated_event(db_session: AsyncSession):
    """B2: successful migration publishes an ``optimizations_migrated`` event."""
    import asyncio

    from app.services.event_bus import event_bus
    from app.services.project_service import migrate_optimizations

    # Prior tests that exercise lifespan may have flipped ``_shutting_down``,
    # which makes ``publish()`` silently return.  Reset so this test is
    # order-independent in the full suite.
    event_bus._shutting_down = False  # type: ignore[attr-defined]

    ids = await _make_projects(db_session)
    await _make_opt(db_session, project_id=ids["Legacy"])

    # Register a raw queue directly so we don't race with the async-generator
    # subscribe() setup before publish() fires.
    queue: asyncio.Queue = asyncio.Queue()
    event_bus._subscribers.add(queue)
    try:
        moved = await migrate_optimizations(
            db_session,
            from_project_id=ids["Legacy"],
            to_project_id=ids["user/repo"],
        )
        await db_session.commit()
        assert moved == 1

        # Drain the queue until we see the expected event or the queue empties.
        # Event bus payload uses "event" as the type key, not "type".
        found = False
        while not queue.empty():
            evt = queue.get_nowait()
            if evt.get("event") == "optimizations_migrated":
                assert evt["data"]["count"] == 1
                assert evt["data"]["from_project_id"] == ids["Legacy"]
                assert evt["data"]["to_project_id"] == ids["user/repo"]
                found = True
                break
        assert found, "optimizations_migrated event was not published"
    finally:
        event_bus._subscribers.discard(queue)
