"""MCP server for PromptForge v2.

Exposes optimization engine, history, and GitHub tooling as MCP tools
via WebSocket transport on port 8001.
"""

import json
import logging
import asyncio
from typing import Optional

from app.config import settings
from app.database import create_tables, async_session
from app.providers.detector import detect_provider
from app.providers.base import MODEL_ROUTING

logger = logging.getLogger(__name__)

# Attempt to import FastMCP
try:
    from mcp.server.fastmcp import FastMCP
    HAS_MCP = True
except ImportError:
    HAS_MCP = False
    logger.warning("mcp package not installed. MCP server will not be available.")


def create_mcp_server() -> "FastMCP":
    """Create and configure the MCP server with all tools."""
    if not HAS_MCP:
        raise ImportError("mcp package is required for the MCP server")

    mcp = FastMCP(
        "promptforge",
        host=settings.MCP_HOST,
        port=settings.MCP_PORT,
    )

    # Per-session in-memory GitHub tokens (keyed by connection context)
    _session_tokens: dict[str, str] = {}

    @mcp.tool()
    async def optimize(
        prompt: str,
        project: Optional[str] = None,
        tags: Optional[list[str]] = None,
        title: Optional[str] = None,
        strategy: Optional[str] = None,
        repo_full_name: Optional[str] = None,
        repo_branch: Optional[str] = None,
    ) -> str:
        """Run the full prompt optimization pipeline.

        Args:
            prompt: The raw prompt to optimize
            project: Optional project name for organization
            tags: Optional tags for categorization
            title: Optional title for this optimization
            strategy: Optional framework override (e.g. 'CO-STAR', 'RISEN')
            repo_full_name: Optional GitHub repo (owner/repo) for codebase-aware optimization
            repo_branch: Optional branch name (defaults to repo's default branch)
        """
        from app.services.pipeline import run_pipeline

        provider = await detect_provider()

        results = {}
        async for event_type, event_data in run_pipeline(
            provider=provider,
            raw_prompt=prompt,
            optimization_id="mcp-" + str(id(prompt))[:8],
            strategy_override=strategy,
            repo_full_name=repo_full_name,
            repo_branch=repo_branch,
        ):
            if event_type in ("analysis", "strategy", "optimization", "validation", "complete"):
                results[event_type] = event_data

        return json.dumps(results, indent=2)

    @mcp.tool()
    async def get(optimization_id: str) -> str:
        """Get a specific optimization by ID.

        Args:
            optimization_id: The UUID of the optimization to retrieve
        """
        from sqlalchemy import select
        from app.models.optimization import Optimization

        async with async_session() as session:
            result = await session.execute(
                select(Optimization).where(Optimization.id == optimization_id)
            )
            opt = result.scalar_one_or_none()
            if not opt:
                return json.dumps({"error": "Optimization not found"})
            return json.dumps(opt.to_dict(), indent=2)

    @mcp.tool()
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
        """List optimization history with filtering and pagination.

        Args:
            project: Filter by project name
            task_type: Filter by task type
            min_score: Minimum overall score filter
            search: Search in prompts and titles
            limit: Max results to return (default 20)
            offset: Offset for pagination
            sort: Sort field (created_at, overall_score, task_type)
            order: Sort order (asc, desc)
        """
        from sqlalchemy import select
        from app.models.optimization import Optimization

        query = select(Optimization)
        if project:
            query = query.where(Optimization.project == project)
        if task_type:
            query = query.where(Optimization.task_type == task_type)
        if min_score is not None:
            query = query.where(Optimization.overall_score >= min_score)
        if search:
            pattern = f"%{search}%"
            query = query.where(
                (Optimization.raw_prompt.ilike(pattern))
                | (Optimization.title.ilike(pattern))
            )

        sort_col = getattr(Optimization, sort, Optimization.created_at)
        if order == "asc":
            query = query.order_by(sort_col.asc())
        else:
            query = query.order_by(sort_col.desc())

        query = query.offset(offset).limit(limit)

        async with async_session() as session:
            result = await session.execute(query)
            opts = result.scalars().all()
            return json.dumps([o.to_dict() for o in opts], indent=2)

    @mcp.tool()
    async def stats(project: Optional[str] = None) -> str:
        """Get aggregated optimization statistics.

        Args:
            project: Optional project filter
        """
        from sqlalchemy import select
        from app.models.optimization import Optimization

        query = select(Optimization)
        if project:
            query = query.where(Optimization.project == project)

        async with async_session() as session:
            result = await session.execute(query)
            opts = result.scalars().all()

        total = len(opts)
        scores = [o.overall_score for o in opts if o.overall_score is not None]
        avg = sum(scores) / len(scores) if scores else None

        return json.dumps({
            "total": total,
            "average_score": round(avg, 2) if avg else None,
            "task_types": list(set(o.task_type for o in opts if o.task_type)),
        }, indent=2)

    @mcp.tool()
    async def delete(optimization_id: str) -> str:
        """Delete an optimization record.

        Args:
            optimization_id: The UUID of the optimization to delete
        """
        from sqlalchemy import select
        from app.models.optimization import Optimization

        async with async_session() as session:
            result = await session.execute(
                select(Optimization).where(Optimization.id == optimization_id)
            )
            opt = result.scalar_one_or_none()
            if not opt:
                return json.dumps({"error": "Optimization not found"})
            await session.delete(opt)
            await session.commit()
            return json.dumps({"deleted": True, "id": optimization_id})

    @mcp.tool()
    async def search(query: str, limit: int = 10) -> str:
        """Search optimizations by prompt content or title.

        Args:
            query: Search query string
            limit: Max results (default 10)
        """
        from sqlalchemy import select
        from app.models.optimization import Optimization

        pattern = f"%{query}%"
        stmt = (
            select(Optimization)
            .where(
                (Optimization.raw_prompt.ilike(pattern))
                | (Optimization.optimized_prompt.ilike(pattern))
                | (Optimization.title.ilike(pattern))
            )
            .order_by(Optimization.created_at.desc())
            .limit(limit)
        )

        async with async_session() as session:
            result = await session.execute(stmt)
            opts = result.scalars().all()
            return json.dumps([o.to_dict() for o in opts], indent=2)

    @mcp.tool()
    async def get_by_project(
        project: str,
        include_prompts: bool = True,
        limit: int = 50,
    ) -> str:
        """Get optimizations filtered by project name.

        Args:
            project: Project name to filter by
            include_prompts: Whether to include full prompt texts (default True)
            limit: Max results to return (default 50)
        """
        from sqlalchemy import select
        from app.models.optimization import Optimization

        stmt = (
            select(Optimization)
            .where(Optimization.project == project)
            .order_by(Optimization.created_at.desc())
            .limit(limit)
        )

        async with async_session() as session:
            result = await session.execute(stmt)
            opts = result.scalars().all()
            items = []
            for o in opts:
                d = o.to_dict()
                if not include_prompts:
                    d.pop("raw_prompt", None)
                    d.pop("optimized_prompt", None)
                items.append(d)
            return json.dumps(items, indent=2)

    @mcp.tool()
    async def tag(
        optimization_id: str,
        add_tags: Optional[list[str]] = None,
        remove_tags: Optional[list[str]] = None,
        project: Optional[str] = None,
        title: Optional[str] = None,
    ) -> str:
        """Update tags, project, or title on an optimization.

        Args:
            optimization_id: The UUID of the optimization
            add_tags: Tags to add
            remove_tags: Tags to remove
            project: New project name (or null to clear)
            title: New title (or null to clear)
        """
        from sqlalchemy import select
        from app.models.optimization import Optimization
        from datetime import datetime, timezone

        async with async_session() as session:
            result = await session.execute(
                select(Optimization).where(Optimization.id == optimization_id)
            )
            opt = result.scalar_one_or_none()
            if not opt:
                return json.dumps({"error": "Optimization not found"})

            current_tags = json.loads(opt.tags) if opt.tags else []
            if add_tags:
                current_tags = list(set(current_tags + add_tags))
            if remove_tags:
                current_tags = [t for t in current_tags if t not in remove_tags]
            opt.tags = json.dumps(current_tags)

            if project is not None:
                opt.project = project
            if title is not None:
                opt.title = title
            opt.updated_at = datetime.now(timezone.utc)

            await session.commit()
            return json.dumps(opt.to_dict(), indent=2)

    @mcp.tool()
    async def retry(optimization_id: str, strategy: Optional[str] = None) -> str:
        """Retry an optimization with an optional strategy override.

        Args:
            optimization_id: The UUID of the optimization to retry
            strategy: Optional framework override for the retry
        """
        from sqlalchemy import select
        from app.models.optimization import Optimization

        async with async_session() as session:
            result = await session.execute(
                select(Optimization).where(Optimization.id == optimization_id)
            )
            opt = result.scalar_one_or_none()
            if not opt:
                return json.dumps({"error": "Optimization not found"})

        # Re-run the pipeline with the original prompt
        return await optimize(
            prompt=opt.raw_prompt,
            project=opt.project,
            title=opt.title,
            strategy=strategy,
            repo_full_name=opt.linked_repo_full_name,
            repo_branch=opt.linked_repo_branch,
        )

    @mcp.tool()
    async def github_set_token(token: str) -> str:
        """Set a GitHub Personal Access Token for this MCP session.

        Args:
            token: GitHub PAT with 'Contents: Read' permission
        """
        import httpx

        async with httpx.AsyncClient() as client:
            resp = await client.get(
                "https://api.github.com/user",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/json",
                },
            )
            if resp.status_code != 200:
                return json.dumps({"error": "Invalid GitHub token"})
            user = resp.json()

        _session_tokens["current"] = token
        return json.dumps({
            "login": user["login"],
            "id": user["id"],
            "token_set": True,
        })

    @mcp.tool()
    async def github_list_repos() -> str:
        """List repositories accessible with the configured GitHub token."""
        import httpx

        token = _session_tokens.get("current")
        if not token:
            return json.dumps({"error": "No GitHub token set. Use github_set_token first."})

        async with httpx.AsyncClient() as client:
            resp = await client.get(
                "https://api.github.com/user/repos",
                params={"per_page": 30, "sort": "updated"},
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/json",
                },
            )
            if resp.status_code != 200:
                return json.dumps({"error": "Failed to list repos"})

            repos = [
                {"full_name": r["full_name"], "language": r.get("language"), "private": r["private"]}
                for r in resp.json()
            ]
            return json.dumps(repos, indent=2)

    @mcp.tool()
    async def github_read_file(
        full_name: str,
        path: str,
        branch: Optional[str] = None,
    ) -> str:
        """Read a specific file from a GitHub repository.

        Args:
            full_name: Repository full name (owner/repo)
            path: File path within the repository
            branch: Branch name (defaults to repo's default branch)
        """
        import httpx

        token = _session_tokens.get("current")
        if not token:
            return json.dumps({"error": "No GitHub token set. Use github_set_token first."})

        params = {}
        if branch:
            params["ref"] = branch

        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"https://api.github.com/repos/{full_name}/contents/{path}",
                params=params,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github.v3.raw",
                },
            )
            if resp.status_code != 200:
                return json.dumps({"error": f"Failed to read {path}: {resp.status_code}"})
            return resp.text

    @mcp.tool()
    async def github_search_code(
        full_name: str,
        pattern: str,
        extension: Optional[str] = None,
        branch: Optional[str] = None,
    ) -> str:
        """Search for a pattern in a repository's files.

        Args:
            full_name: Repository full name (owner/repo)
            pattern: Text pattern to search for
            extension: Optional file extension filter (e.g. 'py', 'ts')
            branch: Optional branch name
        """
        import httpx

        token = _session_tokens.get("current")
        if not token:
            return json.dumps({"error": "No GitHub token set. Use github_set_token first."})

        query = f"{pattern} repo:{full_name}"
        if extension:
            query += f" extension:{extension}"

        async with httpx.AsyncClient() as client:
            resp = await client.get(
                "https://api.github.com/search/code",
                params={"q": query},
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/json",
                },
            )
            if resp.status_code != 200:
                return json.dumps({"error": f"Search failed: {resp.status_code}"})

            data = resp.json()
            results = [
                {"path": item["path"], "name": item["name"]}
                for item in data.get("items", [])[:20]
            ]
            return json.dumps(results, indent=2)

    @mcp.tool()
    async def github_link_repo(
        full_name: str,
        branch: Optional[str] = None,
    ) -> str:
        """Link a repository for subsequent optimize calls to use as codebase context.

        Args:
            full_name: Repository full name (owner/repo)
            branch: Branch name (defaults to repo's default branch)
        """
        return json.dumps({
            "linked": True,
            "full_name": full_name,
            "branch": branch or "main",
            "note": "Pass repo_full_name to the optimize tool to use this context.",
        })

    return mcp


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    if not HAS_MCP:
        logger.error("Cannot start MCP server: mcp package not installed")
        raise SystemExit(1)

    import uvicorn
    from starlette.applications import Starlette
    from starlette.routing import WebSocketRoute
    from mcp.server.websocket import websocket_server

    async def ws_endpoint(websocket):
        """Handle WebSocket connections for MCP protocol.

        Starlette WebSocketRoute passes a WebSocket object; we extract the raw
        ASGI (scope, receive, send) for the MCP websocket_server context manager.
        """
        async with websocket_server(
            websocket.scope, websocket._receive, websocket._send
        ) as (read_stream, write_stream):
            await mcp._mcp_server.run(
                read_stream,
                write_stream,
                mcp._mcp_server.create_initialization_options(),
            )

    # Initialize database tables
    asyncio.run(create_tables())

    mcp = create_mcp_server()

    app = Starlette(
        routes=[
            WebSocketRoute("/ws", ws_endpoint),
        ],
    )

    logger.info(f"Starting MCP server on ws://{settings.MCP_HOST}:{settings.MCP_PORT}/ws")
    uvicorn.run(app, host=settings.MCP_HOST, port=settings.MCP_PORT, log_level="info")
