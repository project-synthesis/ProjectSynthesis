# CLAUDE.md — Project Synthesis

Guidance for Claude Code when working in this repository.

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
- `AnalysisResult.task_type` is a `Literal` — valid values: `coding`, `writing`, `analysis`, `creative`, `data`, `system`, `general`. `selected_strategy` is a plain `str` — validated at runtime against files in `prompts/strategies/` (fully adaptive, no hardcoded list). `intent_label` (3-6 word phrase) and `domain` (`backend`, `frontend`, `database`, `devops`, `security`, `fullstack`, `general`) are extracted by the analyzer and default to `"general"`.

### Key services (`backend/app/services/`)
- `pipeline.py` — orchestrates analyzer → optimizer → scorer (3-phase pipeline)
- `sampling_pipeline.py` — MCP sampling-based pipeline (extracted from `mcp_server.py`). Full feature parity with internal pipeline via `run_sampling_pipeline()` and `run_sampling_analyze()`. Uses structured output via tool calling in sampling, model preferences per phase, `SamplingLLMAdapter` for explore phase. Falls back to text parsing when client doesn't support tools in sampling.
- `prompt_loader.py` — template loading + variable substitution from `prompts/`. Validates all templates at startup.
- `strategy_loader.py` — strategy file discovery from `prompts/strategies/` with YAML frontmatter parsing (tagline, description). Warns if empty at startup (does not crash). `load()` strips frontmatter before injection. Fully adaptive — adding/removing `.md` files changes available strategies.
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
- `pattern_extractor.py` — post-completion pattern extraction: embeds prompts, clusters into families (cosine ≥0.78), extracts meta-patterns via Haiku (cosine ≥0.82 for merge). Subscribes to `optimization_created` events.
- `pattern_matcher.py` — on-paste similarity search: matches prompt text against family centroids (cosine ≥0.72 suggestion threshold). Returns best family + meta-patterns.
- `knowledge_graph.py` — graph building for radial mindmap: family nodes, domain grouping, cross-family edges (cosine ≥0.55), semantic search, family detail with linked optimizations.
- `routing.py` — intelligent routing service: `RoutingState` (immutable capabilities snapshot), `RoutingContext` (per-request), `RoutingDecision` (tier + reason), `resolve_route()` (pure 5-tier decision function), `RoutingManager` (state lifecycle, SSE events, disconnect detection, persistence recovery)

### Model configuration
Model IDs are centralized in `config.py` as `MODEL_SONNET`, `MODEL_OPUS`, `MODEL_HAIKU` (default: `claude-sonnet-4-6`, `claude-opus-4-6`, `claude-haiku-4-5`). Never hardcode model IDs in service code — use `PreferencesService.resolve_model(phase, snapshot)` which maps user preferences to full model IDs.

### Providers (`backend/app/providers/`)
- `detector.py` — auto-selects: Claude CLI → Anthropic API
- `claude_cli.py` — CLI subprocess (Max subscription, zero cost)
- `anthropic_api.py` — direct API via `anthropic` SDK with prompt caching (`cache_control: ephemeral`)
- `base.py` — `LLMProvider` abstract base with `complete_parsed()` and `thinking_config()`

Provider is detected **once at startup** and stored on `app.state.routing` (a `RoutingManager` instance that wraps the provider and MCP state). Never call `detect_provider()` inside a request handler.

### Routers (`backend/app/routers/`)
- `optimize.py` — `POST /api/optimize` (SSE), `GET /api/optimize/{trace_id}`
- `history.py` — `GET /api/history` (sort/filter with pagination envelope, includes truncated `raw_prompt` + `optimized_prompt`)
- `feedback.py` — `POST /api/feedback`, `GET /api/feedback?optimization_id=X`
- `refinement.py` — `POST /api/refine` (SSE), `GET /api/refine/{id}/versions`, `POST /api/refine/{id}/rollback`
- `providers.py` — `GET /api/providers`, `GET/PATCH/DELETE /api/provider/api-key`
- `preferences.py` — `GET /api/preferences`, `PATCH /api/preferences` (persistent user settings)
- `strategies.py` — `GET /api/strategies`, `GET /api/strategies/{name}`, `PUT /api/strategies/{name}` (strategy template CRUD)
- `settings.py` — `GET /api/settings` (read-only server config)
- `github_auth.py` — OAuth flow (login, callback, me, logout)
- `github_repos.py` — repo management (list, link, linked, unlink)
- `health.py` — `GET /api/health` (status, provider, score_health, recent_errors, avg_duration_ms, sampling_capable, mcp_disconnected, available_tiers)
- `events.py` — `GET /api/events` (SSE event stream), `POST /api/events/_publish` (internal cross-process)
- `patterns.py` — `GET /api/patterns/graph`, `POST /api/patterns/match`, `GET /api/patterns/families`, `GET /api/patterns/families/{id}`, `PATCH /api/patterns/families/{id}`, `GET /api/patterns/search`, `GET /api/patterns/stats`

### Sort column whitelist
`optimization_service.py` defines `_VALID_SORT_COLUMNS`. Add new sortable columns there before using them.

### Shared utilities
- `app/utils/sse.py` — shared `format_sse()` for SSE event formatting (used by optimize + refinement routers)
- `app/dependencies/rate_limit.py` — in-memory rate limiting FastAPI dependency via `limits` library

### Pattern knowledge graph models (`app/models.py`)
- `PatternFamily` — cluster of related optimizations. Fields: `centroid_embedding` (running mean, bytes), `intent_label`, `domain`, `task_type`, `member_count`, `usage_count`, `avg_score`.
- `MetaPattern` — reusable technique extracted from family members. `embedding` (bytes), `pattern_text`, `source_count`, `family_id` FK.
- `OptimizationPattern` — join table linking `Optimization` → `PatternFamily` with similarity score.

## Frontend

- **Framework**: SvelteKit 2 (Svelte 5 runes) + Tailwind CSS 4
- **Dev server**: `npm run dev` → port 5199
- **API client**: `frontend/src/lib/api/client.ts` — all backend calls go through here
- **Theme**: industrial cyberpunk — dark backgrounds (`#06060c`), 1px neon contours (`#00e5ff`), no rounded corners, no drop shadows, no glow effects

### Stores (`frontend/src/lib/stores/`)
- `forge.svelte.ts` — optimization pipeline state (prompt, strategy, SSE events, result, feedback). Session persistence via `localStorage` (`synthesis:last_trace_id`) — page refresh restores last optimization from DB.
- `editor.svelte.ts` — tab management (prompt/result/diff/mindmap types)
- `github.svelte.ts` — GitHub auth + repo link state
- `refinement.svelte.ts` — refinement sessions (turns, branches, suggestions, score progression)
- `preferences.svelte.ts` — persistent user preferences loaded from backend
- `toast.svelte.ts` — toast notification queue with `addToast()` API
- `patterns.svelte.ts` — pattern knowledge graph state: paste detection (50-char delta, 300ms debounce), suggestion lifecycle (auto-dismiss 10s), graph data for mindmap, family selection for Inspector, graph invalidation via `pattern_updated` SSE

### Component layout
```
src/lib/components/
  layout/       # ActivityBar, Navigator, PatternNavigator, EditorGroups, Inspector, StatusBar
  editor/       # PromptEdit, ForgeArtifact, PatternSuggestion
  patterns/     # RadialMindmap
  refinement/   # RefinementTimeline, RefinementTurnCard, SuggestionChips,
                # BranchSwitcher, ScoreSparkline, RefinementInput
  shared/       # CommandPalette, DiffView, MarkdownRenderer, ProviderBadge, ScoreCard, Toast
```

### Shared frontend utilities
- `constants/patterns.ts` — domain color map, `scoreColor()` helper (shared by Navigator, RadialMindmap, PatternNavigator, Inspector)

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
| `extract_patterns.md` | Meta-pattern extraction from completed optimizations (Haiku) |
| `strategies/*.md` | Strategy files with YAML frontmatter (`tagline`, `description`). Fully adaptive — add/remove files to change available strategies. Ships with 6: auto, chain-of-thought, few-shot, meta-prompting, role-playing, structured-output |

Variable reference: `prompts/manifest.json`

## MCP server

4 tools with `synthesis_` prefix on port 8001 (`http://127.0.0.1:8001/mcp`). All tools use `structured_output=True` (return Pydantic models, expose `outputSchema` to MCP clients):
- `synthesis_optimize` — full pipeline execution. Params: `prompt`, `strategy`, `repo_full_name`, `workspace_path`, `applied_pattern_ids` (list of meta-pattern IDs to inject into optimizer context). Returns `OptimizeOutput`.
- `synthesis_analyze` — analysis + baseline scoring. Falls back to MCP sampling when no local provider. Returns `AnalyzeOutput`.
- `synthesis_prepare_optimization` — assemble prompt + context for external LLM. Returns `PrepareOutput`.
- `synthesis_save_result` — persist result with bias correction. Returns `SaveResultOutput`.

### Sampling capability detection

Sampling capability is detected via `RoutingManager` (in-memory primary, `mcp_session.json` write-through for restart recovery). Two detection layers:

1. **ASGI middleware** (`_CapabilityDetectionMiddleware`) — intercepts `initialize` JSON-RPC messages, calls `RoutingManager.on_mcp_initialize(sampling_capable)`. Detects capability instantly on connection, before any tool call.
2. **Activity tracking** — middleware calls `RoutingManager.on_mcp_activity()` on every POST (throttled to 10s). Background disconnect checker (60s poll, 300s staleness) marks `mcp_connected=False` when activity goes stale.

**Optimistic strategy**: `False` never overwrites a fresh `True` within the 30-minute staleness window (`MCP_CAPABILITY_STALENESS_MINUTES` in `config.py`). Prevents VS Code multi-session flicker.

**Health endpoint**: reads live state from `app.state.routing` (not file). Returns `sampling_capable: bool | null`, `mcp_disconnected: bool`, and `available_tiers: list[str]`.

**Frontend**: purely reactive — receives `routing_state_changed` SSE events for tier availability changes. Fixed 60s health polling for display only (no routing decisions). Shows toasts on MCP connect/disconnect/sampling capability changes.

**Toggle safety**: disabled conditions are prefixed with `!currentValue &&` so a toggle that's already ON is always interactive (user can turn it OFF even if preconditions change).

### Sampling pipeline

When no local provider is available (or `force_sampling=True`), the full pipeline runs via MCP `sampling/createMessage`. Implemented in `services/sampling_pipeline.py`:

- **Structured output via tool calling**: sends Pydantic-derived `Tool` schemas via `tools` + `tool_choice` on `create_message()`. Falls back to text parsing if client doesn't support tools in sampling.
- **Model preferences per phase**: `ModelPreferences` with `ModelHint` steer the IDE's model selection (analyze=Sonnet, optimize=Opus, score=Sonnet, suggest=Haiku). Overridable via user preferences.
- **Full feature parity**: explore (via `SamplingLLMAdapter`), applied patterns, adaptation state, suggest phase, intent drift detection, z-score normalization — all features from the internal pipeline.
- **Model ID capture**: `result.model` from each sampling response is collected per phase and persisted to DB (replaces hardcoded `"ide_llm"`).

### Adding a tool
1. Add a `@mcp.tool(structured_output=True)` function in `mcp_server.py`
2. Use the `synthesis_` prefix for all tool names
3. The tool handler automatically participates in routing via `_routing.resolve()` — no manual capability writes needed
4. Return a Pydantic model (define in `schemas/mcp_models.py`); raise `ValueError` for errors

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

- **Pipeline**: 3 subagent phases (analyze → optimize → score) orchestrated by `pipeline.py`. Each phase is an independent LLM call with a fresh context window. Explore phase runs when a GitHub repo is linked AND `enable_explore` preference is true. Scoring phase skippable via `enable_scoring` preference (lean mode = 2 LLM calls only).
- **Provider injection**: detected once at startup, injected via `app.state.routing` (RoutingManager) and MCP lifespan context. All routers call `routing.resolve()` to get the active provider.
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
- **Real-time event bus**: `event_bus.py` publishes events to all SSE subscribers. Event types: `optimization_created`, `optimization_analyzed`, `optimization_failed`, `feedback_submitted`, `refinement_turn`, `strategy_changed`, `pattern_updated`, `routing_state_changed`. MCP server (separate process) notifies via HTTP POST to `/api/events/_publish`. Frontend auto-refreshes History on events, shows toast notifications, syncs Inspector feedback state, and updates StatusBar metrics.
- **Workspace intelligence**: `workspace_intelligence.py` auto-detects project type from manifest files (package.json, requirements.txt, etc.) and injects workspace profile into MCP tool context via `roots/list`.
- **MCP sampling detection**: ASGI middleware on `initialize` calls `RoutingManager.on_mcp_initialize()` (in-memory state primary, `mcp_session.json` write-through for restart recovery). Optimistic strategy prevents VS Code multi-session flicker (False never overwrites fresh True within 30-min window). `RoutingManager` owns in-memory `sampling_capable`, `mcp_connected`, timestamps. Health endpoint reads live state from `app.state.routing`. Background disconnect checker (60s poll, 300s staleness window). Dual staleness windows in `config.py`: `MCP_CAPABILITY_STALENESS_MINUTES` (30 min), `MCP_ACTIVITY_STALENESS_SECONDS` (300s). MCP server is sole writer to `mcp_session.json`. Frontend receives `routing_state_changed` SSE events — no longer makes routing decisions. Fixed 60s health polling for display only.
- **MCP sampling pipeline**: full feature parity with internal pipeline — extracted into `services/sampling_pipeline.py`. Uses structured output via tool calling (`tools` + `tool_choice` on `create_message()`), `ModelPreferences` per phase, `SamplingLLMAdapter` for explore, and captures `result.model` for DB persistence. All 4 MCP tools use `structured_output=True` (return Pydantic models, expose `outputSchema`). `synthesis_analyze` falls back to sampling when no local provider.
- **Knowledge graph**: self-building pattern library. Post-completion background job embeds prompts, clusters into `PatternFamily` groups (cosine ≥0.78 merge), extracts meta-patterns via Haiku (cosine ≥0.82 pattern merge). On-paste detection (50-char delta, 300ms debounce) suggests matching families (cosine ≥0.72). Applied patterns injected into optimizer context. Models: `PatternFamily` (centroid running mean), `MetaPattern` (enriched on duplicate), `OptimizationPattern` (join). Cross-family edges at cosine ≥0.55. Domain color coding: backend=#a855f7, frontend=#f59e0b, database=#10b981, security=#ef4444, devops=#3b82f6, fullstack=#00e5ff, general=#6b7280. UI: PatternNavigator (search + paginated family list + domain filter), Inspector family detail (linked optimizations, rename), RadialMindmap (D3.js force-directed SVG), StatusBar pattern count. Activity type `'patterns'` in layout routing.
- **Intelligent routing**: centralized 5-tier priority chain in `services/routing.py`: force_passthrough > force_sampling > internal provider > auto sampling > passthrough fallback. Pure `resolve_route()` function (deterministic, no I/O) + thin `RoutingManager` wrapper (state lifecycle, SSE events, persistence, disconnect detection). Both FastAPI and MCP server own their own `RoutingManager` instance. `routing` SSE event emitted as first event in every optimize stream. `routing_state_changed` ambient SSE for tier availability changes. Frontend is purely reactive — never makes routing decisions. `caller` field gates sampling (REST callers never reach sampling tiers). Unified `POST /api/optimize` handles passthrough inline via SSE (no more 503).
