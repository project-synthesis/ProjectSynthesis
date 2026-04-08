"""Tests for Phase 2A per-project Q metrics."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import PromptCluster


@pytest.mark.asyncio
async def test_load_active_nodes_with_project_filter(db: AsyncSession):
    """_load_active_nodes with project_id returns only project's clusters."""
    from app.services.taxonomy.warm_path import _load_active_nodes

    # Project A with one cluster
    proj_a = PromptCluster(
        label="proj-a", state="project", domain="general",
        task_type="general", member_count=0,
    )
    db.add(proj_a)
    await db.flush()

    domain_a = PromptCluster(
        label="general", state="domain", domain="general",
        task_type="general", member_count=0, parent_id=proj_a.id,
    )
    db.add(domain_a)
    await db.flush()

    cluster_a = PromptCluster(
        label="cluster-a", state="active", domain="general",
        task_type="coding", member_count=5, parent_id=domain_a.id,
    )
    db.add(cluster_a)

    # Project B with one cluster
    proj_b = PromptCluster(
        label="proj-b", state="project", domain="general",
        task_type="general", member_count=0,
    )
    db.add(proj_b)
    await db.flush()

    domain_b = PromptCluster(
        label="general-b", state="domain", domain="general",
        task_type="general", member_count=0, parent_id=proj_b.id,
    )
    db.add(domain_b)
    await db.flush()

    cluster_b = PromptCluster(
        label="cluster-b", state="active", domain="general",
        task_type="coding", member_count=3, parent_id=domain_b.id,
    )
    db.add(cluster_b)
    await db.flush()

    # Without filter: both clusters
    all_nodes = await _load_active_nodes(db)
    all_ids = {n.id for n in all_nodes}
    assert cluster_a.id in all_ids
    assert cluster_b.id in all_ids

    # With project_id filter: only project A's cluster
    proj_a_nodes = await _load_active_nodes(db, project_id=proj_a.id)
    proj_a_ids = {n.id for n in proj_a_nodes}
    assert cluster_a.id in proj_a_ids
    assert cluster_b.id not in proj_a_ids


@pytest.mark.asyncio
async def test_load_active_nodes_empty_project(db: AsyncSession):
    """Project with no domains returns empty list."""
    from app.services.taxonomy.warm_path import _load_active_nodes

    proj = PromptCluster(
        label="empty-project", state="project", domain="general",
        task_type="general", member_count=0,
    )
    db.add(proj)
    await db.flush()

    nodes = await _load_active_nodes(db, project_id=proj.id)
    assert nodes == []
