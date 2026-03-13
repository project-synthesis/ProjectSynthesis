"""MCP server for Project Synthesis.

Supports two transports:
  - Streamable HTTP (primary, modern): mounted at /mcp on the FastAPI app
  - WebSocket (secondary, backward-compat): mounted at /mcp/ws on the FastAPI app

Provider is resolved dynamically when mounted in FastAPI (via provider_getter), so
hot-reloaded API keys take effect immediately. Standalone mode detects once at startup.
GitHub tools accept explicit token parameter — no shared mutable session state.
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
from app.services.url_fetcher import fetch_url_contexts

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

_GITHUB_API = "https://api.github.com"

# H5: Tool category registry — structured metadata for future tool search.
# No runtime behavior change; enables category-based filtering and discovery.
TOOL_CATEGORIES: dict[str, dict] = {
    "optimize":                  {"category": "pipeline", "tags": ["llm", "execute"]},
    "retry_optimization":        {"category": "pipeline", "tags": ["llm", "retry"]},
    "get_optimization":          {"category": "crud",     "tags": ["read"]},
    "list_optimizations":        {"category": "crud",     "tags": ["read", "list"]},
    "search_optimizations":      {"category": "crud",     "tags": ["read", "search"]},
    "get_by_project":            {"category": "crud",     "tags": ["read", "project"]},
    "get_stats":                 {"category": "crud",     "tags": ["read", "analytics"]},
    "tag_optimization":          {"category": "crud",     "tags": ["write", "metadata"]},
    "delete_optimization":       {"category": "crud",     "tags": ["write", "lifecycle"]},
    "batch_delete_optimizations": {"category": "crud",    "tags": ["write", "batch"]},
    "list_trash":                {"category": "crud",     "tags": ["read", "trash"]},
    "restore_optimization":      {"category": "crud",     "tags": ["write", "trash"]},
    "github_list_repos":         {"category": "github",   "tags": ["read", "repos"]},
    "github_read_file":          {"category": "github",   "tags": ["read", "files"]},
    "github_search_code":        {"category": "github",   "tags": ["read", "search"]},
    "submit_feedback":           {"category": "feedback", "tags": ["write"]},
    "get_branches":              {"category": "refinement", "tags": ["read"]},
    "get_adaptation_state":      {"category": "feedback", "tags": ["read"]},
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
    """Lifespan context with dynamic provider resolution.

    When ``provider_getter`` is supplied, the ``provider`` property resolves
    live on each access so MCP tools always see the current app.state.provider
    (updated by hot-reload).  When a static provider is given (standalone mode),
    it is returned directly.
    """

    def __init__(
        self,
        provider: object | None = None,
        provider_getter: Callable[[], object | None] | None = None,
    ):
        self._static_provider = provider
        self._provider_getter = provider_getter

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
    yield MCPAppContext(provider=provider)


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
) -> dict:
    """Create Optimization record, run pipeline, persist results. Returns stage events dict."""
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
            github_token=github_token,
            file_contexts=file_contexts,
            instructions=instructions,
            url_fetched_contexts=url_fetched,
        ):
            acc.process_event(event_type, event_data)

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
    """Create and configure the MCP server.

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
        # B4: Dynamic provider — resolves live on each tool call.
        @asynccontextmanager
        async def _injected_lifespan(server: FastMCP) -> AsyncIterator[MCPAppContext]:
            yield MCPAppContext(provider_getter=provider_getter)
        lifespan_fn = _injected_lifespan
    elif provider is not None:
        # Provider already known (called from FastAPI lifespan) — skip re-detection.
        @asynccontextmanager
        async def _injected_lifespan(server: FastMCP) -> AsyncIterator[MCPAppContext]:  # type: ignore[no-redef]
            yield MCPAppContext(provider=provider)
        lifespan_fn = _injected_lifespan
    else:
        lifespan_fn = _mcp_lifespan

    mcp = FastMCP(
        "project-synthesis",
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

    def _not_found(optimization_id: str) -> str:
        """Actionable 'not found' error with next-step hint."""
        return json.dumps({
            "error": "Optimization not found",
            "id": optimization_id,
            "hint": "Use list_optimizations or search_optimizations to find valid IDs.",
        })

    def _github_error(resp: httpx.Response, context: str) -> str:
        """Actionable GitHub API error with status and hint."""
        hints = {
            401: "Check that the token is valid and not expired.",
            403: "The token lacks required permissions. Ensure it has 'Contents: Read' scope.",
            404: "Resource not found. Verify the repo name (owner/repo) and path are correct.",
            422: "GitHub rejected the request. Check parameter formatting.",
            429: "GitHub rate limit exceeded. Wait before retrying.",
        }
        return json.dumps({
            "error": context,
            "status": resp.status_code,
            "hint": hints.get(resp.status_code, "Check the GitHub API documentation for status code details."),
        })

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

    # ── Core optimization tools ───────────────────────────────────────────────

    @mcp.tool(
        name="optimize",
        annotations=ToolAnnotations(
            title= "Optimize a Prompt",
            readOnlyHint= False,
            destructiveHint= False,
            idempotentHint= False,
            openWorldHint= True,
        ),
    )
    async def optimize(
        prompt: str,
        strategy: Optional[str] = None,
        repo_full_name: Optional[str] = None,
        repo_branch: Optional[str] = None,
        github_token: Optional[str] = None,
        file_contexts: Optional[list[dict]] = None,  # N31: [{name, content}]
        instructions: Optional[list[str]] = None,    # N31: output constraints
        url_contexts: Optional[list[str]] = None,    # N31: URLs to fetch+inject
        project: Optional[str] = None,
        title: Optional[str] = None,
        ctx: Optional[Context] = None,
    ) -> str:
        """Run the full prompt optimization pipeline. Returns JSON with all stage results.

        Runs up to 5 stages: Explore (if repo linked), Analyze, Strategy, Optimize, Validate.
        The optimized prompt and quality scores are returned when complete.

        Args:
            prompt: The raw prompt text to optimize (required)
            strategy: Optional framework override — one of: chain-of-thought, constraint-injection,
                      context-enrichment, CO-STAR, few-shot-scaffolding, persona-assignment,
                      RISEN, role-task-format, step-by-step, structured-output.
                      Omit to let the pipeline auto-select.
            repo_full_name: GitHub repo (owner/repo) for codebase-aware optimization.
                            When set, the Explore stage reads the repo to ground the prompt.
            repo_branch: Branch to explore (defaults to 'main' when repo_full_name is set)
            github_token: GitHub token for repo exploration. Omit to use platform bot
                          credentials (installation token generated automatically).
            file_contexts: List of {"name": str, "content": str} dicts for attached files.
                           Content is injected into all pipeline stages for domain context.
            instructions: List of output constraint strings (e.g. "always use bullet points").
                          These take absolute priority in the optimized prompt.
            url_contexts: List of URLs to fetch and inject as reference material.
                          HTML is stripped; plain text is extracted automatically.
            project: Project label for grouping optimizations in history
            title: Human-readable title for this optimization run

        Returns:
            JSON with keys: analysis, strategy, optimization, validation.
            The 'optimization.optimized_prompt' field contains the final result.
            The 'validation.scores.overall_score' field is 1.0-10.0 (float, 1 decimal).
        """
        assert ctx is not None
        prov = ctx.request_context.lifespan_context.provider
        if prov is None:
            return json.dumps({
                "error": "No LLM provider configured",
                "hint": "Configure an API key via the UI (Settings > Provider) or set ANTHROPIC_API_KEY.",
            })
        url_fetched = await fetch_url_contexts(url_contexts)
        opt_id = _new_run_id("mcp")
        results = await _run_and_persist(
            prov, prompt, opt_id=opt_id, strategy=strategy,
            repo_full_name=repo_full_name, repo_branch=repo_branch,
            github_token=github_token, file_contexts=file_contexts,
            instructions=instructions, url_fetched=url_fetched,
            project=project, title=title,
        )
        return json.dumps({"optimization_id": opt_id, **results}, default=str, indent=2)

    @mcp.tool(
        name="get_optimization",
        annotations=ToolAnnotations(
            title= "Get Optimization by ID",
            readOnlyHint= True,
            destructiveHint= False,
            idempotentHint= True,
            openWorldHint= False,
        ),
    )
    async def get_optimization(
        optimization_id: str, user_id: Optional[str] = None
    ) -> str:
        """Get a specific optimization record by ID.

        Args:
            optimization_id: The UUID of the optimization to retrieve.
                             Use list_optimizations to discover valid IDs.
            user_id: Optional owner filter. When set, only records owned by this user
                     are visible. Omit for unscoped access (single-user/localhost mode).

        Returns:
            JSON with full optimization record including scores, prompts, and metadata.
            Returns {"error": ...} with a hint if not found.
        """
        async with _opt_session(optimization_id, user_id=user_id) as (_, opt):
            if not opt:
                return _not_found(optimization_id)
            return json.dumps(opt.to_dict(), indent=2)

    @mcp.tool(
        name="list_optimizations",
        annotations=ToolAnnotations(
            title= "List Optimization History",
            readOnlyHint= True,
            destructiveHint= False,
            idempotentHint= True,
            openWorldHint= False,
        ),
    )
    async def list_optimizations(
        project: Optional[str] = None,
        task_type: Optional[str] = None,
        min_score: Optional[float] = None,
        search: Optional[str] = None,
        limit: int = 20,
        offset: int = 0,
        sort: str = "created_at",
        order: str = "desc",
        user_id: Optional[str] = None,
    ) -> str:
        """List optimization history with filtering, sorting, and pagination.

        Args:
            project: Filter by project label (exact match)
            task_type: Filter by task classification (e.g. 'coding', 'writing', 'analysis')
            min_score: Only return optimizations with overall_score >= this value (1.0-10.0)
            search: Text search across raw_prompt and title fields
            limit: Maximum results per page (default 20, max 100)
            offset: Number of records to skip for pagination (default 0)
            sort: Sort column — one of: created_at, overall_score, task_type, updated_at
            order: Sort direction — 'asc' or 'desc' (default 'desc')
            user_id: Optional owner filter. When set, only records owned by this user
                     are visible. Omit for unscoped access (single-user/localhost mode).

        Returns:
            JSON with: items (array), total, count, offset, has_more, next_offset.
            Use next_offset with a follow-up call to paginate through results.
        """
        limit = min(max(1, limit), 100)
        try:
            validate_sort_params(sort, order)
        except ValueError as e:
            return json.dumps({"error": str(e)})

        async with async_session() as session:
            envelope = await query_optimizations(session, OptimizationQuery(
                limit=limit, offset=offset, project=project, task_type=task_type,
                min_score=min_score, search=search, sort=sort, order=order,
                user_id=user_id,
            ))
        return json.dumps(envelope, indent=2)

    @mcp.tool(
        name="get_stats",
        annotations=ToolAnnotations(
            title= "Get Optimization Statistics",
            readOnlyHint= True,
            destructiveHint= False,
            idempotentHint= True,
            openWorldHint= False,
        ),
    )
    async def get_stats(
        project: Optional[str] = None,
        user_id: Optional[str] = None,
        ctx: Optional[Context] = None,
    ) -> str:
        """Get aggregated statistics across optimization history.

        Args:
            project: Scope stats to this project label. Omit for global stats.
            user_id: Optional owner filter. When set, only records owned by this user
                     are visible. Omit for unscoped access (single-user/localhost mode).

        Returns:
            JSON with total_optimizations, average_score, task_type_breakdown,
            framework_breakdown, provider_breakdown, model_usage,
            codebase_aware_count, improvement_rate.
        """
        async with async_session() as session:
            stats = await compute_stats(session, project=project, user_id=user_id)
        return json.dumps(stats, indent=2)

    @mcp.tool(
        name="delete_optimization",
        annotations=ToolAnnotations(
            title= "Delete Optimization",
            readOnlyHint= False,
            destructiveHint= True,
            idempotentHint= True,
            openWorldHint= False,
        ),
    )
    async def delete_optimization(
        optimization_id: str, user_id: Optional[str] = None
    ) -> str:
        """Soft-delete an optimization record by ID (sets deleted_at; purged after 7 days).

        Args:
            optimization_id: The UUID of the optimization to delete.
                             Use list_optimizations to discover valid IDs.
            user_id: Optional owner filter. When set, only records owned by this user
                     are visible. Omit for unscoped access (single-user/localhost mode).

        Returns:
            JSON confirming deletion with {"deleted": true, "id": "..."}.
            Returns {"error": ...} with a hint if not found.
        """
        from app.services.optimization_service import delete_optimization as svc_delete
        async with async_session() as session:
            deleted = await svc_delete(session, optimization_id, user_id=user_id)
            await session.commit()
        if not deleted:
            return _not_found(optimization_id)
        return json.dumps({"deleted": True, "id": optimization_id})

    @mcp.tool(
        name="list_trash",
        annotations=ToolAnnotations(
            title="List Trash",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    async def list_trash(
        limit: int = 20,
        offset: int = 0,
        user_id: Optional[str] = None,
    ) -> str:
        """List soft-deleted optimizations still within the 7-day recovery window.

        Deleted records are permanently purged after 7 days. Use restore_optimization
        to recover an item before it expires.

        Args:
            limit: Maximum results per page (default 20, max 100).
            offset: Number of records to skip for pagination (default 0).
            user_id: Optional owner filter. When set, only records owned by this user
                     are visible. Omit for unscoped access (single-user/localhost mode).

        Returns:
            JSON pagination envelope: {total, count, offset, items, has_more,
            next_offset}. Each item includes id, raw_prompt, title, deleted_at,
            and created_at.
        """
        async with async_session() as session:
            envelope = await query_optimizations(session, OptimizationQuery(
                limit=limit, offset=offset, user_id=user_id, deleted_only=True,
            ))
        return json.dumps(envelope, indent=2, default=str)

    @mcp.tool(
        name="restore_optimization",
        annotations=ToolAnnotations(
            title="Restore Optimization",
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    async def restore_optimization(
        optimization_id: str, user_id: Optional[str] = None
    ) -> str:
        """Restore a soft-deleted optimization from the trash (clears deleted_at).

        The record must still be within the 7-day recovery window. Use list_trash
        to discover restorable IDs.

        Args:
            optimization_id: The UUID of the optimization to restore.
            user_id: Optional owner filter. When set, only records owned by this user
                     are visible. Omit for unscoped access (single-user/localhost mode).

        Returns:
            JSON {"restored": true, "id": "..."} on success.
            Returns {"error": ...} if not found in trash or recovery window expired.
        """
        from app.services.optimization_service import restore_optimization as svc_restore
        async with async_session() as session:
            restored = await svc_restore(session, optimization_id, user_id=user_id)
            if restored:
                await session.commit()
        if not restored:
            return json.dumps({
                "error": f"Optimization '{optimization_id}' not found in trash or recovery window expired.",
                "hint": "Use list_trash to see restorable items.",
            })
        return json.dumps({"restored": True, "id": optimization_id})

    @mcp.tool(
        name="batch_delete_optimizations",
        annotations=ToolAnnotations(
            title="Batch Delete Optimizations",
            readOnlyHint=False,
            destructiveHint=True,
            idempotentHint=False,
            openWorldHint=False,
        ),
    )
    async def batch_delete_optimizations(
        ids: list[str], user_id: Optional[str] = None
    ) -> str:
        """Batch soft-delete multiple optimization records (sets deleted_at; purged after 7 days).

        All-or-nothing: if any ID is not found, none are deleted.
        Maximum 50 IDs per request.

        Args:
            ids: List of optimization UUIDs to delete (1–50 items).
                 Use list_optimizations to discover valid IDs.
            user_id: Optional owner filter. When set, all records must belong to
                     this user. Omit for unscoped access (single-user/localhost mode).

        Returns:
            JSON {"deleted_count": N, "ids": [...]} on success.
            Returns {"error": ...} on validation failure.
        """
        if len(ids) < 1 or len(ids) > 50:
            return json.dumps({
                "error": "ids must contain 1–50 items",
                "count": len(ids),
            })

        from app.services.optimization_service import (
            batch_delete_optimizations as svc_batch_delete,
        )

        try:
            async with async_session() as session:
                deleted_ids = await svc_batch_delete(session, user_id, ids)
                await session.commit()
        except Exception as e:
            # Service raises HTTPException for 404/403 — extract detail
            detail = getattr(e, "detail", str(e))
            status = getattr(e, "status_code", 500)
            return json.dumps({"error": detail, "status": status})

        return json.dumps({"deleted_count": len(deleted_ids), "ids": deleted_ids})

    @mcp.tool(
        name="search_optimizations",
        annotations=ToolAnnotations(
            title= "Search Optimizations",
            readOnlyHint= True,
            destructiveHint= False,
            idempotentHint= True,
            openWorldHint= False,
        ),
    )
    async def search_optimizations(
        query: str,
        limit: int = 10,
        offset: int = 0,
        user_id: Optional[str] = None,
    ) -> str:
        """Full-text search across prompt content, optimized prompts, and titles.

        Searches raw_prompt, optimized_prompt, and title fields. Ordered by most recent.

        Args:
            query: Search string to match against prompt and title text
            limit: Maximum results per page (default 10, max 100)
            offset: Number of records to skip for pagination (default 0)
            user_id: Optional owner filter. When set, only records owned by this user
                     are visible. Omit for unscoped access (single-user/localhost mode).

        Returns:
            JSON with: items, total, count, offset, has_more, next_offset.
        """
        limit = min(max(1, limit), 100)
        async with async_session() as session:
            envelope = await query_optimizations(session, OptimizationQuery(
                limit=limit, offset=offset, search=query,
                search_columns=3, user_id=user_id,
            ))
        return json.dumps(envelope, indent=2)

    @mcp.tool(
        name="get_by_project",
        annotations=ToolAnnotations(
            title= "Get Optimizations by Project",
            readOnlyHint= True,
            destructiveHint= False,
            idempotentHint= True,
            openWorldHint= False,
        ),
    )
    async def get_by_project(
        project: str,
        include_prompts: bool = True,
        limit: int = 50,
        user_id: Optional[str] = None,
    ) -> str:
        """Get all optimizations belonging to a project, ordered by most recent.

        Args:
            project: Project label to filter by (exact match, case-sensitive)
            include_prompts: Include raw_prompt and optimized_prompt text (default True).
                             Set False for a compact summary view.
            limit: Maximum results to return (default 50)
            user_id: Optional owner filter. When set, only records owned by this user
                     are visible. Omit for unscoped access (single-user/localhost mode).

        Returns:
            JSON array of optimization records. Empty array if project has no records.
        """
        async with async_session() as session:
            envelope = await query_optimizations(session, OptimizationQuery(
                limit=limit, project=project, user_id=user_id,
            ))
        items = envelope["items"]
        if not include_prompts:
            for d in items:
                d.pop("raw_prompt", None)
                d.pop("optimized_prompt", None)
        return json.dumps(items, indent=2)

    @mcp.tool(
        name="tag_optimization",
        annotations=ToolAnnotations(
            title= "Update Optimization Tags / Metadata",
            readOnlyHint= False,
            destructiveHint= False,
            idempotentHint= False,
            openWorldHint= False,
        ),
    )
    async def tag_optimization(
        optimization_id: str,
        add_tags: Optional[list[str]] = None,
        remove_tags: Optional[list[str]] = None,
        project: Optional[str] = None,
        title: Optional[str] = None,
        expected_version: Optional[int] = None,
        user_id: Optional[str] = None,
    ) -> str:
        """Update tags, project label, or title on an existing optimization.

        Tags are merged (add_tags) or filtered (remove_tags) from the current tag list.
        Pass project="" or title="" to clear those fields.

        Args:
            optimization_id: UUID of the optimization to update
            add_tags: Tags to add (duplicates are ignored)
            remove_tags: Tags to remove (missing tags are silently ignored)
            project: New project label. Pass empty string to clear.
            title: New title. Pass empty string to clear.
            expected_version: Expected row_version for optimistic locking. If provided and
                              does not match the current row_version, the update is rejected
                              with a VERSION_CONFLICT error. Omit to skip the check.
            user_id: Optional owner filter. When set, only records owned by this user
                     are visible. Omit for unscoped access (single-user/localhost mode).

        Returns:
            JSON with the full updated optimization record.
            Returns {"error": ...} with a hint if not found.
        """
        async with _opt_session(optimization_id, user_id=user_id) as (session, opt):
            if not opt:
                return _not_found(optimization_id)
            if expected_version is not None and opt.row_version != expected_version:
                return json.dumps({
                    "error": "VERSION_CONFLICT",
                    "message": "Record was modified. Refetch and retry.",
                    "current_version": opt.row_version,
                })
            current_tags: list[str] = json.loads(opt.tags) if opt.tags else []
            if add_tags:
                current_tags = list(dict.fromkeys(current_tags + add_tags))  # dedup, preserve order
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
        return json.dumps(updated, indent=2)

    @mcp.tool(
        name="retry_optimization",
        annotations=ToolAnnotations(
            title= "Retry Optimization",
            readOnlyHint= False,
            destructiveHint= False,
            idempotentHint= False,
            openWorldHint= True,
        ),
    )
    async def retry_optimization(
        optimization_id: str,
        strategy: Optional[str] = None,
        github_token: Optional[str] = None,
        file_contexts: Optional[list[dict]] = None,  # N31: [{name, content}]
        instructions: Optional[list[str]] = None,    # N31: output constraints
        url_contexts: Optional[list[str]] = None,    # N31: URLs to fetch+inject
        user_id: Optional[str] = None,
        ctx: Optional[Context] = None,
    ) -> str:
        """Re-run the optimization pipeline for an existing record with an optional strategy override.

        Loads the original prompt and repo settings from the stored record, then
        runs the full pipeline again. Creates a new optimization record.

        Args:
            optimization_id: UUID of the optimization to retry.
                             Use list_optimizations to find valid IDs.
            strategy: Optional framework override for this retry run.
                      Omit to let the pipeline auto-select again.
            github_token: GitHub token if the original had a linked repo and you want
                          the Explore stage to run. Omit to use platform bot credentials.
            file_contexts: List of {"name": str, "content": str} dicts for attached files.
            instructions: List of output constraint strings for this retry run.
            url_contexts: List of URLs to fetch and inject as reference material.
            user_id: Optional owner filter. When set, only records owned by this user
                     are visible. Omit for unscoped access (single-user/localhost mode).

        Returns:
            JSON with keys: analysis, strategy, optimization, validation.
            Returns {"error": ...} with a hint if not found.
        """
        # B6: capture all ORM values as locals inside the session block
        async with _opt_session(optimization_id, user_id=user_id) as (_, orig):
            if not orig:
                return _not_found(optimization_id)
            raw_prompt = orig.raw_prompt
            repo_full_name = orig.linked_repo_full_name
            repo_branch = orig.linked_repo_branch

        assert ctx is not None
        prov = ctx.request_context.lifespan_context.provider
        if prov is None:
            return json.dumps({
                "error": "No LLM provider configured",
                "hint": "Configure an API key via the UI (Settings > Provider) or set ANTHROPIC_API_KEY.",
            })
        url_fetched = await fetch_url_contexts(url_contexts)
        opt_id = _new_run_id("mcp-retry")
        results = await _run_and_persist(
            prov, raw_prompt, opt_id=opt_id, strategy=strategy,
            repo_full_name=repo_full_name, repo_branch=repo_branch,
            github_token=github_token, file_contexts=file_contexts,
            instructions=instructions, url_fetched=url_fetched,
            retry_of=optimization_id,
        )
        return json.dumps({"optimization_id": opt_id, "retry_of": optimization_id, **results}, default=str, indent=2)

    # ── GitHub tools — stateless: token optional, bot credentials used by default ──

    @mcp.tool(
        name="github_list_repos",
        annotations=ToolAnnotations(
            title= "List GitHub Repositories",
            readOnlyHint= True,
            destructiveHint= False,
            idempotentHint= True,
            openWorldHint= True,
        ),
    )
    async def github_list_repos(token: str = "") -> str:
        """List repositories accessible with the given GitHub token.

        Returns the 30 most recently updated repositories. Use full_name with
        the optimize tool's repo_full_name parameter to enable codebase-aware
        prompt optimization.

        Args:
            token: GitHub token. Leave empty to use platform bot credentials
                   (installation token generated automatically).

        Returns:
            JSON array of repos with full_name, default_branch, language, private.
        """
        token = await _get_mcp_token(token)
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{_GITHUB_API}/user/repos",
                params={"per_page": 30, "sort": "updated"},
                headers=_github_headers(token),
            )
        if resp.status_code != 200:
            return _github_error(resp, "Failed to list repositories")
        return json.dumps(
            [
                {
                    "full_name": r["full_name"],
                    "default_branch": r.get("default_branch", "main"),
                    "language": r.get("language"),
                    "private": r["private"],
                }
                for r in resp.json()
            ],
            indent=2,
        )

    @mcp.tool(
        name="github_read_file",
        annotations=ToolAnnotations(
            title= "Read File from GitHub Repository",
            readOnlyHint= True,
            destructiveHint= False,
            idempotentHint= True,
            openWorldHint= True,
        ),
    )
    async def github_read_file(
        full_name: str,
        path: str,
        token: str = "",
        branch: Optional[str] = None,
    ) -> str:
        """Read a specific file from a GitHub repository.

        Returns the raw file content as plain text. For binary files (images,
        compiled artifacts) the GitHub API returns an error.

        Args:
            full_name: Repository in 'owner/repo' format (e.g. 'anthropics/anthropic-sdk-python')
            path: File path within the repository (e.g. 'src/main.py', 'README.md')
            token: GitHub token. Leave empty to use platform bot credentials
                   (installation token generated automatically).
            branch: Branch, tag, or commit SHA (defaults to repo's default branch)

        Returns:
            Raw file content as plain text, or JSON error with hint.
        """
        token = await _get_mcp_token(token)
        params = {"ref": branch} if branch else {}
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{_GITHUB_API}/repos/{full_name}/contents/{path}",
                params=params,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github.v3.raw",
                },
            )
        if resp.status_code != 200:
            return _github_error(resp, f"Failed to read '{path}' from {full_name}")
        return resp.text

    @mcp.tool(
        name="github_search_code",
        annotations=ToolAnnotations(
            title= "Search Code in GitHub Repository",
            readOnlyHint= True,
            destructiveHint= False,
            idempotentHint= True,
            openWorldHint= True,
        ),
    )
    async def github_search_code(
        full_name: str,
        pattern: str,
        token: str = "",
        extension: Optional[str] = None,
    ) -> str:
        """Search for a text pattern across files in a GitHub repository.

        Uses the GitHub code search API. Results are capped at 20 matches.
        Note: GitHub's code search index may lag behind the latest commits.

        Args:
            full_name: Repository in 'owner/repo' format
            pattern: Literal text pattern or keyword to search for
            token: GitHub token. Leave empty to use platform bot credentials
                   (installation token generated automatically).
            extension: Restrict search to files with this extension (e.g. 'py', 'ts', 'md')

        Returns:
            JSON array of matches with path and filename.
            Returns {"error": ..., "hint": ...} on failure.
        """
        token = await _get_mcp_token(token)
        q = f"{pattern} repo:{full_name}"
        if extension:
            q += f" extension:{extension}"
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{_GITHUB_API}/search/code",
                params={"q": q},
                headers=_github_headers(token),
            )
        if resp.status_code != 200:
            return _github_error(resp, f"Code search failed for pattern '{pattern}' in {full_name}")
        items = resp.json().get("items", [])[:20]
        return json.dumps(
            [{"path": i["path"], "name": i["name"]} for i in items],
            indent=2,
        )

    @mcp.tool(
        name="submit_feedback",
        annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    )
    async def submit_feedback_tool(
        ctx: Context,
        optimization_id: str,
        rating: int,
        dimension_overrides: dict | None = None,
        comment: str | None = None,
    ) -> str:
        """Submit feedback (thumbs up/down) on an optimization. Rating: -1, 0, or 1."""
        from app.services.feedback_service import upsert_feedback
        if rating not in (-1, 0, 1):
            return json.dumps({"error": "Rating must be -1, 0, or 1"})
        async with _opt_session(optimization_id) as (db, opt):
            result = await upsert_feedback(
                optimization_id=optimization_id,
                user_id="mcp",
                rating=rating,
                dimension_overrides=dimension_overrides,
                corrected_issues=None,
                comment=comment,
                db=db,
            )
            await db.commit()
        return json.dumps(result)

    @mcp.tool(
        name="get_branches",
        annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    )
    async def get_branches_tool(ctx: Context, optimization_id: str) -> str:
        """List all refinement branches for an optimization."""
        from app.services.refinement_service import get_branches
        async with _opt_session(optimization_id) as (db, opt):
            branches = await get_branches(optimization_id, db)
        return json.dumps({"branches": branches, "total": len(branches)})

    @mcp.tool(
        name="get_adaptation_state",
        annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    )
    async def get_adaptation_state_tool(ctx: Context, user_id: str) -> str:
        """Get the current adaptation state (learned weights, threshold, affinities) for a user."""
        from app.services.adaptation_engine import load_adaptation
        from app.database import get_session_context
        async with get_session_context() as db:
            state = await load_adaptation(user_id, db)
        if not state:
            return json.dumps({"error": "No adaptation state found", "user_id": user_id})
        return json.dumps(state)

    return mcp


# ── WebSocket ASGI endpoint factory ──────────────────────────────────────────

def make_websocket_asgi(mcp: FastMCP) -> object:
    """Return a raw ASGI callable class instance for MCP WebSocket transport.

    B1 fix: uses raw (scope, receive, send) — no private Starlette attrs.
    Returns a class instance (not a bare coroutine function) so that Starlette's
    WebSocketRoute does NOT wrap it in WebSocketSession. Starlette only wraps
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
