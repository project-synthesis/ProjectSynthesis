"""GitHub repo listing and linking endpoints."""

import logging
import re

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import LinkedRepo
from app.routers.github_auth import _get_session_token
from app.services.github_client import GitHubClient

logger = logging.getLogger(__name__)

_REPO_NAME_RE = re.compile(r"^[a-zA-Z0-9_.-]+/[a-zA-Z0-9._-]+$")

router = APIRouter(prefix="/api/github", tags=["github"])


class LinkRepoRequest(BaseModel):
    full_name: str = Field(description="GitHub repo in 'owner/repo' format.")
    branch: str | None = Field(default=None, description="Branch to use (defaults to repo default branch).")


class RepoListResponse(BaseModel):
    repos: list[dict] = Field(description="List of GitHub repository objects from the API.")
    count: int = Field(description="Number of repos returned in this page.")


class LinkRepoResponse(BaseModel):
    full_name: str = Field(description="GitHub repo in 'owner/repo' format.")
    default_branch: str = Field(description="Repository default branch name.")
    branch: str = Field(description="Active branch for this link.")
    language: str | None = Field(default=None, description="Primary programming language.")


class LinkedRepoResponse(BaseModel):
    full_name: str = Field(description="GitHub repo in 'owner/repo' format.")
    default_branch: str = Field(description="Repository default branch name.")
    branch: str = Field(description="Active branch for this link.")
    language: str | None = Field(default=None, description="Primary programming language.")
    linked_at: str | None = Field(default=None, description="ISO 8601 timestamp when the repo was linked.")


class OkResponse(BaseModel):
    ok: bool = Field(default=True, description="Operation success indicator.")


@router.get("/repos")
async def list_repos(
    request: Request,
    per_page: int = Query(30, ge=1, le=100, description="Results per page."),
    page: int = Query(1, ge=1, description="Page number."),
    db: AsyncSession = Depends(get_db),
) -> RepoListResponse:
    """List GitHub repos for the authenticated user."""
    _session_id, token = await _get_session_token(request, db)
    github_client = GitHubClient()
    repos = await github_client.list_repos(token, per_page=per_page, page=page)
    return RepoListResponse(repos=repos, count=len(repos))


@router.post("/repos/link")
async def link_repo(
    body: LinkRepoRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> LinkRepoResponse:
    """Store a linked repo in DB for the session. Triggers background index (wired later)."""
    session_id, token = await _get_session_token(request, db)

    full_name = body.full_name
    branch = body.branch

    if not _REPO_NAME_RE.match(full_name):
        raise HTTPException(422, "Invalid repository name format. Expected 'owner/repo'.")

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

    return LinkRepoResponse(
        full_name=full_name,
        default_branch=default_branch,
        branch=active_branch,
        language=language,
    )


@router.get("/repos/linked")
async def get_linked(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> LinkedRepoResponse:
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
    return LinkedRepoResponse(
        full_name=linked.full_name,
        default_branch=linked.default_branch,
        branch=linked.branch,
        language=linked.language,
        linked_at=linked.linked_at.isoformat() if linked.linked_at else None,
    )


@router.delete("/repos/unlink")
async def unlink_repo(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> OkResponse:
    """Remove the linked repo for the current session."""
    session_id, _token = await _get_session_token(request, db)
    result = await db.execute(
        select(LinkedRepo).where(LinkedRepo.session_id == session_id)
    )
    linked = result.scalar_one_or_none()
    if linked:
        await db.delete(linked)
        await db.commit()
    return OkResponse()
