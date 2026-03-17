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
./init.sh stop       # graceful stop (process group kill)
./init.sh restart    # stop + start
./init.sh status     # show running/stopped with PIDs
./init.sh logs       # tail all service logs
```

Logs: `data/backend.log`, `data/frontend.log`, `data/mcp.log`
PIDs: `data/pids/backend.pid`, `data/pids/mcp.pid`, `data/pids/frontend.pid`

## Backend

- **Framework**: FastAPI + uvicorn with `--reload` (watches `backend/app/`)
- **Database**: SQLite via SQLAlchemy async + aiosqlite (`data/synthesis.db`)
- **Config**: `backend/app/config.py` — reads from `.env` via pydantic-settings
- **Key env vars**: `ANTHROPIC_API_KEY` (optional — configurable via UI or env), `GITHUB_OAUTH_CLIENT_ID`, `GITHUB_OAUTH_CLIENT_SECRET`, `SECRET_KEY` (auto-generated if not set)
- **Auto-generated secrets**: `SECRET_KEY` auto-generated on first startup and persisted to `data/.app_secrets` (0o600)
- **Encrypted credentials**: API key stored Fernet-encrypted in `data/.api_credentials`

### Layer rules
- `routers/` → `services/` → `models/` only. Services must never import from routers.
- `PromptLoader.load()` for static templates (no variables: `agent-guidance.md`, `scoring.md`). `PromptLoader.render()` for templates with `{{variables}}`.
- `AnalysisResult.task_type` is a `Literal` — valid values: `coding`, `writing`, `analysis`, `creative`, `data`, `system`, `general`. Same constraint on `selected_strategy`: must match a filename in `prompts/strategies/`.

### Key services (`backend/app/services/`)
- `pipeline.py` — orchestrates analyzer → optimizer → scorer (3-phase pipeline)
- `prompt_loader.py` — template loading + variable substitution from `prompts/`. Validates all templates at startup.
- `strategy_loader.py` — strategy file discovery from `prompts/strategies/`. Validates non-empty at startup.
- `context_resolver.py` — per-source character caps, untrusted-context wrapping, workspace roots scanning
- `roots_scanner.py` — discovers agent guidance files (CLAUDE.md, AGENTS.md, .cursorrules, etc.) from workspace paths
- `optimization_service.py` — CRUD, sort/filter, score distribution tracking, recent error counts
- `feedback_service.py` — feedback CRUD + synchronous adaptation tracker update
- `adaptation_tracker.py` — strategy affinity tracking with degenerate pattern detection
- `heuristic_scorer.py` — 5-dimension heuristics (clarity, specificity, structure, faithfulness, conciseness) + `score_prompt()` facade + passthrough bias correction
- `score_blender.py` — hybrid scoring engine: blends LLM + heuristic scores with z-score normalization and divergence detection
- `preferences.py` — persistent user preferences (model selection, pipeline toggles, default strategy). File-based JSON at `data/preferences.json`. Snapshot pattern for pipeline consistency.
- `file_watcher.py` — background watchfiles.awatch() task for strategy file hot-reload. Publishes `strategy_changed` events to event bus on file add/modify/delete.
- `refinement_service.py` — refinement sessions, version CRUD, branching/rollback, suggestion generation
- `trace_logger.py` — per-phase JSONL traces to `data/traces/`, daily rotation
- `embedding_service.py` — singleton sentence-transformers (`all-MiniLM-L6-v2`, 384-dim). Async wrappers via `aembed_single`/`aembed_texts`.
- `codebase_explorer.py` — semantic retrieval + single-shot Haiku synthesis. SHA-based result caching.
- `explore_cache.py` — in-memory TTL cache with LRU eviction for explore results
- `repo_index_service.py` — background repo file indexing and semantic query
- `github_service.py` — Fernet token encryption/decryption
- `github_client.py` — raw GitHub API calls; explicit token parameter on every method
- `event_bus.py` — in-process pub/sub for real-time cross-client notifications
- `workspace_intelligence.py` — zero-config workspace analysis (project type, tech stack from manifest files)

### Model configuration
Model IDs are centralized in `config.py` as `MODEL_SONNET`, `MODEL_OPUS`, `MODEL_HAIKU` (default: `claude-sonnet-4-6`, `claude-opus-4-6`, `claude-haiku-4-5`). Never hardcode model IDs in service code — use `PreferencesService.resolve_model(phase, snapshot)` which maps user preferences to full model IDs.

### Providers (`backend/app/providers/`)
- `detector.py` — auto-selects: Claude CLI → Anthropic API
- `claude_cli.py` — CLI subprocess (Max subscription, zero cost)
- `anthropic_api.py` — direct API via `anthropic` SDK with prompt caching (`cache_control: ephemeral`)
- `base.py` — `LLMProvider` abstract base with `complete_parsed()` and `thinking_config()`

Provider is detected **once at startup** and stored in `app.state.provider`. Never call `detect_provider()` inside a request handler.

### Routers (`backend/app/routers/`)
- `optimize.py` — `POST /api/optimize` (SSE), `GET /api/optimize/{trace_id}`
- `history.py` — `GET /api/history` (sort/filter with pagination envelope)
- `feedback.py` — `POST /api/feedback`, `GET /api/feedback?optimization_id=X`
- `refinement.py` — `POST /api/refine` (SSE), `GET /api/refine/{id}/versions`, `POST /api/refine/{id}/rollback`
- `providers.py` — `GET /api/providers`, `GET/PATCH/DELETE /api/provider/api-key`
- `preferences.py` — `GET /api/preferences`, `PATCH /api/preferences` (persistent user settings)
- `strategies.py` — `GET /api/strategies`, `GET /api/strategies/{name}`, `PUT /api/strategies/{name}` (strategy template CRUD)
- `settings.py` — `GET /api/settings` (read-only server config)
- `github_auth.py` — OAuth flow (login, callback, me, logout)
- `github_repos.py` — repo management (list, link, linked, unlink)
- `health.py` — `GET /api/health` (status, provider, score_health, recent_errors, avg_duration_ms)
- `events.py` — `GET /api/events` (SSE event stream), `POST /api/events/_publish` (internal cross-process)

### Sort column whitelist
`optimization_service.py` defines `_VALID_SORT_COLUMNS`. Add new sortable columns there before using them.

### Shared utilities
- `app/utils/sse.py` — shared `format_sse()` for SSE event formatting (used by optimize + refinement routers)
- `app/dependencies/rate_limit.py` — in-memory rate limiting FastAPI dependency via `limits` library

## Frontend

- **Framework**: SvelteKit 2 (Svelte 5 runes) + Tailwind CSS 4
- **Dev server**: `npm run dev` → port 5199
- **API client**: `frontend/src/lib/api/client.ts` — all backend calls go through here
- **Theme**: industrial cyberpunk — dark backgrounds (`#06060c`), 1px neon contours (`#00e5ff`), no rounded corners, no drop shadows, no glow effects

### Stores (`frontend/src/lib/stores/`)
- `forge.svelte.ts` — optimization pipeline state (prompt, strategy, SSE events, result, feedback)
- `editor.svelte.ts` — tab management (prompt/result/diff types)
- `github.svelte.ts` — GitHub auth + repo link state
- `refinement.svelte.ts` — refinement sessions (turns, branches, suggestions, score progression)

### Component layout
```
src/lib/components/
  layout/       # ActivityBar, Navigator, EditorGroups, Inspector, StatusBar
  editor/       # PromptEdit, ForgeArtifact
  refinement/   # RefinementTimeline, RefinementTurnCard, SuggestionChips,
                # BranchSwitcher, ScoreSparkline, RefinementInput
  shared/       # CommandPalette, DiffView, ProviderBadge, ScoreCard
```

## Prompt templates

All prompts live in `prompts/`. `{{variable}}` syntax. Hot-reloaded on each call. Validated at startup against `manifest.json`.

| Template | Purpose |
|----------|---------|
| `agent-guidance.md` | Orchestrator system prompt (static) |
| `analyze.md` | Analyzer: classify + detect weaknesses |
| `optimize.md` | Optimizer: rewrite using strategy |
| `scoring.md` | Scorer: independent 5-dimension evaluation (static) |
| `refine.md` | Refinement optimizer (replaces optimize.md during refinement) |
| `suggest.md` | Suggestion generator (3 per turn) |
| `explore.md` | Codebase exploration synthesis (Haiku) |
| `adaptation.md` | Adaptation state formatter |
| `passthrough.md` | MCP passthrough combined template |
| `strategies/*.md` | 6 strategy files: chain-of-thought, few-shot, role-playing, structured-output, meta-prompting, auto |

Variable reference: `prompts/manifest.json`

## MCP server

4 tools with `synthesis_` prefix on port 8001 (`http://127.0.0.1:8001/mcp`):
- `synthesis_optimize` — full pipeline execution
- `synthesis_analyze` — analysis + baseline scoring (task type, weaknesses, strengths, strategy, original scores, actionable next steps)
- `synthesis_prepare_optimization` — assemble prompt + context for external LLM (supports `workspace_path` for roots scanning)
- `synthesis_save_result` — persist result with bias correction

### Adding a tool
1. Add a `@mcp.tool(name="synthesis_...", ...)` function in `mcp_server.py`
2. Use the `synthesis_` prefix for all tool names
3. Return a Pydantic model for structured output; raise `ValueError` for errors

## Common tasks

### Restart backend only
```bash
./init.sh stop && ./init.sh start
```

### Run backend tests
```bash
cd backend && source .venv/bin/activate && pytest --cov=app -v
```

### Run frontend dev server standalone
```bash
cd frontend && npm run dev
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

## Key architectural decisions

- **Pipeline**: 3 subagent phases (analyze → optimize → score) orchestrated by `pipeline.py`. Each phase is an independent LLM call with a fresh context window. Explore phase runs when a GitHub repo is linked.
- **Provider injection**: detected once at startup, injected via `app.state.provider` and MCP lifespan context.
- **Prompt templates**: all prompts live in `prompts/` with `{{variable}}` substitution. Validated at startup. Hot-reloaded on every call. Never hardcode prompts in application code.
- **Scorer bias mitigation**: A/B randomized presentation order + **hybrid scoring** (LLM scores blended with model-independent heuristics via `score_blender.py`). Dimension-specific weights: structure 50% heuristic, conciseness/specificity 40%, clarity 30%, faithfulness 20%. Z-score normalization applied when ≥10 historical samples exist. Divergence flags when LLM and heuristic disagree by >2.5 points.
- **User preferences**: file-based JSON (`data/preferences.json`), loaded as frozen snapshot per pipeline run. Model selection per phase (analyzer/optimizer/scorer), pipeline toggles (explore/scoring/adaptation), default strategy. Non-configurable: explore synthesis and suggestions always use Haiku. Lean mode = explore+scoring off = 2 LLM calls only.
- **Passthrough protocol**: MCP `synthesis_prepare_optimization` assembles the full prompt; external LLM processes it; `synthesis_save_result` persists with heuristic bias correction.
- **Pagination envelope**: all list endpoints return `{total, count, offset, items, has_more, next_offset}`.
- **GitHub token layer**: tokens are Fernet-encrypted at rest. `github_service.encrypt_token` / `decrypt_token` are the only entry points.
- **API key management**: `GET/PATCH/DELETE /api/provider/api-key`. Key encrypted at rest in `data/.api_credentials`. Provider hot-reloads when key is set.
- **Explore architecture**: semantic retrieval + single-shot synthesis (not an agentic loop). SHA-based result caching. Background indexing with `all-MiniLM-L6-v2` embeddings.
- **Roots scanning**: workspace directories scanned for agent guidance files (CLAUDE.md, AGENTS.md, .cursorrules, etc.). Per-file cap: 500 lines / 10K chars. Content wrapped in `<untrusted-context>`.
- **Feedback adaptation**: simple strategy affinity counter. Degenerate pattern detection (>90% same rating over 10+ feedbacks).
- **Refinement**: each turn is a fresh pipeline invocation (not multi-turn accumulation). Rollback creates a branch fork. 3 suggestions generated per turn.
- **Trace logging**: `trace_logger.py` writes per-phase JSONL traces. Daily rotation with configurable retention (`TRACE_RETENTION_DAYS`).
- **Real-time event bus**: `event_bus.py` publishes events (optimization_created, feedback_submitted, refinement_turn) to all SSE subscribers. MCP server (separate process) notifies via HTTP POST to `/api/events/_publish`. Frontend auto-refreshes History on events.
- **Workspace intelligence**: `workspace_intelligence.py` auto-detects project type from manifest files (package.json, requirements.txt, etc.) and injects workspace profile into MCP tool context via `roots/list`.
