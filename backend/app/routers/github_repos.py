"""GitHub repo listing and linking endpoints."""

import logging
import re

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import LinkedRepo, PromptCluster
from app.routers.github_auth import _get_session_token
from app.services.github_client import GitHubClient

logger = logging.getLogger(__name__)

_REPO_NAME_RE = re.compile(r"^[a-zA-Z0-9_.-]+/[a-zA-Z0-9._-]+$")

router = APIRouter(prefix="/api/github", tags=["github"])


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
        import asyncio

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

            # Step 2: Run explore synthesis and store in RepoIndexMeta
            try:
                from app.services.codebase_explorer import CodebaseExplorer
                from app.services.prompt_loader import PromptLoader

                if _idx_provider:
                    explorer = CodebaseExplorer(
                        prompt_loader=PromptLoader(),
                        github_client=GitHubClient(),
                        embedding_service=EmbeddingService(),
                        provider=_idx_provider,
                    )
                    synthesis = await explorer.explore(
                        raw_prompt="Describe the project architecture, key patterns, and conventions",
                        repo_full_name=_idx_repo,
                        branch=_idx_branch,
                        token=_idx_token,
                    )
                    if synthesis:
                        async with async_session_factory() as bg_db2:
                            from app.models import RepoIndexMeta
                            meta_q = await bg_db2.execute(
                                select(RepoIndexMeta).where(
                                    RepoIndexMeta.repo_full_name == _idx_repo,
                                    RepoIndexMeta.branch == _idx_branch,
                                )
                            )
                            meta = meta_q.scalar_one_or_none()
                            if meta:
                                meta.explore_synthesis = synthesis
                                await bg_db2.commit()
                                logger.info(
                                    "Explore synthesis stored for %s@%s (%d chars)",
                                    _idx_repo, _idx_branch, len(synthesis),
                                )
                else:
                    logger.debug("No LLM provider available — skipping explore synthesis")
            except Exception as synth_exc:
                logger.warning("Background explore synthesis failed (non-fatal): %s", synth_exc)

        asyncio.create_task(_bg_index())
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
        branch=linked.branch,
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
    branch: str = Query("main"),
    request: Request = None,
    db: AsyncSession = Depends(get_db),
):
    """Get recursive file tree for a repository."""
    _session_id, token = await _get_session_token(request, db)
    client = GitHubClient()
    tree = await client.get_tree(token, f"{owner}/{repo}", branch)
    return {"tree": tree, "full_name": f"{owner}/{repo}", "branch": branch}


@router.get("/repos/{owner}/{repo}/files/{path:path}")
async def get_file_content(
    owner: str,
    repo: str,
    path: str,
    branch: str = Query("main"),
    request: Request = None,
    db: AsyncSession = Depends(get_db),
):
    """Read a single file from a repository."""
    _session_id, token = await _get_session_token(request, db)
    client = GitHubClient()
    content = await client.get_file_content(token, f"{owner}/{repo}", path, ref=branch)
    if content is None:
        raise HTTPException(404, f"File not found: {path}")
    return {"path": path, "content": content, "full_name": f"{owner}/{repo}"}


@router.get("/repos/{owner}/{repo}/branches")
async def list_branches(
    owner: str,
    repo: str,
    request: Request = None,
    db: AsyncSession = Depends(get_db),
):
    """List branches for a repository."""
    _session_id, token = await _get_session_token(request, db)
    client = GitHubClient()
    branches = await client.list_branches(token, f"{owner}/{repo}")
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
        return {"status": "no_repo", "file_count": 0, "indexed_at": None}

    from app.models import RepoIndexMeta

    meta_q = await db.execute(
        select(RepoIndexMeta).where(
            RepoIndexMeta.repo_full_name == linked.full_name,
            RepoIndexMeta.branch == (linked.branch or linked.default_branch),
        )
    )
    meta = meta_q.scalar_one_or_none()
    if not meta:
        return {"status": "not_indexed", "file_count": 0, "indexed_at": None}
    return {
        "status": meta.status,
        "file_count": meta.file_count or 0,
        "head_sha": meta.head_sha,
        "indexed_at": meta.indexed_at.isoformat() if meta.indexed_at else None,
    }


@router.post("/repos/reindex")
async def reindex_repo(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Trigger re-indexing of the linked repository."""
    import asyncio

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

    async def _bg_index():
        async with async_session_factory() as bg_db:
            svc = RepoIndexService(
                db=bg_db,
                github_client=GitHubClient(),
                embedding_service=EmbeddingService(),
            )
            await svc.build_index(linked.full_name, branch, token)

    asyncio.create_task(_bg_index())
    return {"status": "indexing", "repo": linked.full_name, "branch": branch}
