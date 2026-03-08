"""GitHub repository browsing endpoints.

Provides file tree traversal and file content reading for linked repositories.
Authentication uses the Starlette session (session_id stored in the encrypted
session cookie by github_auth.py).

OAuth authentication is handled by github_auth.py.
Repository list, link, and unlink are handled by github_repos.py.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.services.github_service import (
    get_repo_tree,
    get_token_for_session,
    read_file_by_path,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["github"])


def _get_session_id(request: Request) -> str | None:
    """Extract the session ID from the Starlette encrypted session."""
    return request.session.get("session_id")


@router.get("/api/github/repos/{owner}/{repo}/tree")
async def get_repo_tree_endpoint(
    owner: str,
    repo: str,
    request: Request,
    branch: str = Query("main"),
    session: AsyncSession = Depends(get_session),
):
    """Get the file tree of a repository.

    Returns a list of files with paths, SHA hashes, and sizes.
    Binary files, node_modules, and other excluded paths are filtered out.

    Args:
        owner: Repository owner (GitHub username or org).
        repo: Repository name.
        branch: Branch name (default: main).
    """
    session_id = _get_session_id(request)
    if not session_id:
        raise HTTPException(status_code=401, detail="GitHub not connected")

    token = await get_token_for_session(session, session_id)
    if not token:
        raise HTTPException(status_code=401, detail="GitHub not connected")

    full_name = f"{owner}/{repo}"
    tree = await get_repo_tree(token, full_name, branch)
    return {"tree": tree, "full_name": full_name, "branch": branch}


@router.get("/api/github/repos/{owner}/{repo}/files/{path:path}")
async def read_file(
    owner: str,
    repo: str,
    path: str,
    request: Request,
    branch: str = Query("main"),
    session: AsyncSession = Depends(get_session),
):
    """Read a file from a repository by path.

    Args:
        owner: Repository owner.
        repo: Repository name.
        path: File path within the repository.
        branch: Branch name (default: main).
    """
    session_id = _get_session_id(request)
    if not session_id:
        raise HTTPException(status_code=401, detail="GitHub not connected")

    token = await get_token_for_session(session, session_id)
    if not token:
        raise HTTPException(status_code=401, detail="GitHub not connected")

    full_name = f"{owner}/{repo}"

    content = await read_file_by_path(token, full_name, path, branch)
    if content is None:
        raise HTTPException(status_code=404, detail=f"File not found or unreadable: {path}")

    return {
        "path": path,
        "content": content,
        "size_bytes": len(content.encode("utf-8")),
        "sha": "",
    }
