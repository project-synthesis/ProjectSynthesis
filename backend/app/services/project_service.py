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
    target_project_id: str | None = None,
) -> str:
    """Find or create a project node for the given repo.

    Args:
        db: Active database session.
        repo_full_name: GitHub repo in "owner/repo" format.
        target_project_id: If provided, link the repo to this existing project
            instead of auto-creating. Validates the project exists.

    Logic (when target_project_id is None):
    1. If LinkedRepo already has project_node_id set, return it.
    2. If a project node matching this repo label exists, reattach (re-link).
    3. Otherwise, create a new project node.

    Legacy is never renamed — it stays as the permanent home for
    pre-repo and non-repo work.

    Returns the project node ID (PromptCluster.id with state="project").
    """
    # Explicit project choice — validate and use directly
    if target_project_id:
        project = await db.get(PromptCluster, target_project_id)
        if project and project.state == "project":
            lr = (await db.execute(
                select(LinkedRepo).where(
                    LinkedRepo.full_name == repo_full_name,
                ).limit(1)
            )).scalar_one_or_none()
            if lr:
                lr.project_node_id = target_project_id
            logger.info(
                "Phase 2A: linked repo '%s' to existing project '%s' (%s)",
                repo_full_name, project.label, target_project_id[:8],
            )
            return target_project_id
        logger.warning(
            "Invalid target_project_id '%s' — falling through to auto-creation",
            target_project_id,
        )

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

    # Always create a new project — never rename Legacy.
    # Legacy is the permanent home for pre-repo and non-repo work.
    # A user may have hundreds of unrelated prompts before linking
    # their first repo; renaming Legacy would miscategorize all of them.
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

    project_node_id = (await db.execute(
        select(LinkedRepo.project_node_id)
        .where(LinkedRepo.full_name == repo_full_name)
        .limit(1)
    )).scalar_one_or_none()

    if project_node_id:
        return project_node_id

    return legacy_project_id
