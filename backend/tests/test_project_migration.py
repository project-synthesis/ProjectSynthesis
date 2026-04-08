"""Tests for ADR-005 Legacy project node migration."""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Optimization, PromptCluster


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


@pytest.mark.asyncio
async def test_domain_nodes_reparented(db_session: AsyncSession):
    """All domain nodes get parent_id pointing to Legacy project."""
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
    assert domain.parent_id is not None

    parent = await db_session.get(PromptCluster, domain.parent_id)
    assert parent.state == "project"


@pytest.mark.asyncio
async def test_migration_is_idempotent(db_session: AsyncSession):
    """Running migration twice doesn't create duplicate project nodes."""
    from app.main import _run_adr005_migration

    await _run_adr005_migration(db_session)
    await db_session.commit()
    await _run_adr005_migration(db_session)
    await db_session.commit()

    result = await db_session.execute(
        select(PromptCluster).where(PromptCluster.state == "project")
    )
    projects = result.scalars().all()
    assert len(projects) == 1


@pytest.mark.asyncio
async def test_optimization_project_id_backfilled(db_session: AsyncSession):
    """Optimizations get project_id from their cluster's project ancestry."""
    from app.main import _run_adr005_migration

    project = PromptCluster(label="TestProject", state="project", domain="general", task_type="general", member_count=0)
    db_session.add(project)
    await db_session.flush()

    domain = PromptCluster(
        label="backend", state="domain", domain="backend",
        task_type="general", member_count=0, parent_id=project.id,
    )
    db_session.add(domain)
    await db_session.flush()

    cluster = PromptCluster(
        label="API patterns", state="active", domain="backend",
        task_type="coding", member_count=1, parent_id=domain.id,
    )
    db_session.add(cluster)
    await db_session.flush()

    opt = Optimization(raw_prompt="test", status="completed", cluster_id=cluster.id)
    db_session.add(opt)
    await db_session.flush()
    assert opt.project_id is None

    await _run_adr005_migration(db_session)
    await db_session.commit()

    await db_session.refresh(opt)
    assert opt.project_id == project.id
