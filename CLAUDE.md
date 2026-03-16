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
./init.sh restart    # stop + start
./init.sh stop       # stop all
./init.sh status     # check running/stopped
```

Logs: `data/backend.log`, `data/frontend.log`, `data/mcp.log`

## Backend

- **Framework**: FastAPI + uvicorn with `--reload`
- **Database**: SQLite via SQLAlchemy async + aiosqlite (`data/synthesis.db`)
- **Config**: `backend/app/config.py` — reads from `.env` via pydantic-settings
- **Key env vars**: `ANTHROPIC_API_KEY` (optional — configurable via UI), `GITHUB_APP_CLIENT_ID`, `GITHUB_APP_CLIENT_SECRET`, `SECRET_KEY`
- **Auto-generated secrets**: `SECRET_KEY`, `JWT_SECRET`, `JWT_REFRESH_SECRET` are auto-generated on first startup and persisted to `data/.app_secrets`

### Layer rules
- `routers/` -> `services/` -> `models/` only. Services must never import from routers.

### Key services (`backend/app/services/`)
- `pipeline.py` — orchestrates analyzer -> optimizer -> scorer
- `prompt_loader.py` — template loading + variable substitution from `prompts/`
- `strategy_loader.py` — strategy file discovery from `prompts/strategies/`
- `context_resolver.py` — per-source character caps + injection hardening
- `optimization_service.py` — CRUD, sort/filter, score distribution
- `feedback_service.py` — feedback CRUD + adaptation update
- `adaptation_tracker.py` — strategy affinity tracking
- `heuristic_scorer.py` — passthrough bias correction
- `refinement_service.py` — refinement sessions, versioning, branching
- `trace_logger.py` — per-phase JSONL traces
- `embedding_service.py` — singleton sentence-transformers model loader
- `codebase_explorer.py` — semantic retrieval + single-shot synthesis
- `repo_index_service.py` — background repo file indexing and semantic query
- `github_service.py` — token encryption/decryption (Fernet)
- `github_client.py` — raw GitHub API calls; token always resolved here

### Providers (`backend/app/providers/`)
- `detector.py` — auto-selects provider in order: Claude CLI -> Anthropic API
- `claude_cli.py` — uses `claude` CLI subprocess (Max subscription, zero cost)
- `anthropic_api.py` — direct API via `anthropic` SDK
- `base.py` — `LLMProvider` abstract base

Provider is detected **once at startup** and stored in `app.state.provider`. Never call `detect_provider()` inside a request handler or tool.

### Routers (`backend/app/routers/`)
- `optimize.py` — `POST /api/optimize` (SSE), `GET /api/optimize/{trace_id}`
- `history.py` — `GET /api/history` (sort/filter)
- `feedback.py` — `POST /api/feedback`, `GET /api/feedback`
- `refinement.py` — `POST /api/refine` (SSE), `GET` versions, `POST` rollback
- `providers.py` — `GET /api/providers`
- `settings.py` — `GET /api/settings`
- `github_auth.py` — OAuth flow
- `github_repos.py` — repo management
- `health.py` — `GET /api/health`

### Sort column whitelist
`optimization_service.py` defines `_VALID_SORT_COLUMNS`. Add new sortable columns there before using them.

## Frontend

- **Framework**: SvelteKit 2 (Svelte 5 runes) + Tailwind CSS 4
- **Dev server**: `npm run dev` -> port 5199
- **API client**: `frontend/src/lib/api/client.ts` — all backend calls go through here
- **Theme**: industrial cyberpunk — dark backgrounds, 1px neon contours, no rounded corners, no shadows

### Stores (`frontend/src/lib/stores/`)
- `forge.svelte.ts` — optimization pipeline state
- `editor.svelte.ts` — tab management
- `github.svelte.ts` — GitHub state
- `refinement.svelte.ts` — refinement sessions

### Component layout
```
src/lib/components/
  layout/       # Navigator, Inspector, StatusBar, EditorGroups
  editor/       # PromptEdit, ForgeArtifact, PromptPipeline, ContextBar
  refinement/   # Refinement UI components
  shared/       # CommandPalette, DiffView, ToastContainer, ProviderBadge
```

## Prompt templates

All prompts live in `prompts/`. `{{variable}}` syntax. Hot-reloaded on each call — edit any file and changes take effect immediately.

| Template | Purpose |
|----------|---------|
| `agent-guidance.md` | Orchestrator system prompt (static) |
| `analyze.md` | Analyzer: classify + detect weaknesses |
| `optimize.md` | Optimizer: rewrite using strategy |
| `scoring.md` | Scorer: independent 5-dimension evaluation (static) |
| `refine.md` | Refinement optimizer |
| `suggest.md` | Suggestion generator |
| `explore.md` | Codebase exploration synthesis |
| `adaptation.md` | Adaptation state formatter |
| `passthrough.md` | MCP passthrough combined template |
| `strategies/*.md` | 6 strategy files (static) |

Variable reference: `prompts/manifest.json`

## MCP server

3 tools with `synthesis_` prefix on port 8001:
- `synthesis_optimize` — full pipeline execution
- `synthesis_prepare_optimization` — assemble prompt + context for external LLM
- `synthesis_save_result` — persist result with bias correction

**Transports:**
- `http://127.0.0.1:8001/mcp` — streamable HTTP, standalone process (primary)
- `http://localhost:8000/mcp` — streamable HTTP, FastAPI-mounted

**`.mcp.json`** points Claude Code at `http://127.0.0.1:8001/mcp` automatically when this directory is open.

### Adding a tool
1. Add a `@mcp.tool(name="synthesis_...", ...)` function in `mcp_server.py`
2. Use the `synthesis_` prefix for all tool names
3. Return a Pydantic model for structured output; raise `ValueError` for errors
4. Document the tool in `docs/MCP.md`

## Common tasks

### Restart backend only
```bash
pkill -f "uvicorn app.main" && cd backend && source .venv/bin/activate && \
  nohup python -m uvicorn app.main:asgi_app --host 0.0.0.0 --port 8000 --reload \
  > ../data/backend.log 2>&1 &
```
Use `./init.sh restart` instead when site-packages changed — `--reload` does not watch installed packages.

### Run backend tests
```bash
cd backend && source .venv/bin/activate && pytest --cov=app -v
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

### Docker deployment
```bash
docker compose up --build -d
```

## Claude Code automation

### `.mcp.json`
Auto-loads the Project Synthesis MCP server (`http://127.0.0.1:8001/mcp`) when this directory is open in Claude Code. Verify the server is running with `./init.sh status`.

### Hooks (`.claude/hooks/`)
Pre-tool-use hooks run automatically before `git push` and `gh pr create`:

| Hook | Purpose | Timeout |
|------|---------|---------|
| `pre-pr-ruff.sh` | Python lint via Ruff on `backend/app/` and `backend/tests/` | 60s |
| `pre-pr-svelte.sh` | Svelte type check via `npx svelte-check` on `frontend/` | 120s |

Exit codes: `0` = allow, `2` = block (fix errors first).

### Subagents (`.claude/agents/`)
- **`code-reviewer.md`** — Architecture compliance, brand guidelines, and consistency review.

## Changelog

`CHANGELOG.md` follows flat bullet list per version, past-tense prefix verb.

**Format:** `- {Verb} {what changed}` — one line, no trailing period.

| Verb | Use for |
|------|---------|
| Added | New feature, endpoint, tool, config, UI element |
| Fixed | Bug fix, data correction, crash resolution |
| Changed | Behavioral change to existing functionality |
| Removed | Deleted feature, deprecated code cleanup |
| Improved | Performance, UX, or quality enhancement to existing feature |

## Versioning

Single source of truth: `backend/app/_version.py`. All backend consumers import `__version__` from there.

**When bumping the version:**
1. Update `__version__` in `backend/app/_version.py`
2. Update `version` in `frontend/package.json` to match
3. Move `## Unreleased` entries in `CHANGELOG.md` under the new `## X.Y.Z` heading

## Key architectural decisions

- **Pipeline**: 3 subagent phases (analyze -> optimize -> score) orchestrated by `pipeline.py`. Explore stage runs when a GitHub repo is linked.
- **Provider injection**: detected once at startup, injected via `app.state.provider` and MCP lifespan context.
- **Prompt templates**: all prompts live in `prompts/` with `{{variable}}` substitution. `prompt_loader.py` hot-reloads on every call. Never hardcode prompts in application code.
- **Passthrough protocol**: MCP `synthesis_prepare_optimization` assembles the full prompt; external LLM processes it; `synthesis_save_result` persists with heuristic bias correction.
- **Pagination envelope**: all list endpoints return `{total, count, offset, items, has_more, next_offset}`.
- **GitHub token layer**: tokens are Fernet-encrypted at rest. `github_service.encrypt_token` / `decrypt_token` are the only entry points.
- **Explore architecture**: semantic retrieval + single-shot synthesis (not an agentic loop). Background indexing with `all-MiniLM-L6-v2` embeddings. Auto-refresh on branch changes via HEAD SHA detection.
- **Feedback adaptation**: progressive damping from first feedback with strategy affinity tracking. Framework performance tracked per-user per-task.
- **Bias correction**: `heuristic_scorer.py` applies passthrough bias correction when scores come from an external LLM via the MCP passthrough path.
- **Trace logging**: `trace_logger.py` writes per-phase JSONL traces for pipeline observability and debugging.
