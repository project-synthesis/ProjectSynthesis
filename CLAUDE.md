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
- `sampling_pipeline.py` — MCP sampling-based pipeline (extracted from `mcp_server.py`). Full feature parity with internal pipeline via `run_sampling_pipeline()` and `run_sampling_analyze()`. Uses structured output via tool calling in sampling, per-phase model ID capture (`result.model` persisted to DB), `SamplingLLMAdapter` for explore phase. Falls back to text parsing with JSON schema injection when client doesn't support tools (catches `McpError`). Scoring capped at 1024 tokens with JSON terminal directive. Text fallback uses `strip_meta_header` and `split_prompt_and_changes` from `app.utils.text_cleanup` (preamble/fence/orphan removal, 14 change-marker patterns), `_build_analysis_from_text` (keyword-based classification from raw prompt).
- `prompt_loader.py` — template loading + variable substitution from `prompts/`. Validates all templates at startup.
- `strategy_loader.py` — strategy file discovery from `prompts/strategies/` with YAML frontmatter parsing (tagline, description). Warns if empty at startup (does not crash). `load()` strips frontmatter before injection. Fully adaptive — adding/removing `.md` files changes available strategies.
- `context_enrichment.py` — unified enrichment orchestrator for all routing tiers. Single `enrich()` entry point resolving workspace guidance, heuristic analysis (passthrough), curated codebase context (all tiers when repo linked), adaptation state, and applied patterns. Returns frozen `EnrichedContext` with accessor properties. All call sites default `workspace_path` to `PROJECT_ROOT`.
- `heuristic_analyzer.py` — zero-LLM prompt classifier for passthrough tier. 6-layer analysis: keyword classification, structural analysis, weakness/strength detection, adaptation-aware strategy selection, historical learning, intent label generation.
- `context_resolver.py` — per-source character caps, untrusted-context wrapping, workspace roots scanning (internal pipeline path)
- `roots_scanner.py` — discovers agent guidance files (CLAUDE.md, AGENTS.md, GEMINI.md, .cursorrules, .clinerules, CONVENTIONS.md, etc.) from workspace paths. `discover_project_dirs()` detects manifest-containing subdirectories. SHA256 content deduplication (root copy wins).
- `optimization_service.py` — CRUD, sort/filter, score distribution tracking, recent error counts
- `feedback_service.py` — feedback CRUD + synchronous adaptation tracker update
- `adaptation_tracker.py` — strategy affinity tracking with degenerate pattern detection
- `heuristic_scorer.py` — 5-dimension heuristics (clarity, specificity, structure, faithfulness, conciseness) + `score_prompt()` facade. Consistent `max(1.0, min(10.0, score))` clamping across all dimensions
- `score_blender.py` — hybrid scoring engine: blends LLM + heuristic scores with z-score normalization and divergence detection
- `preferences.py` — persistent user preferences (model selection, pipeline toggles, default strategy, per-phase effort). File-based JSON at `data/preferences.json`. Snapshot pattern for pipeline consistency. Validates `optimizer_effort`, `analyzer_effort`, `scorer_effort` (`"low"` | `"medium"` | `"high"` | `"max"`).
- `file_watcher.py` — background watchfiles.awatch() task for strategy file hot-reload. Publishes `strategy_changed` events to event bus on file add/modify/delete.
- `refinement_service.py` — refinement sessions, version CRUD, branching/rollback, suggestion generation
- `trace_logger.py` — per-phase JSONL traces to `data/traces/`, daily rotation
- `embedding_service.py` — singleton sentence-transformers (`all-MiniLM-L6-v2`, 384-dim). Async wrappers via `aembed_single`/`aembed_texts`.
- `codebase_explorer.py` — semantic retrieval + single-shot Haiku synthesis. SHA-based result caching.
- `explore_cache.py` — in-memory TTL cache with LRU eviction for explore results
- `repo_index_service.py` — background repo file indexing with type-aware structured outlines (`FileOutline`) and `query_curated_context()` for token-conscious semantic retrieval with domain boosting and budget packing
- `passthrough.py` — shared passthrough prompt assembly (strategy resolution, scoring rubric loading, template rendering) used by both REST and MCP passthrough paths
- `pattern_injection.py` — shared `auto_inject_patterns()` for cluster meta-pattern discovery from taxonomy embedding index (used by internal + sampling pipelines)
- `pipeline_constants.py` — shared pipeline constants (`CONFIDENCE_GATE`, `FALLBACK_STRATEGY`, `VALID_DOMAINS`, `resolve_fallback_strategy()`) for both pipeline implementations
- `event_notification.py` — cross-process HTTP-based event bus notification for MCP server → backend event publishing
- `mcp_session_file.py` — stateless `mcp_session.json` read/write/staleness helper (used by MCP server and health endpoint)
- `mcp_proxy.py` — async MCP client for REST→MCP sampling proxy via Streamable HTTP tool calls
- `github_service.py` — Fernet token encryption/decryption
- `github_client.py` — raw GitHub API calls; explicit token parameter on every method
- `event_bus.py` — in-process pub/sub for real-time cross-client notifications
- `workspace_intelligence.py` — zero-config workspace analysis: manifest-based stack detection + deep scanning (README.md first 80 lines, entry point files first 40 lines × 3, architecture docs first 60 lines × 3). All enrichment call sites default `workspace_path` to `PROJECT_ROOT`.
- `embedding_index.py` — in-memory numpy EmbeddingIndex (384-dim, `all-MiniLM-L6-v2`). O(1) upsert/remove, batch cosine search. Used by taxonomy engine hot/warm/cold paths.
- `prompt_lifecycle.py` — auto-curation service: state promotion (active→mature→template), quality pruning (flag+archive), temporal usage decay (0.9× after 30d), strategy affinity tracking, orphan backfill. Called post-hot-path and post-warm-path.
- `routing.py` — intelligent routing service: `RoutingState` (immutable capabilities snapshot), `RoutingContext` (per-request), `RoutingDecision` (tier + reason), `resolve_route()` (pure 5-tier decision function), `RoutingManager` (process-level singleton, state lifecycle, SSE events, dual disconnect signals, persistence recovery). See `backend/CLAUDE.md` for routing internals
- `taxonomy/` — evolutionary taxonomy engine. Process-wide singleton via `get_engine()`/`set_engine()` with thread-safe double-checked locking. `engine.py` orchestrates 3 paths: hot (per-optimization embedding + nearest-node cosine search), warm (periodic HDBSCAN clustering + speculative lifecycle mutations gated by Q_system non-regression), cold (full refit + UMAP 3D projection + OKLab coloring + Haiku labeling). Sub-modules: `clustering.py` (HDBSCAN wrapper), `lifecycle.py` (emerge/merge/split/retire operations), `quality.py` (5-dimension Q_system with adaptive weights), `projection.py` (UMAP + Procrustes alignment + PCA fallback), `coloring.py` (OKLab from UMAP position), `labeling.py` (Haiku 2-4 word labels), `snapshot.py` (audit trail CRUD + retention), `sparkline.py` (LTTB downsampling + OLS trend), `family_ops.py` (family CRUD), `matching.py` (cascade search), `embedding_index.py` (numpy index). Key types: `TaxonomyMapping` (domain embed result), `PatternMatch` (cascade search result), `QWeights` (frozen constant-sum weights), `SparklineData`

### Model configuration
Model IDs are centralized in `config.py` as `MODEL_SONNET`, `MODEL_OPUS`, `MODEL_HAIKU` (default: `claude-sonnet-4-6`, `claude-opus-4-6`, `claude-haiku-4-5`). Never hardcode model IDs in service code — use `PreferencesService.resolve_model(phase, snapshot)` which maps user preferences to full model IDs.

### Providers (`backend/app/providers/`)
- `detector.py` — auto-selects: Claude CLI → Anthropic API
- `claude_cli.py` — CLI subprocess (Claude Code subscription, zero marginal cost). Gates `--effort` for Haiku
- `anthropic_api.py` — direct API via `anthropic` SDK with prompt caching (`cache_control: ephemeral`), streaming via `messages.stream()`, `max_retries=0` (app-level retry is sole controller)
- `base.py` — `LLMProvider` abstract base with `complete_parsed()`, `complete_parsed_streaming()` (default falls back to non-streaming), `thinking_config()`, and `call_provider_with_retry()` (dispatches to streaming/non-streaming with smart retryable classification)

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
- `clusters.py` — `GET /api/clusters` (paginated list with state/domain filter), `GET /api/clusters/{id}` (detail with children/breadcrumb/optimizations), `POST /api/clusters/match` (paste-time similarity), `GET /api/clusters/tree` (flat node list for 3D viz), `GET /api/clusters/stats` (Q metrics + sparkline), `GET /api/clusters/templates` (proven templates), `POST /api/clusters/recluster` (cold-path trigger), `PATCH /api/clusters/{id}` (rename/state override). Legacy 301 redirects for `/api/patterns/*` and `/api/taxonomy/*`.

### Sort column whitelist
`optimization_service.py` defines `VALID_SORT_COLUMNS`. Add new sortable columns there before using them.

### Shared utilities
- `app/utils/sse.py` — shared `format_sse()` for SSE event formatting (used by optimize + refinement routers)
- `app/dependencies/rate_limit.py` — in-memory rate limiting FastAPI dependency via `limits` library

### Cluster and snapshot models (`app/models.py`)
- `PromptCluster` — unified cluster model. UUID PK, self-join `parent_id`, L2-normalized centroid embedding (384-dim), per-node metrics (coherence, separation, stability, persistence), lifecycle state (`candidate`|`active`|`mature`|`template`|`archived`), intent/domain/task_type, member/usage counts, avg_score, preferred_strategy, promoted_at/archived_at timestamps.
- `TaxonomySnapshot` — audit trail. UUID PK, trigger (`warm_path`|`cold_path`|`manual`), system metrics (q_system, q_coherence, q_separation, q_coverage, q_dbcv), operation log + tree_state (JSON), node creation/retirement/merge/split counts.
- `MetaPattern` — reusable technique extracted from cluster members. `embedding` (bytes), `pattern_text`, `source_count`, `cluster_id` FK.
- `OptimizationPattern` — join table linking `Optimization` → `PromptCluster` with similarity score and relationship type.

## Frontend

- **Framework**: SvelteKit 2 (Svelte 5 runes) + Tailwind CSS 4
- **Dev server**: `npm run dev` → port 5199
- **API client**: `frontend/src/lib/api/client.ts` — all backend calls go through here
- **Brand**: industrial cyberpunk — see `brand-guidelines` skill for color system, tier aesthetics, typography, zero-effects directive, and component patterns

### Stores (`frontend/src/lib/stores/`)
- `forge.svelte.ts` — optimization pipeline state (prompt, strategy, SSE events, result, feedback). Session persistence via `localStorage` (`synthesis:last_trace_id`) — page refresh restores last optimization from DB.
- `editor.svelte.ts` — tab management (prompt/result/diff/mindmap types)
- `github.svelte.ts` — GitHub auth + repo link state
- `refinement.svelte.ts` — refinement sessions (turns, branches, suggestions, score progression)
- `preferences.svelte.ts` — persistent user preferences loaded from backend
- `toast.svelte.ts` — toast notification queue with `addToast()` API
- `routing.svelte.ts` — unified derived routing state mirroring backend 5-tier priority chain (force_passthrough > force_sampling > internal > auto_sampling > passthrough). Reactive tier resolver for UI adaptation
- `passthrough-guide.svelte.ts` — passthrough workflow guide modal state (visibility, "don't show again" preference)
- `clusters.svelte.ts` — cluster state: paste detection (50-char delta, 300ms debounce), suggestion lifecycle (auto-dismiss 10s), cluster tree/stats for topology, cluster detail for Inspector, template spawning, graph invalidation via `taxonomy_changed` SSE

### Component layout
```
src/lib/components/
  layout/       # ActivityBar, Navigator, ClusterNavigator, EditorGroups, Inspector, StatusBar
  editor/       # PromptEdit, ForgeArtifact, PatternSuggestion, PassthroughView
  taxonomy/     # SemanticTopology, TopologyControls, TopologyRenderer, TopologyData,
                # TopologyInteraction, TopologyLabels, TopologyWorker
  refinement/   # RefinementTimeline, RefinementTurnCard, SuggestionChips,
                # BranchSwitcher, ScoreSparkline, RefinementInput
  shared/       # CommandPalette, DiffView, Logo, MarkdownRenderer, PassthroughGuide,
                # ProviderBadge, ScoreCard, Toast
```

### Shared frontend utilities
- `utils/colors.ts` — `scoreColor()`, `taxonomyColor()` (hex/domain/null → color), `qHealthColor()`, `stateColor()`. Used by Navigator, ClusterNavigator, Inspector, StatusBar, TopologyControls

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
| `strategies/*.md` | Strategy files with YAML frontmatter (`tagline`, `description`). Fully adaptive — add/remove files to change available strategies. Ships with 6: auto, chain-of-thought, few-shot, meta-prompting, role-playing, structured-output |

Variable reference: `prompts/manifest.json`

## MCP server

11 tools with `synthesis_` prefix on port 8001 (`http://127.0.0.1:8001/mcp`). All tools use `structured_output=True` (return Pydantic models, expose `outputSchema` to MCP clients). Tool handlers live in `backend/app/tools/*.py`; `mcp_server.py` is a thin registration layer (~700 lines).

### Core pipeline tools
- `synthesis_optimize` — full pipeline execution. Params: `prompt`, `strategy`, `repo_full_name`, `workspace_path`, `applied_pattern_ids` (list of meta-pattern IDs to inject into optimizer context). Returns `OptimizeOutput`.
- `synthesis_analyze` — analysis + baseline scoring. Falls back to MCP sampling when no local provider. Returns `AnalyzeOutput`.
- `synthesis_prepare_optimization` — assemble prompt + context for external LLM (step 1 of passthrough workflow). Returns `PrepareOutput`.
- `synthesis_save_result` — persist result with hybrid scoring and domain validation (step 3 of passthrough workflow). Returns `SaveResultOutput`.

### Workflow tools
- `synthesis_health` — system capabilities check (provider, tiers, strategies, stats). Call at session start. Returns `HealthOutput`.
- `synthesis_strategies` — list available optimization strategies with metadata. Returns `StrategiesOutput`.
- `synthesis_history` — paginated optimization history with sort/filter. Returns `HistoryOutput`.
- `synthesis_get_optimization` — full detail for a specific optimization (by ID or trace_id). Returns `OptimizationDetailOutput`.
- `synthesis_match` — knowledge graph search for similar clusters and reusable patterns. Returns `MatchOutput`.
- `synthesis_feedback` — submit quality feedback (thumbs_up/thumbs_down) to drive strategy adaptation. Returns `FeedbackOutput`.
- `synthesis_refine` — iteratively improve an optimized prompt with specific instructions. Requires a local provider. Returns `RefineOutput`.

### Sampling capability detection

Sampling capability is detected via a **process-level singleton `RoutingManager`** (in-memory primary, `mcp_session.json` write-through for restart recovery). The MCP server's lifespan runs per Streamable HTTP session, but all singletons (RoutingManager, provider, taxonomy engine, context service) are initialized once via a `_process_initialized` guard — never replaced or cleaned up per-session. Two detection layers:

1. **ASGI middleware** (`_CapabilityDetectionMiddleware`) — intercepts `initialize` JSON-RPC messages. Non-sampling clients (Claude Code) are blocked from overwriting `sampling_capable=True` via a dual-layer guard: primary check on RoutingManager state + secondary check on class-level `_sampling_sse_sessions` set. Sampling clients call `RoutingManager.on_mcp_initialize(sampling_capable=True)`.
2. **Activity tracking** — middleware calls `RoutingManager.on_mcp_activity()` only for sampling clients (throttled to 10s). Non-sampling clients keep the session file fresh but never refresh routing activity. Background disconnect checker (30s poll, 60s staleness) reads `mcp_session.json` before disconnecting to detect cross-process activity.

**Disconnect model**: two distinct disconnect signals:
- `on_mcp_disconnect()` — all SSE streams closed. Clears both `mcp_connected` and `sampling_capable`.
- `on_sampling_disconnect()` — last sampling SSE closed, non-sampling clients remain. Clears only `sampling_capable`, keeps `mcp_connected=True`.

**Capability trust model**: `on_mcp_initialize()` always trusts the incoming value — no optimistic buffering. Recovery from `mcp_session.json` applies a 30-minute staleness window (`MCP_CAPABILITY_STALENESS_MINUTES`). Stale session file is cleared once at process startup (`__main__`), not per-session.

**Health endpoint**: reads live state from `app.state.routing` (not file). Returns `sampling_capable: bool | null`, `mcp_disconnected: bool`, and `available_tiers: list[str]`.

**Frontend**: purely reactive — receives `routing_state_changed` SSE events for tier availability changes. Fixed 60s health polling for display only (no routing decisions). Shows toasts on MCP connect/disconnect/sampling capability changes.

**Toggle safety**: disabled conditions are prefixed with `!currentValue &&` so a toggle that's already ON is always interactive (user can turn it OFF even if preconditions change).

### VS Code bridge extension

The MCP Copilot Bridge (`VSGithub/mcp-copilot-extension/`) is a VS Code extension that connects to the MCP server via Streamable HTTP and declares `sampling` capability. It enables the sampling tier by forwarding `sampling/createMessage` requests to VS Code's Language Model API.

- **Connection**: Streamable HTTP to `http://127.0.0.1:8001/mcp` (SSE fallback). Declares `capabilities: { sampling: {}, roots: {} }`.
- **Tool registration**: discovers `synthesis_*` tools via `listTools()`, registers each with `vscode.lm.registerTool()`.
- **System prompt workaround**: VS Code's LM API has no native system role. System prompt injected as a user message in `<system-instructions>` tags, followed by an assistant acknowledgment.
- **Free-text phases**: `OptimizationResult` and `SuggestionsOutput` skip JSON schema injection to preserve markdown output quality. All other phases get explicit JSON schema instructions.
- **Health check**: 10s interval via `listTools()`. Auto-reconnects on failure (server restarts, network drops).
- **Timeouts**: 90s per sampling request (under MCP server's 120s ceiling).

### Sampling pipeline

When no local provider is available (or `force_sampling=True`), the full pipeline runs via MCP `sampling/createMessage`. Implemented in `services/sampling_pipeline.py`:

- **Structured output via tool calling**: sends Pydantic-derived `Tool` schemas via `tools` + `tool_choice` on `create_message()`. Falls back to text parsing with JSON schema injection when client doesn't support tools (catches `McpError`). Scoring capped at 1024 tokens with JSON terminal directive. Text fallback uses `strip_meta_header` and `split_prompt_and_changes` from `app.utils.text_cleanup` (preamble/fence/orphan removal, 14 change-marker patterns), `_build_analysis_from_text` (keyword-based classification from raw prompt).
- **Per-phase model capture**: the actual model used by the IDE is recorded per phase (`result.model`) and persisted to DB (replaces hardcoded `"ide_llm"`). IDE selects model freely — no advisory hints sent.
- **Full feature parity**: explore (via `SamplingLLMAdapter`), applied patterns, adaptation state, suggest phase, intent drift detection, z-score normalization — all features from the internal pipeline.

See `backend/CLAUDE.md` for sampling pipeline internals (fallback chain, phase details, text parsing).

### Adding a tool
1. Define a Pydantic output model in `schemas/mcp_models.py`
2. Create a handler in `backend/app/tools/<name>.py` with a `handle_<name>()` async function
3. Re-export from `backend/app/tools/__init__.py`
4. Add a thin `@mcp.tool(structured_output=True)` wrapper in `mcp_server.py` that calls the handler
5. Use the `synthesis_` prefix for all tool names
6. The handler accesses routing via `get_routing()` from `tools/_shared.py` — no manual capability writes needed
7. Raise `ValueError` for user-facing errors

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

- **Pipeline**: 3 subagent phases (analyze → optimize → score) orchestrated by `pipeline.py`. Each phase is an independent LLM call with a fresh context window. Optimize/refine phases use streaming (`messages.stream()`) to prevent HTTP timeouts on long Opus outputs (up to 128K tokens). Explore phase runs when a GitHub repo is linked AND `enable_explore` preference is true. Scoring phase skippable via `enable_scoring` preference (lean mode = 2 LLM calls only).
- **Provider injection**: detected once at startup, injected via `app.state.routing` (RoutingManager) and MCP lifespan context. All routers call `routing.resolve()` to get the active provider.
- **Prompt templates**: all prompts live in `prompts/` with `{{variable}}` substitution. Validated at startup. Hot-reloaded on every call. Never hardcode prompts in application code.
- **Scorer bias mitigation**: A/B randomized presentation order + **hybrid scoring** (LLM scores blended with model-independent heuristics via `score_blender.py`). Dimension-specific weights: structure 50% heuristic, conciseness/specificity 40%, clarity 30%, faithfulness 20%. Z-score normalization applied when ≥10 historical samples exist. Divergence flags when LLM and heuristic disagree by >2.5 points. Passthrough scores clamped to [1.0, 10.0] before blending; `hybrid_passthrough` excluded from z-score historical distribution to prevent cross-mode contamination.
- **User preferences**: file-based JSON (`data/preferences.json`), loaded as frozen snapshot per pipeline run. Model selection per phase (analyzer/optimizer/scorer), pipeline toggles (explore/scoring/adaptation), default strategy, per-phase effort (`"low"` | `"medium"` | `"high"` | `"max"` for analyzer, optimizer, scorer). Non-configurable: explore synthesis and suggestions always use Haiku. Lean mode = explore+scoring off = 2 LLM calls only.
- **Passthrough protocol**: MCP `synthesis_prepare_optimization` assembles the full prompt (with workspace path safety validation); external LLM processes it; `synthesis_save_result` persists with hybrid scoring (no bias correction — z-score + heuristic blending suffice) and domain whitelist validation.
- **Pagination envelope**: all list endpoints return `{total, count, offset, items, has_more, next_offset}`.
- **GitHub token layer**: tokens are Fernet-encrypted at rest. `github_service.encrypt_token` / `decrypt_token` are the only entry points.
- **API key management**: `GET/PATCH/DELETE /api/provider/api-key`. Key encrypted at rest in `data/.api_credentials`. Provider hot-reloads when key is set.
- **Explore architecture**: semantic retrieval + single-shot synthesis (not an agentic loop). SHA-based result caching. Background indexing with `all-MiniLM-L6-v2` embeddings.
- **Context enrichment**: unified `ContextEnrichmentService` replaces 5 scattered context resolution sites. Single `enrich()` entry point resolves workspace guidance, heuristic analysis (passthrough), curated codebase context, adaptation state, and applied patterns. Returns frozen `EnrichedContext` with `MappingProxyType` immutability and accessor properties. All routing tiers (internal, sampling, passthrough) use the same enrichment path.
- **Roots scanning**: workspace directories scanned for agent guidance files (CLAUDE.md, AGENTS.md, GEMINI.md, .cursorrules, .clinerules, CONVENTIONS.md, etc.). `discover_project_dirs()` detects manifest-containing subdirectories for monorepo scanning. SHA256 content deduplication (root copy wins). Per-file cap: 500 lines / 10K chars. Content wrapped in `<untrusted-context>`.
- **Feedback adaptation**: simple strategy affinity counter. Degenerate pattern detection (>90% same rating over 10+ feedbacks).
- **Refinement**: each turn is a fresh pipeline invocation (not multi-turn accumulation). Rollback creates a branch fork. 3 suggestions generated per turn.
- **Trace logging**: `trace_logger.py` writes per-phase JSONL traces. Daily rotation with configurable retention (`TRACE_RETENTION_DAYS`).
- **Real-time event bus**: `event_bus.py` publishes events to all SSE subscribers. Event types: `optimization_created`, `optimization_analyzed`, `optimization_failed`, `feedback_submitted`, `refinement_turn`, `strategy_changed`, `taxonomy_changed`, `routing_state_changed`. MCP server (separate process) notifies via HTTP POST to `/api/events/_publish`. Frontend auto-refreshes History on events, shows toast notifications, syncs Inspector feedback state, and updates StatusBar metrics.
- **Workspace intelligence**: `workspace_intelligence.py` auto-detects project type from manifest files + deep scanning (README.md, entry points, architecture docs). All enrichment call sites default `workspace_path` to `PROJECT_ROOT` so web UI always gets workspace context.
- **MCP sampling detection**: ASGI middleware on `initialize` calls `RoutingManager.on_mcp_initialize()` (in-memory state primary, `mcp_session.json` write-through for restart recovery). RoutingManager is a **process-level singleton** — FastMCP's Streamable HTTP enters the lifespan per session, so all singletons are guarded by `_process_initialized` and never torn down per-session. Dual-layer guard in `_inspect_initialize` prevents non-sampling clients from overwriting sampling state: primary check on RoutingManager state + secondary on class-level `_sampling_sse_sessions`. Two disconnect signals: `on_mcp_disconnect()` (all SSE closed → clears both fields) vs `on_sampling_disconnect()` (sampling SSE closed, non-sampling remain → clears only `sampling_capable`). Stale session cleared once at `__main__` (process startup), not per-session. Frontend receives `routing_state_changed` SSE events — no longer makes routing decisions.
- **MCP sampling pipeline**: full feature parity with internal pipeline — extracted into `services/sampling_pipeline.py`. Uses structured output via tool calling (`tools` + `tool_choice` on `create_message()`), per-phase model ID capture (`result.model`), `SamplingLLMAdapter` for explore. IDE selects model freely; no advisory `ModelPreferences`/`ModelHint` sent. All 11 MCP tools use `structured_output=True` (return Pydantic models, expose `outputSchema`). `synthesis_analyze` falls back to sampling when no local provider.
- **Taxonomy engine**: evolutionary hierarchical clustering in `services/taxonomy/`. Process-wide singleton (`get_engine()`/`set_engine()`) with async lock-gated hot path. Three execution paths: hot (embed + cosine search nearest active node per optimization), warm (periodic HDBSCAN + speculative lifecycle mutations — emerge/merge/split/retire — gated by Q_system non-regression), cold (full HDBSCAN refit + UMAP 3D projection + OKLab coloring + Haiku labeling). Quality system: 5-dimension Q_system (coherence, separation, coverage, DBCV, stability) with adaptive weights that scale by active node count. Snapshot audit trail records every mutation cycle. Module decomposition: `engine.py` (~500 LOC) + `family_ops.py` (family CRUD) + `matching.py` (cascade search) + `embedding_index.py` (numpy index) extracted from former monolithic `engine.py` (~2100 LOC). Frontend: Three.js `SemanticTopology` with LOD tiers (persistence thresholds: far=0.6, mid=0.3, near=0.0), state-based chromatic encoding (opacity, size multiplier, color override per lifecycle state), raycasting interaction, force-directed layout (`TopologyWorker`), and `TopologyControls` overlay.
- **Unified prompt lifecycle**: self-building cluster library. Post-completion background job embeds prompts, clusters into `PromptCluster` groups (cosine ≥0.78 merge), extracts meta-patterns via Haiku (cosine ≥0.82 pattern merge). On-paste detection (50-char delta, 300ms debounce) suggests matching clusters (cosine ≥0.72). Applied patterns injected into optimizer context via pre-phase `EmbeddingIndex` search. Models: `PromptCluster` (unified, lifecycle states), `MetaPattern` (enriched on duplicate, `cluster_id` FK), `OptimizationPattern` (join with relationship type). Cross-cluster edges at cosine ≥0.55. Domain color coding (neon vibrant, no tier overlap): backend=#c084fc, frontend=#ff8c00, database=#00bcd4, security=#ff3366, devops=#4d8eff, fullstack=#bf5af2, general=#7a7a9e. Endpoints unified under `/api/clusters/*` (legacy 301 redirects for `/api/patterns/*` and `/api/taxonomy/*`). UI: `ClusterNavigator` (state filter tabs, Proven Templates section, domain filter), Inspector cluster detail (linked optimizations, rename, state badge), SemanticTopology (state-based opacity/size/color encoding), StatusBar cluster count. Activity type `'clusters'` in layout routing.
- **Intelligent routing**: centralized 5-tier priority chain in `services/routing.py`: force_passthrough > force_sampling > internal provider > auto sampling > passthrough fallback. Pure `resolve_route()` function (deterministic, no I/O) + thin `RoutingManager` wrapper (state lifecycle, SSE events, persistence, disconnect detection). Both FastAPI and MCP server own their own `RoutingManager` instance (MCP's is a process-level singleton, initialized once via `_process_initialized` guard). `routing` SSE event emitted as first event in every optimize stream. `routing_state_changed` ambient SSE for tier availability changes. Frontend is purely reactive — never makes routing decisions. `caller` field gates sampling (REST callers never reach sampling tiers). Unified `POST /api/optimize` handles passthrough inline via SSE (no more 503). See `backend/CLAUDE.md` for detailed routing internals and `docs/routing-architecture.md` for comprehensive documentation with diagrams.
