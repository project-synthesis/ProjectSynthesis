import logging
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.models.github import LinkedRepo
from app.schemas.github import LinkRepoRequest
from app.services import github_service

logger = logging.getLogger(__name__)
router = APIRouter(tags=["github-repos"])

# Simple in-memory cache for repo lists
_repo_cache: dict[str, tuple[float, list]] = {}
CACHE_TTL_SECONDS = 300  # 5 minutes


def evict_repo_cache(session_id: str) -> None:
    """Remove cached repo list for a session (call on logout/disconnect)."""
    _repo_cache.pop(session_id, None)


async def _get_github_token(request: Request, session: AsyncSession) -> str:
    """Retrieve and decrypt the GitHub token for the current session."""
    session_id = request.session.get("session_id")
    if not session_id:
        raise HTTPException(status_code=401, detail="Not authenticated with GitHub")
    token = await github_service.get_token_for_session(session, session_id)
    if not token:
        raise HTTPException(status_code=401, detail="No GitHub token found. Connect GitHub first.")
    return token


@router.get("/api/github/repos")
async def list_repos(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """List repositories accessible with the user's GitHub token."""
    token = await _get_github_token(request, session)

    # Check cache
    cache_key = request.session.get("session_id", "")
    if cache_key in _repo_cache:
        cached_time, cached_repos = _repo_cache[cache_key]
        if time.time() - cached_time < CACHE_TTL_SECONDS:
            return cached_repos

    # Fetch from GitHub via service layer
    try:
        repos = await github_service.get_user_repos(token)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to fetch repos: {e}")

    _repo_cache[cache_key] = (time.time(), repos)
    return repos


@router.get("/api/github/repos/{owner}/{repo}/branches")
async def list_branches(
    owner: str,
    repo: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """List branches for a repository (max 50)."""
    token = await _get_github_token(request, session)
    branches = await github_service.get_repo_branches(token, f"{owner}/{repo}")
    return branches


@router.post("/api/github/repos/link")
async def link_repo(
    body: LinkRepoRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Link a GitHub repository for codebase-aware optimization."""
    token = await _get_github_token(request, session)
    session_id = request.session.get("session_id", "")

    # Validate repo access via service layer
    repo_data = await github_service.get_repo_info(token, body.full_name)
    if repo_data is None:
        raise HTTPException(status_code=404, detail="Repository not found or not accessible")

    branch = body.branch or repo_data.get("default_branch", "main")

    # Remove any existing linked repo for this session
    await session.execute(
        delete(LinkedRepo).where(LinkedRepo.session_id == session_id)
    )

    linked = LinkedRepo(
        session_id=session_id,
        full_name=body.full_name,
        branch=branch,
        default_branch=repo_data.get("default_branch"),
        language=repo_data.get("language"),
    )
    session.add(linked)
    await session.commit()

    return {
        "id": linked.id,
        "full_name": linked.full_name,
        "branch": linked.branch,
        "default_branch": linked.default_branch,
        "language": linked.language,
    }


@router.get("/api/github/repos/linked")
async def get_linked_repo(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Get the currently linked repository for this session."""
    session_id = request.session.get("session_id")
    if not session_id:
        return None

    result = await session.execute(
        select(LinkedRepo).where(LinkedRepo.session_id == session_id)
    )
    linked = result.scalar_one_or_none()
    if not linked:
        return None

    return {
        "id": linked.id,
        "full_name": linked.full_name,
        "branch": linked.branch,
        "default_branch": linked.default_branch,
        "language": linked.language,
        "linked_at": linked.linked_at.isoformat() if linked.linked_at else None,
    }


@router.delete("/api/github/repos/unlink")
async def unlink_repo(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Unlink the currently linked repository."""
    session_id = request.session.get("session_id")
    if not session_id:
        return {"unlinked": False, "reason": "No session"}

    await session.execute(
        delete(LinkedRepo).where(LinkedRepo.session_id == session_id)
    )
    await session.commit()

    return {"unlinked": True}
