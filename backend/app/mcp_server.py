"""MCP server for Project Synthesis.

Supports two transports:
  - Streamable HTTP (primary, modern): mounted at /mcp on the FastAPI app
  - WebSocket (secondary, backward-compat): mounted at /mcp/ws on the FastAPI app

Provider is injected via FastMCP lifespan — no re-detection on each tool call.
GitHub tools accept explicit token parameter — no shared mutable session state.
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx
from sqlalchemy import func, select

from app.config import settings
from app.database import async_session
from app.models.optimization import Optimization
from app.services.url_fetcher import fetch_url_contexts

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

_GITHUB_API = "https://api.github.com"

try:
    from mcp.server.fastmcp import Context, FastMCP
    from mcp.types import ToolAnnotations
    HAS_MCP = True
except ImportError:
    HAS_MCP = False
    logger.warning("mcp package not installed. MCP server will not be available.")


# ── Lifespan context ──────────────────────────────────────────────────────────

@dataclass
class MCPAppContext:
    provider: object  # LLMProvider


@asynccontextmanager
async def _mcp_lifespan(server: FastMCP) -> AsyncIterator[MCPAppContext]:
    """Detect the LLM provider once at startup — shared across all tool calls."""
    from app.providers.detector import detect_provider
    provider = await detect_provider()
    logger.info("MCP server lifespan: using provider %s", provider.name)
    yield MCPAppContext(provider=provider)


# ── Shared DB helper ──────────────────────────────────────────────────────────

@asynccontextmanager
async def _opt_session(optimization_id: str) -> AsyncGenerator[tuple, None]:
    """Context manager yielding (session, opt) for a given optimization ID.

    Yields (session, None) when not found — callers must check for None.
    Keeps the session open so callers can mutate and commit within the same
    transaction without hitting DetachedInstanceError.
    """
    async with async_session() as session:
        result = await session.execute(
            select(Optimization).where(Optimization.id == optimization_id)
        )
        yield session, result.scalar_one_or_none()


# ── Module-level helpers ───────────────────────────────────────────────────────

def _accumulate_event(opt: "Optimization", event_type: str, event_data: dict) -> None:
    """Map one pipeline event to fields on the Optimization ORM object."""
    if event_type == "codebase_context":
        opt.model_explore = event_data.get("model")
    elif event_type == "analysis":
        opt.task_type = event_data.get("task_type")
        opt.complexity = event_data.get("complexity")
        opt.weaknesses = json.dumps(event_data.get("weaknesses", []))
        opt.strengths = json.dumps(event_data.get("strengths", []))
        opt.model_analyze = event_data.get("model")
    elif event_type == "strategy":
        opt.primary_framework = event_data.get("primary_framework")
        opt.secondary_frameworks = json.dumps(event_data.get("secondary_frameworks", []))
        opt.approach_notes = event_data.get("approach_notes")
        opt.strategy_rationale = event_data.get("rationale")
        opt.strategy_source = event_data.get("strategy_source")
        opt.model_strategy = event_data.get("model")
    elif event_type == "optimization":
        opt.optimized_prompt = event_data.get("optimized_prompt")
        opt.changes_made = json.dumps(event_data.get("changes_made", []))
        opt.framework_applied = event_data.get("framework_applied")
        opt.optimization_notes = event_data.get("optimization_notes")
        opt.model_optimize = event_data.get("model")
    elif event_type == "validation":
        scores = event_data.get("scores", {})
        opt.clarity_score = scores.get("clarity_score")
        opt.specificity_score = scores.get("specificity_score")
        opt.structure_score = scores.get("structure_score")
        opt.faithfulness_score = scores.get("faithfulness_score")
        opt.conciseness_score = scores.get("conciseness_score")
        opt.overall_score = scores.get("overall_score")
        opt.is_improvement = event_data.get("is_improvement")
        opt.verdict = event_data.get("verdict")
        opt.issues = json.dumps(event_data.get("issues", []))
        opt.model_validate = event_data.get("model")


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
) -> tuple[dict, "Optimization"]:
    """Create Optimization record, run pipeline, persist results. Returns (events, opt)."""
    import time

    from app.services.pipeline import run_pipeline

    opt = Optimization(
        id=opt_id,
        raw_prompt=prompt,
        status="running",
        linked_repo_full_name=repo_full_name,
        linked_repo_branch=repo_branch,
        retry_of=retry_of,
    )
    async with async_session() as s:
        s.add(opt)
        await s.commit()

    results: dict = {}
    start = time.time()
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
        _accumulate_event(opt, event_type, event_data)
        if event_type in ("analysis", "strategy", "optimization", "validation"):
            results[event_type] = event_data

    opt.status = "completed"
    opt.duration_ms = int((time.time() - start) * 1000)
    opt.provider_used = provider.name

    async with async_session() as s:
        await s.merge(opt)
        await s.commit()

    return results, opt


# ── Server factory ────────────────────────────────────────────────────────────

def create_mcp_server(provider=None) -> FastMCP:
    """Create and configure the MCP server.

    Args:
        provider: Optional pre-detected LLMProvider. If None, the lifespan
                  will detect one at startup (suitable for standalone runs).
    """
    if not HAS_MCP:
        raise ImportError("mcp package is required. Install with: pip install mcp")

    if provider is not None:
        # Provider already known (called from FastAPI lifespan) — skip re-detection.
        @asynccontextmanager
        async def _injected_lifespan(server: FastMCP) -> AsyncIterator[MCPAppContext]:
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

    # Whitelisted sort columns — prevents getattr on arbitrary user input (B5)
    from app.services.optimization_service import VALID_SORT_COLUMNS as _SORT_COLUMNS
    from app.services.optimization_service import compute_stats

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
            strategy: Optional framework override — one of: CO-STAR, RISEN, CARE, TRACE,
                      RODES, RTF, RACE. Omit to let the pipeline auto-select.
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
            The 'validation.scores.overall_score' field is 1-10.
        """
        assert ctx is not None
        prov = ctx.request_context.lifespan_context.provider
        url_fetched = await fetch_url_contexts(url_contexts)
        opt_id = _new_run_id("mcp")
        results, opt = await _run_and_persist(
            prov, prompt, opt_id=opt_id, strategy=strategy,
            repo_full_name=repo_full_name, repo_branch=repo_branch,
            github_token=github_token, file_contexts=file_contexts,
            instructions=instructions, url_fetched=url_fetched,
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
    async def get_optimization(optimization_id: str) -> str:
        """Get a specific optimization record by ID.

        Args:
            optimization_id: The UUID of the optimization to retrieve.
                             Use list_optimizations to discover valid IDs.

        Returns:
            JSON with full optimization record including scores, prompts, and metadata.
            Returns {"error": ...} with a hint if not found.
        """
        async with _opt_session(optimization_id) as (_, opt):
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
        min_score: Optional[int] = None,
        search: Optional[str] = None,
        limit: int = 20,
        offset: int = 0,
        sort: str = "created_at",
        order: str = "desc",
    ) -> str:
        """List optimization history with filtering, sorting, and pagination.

        Args:
            project: Filter by project label (exact match)
            task_type: Filter by task classification (e.g. 'coding', 'writing', 'analysis')
            min_score: Only return optimizations with overall_score >= this value (1-10)
            search: Text search across raw_prompt and title fields
            limit: Maximum results per page (default 20, max 100)
            offset: Number of records to skip for pagination (default 0)
            sort: Sort column — one of: created_at, overall_score, task_type, updated_at
            order: Sort direction — 'asc' or 'desc' (default 'desc')

        Returns:
            JSON with: items (array), total, count, offset, has_more, next_offset.
            Use next_offset with a follow-up call to paginate through results.
        """
        if sort not in _SORT_COLUMNS:
            sort = "created_at"
        if order not in ("asc", "desc"):
            order = "desc"
        limit = min(max(1, limit), 100)

        query = select(Optimization).where(Optimization.deleted_at.is_(None))
        if project:
            query = query.where(Optimization.project == project)
        if task_type:
            query = query.where(Optimization.task_type == task_type)
        if min_score is not None:
            query = query.where(Optimization.overall_score >= min_score)
        if search:
            pattern = f"%{search}%"
            query = query.where(
                (Optimization.raw_prompt.ilike(pattern)) | (Optimization.title.ilike(pattern))
            )

        sort_col = getattr(Optimization, sort)
        query = query.order_by(sort_col.asc() if order == "asc" else sort_col.desc())

        async with async_session() as session:
            # Total count for pagination metadata
            count_result = await session.execute(
                select(func.count()).select_from(query.subquery())
            )
            total = count_result.scalar() or 0

            # Paginated results
            result = await session.execute(query.offset(offset).limit(limit))
            items = [o.to_dict() for o in result.scalars().all()]

        fetched = len(items)
        has_more = (offset + fetched) < total
        return json.dumps({
            "total": total,
            "count": fetched,
            "offset": offset,
            "items": items,
            "has_more": has_more,
            "next_offset": offset + fetched if has_more else None,
        }, indent=2)

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
    async def get_stats(project: Optional[str] = None, ctx: Optional[Context] = None) -> str:
        """Get aggregated statistics across optimization history.

        Args:
            project: Scope stats to this project label. Omit for global stats.

        Returns:
            JSON with total_optimizations, average_score, task_type_breakdown,
            framework_breakdown, provider_breakdown, model_usage,
            codebase_aware_count, improvement_rate.
        """
        async with async_session() as session:
            stats = await compute_stats(session, project=project)
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
    async def delete_optimization(optimization_id: str) -> str:
        """Soft-delete an optimization record by ID (sets deleted_at; purged after 7 days).

        Args:
            optimization_id: The UUID of the optimization to delete.
                             Use list_optimizations to discover valid IDs.

        Returns:
            JSON confirming deletion with {"deleted": true, "id": "..."}.
            Returns {"error": ...} with a hint if not found.
        """
        from app.services.optimization_service import delete_optimization as svc_delete
        async with async_session() as session:
            deleted = await svc_delete(session, optimization_id)
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
    ) -> str:
        """List soft-deleted optimizations still within the 7-day recovery window.

        Deleted records are permanently purged after 7 days. Use restore_optimization
        to recover an item before it expires.

        Args:
            limit: Maximum results per page (default 20, max 100).
            offset: Number of records to skip for pagination (default 0).

        Returns:
            JSON pagination envelope: {total, count, offset, items, has_more,
            next_offset}. Each item includes id, raw_prompt, title, deleted_at,
            and created_at.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(days=7)
        async with async_session() as session:
            base = [
                Optimization.deleted_at.isnot(None),
                Optimization.deleted_at >= cutoff,
            ]
            count_result = await session.execute(
                select(func.count(Optimization.id)).where(*base)
            )
            total = count_result.scalar() or 0

            result = await session.execute(
                select(Optimization)
                .where(*base)
                .order_by(Optimization.deleted_at.desc())
                .offset(offset)
                .limit(limit)
            )
            items = [opt.to_dict() for opt in result.scalars().all()]

        fetched = len(items)
        has_more = (offset + fetched) < total
        return json.dumps({
            "total": total,
            "count": fetched,
            "offset": offset,
            "items": items,
            "has_more": has_more,
            "next_offset": offset + fetched if has_more else None,
        }, indent=2, default=str)

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
    async def restore_optimization(optimization_id: str) -> str:
        """Restore a soft-deleted optimization from the trash (clears deleted_at).

        The record must still be within the 7-day recovery window. Use list_trash
        to discover restorable IDs.

        Args:
            optimization_id: The UUID of the optimization to restore.

        Returns:
            JSON {"restored": true, "id": "..."} on success.
            Returns {"error": ...} if not found in trash or recovery window expired.
        """
        from app.services.optimization_service import restore_optimization as svc_restore
        # MCP operates without user sessions — pass None to skip user-ownership check
        async with async_session() as session:
            restored = await svc_restore(session, optimization_id, user_id=None)
            if restored:
                await session.commit()
        if not restored:
            return json.dumps({
                "error": f"Optimization '{optimization_id}' not found in trash or recovery window expired.",
                "hint": "Use list_trash to see restorable items.",
            })
        return json.dumps({"restored": True, "id": optimization_id})

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
    ) -> str:
        """Full-text search across prompt content, optimized prompts, and titles.

        Searches raw_prompt, optimized_prompt, and title fields. Ordered by most recent.

        Args:
            query: Search string to match against prompt and title text
            limit: Maximum results per page (default 10, max 100)
            offset: Number of records to skip for pagination (default 0)

        Returns:
            JSON with: items, total, count, offset, has_more, next_offset.
        """
        limit = min(max(1, limit), 100)
        pattern = f"%{query}%"
        stmt = (
            select(Optimization)
            .where(
                Optimization.deleted_at.is_(None),
                (Optimization.raw_prompt.ilike(pattern))
                | (Optimization.optimized_prompt.ilike(pattern))
                | (Optimization.title.ilike(pattern))
            )
            .order_by(Optimization.created_at.desc())
        )
        async with async_session() as session:
            count_result = await session.execute(
                select(func.count()).select_from(stmt.subquery())
            )
            total = count_result.scalar() or 0
            result = await session.execute(stmt.offset(offset).limit(limit))
            items = [o.to_dict() for o in result.scalars().all()]

        fetched = len(items)
        has_more = (offset + fetched) < total
        return json.dumps({
            "total": total,
            "count": fetched,
            "offset": offset,
            "items": items,
            "has_more": has_more,
            "next_offset": offset + fetched if has_more else None,
        }, indent=2)

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
    ) -> str:
        """Get all optimizations belonging to a project, ordered by most recent.

        Args:
            project: Project label to filter by (exact match, case-sensitive)
            include_prompts: Include raw_prompt and optimized_prompt text (default True).
                             Set False for a compact summary view.
            limit: Maximum results to return (default 50)

        Returns:
            JSON array of optimization records. Empty array if project has no records.
        """
        stmt = (
            select(Optimization)
            .where(Optimization.project == project)
            .order_by(Optimization.created_at.desc())
            .limit(limit)
        )
        async with async_session() as session:
            result = await session.execute(stmt)
            items = []
            for o in result.scalars().all():
                d = o.to_dict()
                if not include_prompts:
                    d.pop("raw_prompt", None)
                    d.pop("optimized_prompt", None)
                items.append(d)
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

        Returns:
            JSON with the full updated optimization record.
            Returns {"error": ...} with a hint if not found.
        """
        async with _opt_session(optimization_id) as (session, opt):
            if not opt:
                return _not_found(optimization_id)
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

        Returns:
            JSON with keys: analysis, strategy, optimization, validation.
            Returns {"error": ...} with a hint if not found.
        """
        # B6: capture all ORM values as locals inside the session block
        async with _opt_session(optimization_id) as (_, orig):
            if not orig:
                return _not_found(optimization_id)
            raw_prompt = orig.raw_prompt
            repo_full_name = orig.linked_repo_full_name
            repo_branch = orig.linked_repo_branch

        assert ctx is not None
        prov = ctx.request_context.lifespan_context.provider
        url_fetched = await fetch_url_contexts(url_contexts)
        opt_id = _new_run_id("mcp-retry")
        results, opt = await _run_and_persist(
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
