"""GitHub repo listing and linking endpoints."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import LinkedRepo
from app.routers.github_auth import _get_session_token
from app.services.github_client import GitHubClient

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/github", tags=["github"])


@router.get("/repos")
async def list_repos(
    request: Request,
    per_page: int = 30,
    page: int = 1,
    db: AsyncSession = Depends(get_db),
):
    """List GitHub repos for the authenticated user."""
    _session_id, token = await _get_session_token(request, db)
    github_client = GitHubClient()
    repos = await github_client.list_repos(token, per_page=per_page, page=page)
    return {"repos": repos, "count": len(repos)}


@router.post("/repos/link")
async def link_repo(
    body: dict,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Store a linked repo in DB for the session. Triggers background index (wired later)."""
    session_id, token = await _get_session_token(request, db)

    full_name = body.get("full_name")
    if not full_name:
        raise HTTPException(
            400,
            "full_name is required. Provide the GitHub repository as 'owner/repo'.",
        )

    branch = body.get("branch")

    # Fetch repo info to get default branch and language
    github_client = GitHubClient()
    try:
        repo_info = await github_client.get_repo(token, full_name)
    except Exception:
        logger.warning("Failed to fetch GitHub repo: %s", full_name)
        raise HTTPException(
            404,
            "Repository '%s' not found or not accessible. Check the name and your permissions."
            % full_name,
        )

    default_branch = repo_info.get("default_branch", "main")
    language = repo_info.get("language")
    active_branch = branch or default_branch

    # Remove any existing linked repo for this session
    result = await db.execute(
        select(LinkedRepo).where(LinkedRepo.session_id == session_id)
    )
    existing = result.scalar_one_or_none()
    if existing:
        await db.delete(existing)
        await db.flush()

    linked = LinkedRepo(
        session_id=session_id,
        full_name=full_name,
        default_branch=default_branch,
        branch=active_branch,
        language=language,
    )
    db.add(linked)
    await db.commit()

    logger.info("Repo linked: %s branch=%s language=%s", full_name, active_branch, language)

    return {
        "full_name": full_name,
        "default_branch": default_branch,
        "branch": active_branch,
        "language": language,
    }


@router.get("/repos/linked")
async def get_linked(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Return the linked repo for the current session."""
    session_id, _token = await _get_session_token(request, db)
    result = await db.execute(
        select(LinkedRepo).where(LinkedRepo.session_id == session_id)
    )
    linked = result.scalar_one_or_none()
    if not linked:
        raise HTTPException(
            404,
            "No linked repository for this session. Link a repo first via POST /api/github/repos/link.",
        )
    return {
        "full_name": linked.full_name,
        "default_branch": linked.default_branch,
        "branch": linked.branch,
        "language": linked.language,
        "linked_at": linked.linked_at.isoformat() if linked.linked_at else None,
    }


@router.delete("/repos/unlink")
async def unlink_repo(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Remove the linked repo for the current session."""
    session_id, _token = await _get_session_token(request, db)
    result = await db.execute(
        select(LinkedRepo).where(LinkedRepo.session_id == session_id)
    )
    linked = result.scalar_one_or_none()
    if linked:
        await db.delete(linked)
        await db.commit()
    return {"ok": True}
