"""MCP server for Project Synthesis (synthesis_mcp).

Supports two transports:
  - Streamable HTTP (primary, modern): mounted at /mcp on the FastAPI app
  - WebSocket (secondary, backward-compat): mounted at /mcp/ws on the FastAPI app

Provider is resolved dynamically when mounted in FastAPI (via provider_getter), so
hot-reloaded API keys take effect immediately. Standalone mode detects once at startup.
GitHub tools accept explicit token parameter — no shared mutable session state.

All tools use the ``synthesis_`` prefix to avoid name collisions in multi-server
environments.  Tools return Pydantic models for structured output (outputSchema +
structuredContent) and raise ``ValueError`` for actionable error responses.
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncGenerator, AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional

import httpx
from sqlalchemy import select, update

from app._version import __version__
from app.config import settings
from app.database import async_session
from app.models.optimization import Optimization
from app.schemas.mcp_models import (
    BatchDeleteResult,
    BranchesResult,
    BranchItem,
    DeleteResult,
    FeedbackSubmitResult,
    GitHubCodeMatch,
    GitHubFileContent,
    GitHubRepoItem,
    GitHubSearchResult,
    OptimizationRecord,
    PaginationEnvelope,
    PipelineResult,
    RestoreResult,
    StatsResult,
    SubmitFeedbackInput,
)
from app.services.url_fetcher import fetch_url_contexts

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

_GITHUB_API = "https://api.github.com"

# Tool category registry — structured metadata for tool search and filtering.
TOOL_CATEGORIES: dict[str, dict] = {
    "synthesis_optimize":              {"category": "pipeline", "tags": ["llm", "execute"]},
    "synthesis_retry":                 {"category": "pipeline", "tags": ["llm", "retry"]},
    "synthesis_get_optimization":      {"category": "crud",     "tags": ["read"]},
    "synthesis_list_optimizations":    {"category": "crud",     "tags": ["read", "list"]},
    "synthesis_search_optimizations":  {"category": "crud",     "tags": ["read", "search"]},
    "synthesis_get_by_project":        {"category": "crud",     "tags": ["read", "project"]},
    "synthesis_get_stats":             {"category": "crud",     "tags": ["read", "analytics"]},
    "synthesis_tag_optimization":      {"category": "crud",     "tags": ["write", "metadata"]},
    "synthesis_delete_optimization":   {"category": "crud",     "tags": ["write", "lifecycle"]},
    "synthesis_batch_delete":          {"category": "crud",     "tags": ["write", "batch"]},
    "synthesis_list_trash":            {"category": "crud",     "tags": ["read", "trash"]},
    "synthesis_restore":               {"category": "crud",     "tags": ["write", "trash"]},
    "synthesis_github_list_repos":     {"category": "github",   "tags": ["read", "repos"]},
    "synthesis_github_read_file":      {"category": "github",   "tags": ["read", "files"]},
    "synthesis_github_search_code":    {"category": "github",   "tags": ["read", "search"]},
    "synthesis_submit_feedback":            {"category": "feedback", "tags": ["write"]},
    "synthesis_get_branches":               {"category": "refinement", "tags": ["read"]},
    "synthesis_get_adaptation_state":       {"category": "feedback", "tags": ["read"]},
    "synthesis_get_framework_performance":  {"category": "feedback", "tags": ["read"]},
    "synthesis_get_adaptation_summary":     {"category": "feedback", "tags": ["read"]},
}

# Stage → (progress_fraction, human_message) for pipeline progress reporting.
_STAGE_PROGRESS: dict[str, tuple[float, str]] = {
    "explore_result": (0.20, "Explore complete — codebase context gathered"),
    "analysis":       (0.40, "Analysis complete — prompt classified"),
    "strategy":       (0.55, "Strategy selected — framework chosen"),
    "optimization":   (0.80, "Optimization complete — prompt rewritten"),
    "validation":     (1.00, "Validation complete — scores computed"),
}

try:
    from mcp.server.fastmcp import Context, FastMCP
    from mcp.types import ToolAnnotations
    HAS_MCP = True
except ImportError:
    HAS_MCP = False
    logger.warning("mcp package not installed. MCP server will not be available.")


# ── Lifespan context ──────────────────────────────────────────────────────────

class MCPAppContext:
    """Lifespan context with dynamic provider resolution and shared httpx client.

    When ``provider_getter`` is supplied, the ``provider`` property resolves
    live on each access so MCP tools always see the current app.state.provider
    (updated by hot-reload).  When a static provider is given (standalone mode),
    it is returned directly.

    The ``http_client`` is a shared :class:`httpx.AsyncClient` for GitHub API
    calls, providing connection pooling across tool invocations.
    """

    def __init__(
        self,
        provider: object | None = None,
        provider_getter: Callable[[], object | None] | None = None,
        http_client: httpx.AsyncClient | None = None,
    ):
        self._static_provider = provider
        self._provider_getter = provider_getter
        self.http_client = http_client

    @property
    def provider(self) -> object | None:  # type: ignore[override]
        if self._provider_getter is not None:
            return self._provider_getter()
        return self._static_provider


@asynccontextmanager
async def _mcp_lifespan(server: FastMCP) -> AsyncIterator[MCPAppContext]:
    """Detect the LLM provider once at startup — shared across all tool calls."""
    from app.providers.detector import detect_provider
    provider = await detect_provider()
    logger.info("MCP server lifespan: using provider %s", provider.name)
    async with httpx.AsyncClient(timeout=30.0) as client:
        yield MCPAppContext(provider=provider, http_client=client)


# ── Shared DB helper ──────────────────────────────────────────────────────────

@asynccontextmanager
async def _opt_session(
    optimization_id: str, user_id: Optional[str] = None
) -> AsyncGenerator[tuple, None]:
    """Context manager yielding (session, opt) for a given optimization ID.

    Yields (session, None) when not found — callers must check for None.
    Keeps the session open so callers can mutate and commit within the same
    transaction without hitting DetachedInstanceError.

    Args:
        optimization_id: The UUID to look up.
        user_id: Optional owner filter. When set, only records owned by this
                 user are returned. Omit for unscoped access.
    """
    async with async_session() as session:
        query = select(Optimization).where(
            Optimization.id == optimization_id,
            Optimization.deleted_at.is_(None),
        )
        if user_id:
            query = query.where(Optimization.user_id == user_id)
        result = await session.execute(query)
        yield session, result.scalar_one_or_none()


# ── Module-level helpers ───────────────────────────────────────────────────────

def _not_found_msg(optimization_id: str) -> str:
    """Actionable 'not found' error message with next-step hint."""
    return (
        f"Optimization '{optimization_id}' not found. "
        "Use synthesis_list_optimizations or synthesis_search_optimizations to find valid IDs."
    )


def _github_error_msg(resp: httpx.Response, context: str) -> str:
    """Actionable GitHub API error message with status and hint."""
    hints = {
        401: "Check that the token is valid and not expired.",
        403: "The token lacks required permissions. Ensure it has 'Contents: Read' scope.",
        404: "Resource not found. Verify the repo name (owner/repo) and path are correct.",
        422: "GitHub rejected the request. Check parameter formatting.",
        429: "GitHub rate limit exceeded. Wait before retrying.",
    }
    hint = hints.get(resp.status_code, "Check the GitHub API documentation for status code details.")
    return f"{context} (HTTP {resp.status_code}). {hint}"


def _github_headers(token: str) -> dict:
    """Standard GitHub API request headers."""
    return {"Authorization": f"Bearer {token}", "Accept": "application/json"}


async def _get_mcp_token(token: str) -> str:
    """Return an explicit token if provided, otherwise generate an installation token.

    MCP callers can pass an empty string to let the platform bot credentials
    flow transparently via GitHub App installation token generation.
    """
    if token:
        return token
    try:
        from app.services.github_app_service import get_installation_token
        return await get_installation_token()
    except Exception as e:
        logger.warning("_get_mcp_token: installation token unavailable: %s", e)
        return ""


async def _resolve_mcp_user_id() -> str | None:
    """Resolve the default user_id for MCP-created records.

    The MCP server is localhost-only and has no auth layer.  To ensure
    records created via MCP tools appear in the frontend (which filters
    by the authenticated user), we resolve the most recently active user
    from the DB.  Returns None if no users exist yet.
    """
    from sqlalchemy import text as sa_text
    try:
        async with async_session() as s:
            result = await s.execute(sa_text(
                "SELECT user_id FROM optimizations "
                "WHERE user_id IS NOT NULL "
                "ORDER BY created_at DESC LIMIT 1"
            ))
            row = result.first()
            return row[0] if row else None
    except Exception:
        return None


async def _resolve_linked_repo() -> tuple[str | None, str | None, str | None]:
    """Auto-resolve the most recently linked repo for MCP callers.

    The MCP server is localhost-only and has no session cookie.  To enable
    codebase-aware optimization without explicit ``repo_full_name`` params,
    we look up the most recently linked repo from the DB and return its
    ``session_id`` so the existing token resolution path in
    ``codebase_explorer.py`` can decrypt the stored GitHub token.

    Returns:
        (repo_full_name, branch, session_id) or (None, None, None).
    """
    from sqlalchemy import text as sa_text
    try:
        async with async_session() as s:
            result = await s.execute(sa_text(
                "SELECT full_name, branch, session_id "
                "FROM linked_repos ORDER BY linked_at DESC LIMIT 1"
            ))
            row = result.first()
            if row:
                return row[0], row[1], row[2]
    except Exception:
        pass
    return None, None, None


async def _run_and_persist(
    provider,
    prompt: str,
    *,
    opt_id: str,
    strategy=None,
    repo_full_name=None,
    repo_branch=None,
    github_token=None,
    file_contexts=None,
    instructions=None,
    url_fetched=None,
    retry_of=None,
    project=None,
    title=None,
    progress_ctx=None,
    user_id: str | None = None,
    session_id: str | None = None,
) -> dict:
    """Create Optimization record, run pipeline, persist results.

    Args:
        progress_ctx: Optional MCP Context for reporting stage progress via
                      ``ctx.report_progress()``.  When provided, each pipeline
                      stage completion sends a progress update to the client.

    Returns:
        Stage events dict from ``PipelineAccumulator.results``.
    """
    import time

    from app.services.optimization_service import PipelineAccumulator
    from app.services.pipeline import run_pipeline

    async with async_session() as s:
        s.add(Optimization(
            id=opt_id,
            raw_prompt=prompt,
            status="running",
            linked_repo_full_name=repo_full_name,
            linked_repo_branch=repo_branch,
            retry_of=retry_of,
            project=project,
            title=title,
            user_id=user_id,
        ))
        await s.commit()

    acc = PipelineAccumulator()
    start = time.time()

    try:
        async for event_type, event_data in run_pipeline(
            provider=provider,
            raw_prompt=prompt,
            optimization_id=opt_id,
            strategy_override=strategy,
            repo_full_name=repo_full_name,
            repo_branch=repo_branch,
            session_id=session_id,
            github_token=github_token,
            file_contexts=file_contexts,
            instructions=instructions,
            url_fetched_contexts=url_fetched,
            user_id=user_id,
        ):
            acc.process_event(event_type, event_data)
            # Report progress for mapped stage events
            if progress_ctx and event_type in _STAGE_PROGRESS:
                frac, msg = _STAGE_PROGRESS[event_type]
                try:
                    await progress_ctx.report_progress(
                        progress=frac, total=1.0, message=msg,
                    )
                except Exception:
                    pass  # Progress reporting is best-effort

        updates = acc.finalize(provider.name, start)
    except Exception as e:
        logger.exception("MCP pipeline error for %s: %s", opt_id, e)
        updates = acc.finalize(provider.name, start, error=e)

    async with async_session() as s:
        result = await s.execute(
            update(Optimization)
            .where(Optimization.id == opt_id, Optimization.row_version == 0)
            .values(**updates, row_version=1)
        )
        if result.rowcount == 0:
            logger.error("Pipeline version conflict for opt %s", opt_id)
        await s.commit()

    return acc.results


# ── Server factory ────────────────────────────────────────────────────────────

def create_mcp_server(
    provider=None,
    provider_getter: Callable[[], object | None] | None = None,
) -> FastMCP:
    """Create and configure the synthesis_mcp server.

    Args:
        provider: Optional pre-detected LLMProvider (static). Ignored when
                  ``provider_getter`` is supplied.
        provider_getter: Callable returning the current LLMProvider (dynamic).
                         When supplied, MCP tools resolve the provider on each
                         call so hot-reloaded providers are picked up immediately.
    """
    if not HAS_MCP:
        raise ImportError("mcp package is required. Install with: pip install mcp")

    if provider_getter is not None:
        # Dynamic provider — resolves live on each tool call.
        @asynccontextmanager
        async def _injected_lifespan(server: FastMCP) -> AsyncIterator[MCPAppContext]:
            async with httpx.AsyncClient(timeout=30.0) as client:
                yield MCPAppContext(provider_getter=provider_getter, http_client=client)
        lifespan_fn = _injected_lifespan
    elif provider is not None:
        # Provider already known (called from FastAPI lifespan) — skip re-detection.
        @asynccontextmanager
        async def _injected_lifespan(server: FastMCP) -> AsyncIterator[MCPAppContext]:  # type: ignore[no-redef]
            async with httpx.AsyncClient(timeout=30.0) as client:
                yield MCPAppContext(provider=provider, http_client=client)
        lifespan_fn = _injected_lifespan
    else:
        lifespan_fn = _mcp_lifespan

    mcp = FastMCP(
        "synthesis_mcp",
        instructions="Project Synthesis: Multi-agent development platform with prompt optimization",
        host=settings.MCP_HOST,
        port=settings.MCP_PORT,
        lifespan=lifespan_fn,
        json_response=True,    # Streamable HTTP: return JSON (no SSE overhead)
        stateless_http=False,  # Keep sessions for multi-turn tool interactions
    )
    # FastMCP doesn't expose version in its constructor, but the low-level
    # Server does — set it directly so MCP initialize responses include it.
    mcp._mcp_server.version = __version__

    # Optimization service imports — query building, sort validation, stats
    from app.services.optimization_service import (
        OptimizationQuery,
        compute_stats,
        query_optimizations,
        validate_sort_params,
    )

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _new_run_id(prefix: str = "mcp") -> str:
        """Generate a short unique run ID for pipeline calls."""
        return f"{prefix}-{uuid.uuid4().hex[:12]}"

    def _get_ctx_parts(ctx: Context) -> tuple:
        """Extract provider and http_client from MCP context."""
        lc = ctx.request_context.lifespan_context
        return lc.provider, lc.http_client

    # ══════════════════════════════════════════════════════════════════════════
    # PIPELINE TOOLS
    # ══════════════════════════════════════════════════════════════════════════

    @mcp.tool(
        name="synthesis_optimize",
        annotations=ToolAnnotations(
            title="Optimize a Prompt",
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=False,
            openWorldHint=True,
        ),
    )
    async def synthesis_optimize(
        prompt: str,
        ctx: Context,
        strategy: Optional[str] = None,
        repo_full_name: Optional[str] = None,
        repo_branch: Optional[str] = None,
        github_token: Optional[str] = None,
        file_contexts: Optional[list[dict]] = None,
        instructions: Optional[list[str]] = None,
        url_contexts: Optional[list[str]] = None,
        project: Optional[str] = None,
        title: Optional[str] = None,
    ) -> PipelineResult:
        """Run the full prompt optimization pipeline (Explore → Analyze → Strategy → Optimize → Validate).

        Returns the optimized prompt and quality scores when complete.  The pipeline
        runs up to 5 stages.  Progress updates are sent to the client as each stage
        completes.

        When ``repo_full_name`` is set, the Explore stage reads the repository to
        ground the optimization in real codebase context.  Omit ``github_token`` to
        use the platform's bot credentials (installation token generated
        automatically).

        Args:
            prompt: The raw prompt text to optimize (required).
            ctx: MCP context (injected automatically).
            strategy: Framework override — one of: chain-of-thought, constraint-injection,
                      context-enrichment, CO-STAR, few-shot-scaffolding, persona-assignment,
                      RISEN, role-task-format, step-by-step, structured-output.
                      Omit to let the pipeline auto-select.
            repo_full_name: GitHub repo (owner/repo) for codebase-aware optimization.
            repo_branch: Branch to explore (defaults to 'main' when repo_full_name is set).
            github_token: GitHub token for repo exploration. Omit for platform bot credentials.
            file_contexts: File content objects: [{"name": "file.py", "content": "..."}].
            instructions: Output constraint strings (e.g. "always use bullet points").
                          These take absolute priority in the optimized prompt.
            url_contexts: URLs to fetch and inject as reference material.
            project: Project label for grouping optimizations in history.
            title: Human-readable title for this optimization run.

        Returns:
            PipelineResult with optimization_id and stage results (analysis, strategy,
            optimization, validation).  The ``optimization.optimized_prompt`` field
            contains the final result.  The ``validation.scores.overall_score`` field
            is 1.0–10.0.
        """
        prov, _ = _get_ctx_parts(ctx)
        if prov is None:
            raise ValueError(
                "No LLM provider configured. "
                "Configure an API key via the UI (Settings > Provider) or set ANTHROPIC_API_KEY."
            )

        # Auto-resolve linked repo when not explicitly provided
        resolved_session_id = None
        if not repo_full_name:
            resolved_repo, resolved_branch, resolved_session_id = await _resolve_linked_repo()
            if resolved_repo:
                repo_full_name = resolved_repo
                repo_branch = repo_branch or resolved_branch
        # Fallback to installation token when repo is set but no credentials
        if repo_full_name and not github_token and not resolved_session_id:
            github_token = await _get_mcp_token("")

        url_fetched = await fetch_url_contexts(url_contexts)
        opt_id = _new_run_id("mcp")
        mcp_user = await _resolve_mcp_user_id()
        results = await _run_and_persist(
            prov, prompt, opt_id=opt_id, strategy=strategy,
            repo_full_name=repo_full_name, repo_branch=repo_branch,
            github_token=github_token, file_contexts=file_contexts,
            instructions=instructions, url_fetched=url_fetched,
            project=project, title=title, progress_ctx=ctx,
            user_id=mcp_user, session_id=resolved_session_id,
        )
        return PipelineResult(optimization_id=opt_id, **results)

    @mcp.tool(
        name="synthesis_retry",
        annotations=ToolAnnotations(
            title="Retry Optimization",
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=False,
            openWorldHint=True,
        ),
    )
    async def synthesis_retry(
        optimization_id: str,
        ctx: Context,
        strategy: Optional[str] = None,
        github_token: Optional[str] = None,
        file_contexts: Optional[list[dict]] = None,
        instructions: Optional[list[str]] = None,
        url_contexts: Optional[list[str]] = None,
        user_id: Optional[str] = None,
    ) -> PipelineResult:
        """Re-run the optimization pipeline for an existing record.

        Loads the original prompt and repo settings from the stored record, then
        runs the full pipeline again.  Creates a new optimization record linked
        to the original via ``retry_of``.

        Progress updates are sent to the client as each stage completes.

        Args:
            optimization_id: UUID of the optimization to retry.
                             Use synthesis_list_optimizations to find valid IDs.
            ctx: MCP context (injected automatically).
            strategy: Framework override for this retry run. Omit to auto-select.
            github_token: GitHub token if the original had a linked repo.
                          Omit for platform bot credentials.
            file_contexts: File content objects: [{"name": "file.py", "content": "..."}].
            instructions: Output constraint strings for this retry run.
            url_contexts: URLs to fetch and inject as reference material.
            user_id: Owner filter. Omit for unscoped access (single-user/localhost mode).

        Returns:
            PipelineResult with new optimization_id and retry_of linking to the original.
        """
        async with _opt_session(optimization_id, user_id=user_id) as (_, orig):
            if not orig:
                raise ValueError(_not_found_msg(optimization_id))
            raw_prompt = orig.raw_prompt
            repo_full_name = orig.linked_repo_full_name
            repo_branch = orig.linked_repo_branch

        prov, _ = _get_ctx_parts(ctx)
        if prov is None:
            raise ValueError(
                "No LLM provider configured. "
                "Configure an API key via the UI (Settings > Provider) or set ANTHROPIC_API_KEY."
            )

        # Resolve token for explore when original had a linked repo
        resolved_session_id = None
        if repo_full_name and not github_token:
            _, _, resolved_session_id = await _resolve_linked_repo()
            if not resolved_session_id:
                github_token = await _get_mcp_token("")

        url_fetched = await fetch_url_contexts(url_contexts)
        opt_id = _new_run_id("mcp-retry")
        mcp_user = await _resolve_mcp_user_id()
        results = await _run_and_persist(
            prov, raw_prompt, opt_id=opt_id, strategy=strategy,
            repo_full_name=repo_full_name, repo_branch=repo_branch,
            github_token=github_token, file_contexts=file_contexts,
            instructions=instructions, url_fetched=url_fetched,
            retry_of=optimization_id, progress_ctx=ctx,
            user_id=mcp_user, session_id=resolved_session_id,
        )
        return PipelineResult(optimization_id=opt_id, retry_of=optimization_id, **results)

    # ══════════════════════════════════════════════════════════════════════════
    # CRUD TOOLS
    # ══════════════════════════════════════════════════════════════════════════

    @mcp.tool(
        name="synthesis_get_optimization",
        annotations=ToolAnnotations(
            title="Get Optimization by ID",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    async def synthesis_get_optimization(
        optimization_id: str,
        user_id: Optional[str] = None,
    ) -> OptimizationRecord:
        """Fetch a single optimization record by ID.

        Returns the full record including scores, prompts, stage durations,
        token usage, and metadata.

        Args:
            optimization_id: The UUID of the optimization to retrieve.
                             Use synthesis_list_optimizations to discover valid IDs.
            user_id: Owner filter. When set, only records owned by this user are
                     visible. Omit for unscoped access (single-user/localhost mode).

        Returns:
            OptimizationRecord with all fields from the optimization including
            scores (clarity, specificity, structure, faithfulness, conciseness,
            overall), prompts (raw and optimized), strategy info, and token usage.
        """
        async with _opt_session(optimization_id, user_id=user_id) as (_, opt):
            if not opt:
                raise ValueError(_not_found_msg(optimization_id))
            return OptimizationRecord(**opt.to_dict())

    @mcp.tool(
        name="synthesis_list_optimizations",
        annotations=ToolAnnotations(
            title="List Optimization History",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    async def synthesis_list_optimizations(
        project: Optional[str] = None,
        task_type: Optional[str] = None,
        min_score: Optional[float] = None,
        search: Optional[str] = None,
        limit: int = 20,
        offset: int = 0,
        sort: str = "created_at",
        order: str = "desc",
        user_id: Optional[str] = None,
    ) -> PaginationEnvelope:
        """List optimization history with filtering, sorting, and pagination.

        Supports filtering by project, task type, minimum score, and text search.
        Results are returned in a pagination envelope with ``has_more`` and
        ``next_offset`` for easy iteration.

        Args:
            project: Filter by project label (exact match).
            task_type: Filter by task classification (e.g. 'coding', 'writing', 'analysis').
            min_score: Only return optimizations with overall_score >= this value (1.0–10.0).
            search: Text search across raw_prompt and title fields.
            limit: Maximum results per page (default 20, max 100).
            offset: Number of records to skip for pagination (default 0).
            sort: Sort column — one of: created_at, overall_score, task_type,
                  updated_at, duration_ms, primary_framework, status,
                  refinement_turns, branch_count.
            order: Sort direction — 'asc' or 'desc' (default 'desc').
            user_id: Owner filter. Omit for unscoped access (single-user/localhost mode).

        Returns:
            PaginationEnvelope with total, count, offset, items, has_more, next_offset.
            Use next_offset with a follow-up call to paginate through results.
        """
        limit = min(max(1, limit), 100)
        try:
            validate_sort_params(sort, order)
        except ValueError as e:
            raise ValueError(str(e)) from e

        async with async_session() as session:
            envelope = await query_optimizations(session, OptimizationQuery(
                limit=limit, offset=offset, project=project, task_type=task_type,
                min_score=min_score, search=search, sort=sort, order=order,
                user_id=user_id,
            ))
        return PaginationEnvelope(**envelope)

    @mcp.tool(
        name="synthesis_search_optimizations",
        annotations=ToolAnnotations(
            title="Search Optimizations",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    async def synthesis_search_optimizations(
        query: str,
        limit: int = 10,
        offset: int = 0,
        user_id: Optional[str] = None,
    ) -> PaginationEnvelope:
        """Full-text search across prompt content, optimized prompts, and titles.

        Searches raw_prompt, optimized_prompt, and title fields.  Results are
        ordered by most recent first.

        Args:
            query: Search string to match against prompt and title text.
            limit: Maximum results per page (default 10, max 100).
            offset: Number of records to skip for pagination (default 0).
            user_id: Owner filter. Omit for unscoped access.

        Returns:
            PaginationEnvelope with matching optimization records.
        """
        limit = min(max(1, limit), 100)
        async with async_session() as session:
            envelope = await query_optimizations(session, OptimizationQuery(
                limit=limit, offset=offset, search=query,
                search_columns=3, user_id=user_id,
            ))
        return PaginationEnvelope(**envelope)

    @mcp.tool(
        name="synthesis_get_by_project",
        annotations=ToolAnnotations(
            title="Get Optimizations by Project",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    async def synthesis_get_by_project(
        project: str,
        include_prompts: bool = True,
        limit: int = 50,
        user_id: Optional[str] = None,
    ) -> PaginationEnvelope:
        """Get all optimizations belonging to a project, ordered by most recent.

        Args:
            project: Project label to filter by (exact match, case-sensitive).
            include_prompts: Include raw_prompt and optimized_prompt text (default True).
                             Set False for a compact summary view.
            limit: Maximum results to return (default 50).
            user_id: Owner filter. Omit for unscoped access.

        Returns:
            PaginationEnvelope with optimization records for the project.
            When include_prompts is False, raw_prompt and optimized_prompt
            fields are omitted from each item.
        """
        async with async_session() as session:
            envelope = await query_optimizations(session, OptimizationQuery(
                limit=limit, project=project, user_id=user_id,
            ))
        if not include_prompts:
            for d in envelope["items"]:
                d.pop("raw_prompt", None)
                d.pop("optimized_prompt", None)
        return PaginationEnvelope(**envelope)

    @mcp.tool(
        name="synthesis_get_stats",
        annotations=ToolAnnotations(
            title="Get Optimization Statistics",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    async def synthesis_get_stats(
        project: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> StatsResult:
        """Get aggregated statistics across optimization history.

        Returns counts, averages, breakdowns by task type, framework, and provider,
        plus token usage totals and improvement rate.

        Args:
            project: Scope stats to this project label. Omit for global stats.
            user_id: Owner filter. Omit for unscoped access.

        Returns:
            StatsResult with total_optimizations, average_score, task_type_breakdown,
            framework_breakdown, provider_breakdown, model_usage,
            codebase_aware_count, improvement_rate, token totals, and cost.
        """
        async with async_session() as session:
            stats = await compute_stats(session, project=project, user_id=user_id)
        return StatsResult(**stats)

    @mcp.tool(
        name="synthesis_tag_optimization",
        annotations=ToolAnnotations(
            title="Update Optimization Tags / Metadata",
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=False,
            openWorldHint=False,
        ),
    )
    async def synthesis_tag_optimization(
        optimization_id: str,
        add_tags: Optional[list[str]] = None,
        remove_tags: Optional[list[str]] = None,
        project: Optional[str] = None,
        title: Optional[str] = None,
        expected_version: Optional[int] = None,
        user_id: Optional[str] = None,
    ) -> OptimizationRecord:
        """Update tags, project label, or title on an existing optimization.

        Tags are merged (add_tags) or filtered (remove_tags) from the current tag
        list.  Pass ``project=""`` or ``title=""`` to clear those fields.

        Supports optimistic locking via ``expected_version``: if provided and the
        current ``row_version`` doesn't match, the update is rejected.

        Args:
            optimization_id: UUID of the optimization to update.
            add_tags: Tags to add (duplicates are ignored).
            remove_tags: Tags to remove (missing tags silently ignored).
            project: New project label. Pass empty string to clear.
            title: New title. Pass empty string to clear.
            expected_version: Expected row_version for optimistic locking.
                              Omit to skip the check.
            user_id: Owner filter. Omit for unscoped access.

        Returns:
            OptimizationRecord with the full updated record.
        """
        async with _opt_session(optimization_id, user_id=user_id) as (session, opt):
            if not opt:
                raise ValueError(_not_found_msg(optimization_id))
            if expected_version is not None and opt.row_version != expected_version:
                raise ValueError(
                    f"VERSION_CONFLICT: Record was modified (current version={opt.row_version}). "
                    "Refetch with synthesis_get_optimization and retry."
                )
            current_tags: list[str] = json.loads(opt.tags) if opt.tags else []
            if add_tags:
                current_tags = list(dict.fromkeys(current_tags + add_tags))
            if remove_tags:
                remove_set = set(remove_tags)
                current_tags = [t for t in current_tags if t not in remove_set]
            opt.tags = json.dumps(current_tags)
            if project is not None:
                opt.project = project or None
            if title is not None:
                opt.title = title or None
            opt.updated_at = datetime.now(timezone.utc)
            opt.row_version += 1
            # Capture dict BEFORE commit — SQLAlchemy expires attrs on commit,
            # and async sessions cannot lazy-load expired attrs (raises MissingGreenlet).
            updated = opt.to_dict()
            await session.commit()
        return OptimizationRecord(**updated)

    # ══════════════════════════════════════════════════════════════════════════
    # LIFECYCLE TOOLS (delete, restore, trash)
    # ══════════════════════════════════════════════════════════════════════════

    @mcp.tool(
        name="synthesis_delete_optimization",
        annotations=ToolAnnotations(
            title="Delete Optimization",
            readOnlyHint=False,
            destructiveHint=True,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    async def synthesis_delete_optimization(
        optimization_id: str,
        user_id: Optional[str] = None,
    ) -> DeleteResult:
        """Soft-delete an optimization record (sets deleted_at; purged after 7 days).

        Use synthesis_list_trash to see deleted items and synthesis_restore to undo
        within the 7-day recovery window.

        Args:
            optimization_id: UUID of the optimization to delete.
                             Use synthesis_list_optimizations to discover valid IDs.
            user_id: Owner filter. Omit for unscoped access.

        Returns:
            DeleteResult confirming deletion with the optimization ID.
        """
        from app.services.optimization_service import delete_optimization as svc_delete
        async with async_session() as session:
            deleted = await svc_delete(session, optimization_id, user_id=user_id)
            await session.commit()
        if not deleted:
            raise ValueError(_not_found_msg(optimization_id))
        return DeleteResult(id=optimization_id)

    @mcp.tool(
        name="synthesis_batch_delete",
        annotations=ToolAnnotations(
            title="Batch Delete Optimizations",
            readOnlyHint=False,
            destructiveHint=True,
            idempotentHint=False,
            openWorldHint=False,
        ),
    )
    async def synthesis_batch_delete(
        ids: list[str],
        user_id: Optional[str] = None,
    ) -> BatchDeleteResult:
        """Batch soft-delete multiple optimization records (sets deleted_at; purged after 7 days).

        All-or-nothing semantics: if any ID is not found, none are deleted.
        Maximum 50 IDs per request.

        Args:
            ids: List of optimization UUIDs to delete (1–50 items).
                 Use synthesis_list_optimizations to discover valid IDs.
            user_id: Owner filter. When set, all records must belong to this user.

        Returns:
            BatchDeleteResult with deleted_count and the list of deleted IDs.
        """
        if len(ids) < 1 or len(ids) > 50:
            raise ValueError(f"ids must contain 1–50 items, got {len(ids)}")

        from app.services.optimization_service import (
            batch_delete_optimizations as svc_batch_delete,
        )

        try:
            async with async_session() as session:
                deleted_ids = await svc_batch_delete(session, user_id, ids)
                await session.commit()
        except Exception as e:
            detail = getattr(e, "detail", str(e))
            raise ValueError(str(detail)) from e

        return BatchDeleteResult(deleted_count=len(deleted_ids), ids=deleted_ids)

    @mcp.tool(
        name="synthesis_list_trash",
        annotations=ToolAnnotations(
            title="List Trash",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    async def synthesis_list_trash(
        limit: int = 20,
        offset: int = 0,
        user_id: Optional[str] = None,
    ) -> PaginationEnvelope:
        """List soft-deleted optimizations still within the 7-day recovery window.

        Deleted records are permanently purged after 7 days.  Use synthesis_restore
        to recover an item before it expires.

        Args:
            limit: Maximum results per page (default 20, max 100).
            offset: Number of records to skip for pagination (default 0).
            user_id: Owner filter. Omit for unscoped access.

        Returns:
            PaginationEnvelope with trashed items.  Each item includes id,
            raw_prompt, title, deleted_at, and created_at.
        """
        async with async_session() as session:
            envelope = await query_optimizations(session, OptimizationQuery(
                limit=limit, offset=offset, user_id=user_id, deleted_only=True,
            ))
        return PaginationEnvelope(**envelope)

    @mcp.tool(
        name="synthesis_restore",
        annotations=ToolAnnotations(
            title="Restore Optimization from Trash",
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    async def synthesis_restore(
        optimization_id: str,
        user_id: Optional[str] = None,
    ) -> RestoreResult:
        """Restore a soft-deleted optimization from the trash (clears deleted_at).

        The record must still be within the 7-day recovery window.  Use
        synthesis_list_trash to discover restorable IDs.

        Args:
            optimization_id: UUID of the optimization to restore.
            user_id: Owner filter. Omit for unscoped access.

        Returns:
            RestoreResult confirming restoration with the optimization ID.
        """
        from app.services.optimization_service import restore_optimization as svc_restore
        async with async_session() as session:
            restored = await svc_restore(session, optimization_id, user_id=user_id)
            if restored:
                await session.commit()
        if not restored:
            raise ValueError(
                f"Optimization '{optimization_id}' not found in trash or recovery window expired. "
                "Use synthesis_list_trash to see restorable items."
            )
        return RestoreResult(id=optimization_id)

    # ══════════════════════════════════════════════════════════════════════════
    # GITHUB TOOLS — stateless: token optional, bot credentials used by default
    # ══════════════════════════════════════════════════════════════════════════

    @mcp.tool(
        name="synthesis_github_list_repos",
        annotations=ToolAnnotations(
            title="List GitHub Repositories",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=True,
        ),
    )
    async def synthesis_github_list_repos(
        ctx: Context,
        token: str = "",
        limit: int = 30,
    ) -> list[GitHubRepoItem]:
        """List repositories accessible with the given GitHub token.

        Returns repositories sorted by most recently updated.  Use ``full_name``
        from the results with synthesis_optimize's ``repo_full_name`` parameter
        to enable codebase-aware prompt optimization.

        Args:
            ctx: MCP context (injected automatically).
            token: GitHub token. Leave empty to use platform bot credentials
                   (installation token generated automatically).
            limit: Maximum repos to return (default 30, max 100).

        Returns:
            List of GitHubRepoItem with full_name, default_branch, language, private.
        """
        limit = min(max(1, limit), 100)
        token = await _get_mcp_token(token)
        _, http_client = _get_ctx_parts(ctx)
        resp = await http_client.get(
            f"{_GITHUB_API}/user/repos",
            params={"per_page": limit, "sort": "updated"},
            headers=_github_headers(token),
        )
        if resp.status_code != 200:
            raise ValueError(_github_error_msg(resp, "Failed to list repositories"))
        return [
            GitHubRepoItem(
                full_name=r["full_name"],
                default_branch=r.get("default_branch", "main"),
                language=r.get("language"),
                private=r["private"],
            )
            for r in resp.json()
        ]

    @mcp.tool(
        name="synthesis_github_read_file",
        annotations=ToolAnnotations(
            title="Read File from GitHub Repository",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=True,
        ),
    )
    async def synthesis_github_read_file(
        full_name: str,
        path: str,
        ctx: Context,
        token: str = "",
        branch: Optional[str] = None,
    ) -> GitHubFileContent:
        """Read a specific file from a GitHub repository.

        Returns the raw file content as plain text.  For binary files (images,
        compiled artifacts) the GitHub API returns an error.

        Args:
            full_name: Repository in 'owner/repo' format (e.g. 'anthropics/anthropic-sdk-python').
            path: File path within the repository (e.g. 'src/main.py', 'README.md').
            ctx: MCP context (injected automatically).
            token: GitHub token. Leave empty to use platform bot credentials.
            branch: Branch, tag, or commit SHA. Defaults to the repo's default branch.

        Returns:
            GitHubFileContent with the file content, path, and repo name.
        """
        token = await _get_mcp_token(token)
        _, http_client = _get_ctx_parts(ctx)
        params = {"ref": branch} if branch else {}
        resp = await http_client.get(
            f"{_GITHUB_API}/repos/{full_name}/contents/{path}",
            params=params,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github.v3.raw",
            },
        )
        if resp.status_code != 200:
            raise ValueError(_github_error_msg(resp, f"Failed to read '{path}' from {full_name}"))
        return GitHubFileContent(content=resp.text, path=path, repo=full_name)

    @mcp.tool(
        name="synthesis_github_search_code",
        annotations=ToolAnnotations(
            title="Search Code in GitHub Repository",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=True,
        ),
    )
    async def synthesis_github_search_code(
        full_name: str,
        pattern: str,
        ctx: Context,
        token: str = "",
        extension: Optional[str] = None,
    ) -> GitHubSearchResult:
        """Search for a text pattern across files in a GitHub repository.

        Uses the GitHub code search API.  Results are capped at 20 matches.
        Note: GitHub's code search index may lag behind the latest commits.

        Args:
            full_name: Repository in 'owner/repo' format.
            pattern: Literal text pattern or keyword to search for.
            ctx: MCP context (injected automatically).
            token: GitHub token. Leave empty to use platform bot credentials.
            extension: Restrict search to files with this extension (e.g. 'py', 'ts', 'md').

        Returns:
            GitHubSearchResult with a list of matches (path, name) and total count.
        """
        token = await _get_mcp_token(token)
        _, http_client = _get_ctx_parts(ctx)
        q = f"{pattern} repo:{full_name}"
        if extension:
            q += f" extension:{extension}"
        resp = await http_client.get(
            f"{_GITHUB_API}/search/code",
            params={"q": q},
            headers=_github_headers(token),
        )
        if resp.status_code != 200:
            raise ValueError(
                _github_error_msg(resp, f"Code search failed for pattern '{pattern}' in {full_name}")
            )
        items = resp.json().get("items", [])[:20]
        matches = [GitHubCodeMatch(path=i["path"], name=i["name"]) for i in items]
        return GitHubSearchResult(matches=matches, total=len(matches))

    # ══════════════════════════════════════════════════════════════════════════
    # FEEDBACK & REFINEMENT TOOLS
    # ══════════════════════════════════════════════════════════════════════════

    @mcp.tool(
        name="synthesis_submit_feedback",
        annotations=ToolAnnotations(
            title="Submit Optimization Feedback",
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    async def synthesis_submit_feedback(
        optimization_id: str,
        rating: int,
        dimension_overrides: Optional[dict] = None,
        corrected_issues: Optional[list[str]] = None,
        comment: Optional[str] = None,
    ) -> FeedbackSubmitResult:
        """Submit quality feedback (thumbs up/down) on an optimization.

        One feedback per optimization per user (upsert semantics).  Triggers
        background adaptation recomputation that tunes pipeline parameters
        (dimension weights, retry threshold, strategy affinities) based on
        accumulated feedback.

        Args:
            optimization_id: UUID of the optimization to rate.
            rating: Feedback rating: -1 (negative), 0 (neutral), 1 (positive).
            dimension_overrides: Per-dimension score overrides (1-10), e.g.
                                 {"clarity_score": 8, "specificity_score": 7}.
                                 Valid dimensions: clarity_score, specificity_score,
                                 structure_score, faithfulness_score, conciseness_score.
            corrected_issues: Issue IDs the user observed (e.g. 'lost_key_terms',
                             'too_verbose'). See CORRECTABLE_ISSUES for valid IDs.
            comment: Free-text feedback comment (max 2000 chars).

        Returns:
            FeedbackSubmitResult with the feedback ID and whether it was newly created.
        """
        from pydantic import ValidationError

        from app.services.adaptation_engine import schedule_adaptation_recompute
        from app.services.feedback_service import upsert_feedback

        # Validate through Pydantic model
        try:
            validated = SubmitFeedbackInput(
                optimization_id=optimization_id,
                rating=rating,
                dimension_overrides=dimension_overrides,
                corrected_issues=corrected_issues,
                comment=comment,
            )
        except ValidationError as e:
            raise ValueError(str(e)) from None

        async with _opt_session(validated.optimization_id) as (db, opt):
            if not opt:
                raise ValueError(_not_found_msg(validated.optimization_id))
            mcp_user = await _resolve_mcp_user_id() or "mcp"
            result = await upsert_feedback(
                optimization_id=validated.optimization_id,
                user_id=mcp_user,
                rating=validated.rating,
                dimension_overrides=validated.dimension_overrides,
                corrected_issues=validated.corrected_issues,
                comment=validated.comment,
                db=db,
            )
            await db.commit()

        # Trigger debounced adaptation recomputation
        schedule_adaptation_recompute(mcp_user)

        return FeedbackSubmitResult(**result)

    @mcp.tool(
        name="synthesis_get_branches",
        annotations=ToolAnnotations(
            title="List Refinement Branches",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    async def synthesis_get_branches(
        optimization_id: str,
    ) -> BranchesResult:
        """List all refinement branches for an optimization.

        Refinement branches represent alternative optimization paths created
        through iterative feedback.  Each branch has its own optimized prompt,
        scores, and turn history.

        Args:
            optimization_id: UUID of the optimization.

        Returns:
            BranchesResult with a list of branches and total count.
            Each branch includes id, label, status, turn_count, scores,
            and optimized_prompt.
        """
        from app.services.refinement_service import get_branches
        async with _opt_session(optimization_id) as (db, opt):
            if not opt:
                raise ValueError(_not_found_msg(optimization_id))
            branches_raw = await get_branches(optimization_id, db)
        branches = [BranchItem(**b) for b in branches_raw]
        return BranchesResult(branches=branches, total=len(branches))

    @mcp.tool(
        name="synthesis_get_adaptation_state",
        annotations=ToolAnnotations(
            title="Get User Adaptation State",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    async def synthesis_get_adaptation_state(
        user_id: str,
    ) -> str:
        """Get the current learned adaptation state for a user.

        Returns dimension weights, retry threshold, and strategy affinities
        computed from accumulated feedback history.  These parameters tune
        the pipeline's behavior for this user.

        Args:
            user_id: User identifier to look up adaptation state for.

        Returns:
            JSON with dimension_weights, strategy_affinities, retry_threshold,
            and feedback_count.  Returns an error if the user has no adaptation
            history.
        """
        from app.database import get_session_context
        from app.services.adaptation_engine import load_adaptation
        async with get_session_context() as db:
            state = await load_adaptation(user_id, db)
        if not state:
            raise ValueError(f"No adaptation state found for user '{user_id}'")
        # Return as JSON string — adaptation state shape varies and
        # AdaptationStateResponse from feedback.py is the canonical model.
        return json.dumps(state)

    @mcp.tool(
        name="synthesis_get_framework_performance",
        annotations=ToolAnnotations(
            title="Get Framework Performance",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    async def synthesis_get_framework_performance(
        task_type: str,
        user_id: Optional[str] = None,
    ) -> str:
        """Get framework performance data for a task type.

        Returns per-framework average scores, user rating averages,
        issue frequency, and sample counts for the given task type.

        Args:
            task_type: The task classification to query (e.g. 'coding',
                      'writing', 'analysis').
            user_id: User identifier. Omit to resolve from recent activity.

        Returns:
            JSON with task_type and a list of framework performance records.
        """
        from app.database import get_session_context
        from app.models.framework_performance import FrameworkPerformance
        from app.services.framework_scoring import format_framework_performance

        resolved_user = user_id or await _resolve_mcp_user_id() or "mcp"

        async with get_session_context() as db:
            stmt = select(FrameworkPerformance).where(
                FrameworkPerformance.user_id == resolved_user,
                FrameworkPerformance.task_type == task_type,
            )
            result = await db.execute(stmt)
            rows = result.scalars().all()

        items = format_framework_performance(rows, include_last_updated=False)
        return json.dumps({"task_type": task_type, "frameworks": items})

    @mcp.tool(
        name="synthesis_get_adaptation_summary",
        annotations=ToolAnnotations(
            title="Get Adaptation Summary",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    async def synthesis_get_adaptation_summary(
        user_id: Optional[str] = None,
    ) -> str:
        """Get a high-level adaptation summary for dashboard display.

        Returns feedback count, dimension weight priorities, active
        guardrails, framework preferences, and retry threshold.

        Args:
            user_id: User identifier. Omit to resolve from recent activity.

        Returns:
            JSON with adaptation summary including priorities,
            guardrails, and framework preferences.
        """
        from app.database import get_session_context
        from app.services.adaptation_engine import (
            build_adaptation_summary_data,
            load_adaptation,
        )

        resolved_user = user_id or await _resolve_mcp_user_id() or "mcp"

        async with get_session_context() as db:
            adaptation = await load_adaptation(resolved_user, db)

        return json.dumps(build_adaptation_summary_data(adaptation))

    return mcp


# ── WebSocket ASGI endpoint factory ──────────────────────────────────────────

def make_websocket_asgi(mcp: FastMCP) -> object:
    """Return a raw ASGI callable class instance for MCP WebSocket transport.

    Uses raw (scope, receive, send) — no private Starlette attrs.
    Returns a class instance (not a bare coroutine function) so that Starlette's
    WebSocketRoute does NOT wrap it in WebSocketSession.  Starlette only wraps
    endpoints where inspect.iscoroutinefunction() returns True; class instances
    with __call__ return False, so they are used directly as ASGI apps.
    """
    from mcp.server.websocket import websocket_server

    class _MCPWebSocketASGI:
        async def __call__(self, scope, receive, send) -> None:
            if scope["type"] != "websocket":
                return
            async with websocket_server(scope, receive, send) as (read_stream, write_stream):
                await mcp._mcp_server.run(
                    read_stream,
                    write_stream,
                    mcp._mcp_server.create_initialization_options(),
                )

    return _MCPWebSocketASGI()


# ── Standalone entry point ────────────────────────────────────────────────────

if __name__ == "__main__":
    import asyncio

    from app.database import create_tables

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    if not HAS_MCP:
        raise SystemExit("mcp package not installed. Run: pip install mcp")

    asyncio.run(create_tables())
    mcp = create_mcp_server()  # lifespan will auto-detect provider

    logger.info(
        "MCP server starting on http://%s:%d (streamable-HTTP)",
        settings.MCP_HOST,
        settings.MCP_PORT,
    )
    # mcp.run() is the official FastMCP standalone API — handles lifespan,
    # session_manager, and server startup without conflicting sub-app lifespans.
    # host/port are passed to FastMCP() at construction time in create_mcp_server()
    mcp.run(transport="streamable-http")
