"""GitHub repo listing and linking endpoints."""

import asyncio
import logging
import re
from typing import Any, Coroutine

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import PROMPTS_DIR
from app.database import get_db
from app.models import LinkedRepo, PromptCluster
from app.routers.github_auth import _get_session_token
from app.services.github_client import GitHubApiError, GitHubClient

logger = logging.getLogger(__name__)

_REPO_NAME_RE = re.compile(r"^[a-zA-Z0-9_.-]+/[a-zA-Z0-9._-]+$")

router = APIRouter(prefix="/api/github", tags=["github"])

# Strong-reference set for fire-and-forget background tasks. asyncio's
# event loop only keeps WEAK references to tasks, so a coroutine
# spawned via bare `asyncio.create_task()` can be garbage-collected
# mid-await — silently dropping work like the explore-synthesis Haiku
# call that strands `synthesis_status='running'` in the DB and leaves
# the UI stuck on "INDEXING" forever. Hold tasks here; the
# `done_callback` discards them on completion (success or failure).
_background_tasks: set[asyncio.Task[Any]] = set()


def _spawn_bg_task(coro: Coroutine[Any, Any, Any]) -> asyncio.Task[Any]:
    """Launch a background task with a persistent strong reference.

    Wraps ``asyncio.create_task(coro)`` and registers the task in the
    module-level ``_background_tasks`` set so the event loop can't GC
    it mid-flight. The ``add_done_callback`` removes the task after it
    finishes, regardless of exception state, so the set doesn't leak.
    """
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return task


def _github_error_to_http(exc: GitHubApiError) -> HTTPException:
    """Convert a GitHubApiError to a FastAPI HTTPException with appropriate status code."""
    if exc.status_code == 401:
        logger.warning("GitHub token expired or revoked — user must reconnect: %s", exc.message)
        return HTTPException(401, "GitHub token expired or revoked. Please reconnect your GitHub account.")
    if exc.status_code == 403:
        return HTTPException(403, f"GitHub API access denied: {exc.message}")
    if exc.status_code == 404:
        return HTTPException(404, f"Not found on GitHub: {exc.message}")
    return HTTPException(502, f"GitHub API error ({exc.status_code}): {exc.message}")


async def _update_synthesis_status(
    repo_full_name: str,
    branch: str,
    *,
    status: str,
    synthesis_text: str | None = None,
    error: str | None = None,
) -> None:
    """Update synthesis_status (and optionally explore_synthesis/error) on RepoIndexMeta.

    Opens its own DB session — safe to call from background tasks.
    On ``status="ready"``, clears any previous error.
    On ``status="error"``, preserves existing ``explore_synthesis`` text so
    stale-but-valid synthesis from a prior run remains usable by enrichment.

    Also transitions ``index_phase`` so UI reflects the full pipeline:
        running   → index_phase="synthesizing"
        ready     → index_phase="ready"
        error     → index_phase="error"
        skipped   → index_phase="ready" (file index usable, no synthesis)
    """
    from app.database import async_session_factory
    from app.models import RepoIndexMeta
    from app.services.repo_index_service import _publish_phase_change

    # Map synthesis status → index_phase (UI state machine).
    _phase_by_status = {
        "running": "synthesizing",
        "ready": "ready",
        "error": "error",
        "skipped": "ready",
    }
    new_phase = _phase_by_status.get(status)

    try:
        async with async_session_factory() as db:
            meta_q = await db.execute(
                select(RepoIndexMeta).where(
                    RepoIndexMeta.repo_full_name == repo_full_name,
                    RepoIndexMeta.branch == branch,
                )
            )
            meta = meta_q.scalars().first()
            if meta:
                meta.synthesis_status = status
                meta.synthesis_error = error
                if synthesis_text is not None:
                    meta.explore_synthesis = synthesis_text
                if new_phase is not None:
                    meta.index_phase = new_phase
                await db.commit()

                if new_phase is not None:
                    await _publish_phase_change(
                        repo_full_name, branch,
                        phase=new_phase, status=meta.status,
                        files_seen=meta.files_seen or 0,
                        files_total=meta.files_total or 0,
                        error=error,
                    )
    except Exception:
        logger.debug("_update_synthesis_status failed for %s@%s", repo_full_name, branch, exc_info=True)


async def _run_explore_synthesis(
    repo_full_name: str,
    branch: str,
    token: str,
    provider: object | None,
) -> None:
    """Run explore synthesis and persist result to RepoIndexMeta.

    Synthesis runs on Sonnet (long-context reading comprehension). Handles
    all status transitions (running/ready/error/skipped) and never raises —
    all failures are logged and persisted as ``synthesis_status="error"``.
    """
    try:
        if provider:
            from app.database import async_session_factory
            from app.services.codebase_explorer import CodebaseExplorer
            from app.services.embedding_service import EmbeddingService
            from app.services.prompt_loader import PromptLoader
            from app.services.repo_index_service import RepoIndexService

            await _update_synthesis_status(repo_full_name, branch, status="running")

            es = EmbeddingService()
            gc = GitHubClient()
            async with async_session_factory() as db:
                explorer = CodebaseExplorer(
                    prompt_loader=PromptLoader(PROMPTS_DIR),
                    github_client=gc,
                    embedding_service=es,
                    provider=provider,  # type: ignore[arg-type]
                    repo_index_service=RepoIndexService(db, gc, es),
                )
                synthesis = await explorer.explore(
                    raw_prompt="Describe the project architecture, key patterns, and conventions",
                    repo_full_name=repo_full_name,
                    branch=branch,
                    token=token,
                )
            if synthesis:
                await _update_synthesis_status(
                    repo_full_name, branch, status="ready",
                    synthesis_text=synthesis,
                )
                logger.info(
                    "Explore synthesis stored for %s@%s (%d chars)",
                    repo_full_name, branch, len(synthesis),
                )
            else:
                await _update_synthesis_status(
                    repo_full_name, branch, status="error",
                    error="Explore returned empty result",
                )
                logger.warning(
                    "Explore synthesis returned None for %s@%s",
                    repo_full_name, branch,
                )
        else:
            await _update_synthesis_status(
                repo_full_name, branch, status="skipped",
                error="No LLM provider available at indexing time",
            )
            logger.info(
                "No LLM provider — skipping explore synthesis for %s@%s",
                repo_full_name, branch,
            )
    except Exception as exc:
        logger.warning("Explore synthesis failed (non-fatal) for %s@%s: %s", repo_full_name, branch, exc)
        try:
            await _update_synthesis_status(
                repo_full_name, branch, status="error",
                error=str(exc)[:500],
            )
        except Exception:
            logger.debug("Failed to persist synthesis error status", exc_info=True)


class LinkRepoRequest(BaseModel):
    full_name: str = Field(description="GitHub repo in 'owner/repo' format.")
    branch: str | None = Field(default=None, description="Branch to use (defaults to repo default branch).")
    project_id: str | None = Field(
        default=None,
        description="Existing project to add this repo to. If null, auto-creates.",
    )


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
    linked_at: str | None = Field(default=None)
    project_node_id: str | None = Field(default=None)  # ADR-005
    project_label: str | None = Field(default=None)  # ADR-005


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
    try:
        repos = await github_client.list_repos(token, per_page=per_page, page=page)
    except GitHubApiError as exc:
        raise _github_error_to_http(exc)
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
    except GitHubApiError as exc:
        logger.warning("Failed to fetch GitHub repo %s: %s", full_name, exc)
        raise _github_error_to_http(exc)

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

    # ADR-005 Phase 2A: ensure project node exists for this repo
    from app.services.project_service import ensure_project_for_repo
    project_node_id = await ensure_project_for_repo(db, full_name, target_project_id=body.project_id)
    linked.project_node_id = project_node_id

    await db.commit()

    # Emit project/taxonomy event
    try:
        from app.services.event_bus import event_bus
        event_bus.publish("taxonomy_changed", {
            "trigger": "project_created",
            "project_id": project_node_id,
            "repo": full_name,
        })
    except Exception:
        pass

    logger.info("Repo linked: %s branch=%s language=%s", full_name, active_branch, language)

    # Trigger background indexing for semantic search (explore phase)
    try:
        from app.database import async_session_factory
        from app.services.embedding_service import EmbeddingService
        from app.services.repo_index_service import RepoIndexService

        _idx_token = token  # already decrypted
        _idx_repo = full_name
        _idx_branch = active_branch
        _idx_provider = getattr(getattr(request.app.state, "routing", None), "state", None)
        _idx_provider = _idx_provider.provider if _idx_provider else None

        async def _bg_index():
            async with async_session_factory() as bg_db:
                svc = RepoIndexService(
                    db=bg_db,
                    github_client=GitHubClient(),
                    embedding_service=EmbeddingService(),
                )
                await svc.build_index(_idx_repo, _idx_branch, _idx_token)

            await _run_explore_synthesis(
                _idx_repo, _idx_branch, _idx_token, _idx_provider,
            )

        _spawn_bg_task(_bg_index())
        logger.info("Background indexing triggered for %s@%s", full_name, active_branch)
    except Exception as idx_exc:
        logger.warning("Failed to trigger background indexing: %s", idx_exc)

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
    # Resolve project label for display
    _project_label = None
    if linked.project_node_id:
        _proj = await db.get(PromptCluster, linked.project_node_id)
        if _proj:
            _project_label = _proj.label

    return LinkedRepoResponse(
        full_name=linked.full_name,
        default_branch=linked.default_branch,
        branch=linked.branch or linked.default_branch,
        language=linked.language,
        linked_at=linked.linked_at.isoformat() if linked.linked_at else None,
        project_node_id=linked.project_node_id,
        project_label=_project_label,
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


# ---------------------------------------------------------------------------
# File tree, content, branches, indexing (V2 parity)
# ---------------------------------------------------------------------------


@router.get("/repos/{owner}/{repo}/tree")
async def get_repo_tree(
    owner: str,
    repo: str,
    request: Request,
    branch: str = Query("main"),
    db: AsyncSession = Depends(get_db),
):
    """Get recursive file tree for a repository."""
    _session_id, token = await _get_session_token(request, db)
    client = GitHubClient()
    try:
        tree = await client.get_tree(token, f"{owner}/{repo}", branch)
    except GitHubApiError as exc:
        raise _github_error_to_http(exc)
    return {"tree": tree, "full_name": f"{owner}/{repo}", "branch": branch}


@router.get("/repos/{owner}/{repo}/files/{path:path}")
async def get_file_content(
    owner: str,
    repo: str,
    path: str,
    request: Request,
    branch: str = Query("main"),
    db: AsyncSession = Depends(get_db),
):
    """Read a single file from a repository."""
    _session_id, token = await _get_session_token(request, db)
    client = GitHubClient()
    try:
        content = await client.get_file_content(token, f"{owner}/{repo}", path, ref=branch)
    except GitHubApiError as exc:
        raise _github_error_to_http(exc)
    if content is None:
        raise HTTPException(404, f"File not found: {path}")
    return {"path": path, "content": content, "full_name": f"{owner}/{repo}"}


@router.get("/repos/{owner}/{repo}/branches")
async def list_branches(
    owner: str,
    repo: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """List branches for a repository."""
    _session_id, token = await _get_session_token(request, db)
    client = GitHubClient()
    try:
        branches = await client.list_branches(token, f"{owner}/{repo}")
    except GitHubApiError as exc:
        raise _github_error_to_http(exc)
    return {"branches": [b["name"] for b in branches]}


@router.get("/repos/index-status")
async def get_index_status(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Get indexing status for the linked repository."""
    session_id, _token = await _get_session_token(request, db)
    linked_q = await db.execute(
        select(LinkedRepo).where(LinkedRepo.session_id == session_id)
    )
    linked = linked_q.scalar_one_or_none()
    if not linked:
        return {"status": "no_repo", "file_count": 0, "indexed_at": None,
                "synthesis_status": None, "synthesis_error": None,
                "index_phase": None, "files_seen": 0, "files_total": 0}

    from app.models import RepoIndexMeta

    meta_q = await db.execute(
        select(RepoIndexMeta).where(
            RepoIndexMeta.repo_full_name == linked.full_name,
            RepoIndexMeta.branch == (linked.branch or linked.default_branch),
        ).order_by(RepoIndexMeta.indexed_at.desc())
    )
    meta = meta_q.scalars().first()
    if not meta:
        return {"status": "not_indexed", "file_count": 0, "indexed_at": None,
                "synthesis_status": None, "synthesis_error": None,
                "index_phase": None, "files_seen": 0, "files_total": 0}
    return {
        "status": meta.status,
        "file_count": meta.file_count or 0,
        "head_sha": meta.head_sha,
        "indexed_at": meta.indexed_at.isoformat() if meta.indexed_at else None,
        "synthesis_status": getattr(meta, "synthesis_status", "pending"),
        "synthesis_error": getattr(meta, "synthesis_error", None),
        "index_phase": getattr(meta, "index_phase", "pending"),
        "files_seen": getattr(meta, "files_seen", 0) or 0,
        "files_total": getattr(meta, "files_total", 0) or 0,
        "error_message": meta.error_message,
    }


@router.post("/repos/reindex")
async def reindex_repo(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Trigger re-indexing of the linked repository."""
    session_id, token = await _get_session_token(request, db)
    linked_q = await db.execute(
        select(LinkedRepo).where(LinkedRepo.session_id == session_id)
    )
    linked = linked_q.scalar_one_or_none()
    if not linked:
        raise HTTPException(404, "No linked repo")

    branch = linked.branch or linked.default_branch

    from app.database import async_session_factory
    from app.services.embedding_service import EmbeddingService
    from app.services.repo_index_service import RepoIndexService

    _reindex_provider = getattr(
        getattr(request.app.state, "routing", None), "state", None,
    )
    _reindex_provider = _reindex_provider.provider if _reindex_provider else None
    _reindex_repo = linked.full_name

    async def _bg_index():
        async with async_session_factory() as bg_db:
            svc = RepoIndexService(
                db=bg_db,
                github_client=GitHubClient(),
                embedding_service=EmbeddingService(),
            )
            await svc.build_index(_reindex_repo, branch, token)

        await _run_explore_synthesis(
            _reindex_repo, branch, token, _reindex_provider,
        )

    _spawn_bg_task(_bg_index())
    return {"status": "indexing", "repo": linked.full_name, "branch": branch}
