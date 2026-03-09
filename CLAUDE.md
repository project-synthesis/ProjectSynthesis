# CLAUDE.md — Project Synthesis

Guidance for Claude Code when working in this repository.

## Services and ports

| Service | Port | Entry point |
|---|---|---|
| FastAPI backend | 8000 | `backend/app/main.py` |
| SvelteKit frontend | 5199 | `frontend/src/` |
| MCP server (standalone) | 8001 | `backend/app/mcp_server.py` |

```bash
./init.sh            # start all three services
./init.sh restart    # stop + start (required after changing site-packages)
./init.sh stop       # stop all
./init.sh status     # check running/stopped
```

Logs: `data/backend.log`, `data/frontend.log`, `data/mcp.log`

## Backend

- **Framework**: FastAPI + uvicorn with `--reload` (watches `backend/app/`)
- **Database**: SQLite via SQLAlchemy async + aiosqlite (`data/synthesis.db`)
- **Config**: `backend/app/config.py` — reads from `.env` via pydantic-settings
- **Key env vars**: `ANTHROPIC_API_KEY`, `GITHUB_APP_CLIENT_ID`, `GITHUB_APP_CLIENT_SECRET`, `GITHUB_APP_ID`, `GITHUB_APP_PRIVATE_KEY`, `GITHUB_APP_INSTALLATION_ID`, `GITHUB_TOKEN_ENCRYPTION_KEY`, `SECRET_KEY`

### Layer rules
- `routers/` → `services/` → `models/` only. Services must never import from routers.
- `github_client.py` uses `github_service.decrypt_token()` — never import `_get_fernet` from the router.

### Routers (`backend/app/routers/`)
- `optimize.py` — SSE streaming pipeline endpoint (`POST /api/optimize`)
- `history.py` — optimization history with sort/filter (`GET /api/history`)
- `github_auth.py` — GitHub OAuth flow + token encryption
- `github_repos.py` — repo listing and branch info
- `github.py` — repo link/unlink on an optimization
- `providers.py` — active provider info
- `settings.py` — app settings read/write
- `health.py` — liveness check

### Services (`backend/app/services/`)
- `pipeline.py` — orchestrates the 5 stages; call `run_pipeline()` for SSE events
- `codebase_explorer.py` — Stage 0 (Explore): GitHub file tree + key file reads
- `analyzer.py` — Stage 1 (Analyze): prompt classification
- `strategy_selector.py` / `strategy.py` — Stage 2 (Strategy): framework selection
- `optimizer.py` — Stage 3 (Optimize): prompt rewrite
- `validator.py` — Stage 4 (Validate): scoring and feedback
- `optimization_service.py` — CRUD + sort/filter against the DB
- `github_service.py` — token encryption/decryption (Fernet)
- `github_client.py` — raw GitHub API calls; token always resolved here

### Providers (`backend/app/providers/`)
- `detector.py` — auto-selects provider in order: Claude CLI → Anthropic API
- `claude_cli.py` — uses `claude` CLI subprocess (Max subscription, zero cost)
- `anthropic_api.py` — direct API via `anthropic` SDK
- `base.py` — `LLMProvider` abstract base

Provider is detected **once at startup** and injected via `app.state.provider` and the MCP lifespan context. Never call `detect_provider()` inside a request handler or tool.

### Sort column whitelist
`history.py`, `optimization_service.py`, and `mcp_server.py` all guard `getattr(Optimization, sort)` with a whitelist:
```python
_VALID_SORT_COLUMNS = {"created_at", "overall_score", "task_type",
                       "updated_at", "duration_ms", "primary_framework", "status"}
```
Add new sortable columns here before using them.

## MCP server

16 tools split into two groups — optimization CRUD (including trash/restore) and GitHub read tools. See **[docs/MCP.md](docs/MCP.md)** for the full tool reference, all parameters, and connection instructions.

**Transports:**
- `http://127.0.0.1:8001/mcp` — streamable HTTP, standalone process (primary; used by `.mcp.json`)
- `http://localhost:8000/mcp` — streamable HTTP, FastAPI-mounted
- `ws://localhost:8000/mcp/ws` — WebSocket, backward-compat (bypasses CORS via `_SynthesisASGI`)

**`.mcp.json`** points Claude Code at `http://127.0.0.1:8001/mcp` (streamable HTTP) automatically when this directory is open. The schema field is `"type"` (not `"transport"`); valid values are `stdio`, `sse`, `http`.

### Adding a tool
1. Add a `@mcp.tool(name="...", annotations={...})` function inside `create_mcp_server()` in `mcp_server.py`
2. Include all four annotations: `readOnlyHint`, `destructiveHint`, `idempotentHint`, `openWorldHint`
3. DB access: use the `_opt_session(optimization_id)` context manager
4. GitHub calls: take an explicit `token: str` parameter — no shared session state
5. Document the tool in `docs/MCP.md`

## Frontend

- **Framework**: SvelteKit 2 (Svelte 5 runes) + Tailwind CSS 4
- **Dev server**: `npm run dev` → port 5199
- **API client**: `frontend/src/lib/api/client.ts` — all backend calls go through here
- **Stores**: `frontend/src/lib/stores/` — `forge.svelte.ts` (pipeline state), `editor.svelte.ts`, `github.svelte.ts`
- **Theme**: industrial cyberpunk, flat neon contour — do not introduce rounded corners or drop shadows

### Component layout
```
src/lib/components/
  layout/     # Navigator, Inspector, StatusBar, EditorGroups
  editor/     # PromptEdit, ForgeArtifact, PromptPipeline, ChainComposer, ContextBar
  pipeline/   # StageCard, StageAnalyze, StageExplore, StageOptimize, …
  github/     # RepoBadge, RepoPickerModal
  shared/     # CommandPalette, DiffView, ToastContainer, ProviderBadge
```

## Common tasks

### Restart backend only (after editing Python source)
```bash
pkill -f "uvicorn app.main" && cd backend && source .venv/bin/activate && \
  nohup python -m uvicorn app.main:asgi_app --host 0.0.0.0 --port 8000 --reload \
  > ../data/backend.log 2>&1 &
```
Use `./init.sh restart` instead when SDK or site-packages changed — `--reload` does not watch installed packages.

### Run backend tests
```bash
cd backend && source .venv/bin/activate && pytest
```

### Run frontend dev server standalone
```bash
cd frontend && npm run dev
```

### Verify MCP tools from the CLI
```bash
SESSION=$(curl -s -D - -X POST http://127.0.0.1:8001/mcp \
  -H "Content-Type: application/json" -H "Accept: application/json" \
  -d '{"jsonrpc":"2.0","method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"1"}},"id":1}' \
  | grep -i mcp-session-id | awk '{print $2}' | tr -d '\r')
curl -s -X POST http://127.0.0.1:8001/mcp \
  -H "Content-Type: application/json" -H "Accept: application/json" \
  -H "Mcp-Session-Id: $SESSION" \
  -d '{"jsonrpc":"2.0","method":"tools/list","params":{},"id":2}'
```

## Key architectural decisions

- **Explore gate** (`pipeline.py`): runs when `repo_full_name AND (session_id OR github_token)`. MCP callers pass `github_token` directly since they have no session.
- **MCP lifespan**: provider injected once via `_mcp_lifespan` → `ctx.request_context.lifespan_context.provider`. Tools access it as `ctx.request_context.lifespan_context.provider`.
- **Lazy ASGI wrappers** (`main.py`): `_LazyMCPHttpApp` and `_LazyMCPWSApp` are mounted at module level but populated during lifespan, avoiding FastAPI sub-app startup conflicts.
- **GitHub token layer**: tokens are Fernet-encrypted at rest. `github_service.encrypt_token` / `decrypt_token` are the only entry points — routers do not hold the Fernet key.
- **Pagination envelope**: all list/search endpoints return `{total, count, offset, items, has_more, next_offset}`.
