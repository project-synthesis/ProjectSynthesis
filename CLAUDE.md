# CLAUDE.md — Project Synthesis

Guidance for Claude Code when working in this repository. **Keep all CLAUDE.md files under 200 lines.** See `backend/CLAUDE.md` and `frontend/CLAUDE.md` for service-specific details.

## Versioning

**Single source of truth:** `/version.json` → `scripts/sync-version.sh` propagates to `backend/app/_version.py`, `frontend/package.json`. Frontend reads version via `$lib/version.ts` (JSON import). Health endpoint serves it at `/api/health`.

**Semver:** `MAJOR.MINOR.PATCH[-prerelease]`

| Bump | When | Example |
|------|------|---------|
| `MAJOR` | Breaking API/schema changes, incompatible migrations | 0.x → 1.0.0 |
| `MINOR` | New features, new endpoints, new MCP tools | 0.1.0 → 0.2.0 |
| `PATCH` | Bug fixes, performance, docs, dependency updates | 0.1.0 → 0.1.1 |
| `-dev` suffix | Unreleased work on main | 0.2.0-dev |

**Release workflow:**
1. Edit `version.json` (remove `-dev` or bump)
2. Run `./scripts/sync-version.sh`
3. Move `docs/CHANGELOG.md` items from `## Unreleased` to `## vX.Y.Z — YYYY-MM-DD`
4. Commit: `release: vX.Y.Z`
5. Tag: `git tag vX.Y.Z && git push origin main --tags`
6. Bump to next dev: edit `version.json` to next version with `-dev`, run sync, commit `chore: bump to X.Y.Z-dev`

**Changelog convention:** Every user-visible change gets a line in `docs/CHANGELOG.md` under `## Unreleased`. Categories: `Added`, `Changed`, `Fixed`, `Removed`. Write in past tense, start with a verb.

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

## Architecture overview

- **Backend**: Python 3.12+, FastAPI, SQLAlchemy async + aiosqlite, SQLite (`data/synthesis.db`). See `backend/CLAUDE.md`
- **Frontend**: SvelteKit 2, Svelte 5 runes, Tailwind CSS 4. See `frontend/CLAUDE.md`
- **Pipeline**: 3 phases (analyze → optimize → score), each an independent LLM call. Models configurable per phase via preferences
- **Routing**: 5-tier priority chain — force_passthrough > force_sampling > internal > auto_sampling > passthrough. See `backend/CLAUDE.md` for internals, `docs/routing-architecture.md` for diagrams
- **Scoring**: hybrid LLM + heuristic with z-score normalization and divergence detection
- **Providers**: Claude CLI or Anthropic API, auto-detected once at startup, stored on `app.state.routing`
- **Taxonomy**: evolutionary hierarchical clustering (`services/taxonomy/`). Hot/warm/cold paths. Domain discovery
- **Layer rules**: `routers/` → `services/` → `models/` only. Services must never import from routers
- **Model IDs**: centralized in `config.py` (`MODEL_SONNET/OPUS/HAIKU`). Use `PreferencesService.resolve_model()`, never hardcode
- **Env vars**: `ANTHROPIC_API_KEY` (optional), `GITHUB_OAUTH_CLIENT_ID`, `GITHUB_OAUTH_CLIENT_SECRET`, `SECRET_KEY` (auto-generated)

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
| `explore-guidance.md` | Codebase analysis guidance for structured context extraction (static) |
| `adaptation.md` | Adaptation state formatter |
| `passthrough.md` | MCP passthrough combined template |
| `extract_patterns.md` | Meta-pattern extraction from completed optimizations (Haiku) |
| `strategies/*.md` | Strategy files with YAML frontmatter. Fully adaptive — add/remove files to change available strategies |

Variable reference: `prompts/manifest.json`

## MCP server

11 tools with `synthesis_` prefix on port 8001 (`http://127.0.0.1:8001/mcp`). All use `structured_output=True`. Tool handlers in `backend/app/tools/*.py`; `mcp_server.py` is a thin registration layer. See `AGENTS.md` for detailed tool usage guidance.

**Adding a tool:**
1. Define Pydantic output model in `schemas/mcp_models.py`
2. Create handler in `backend/app/tools/<name>.py` with `handle_<name>()` async function
3. Re-export from `backend/app/tools/__init__.py`
4. Add `@mcp.tool(structured_output=True)` wrapper in `mcp_server.py`
5. Use `synthesis_` prefix. Access routing via `get_routing()` from `tools/_shared.py`. Raise `ValueError` for user errors

## Common tasks

```bash
./init.sh restart                                              # restart all services
cd backend && source .venv/bin/activate && pytest --cov=app -v # backend tests
cd frontend && npm run test                                    # frontend tests
cd frontend && npm run dev                                     # frontend dev server standalone
docker compose up --build -d                                   # docker deployment
```

## Roadmap protocol

`docs/ROADMAP.md` tracks improvements requiring breaking changes (schema migrations, multi-file refactors, breaking API changes). Non-breaking improvements should be implemented immediately — never deferred to the roadmap.

## Claude Code automation

### `.mcp.json`
Auto-loads the Project Synthesis MCP server (`http://127.0.0.1:8001/mcp`) when this directory is open in Claude Code. Verify with `./init.sh status`.

### Hooks (`.claude/hooks/`)

| Hook | Purpose | Timeout |
|------|---------|---------|
| `pre-pr-ruff.sh` | Python lint via Ruff on `backend/app/` and `backend/tests/` | 60s |
| `pre-pr-svelte.sh` | Svelte type check via `npx svelte-check` on `frontend/` | 120s |

Exit codes: `0` = allow, `2` = block (fix errors first).

### Subagents (`.claude/agents/`)
- **`code-reviewer.md`** — Architecture compliance, brand guidelines, and consistency review.

## Key invariants

- **Pagination**: all list endpoints return `{total, count, offset, items, has_more, next_offset}`
- **GitHub tokens**: Fernet-encrypted at rest. `github_service.encrypt_token`/`decrypt_token` are the only entry points
- **API key**: encrypted at rest in `data/.api_credentials`. Provider hot-reloads when key is set via `GET/PATCH/DELETE /api/provider/api-key`
- **Secrets**: `SECRET_KEY` auto-generated on first startup, persisted to `data/.app_secrets` (0o600)
- **Event bus**: `event_bus.py` publishes to SSE subscribers. Types: `optimization_created/analyzed/failed`, `feedback_submitted`, `refinement_turn`, `strategy_changed`, `taxonomy_changed`, `routing_state_changed`, `domain_created`. MCP server notifies via HTTP POST to `/api/events/_publish`
- **Preferences**: file-based JSON (`data/preferences.json`), loaded as frozen snapshot per pipeline run. Model selection per phase, pipeline toggles, default strategy, per-phase effort
- **Trace logging**: per-phase JSONL to `data/traces/`, daily rotation, configurable retention
- **Context enrichment**: unified `ContextEnrichmentService.enrich()` for all routing tiers. All call sites default `workspace_path` to `PROJECT_ROOT`
- **Workspace intelligence**: auto-detects project type from manifests + deep scanning (README, entry points, architecture docs)
- **Refinement**: each turn is a fresh pipeline invocation, not multi-turn accumulation. Rollback creates a branch fork. 3 suggestions per turn
- **Feedback adaptation**: strategy affinity counter. Degenerate pattern detection (>90% same rating over 10+ feedbacks)
