"""Project node management for ADR-005 multi-project isolation.

Handles project creation, re-linking, and resolution from repo name.
Called from routers/github_repos.py (link endpoint) and engine.py
(process_optimization project resolution).
"""

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import LinkedRepo, PromptCluster

logger = logging.getLogger(__name__)


async def ensure_project_for_repo(
    db: AsyncSession,
    repo_full_name: str,
) -> str:
    """Find or create a project node for the given repo.

    Logic:
    1. If LinkedRepo already has project_node_id set, return it.
    2. If only Legacy project exists (label="Legacy"), rename it to repo name.
    3. If a project node matching this repo label exists, reattach (re-link).
    4. Otherwise, create a new project node.

    Returns the project node ID (PromptCluster.id with state="project").
    """
    # Check if LinkedRepo already points to a project
    lr = (await db.execute(
        select(LinkedRepo).where(LinkedRepo.full_name == repo_full_name).limit(1)
    )).scalar_one_or_none()

    if lr and lr.project_node_id:
        return lr.project_node_id

    # Check for existing project node matching this repo label (re-link case)
    existing = (await db.execute(
        select(PromptCluster).where(
            PromptCluster.state == "project",
            PromptCluster.label == repo_full_name,
        ).limit(1)
    )).scalar_one_or_none()

    if existing:
        if lr:
            lr.project_node_id = existing.id
            await db.flush()
        return existing.id

    # Check if Legacy project exists and hasn't been renamed
    all_projects = (await db.execute(
        select(PromptCluster).where(PromptCluster.state == "project")
    )).scalars().all()

    legacy = None
    for p in all_projects:
        if p.label == "Legacy":
            legacy = p
            break

    if legacy and len(all_projects) == 1:
        # First repo: rename Legacy
        legacy.label = repo_full_name
        if lr:
            lr.project_node_id = legacy.id
        await db.flush()
        logger.info(
            "Phase 2A: renamed Legacy project to '%s' (%s)",
            repo_full_name, legacy.id[:8],
        )
        return legacy.id

    # Subsequent repos: create new project node
    new_project = PromptCluster(
        label=repo_full_name,
        state="project",
        domain="general",
        task_type="general",
        member_count=0,
    )
    db.add(new_project)
    await db.flush()

    if lr:
        lr.project_node_id = new_project.id

    logger.info(
        "Phase 2A: created project node '%s' (%s)",
        repo_full_name, new_project.id[:8],
    )
    return new_project.id


async def resolve_project_id(
    db: AsyncSession,
    repo_full_name: str | None,
    legacy_project_id: str | None = None,
) -> str | None:
    """Resolve project_id from repo_full_name.

    Args:
        db: Active database session.
        repo_full_name: From Optimization.repo_full_name.
        legacy_project_id: Cached Legacy project ID (avoids query).

    Returns:
        Project node ID, or legacy_project_id as fallback.
    """
    if not repo_full_name:
        return legacy_project_id

    lr = (await db.execute(
        select(LinkedRepo.project_node_id)
        .where(LinkedRepo.full_name == repo_full_name)
        .limit(1)
    )).scalar_one_or_none()

    if lr:
        return lr

    return legacy_project_id
