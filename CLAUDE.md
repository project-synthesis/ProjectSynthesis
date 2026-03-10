# CLAUDE.md — Project Synthesis

Guidance for Claude Code when working in this repository.

## Services and ports

| Service | Port | Entry point |
|---|---|---|
| FastAPI backend | 8000 | `backend/app/main.py` |
| SvelteKit frontend | 5199 | `frontend/src/` |
| MCP server (standalone) | 8001 | `backend/app/mcp_server.py` |
| Redis (optional) | 6379 | External — user-managed |

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
- **Key env vars**: `ANTHROPIC_API_KEY`, `GITHUB_APP_CLIENT_ID`, `GITHUB_APP_CLIENT_SECRET`, `GITHUB_APP_ID`, `GITHUB_APP_PRIVATE_KEY`, `GITHUB_APP_INSTALLATION_ID`, `GITHUB_TOKEN_ENCRYPTION_KEY`, `SECRET_KEY`, `REDIS_HOST`, `REDIS_PORT`, `REDIS_DB`, `REDIS_PASSWORD`, `TRUSTED_PROXIES`

### Layer rules
- `routers/` → `services/` → `models/` only. Services must never import from routers.
- `github_client.py` uses `github_service.decrypt_token()` — never import `_get_fernet` from the router.

### Routers (`backend/app/routers/`)
- `optimize.py` — SSE streaming pipeline endpoint (`POST /api/optimize`)
- `history.py` — optimization history with sort/filter (`GET /api/history`)
- `github_auth.py` — GitHub OAuth flow + token encryption
- `github_repos.py` — repo listing, branch info, repo link (triggers background embedding index), manual reindex
- `github.py` — repo link/unlink on an optimization
- `providers.py` — active provider info
- `settings.py` — app settings read/write
- `health.py` — liveness check

### Services (`backend/app/services/`)
- `pipeline.py` — orchestrates the 5 stages; call `run_pipeline()` for SSE events
- `codebase_explorer.py` — Stage 0 (Explore): semantic retrieval + single-shot synthesis (see Explore Architecture below)
- `embedding_service.py` — Singleton sentence-transformers model loader + batch embed + cosine search
- `repo_index_service.py` — Background repo file indexing and semantic query (builds on repo link)
- `analyzer.py` — Stage 1 (Analyze): prompt classification
- `strategy_selector.py` / `strategy.py` — Stage 2 (Strategy): framework selection
- `optimizer.py` — Stage 3 (Optimize): prompt rewrite
- `validator.py` — Stage 4 (Validate): scoring and feedback
- `optimization_service.py` — CRUD + sort/filter against the DB
- `github_service.py` — token encryption/decryption (Fernet)
- `github_client.py` — raw GitHub API calls; token always resolved here
- `redis_service.py` — Redis connection singleton with graceful degradation
- `cache_service.py` — Generic async cache (Redis with in-memory LRU fallback)

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

14 tools split into two groups — optimization CRUD (including trash/restore) and GitHub read tools. See **[docs/MCP.md](docs/MCP.md)** for the full tool reference, all parameters, and connection instructions.

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
- **Redis graceful degradation**: Redis is optional. On connect failure, the app logs CRITICAL and falls back to in-memory rate limiting (`limits.storage.MemoryStorage`) and in-memory caching (dict with TTL, bounded at 1000 entries with LRU eviction). When Redis is marked unavailable, `health_check()` retries reconnection every 30 seconds (`_RECONNECT_COOLDOWN`). All Redis consumers use the `redis_service.is_ready` property guard. Health endpoint reports `redis_connected: true/false`; overall status is `"degraded"` (not `"error"`) when Redis is down.
- **Rate limiting**: Uses the `limits` library (not slowapi) via a `RateLimit` FastAPI dependency class (`backend/app/dependencies/rate_limit.py`). Endpoints use `Depends(RateLimit(lambda: settings.RATE_LIMIT_*))` instead of decorators. `X-Forwarded-For` is only trusted from IPs in `TRUSTED_PROXIES` (defaults to loopback) to prevent rate-limit bypass via header spoofing.
- **Pipeline caching**: Strategy (7-day TTL, keyed by task_type+complexity) and Analyze (24-hour TTL, keyed by prompt+context flags) stages are cached. Optimize and Validate are NOT cached (non-deterministic creative output).

### Explore architecture (Stage 0)

The explore phase uses **semantic retrieval + single-shot synthesis**, not an agentic loop.

**Background indexing** (on repo link):
1. `github_repos.py` link endpoint triggers `repo_index_service.build_index()` as a background task
2. Fetches file tree, reads code outlines in parallel (semaphore=10), batch-embeds with `all-MiniLM-L6-v2` (384-dim, CPU)
3. Stores per-file embeddings + outlines in `repo_file_index` table, status in `repo_index_meta` (24h TTL)

**Explore flow** (`codebase_explorer.py`):
1. Token resolution + branch validation (falls back to default branch if not found)
2. Check cache → return immediately on hit (1h TTL)
3. Vector retrieval: embed prompt → cosine search pre-built index → top-K files
4. Extract prompt-referenced files: 3-tier matching (exact path > filename > module stem) against repo tree
5. 3-tier file merge: prompt-referenced > deterministic anchors > semantic-ranked (deduped, capped at `EXPLORE_MAX_FILES`)
6. Dynamic line budget: `EXPLORE_TOTAL_LINE_BUDGET` / file count, capped at `EXPLORE_MAX_LINES_PER_FILE`
7. Parallel file reads: batch-read via GitHub API (semaphore=10), line-numbered content with transparent truncation notices
8. Context overflow guard: trim semantic-tier files if payload exceeds `EXPLORE_MAX_CONTEXT_CHARS`
9. Single-shot synthesis: `complete_json()` (Haiku 4.5) with schema enforcement → normalized output
10. Post-LLM validation: flag unverifiable line references and unsupported bug claims with `[unverified]` suffixes
11. Cache complete results and yield `explore_result` SSE event

**Fallback**: When embeddings are unavailable (model not loaded, index not ready), keyword matching on file paths provides ranked results.

**Key services**:
- `embedding_service.py` — singleton model loader, `embed_texts()`, `embed_single()`, `cosine_search()`
- `repo_index_service.py` — `build_index()`, `query_relevant_files()`, `get_index_status()`, `invalidate_index()`
- `explore_synthesis_prompt.py` — single-shot synthesis system prompt (no tools, no multi-turn)
- `codebase_tools.py` — MCP tool definitions for interactive exploration (legacy agentic tools, still used by MCP)
- `explore_prompt.py` — **DEPRECATED** (old 25-turn agentic prompt, kept for reference only)

**Config** (`config.py`): `EMBEDDING_MODEL`, `REPO_INDEX_TTL_HOURS`, `REPO_INDEX_MAX_FILES`, `EXPLORE_INDEX_WAIT_TIMEOUT`, `EXPLORE_FILE_READ_CONCURRENCY`, `EXPLORE_MAX_FILES`, `EXPLORE_TOTAL_LINE_BUDGET`, `EXPLORE_MAX_LINES_PER_FILE`, `EXPLORE_MAX_AMBIGUOUS_MATCHES`, `EXPLORE_MAX_CONTEXT_CHARS`, `EXPLORE_RESULT_CACHE_TTL`, `EXPLORE_TIMEOUT_SECONDS`

## Docker deployment

Production deployment via Docker Compose. All traffic routes through nginx; only ports 80 (HTTP) and 8001 (standalone MCP) are exposed to the host.

### Quick start
```bash
cp .env.docker.example .env.docker
# Edit .env.docker — at minimum set ANTHROPIC_API_KEY and change all CHANGE-ME secrets
docker compose up --build -d
```

### Architecture
```
           [Browser]
              │
         [nginx :80]  ← only host-exposed port
        ┌─────┼──────────┐
        │     │           │
 [frontend] [backend]  [mcp :8001]
   :5199     :8000        │
                │         │
             [redis]   [db-data vol]
              :6379
```

Routing: `/` → frontend, `/api/*` + `/auth/*` → backend, `/mcp` + `/mcp/ws` → backend, port 8001 → standalone MCP

### Services

| Service | Image | Host port | Health check |
|---------|-------|-----------|-------------|
| nginx | nginx:1.27-alpine (custom) | 80, 8001 | wget /api/health |
| backend | python:3.14-slim (custom) | none | curl /api/health |
| frontend | node:24-slim (custom) | none | curl / |
| mcp | reuses backend image | none | TCP :8001 |
| redis | redis:7-alpine | none | redis-cli ping |

### Volumes
- `db-data` — SQLite database (shared by backend + mcp)
- `redis-data` — Redis AOF persistence

### Key env vars for Docker
- `MCP_HOST=0.0.0.0` — bind all interfaces inside container
- `MCP_PROBE_HOST=mcp` — health check resolves Docker service name
- `REDIS_HOST=redis` — Docker service name
- `TRUSTED_PROXIES=172.16.0.0/12` — Docker bridge subnet
- `FRONTEND_URL=http://localhost` — nginx serves on :80
- `ORIGIN=http://localhost` — SvelteKit adapter-node CSRF

### TLS setup
1. Place `fullchain.pem` and `privkey.pem` in `nginx/certs/`
2. Uncomment the TLS server block in `nginx/nginx.conf`
3. Add `- "443:443"` to nginx ports in `docker-compose.yml`
4. Set `FRONTEND_URL=https://your-domain.com`, `CORS_ORIGINS=https://your-domain.com`, `ORIGIN=https://your-domain.com`, `JWT_COOKIE_SECURE=true`

### Container hardening
- All containers: `no-new-privileges`, `restart: unless-stopped`, JSON log rotation (10 MB / 3 files)
- nginx, frontend, redis: `read_only: true` with tmpfs for writable paths
- All services run as non-root users (UID 10001)
- Resource limits: backend 2 GB / 2 CPU, others capped lower

### Troubleshooting
- **Frontend returns HTML for /api calls**: nginx is not routing — check `docker compose logs nginx`
- **MCP health check fails**: ensure `MCP_PROBE_HOST=mcp` in `.env.docker`
- **Redis not connecting**: verify `REDIS_HOST=redis` (Docker service name, not localhost)
- **Embedding model slow on first request**: the backend Dockerfile pre-caches the model; if using a custom image, ensure `HF_HOME` is set
- **CSRF errors in SvelteKit**: `ORIGIN` must match the external URL exactly (e.g., `http://localhost`)
