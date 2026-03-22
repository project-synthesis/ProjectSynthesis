# Changelog

All notable changes to Project Synthesis. Format follows [Keep a Changelog](https://keepachangelog.com/).

## Unreleased

### Added
- Added streaming support for optimize/refine phases via `messages.stream()` + `get_final_message()` — prevents HTTP timeouts on long Opus outputs up to 128K tokens
- Added `complete_parsed_streaming()` to LLM provider interface with fallback default in base class
- Added `streaming` parameter to `call_provider_with_retry()` dispatcher
- Added `optimizer_effort` user preference (`"high"` | `"max"`) with validation and sanitization in `PreferencesService`
- Added 7 new MCP tools completing the autonomous LLM workflow: `synthesis_health`, `synthesis_strategies`, `synthesis_history`, `synthesis_get_optimization`, `synthesis_match`, `synthesis_feedback`, `synthesis_refine`
- Extracted MCP tool handlers into `backend/app/tools/` package (11 modules) — `mcp_server.py` is now a thin ~420-line registration layer
- Added `tools/_shared.py` for module-level state management (routing, taxonomy engine) with setter/getter pattern

### Changed
- Raised optimize/refine `max_tokens` cap from 65,536 to 131,072 (safe with streaming)
- Refactored `anthropic_api.py` — extracted `_build_kwargs()`, `_track_usage()`, `_raise_provider_error()` helpers, eliminating ~70 lines of duplicated error handling
- Rewrote all 11 MCP tool descriptions for LLM-first consumption with chaining hints (When → Returns → Chain)
- Removed prompt echo from `AnalyzeOutput.optimization_ready` to eliminate token waste on large prompts
- Extracted shared `build_scores_dict()` helper into `tools/_shared.py` (eliminates duplication in get_optimization + refine handlers)
- Moved inline imports to module level in health, history, and optimize handlers for consistency
- Imported `VALID_SORT_COLUMNS` from `OptimizationService` in history handler (single source of truth, no divergence risk)
- Renamed `_VALID_SORT_COLUMNS` to `VALID_SORT_COLUMNS` in optimization_service.py (public API for cross-module use)
- Replaced `hasattr` checks with direct attribute access on ORM columns in get_optimization and match handlers

### Fixed
- Fixed double retry on Anthropic API provider — SDK default `max_retries=2` compounded with app-level retry for up to 6 attempts; now set to `max_retries=0`
- Fixed 3 unprotected LLM call sites (`codebase_explorer`, `taxonomy/labeling`, `taxonomy/family_ops`) missing retry wrappers — transient 429/529 errors silently dropped results
- Fixed effort parameter passed to Haiku models in both API and CLI providers — Haiku doesn't support effort
- Fixed flaky `test_prune_daily_best_retention` — snapshots created near midnight UTC could cross calendar day boundaries
- Fixed MCP internal pipeline path missing `taxonomy_engine` — MCP-originated internal runs now include domain mapping and auto-pattern injection
- Fixed sampling pipeline missing auto-injection of cluster meta-patterns (only used explicit `applied_pattern_ids`, never auto-discovered)
- Fixed sampling pipeline using fixed 16384 `max_tokens` for optimize phase — now dynamically scales with prompt length (16K–65K), matching internal pipeline
- Fixed REST passthrough save using raw heuristic scores without z-score normalization — now applies `blend_scores()` for consistent scoring across all paths
- Fixed `synthesis_save_result` not persisting `domain`, `domain_raw`, or `intent_label` fields for passthrough optimizations
- Fixed `SaveResultOutput.strategy_compliance` description — documented values now match actual output ('matched'/'partial'/'unknown')
- Removed redundant re-raise pattern in feedback handler (`except ValueError: raise ValueError(str)` → let exception propagate)
- Removed unused `selectinload` import from refine handler
- Updated README.md MCP section from 4 to 11 tools with complete tool listing
- Fixed test patch targets for health and history tests after moving imports to module level

### Added
- Added per-phase JSONL trace logging to the MCP sampling pipeline (`provider: "mcp_sampling"`, token counts omitted as MCP sampling does not expose them)
- Added optional `domain` and `intent_label` parameters to `synthesis_save_result` MCP tool (backward-compatible, defaults to `"general"`)
- Extracted shared `auto_inject_patterns()` into `services/pattern_injection.py` and `compute_optimize_max_tokens()` into `pipeline_constants.py` — eliminates duplication between internal and sampling pipelines
- Added optional `domain` and `intent_label` fields to REST `PassthroughSaveRequest` for parity with MCP `synthesis_save_result`
- Added adaptation state injection to all passthrough prepare paths (REST inline, REST dedicated, MCP `synthesis_prepare_optimization`)

### Fixed
- Fixed REST passthrough save event bus notification missing `intent_label`, `domain`, `domain_raw` fields — taxonomy extraction listener now receives full metadata
- Fixed passthrough prompt assembly missing adaptation state in all three prepare paths (REST inline, REST dedicated endpoint, MCP tool)
- Fixed REST dedicated passthrough prepare ignoring `workspace_path` — now scans workspace for guidance files matching the inline passthrough path
- Fixed REST passthrough save missing `scores`, `task_type`, `strategy_used`, and `model` fields — now accepts all fields the `passthrough.md` template instructs the external LLM to return
- Fixed REST passthrough save always using heuristic-only scoring — now supports hybrid blending when external LLM scores are provided (mirrors MCP `save_result` logic)
- Fixed REST passthrough save not normalizing verbose strategy names from external LLMs (now uses same normalization as MCP `save_result`)

## v0.3.0 — 2026-03-22

### Added
- Added 3-phase pipeline orchestrator (analyze → optimize → score) with independent subagent context windows
- Added hybrid scoring engine — blended LLM scores with model-independent heuristics via `score_blender.py`
- Added Z-score normalization against historical distribution to prevent score clustering
- Added scorer A/B randomization to prevent position and verbosity bias
- Added provider error hierarchy with typed exceptions (RateLimitError, AuthError, BadRequestError, OverloadedError)
- Added shared retry utility (`call_provider_with_retry`) with smart retryable/non-retryable classification
- Added token usage tracking with prompt cache hit/miss stats
- Added 3-tier provider layer (Claude CLI, Anthropic API, MCP passthrough) with auto-detection
- Added Claude CLI provider — native `--json-schema` structured output, `--effort` flag, subprocess timeout with zombie reaping
- Added Anthropic API provider — typed SDK exception mapping, prompt cache logging
- Added prompt template system with `{{variable}}` substitution, manifest validation, and hot-reload
- Added 6 optimization strategies with YAML frontmatter (tagline, description) for adaptive discovery
- Added context resolver with per-source character caps and `<untrusted-context>` injection hardening
- Added workspace roots scanning for agent guidance files (CLAUDE.md, AGENTS.md, .cursorrules, etc.)
- Added SHA-based explore caching with TTL and LRU eviction
- Added startup template validation against `manifest.json`
- Added MCP server with 4 tools (`synthesis_optimize`, `synthesis_analyze`, `synthesis_prepare_optimization`, `synthesis_save_result`) — all return Pydantic models with `structured_output=True` and expose `outputSchema` to MCP clients
- Added GitHub OAuth integration with Fernet-encrypted token storage
- Added codebase explorer with semantic retrieval + single-shot Haiku synthesis
- Added sentence-transformers embedding service (`all-MiniLM-L6-v2`, 384-dim) with async wrappers
- Added heuristic scorer with 5-dimension analysis (clarity, specificity, structure, faithfulness, conciseness)
- Added passthrough bias correction (default 15% discount) for MCP self-rated scores
- Added optimization CRUD with sort/filter, pagination envelope, and score distribution tracking
- Added feedback CRUD with synchronous adaptation tracker update
- Added strategy affinity tracking with degenerate pattern detection
- Added conversational refinement with version history, branching/rollback, and 3 suggestions per turn
- Added API key management (GET/PATCH/DELETE) with Fernet encryption at rest
- Added health endpoint with score clustering detection, recent error counts, per-phase duration metrics, `sampling_capable`, `mcp_disconnected`, and `available_tiers` fields
- Added trace logger writing per-phase JSONL to `data/traces/` with daily rotation
- Added in-memory rate limiting (optimize 10/min, refine 10/min, feedback 30/min, default 60/min)
- Added real-time event bus — SSE stream with optimization, feedback, refinement, strategy, taxonomy, and routing events
- Added persistent user preferences (model selection, pipeline toggles, default strategy)
- Added `intent_label` (3-6 word phrase) and `domain` fields to optimization analysis — extracted by analyzer, persisted to DB, included in history and single-optimization API responses
- Added `extract_patterns.md` Haiku prompt template for meta-pattern extraction
- Added `applied_patterns` parameter to optimization pipeline — injects user-selected meta-patterns into optimizer context
- Added background pattern extraction listener on event bus (`optimization_created` → async extraction)
- Added `pipeline.force_passthrough` preference toggle — forces passthrough mode in both MCP and frontend, mutually exclusive with `force_sampling`
- Added `pipeline.force_sampling` preference toggle — forces sampling pipeline (IDE's LLM) even when a local provider is detected; gracefully falls through to local provider if sampling fails
- Added ASGI middleware on MCP server that detects sampling capability at `initialize` handshake — writes `mcp_session.json` before any tool call
- Added runtime MCP sampling capability detection via `data/mcp_session.json` — optimistic strategy prevents multi-session flicker (False never overwrites fresh True within 30-minute staleness window)
- Added evolutionary taxonomy engine (`services/taxonomy/`, 10 submodules: `engine.py`, `family_ops.py`, `matching.py`, `embedding_index.py`, `clustering.py`, `lifecycle.py`, `quality.py`, `projection.py`, `coloring.py`, `labeling.py`, `snapshot.py`, `sparkline.py`) — self-organizing hierarchical clustering with 3-path execution model: hot path (per-optimization embedding + nearest-node cosine search), warm path (periodic HDBSCAN clustering with speculative lifecycle mutations), cold path (full refit + UMAP 3D projection + OKLab coloring + Haiku labeling)
- Added quality metrics system (Q_system) with 5 dimensions: coherence, separation, coverage, DBCV, stability — adaptive threshold weights scale by active node count; DBCV linear ramp over 20 samples
- Added 4 lifecycle operations: emerge (new cluster detection), merge (cosine ≥0.78 similarity), split (coherence < 0.5), retire (idle nodes) — non-regressive gate ensures Q_system never degrades
- Added process-wide taxonomy engine singleton (`get_engine()`/`set_engine()`) with thread-safe double-checked locking
- Added `TaxonomySnapshot` model — audit trail for every warm/cold path with operation log + full tree state (JSON) and configurable retention
- Added UMAP 3D projection with Procrustes alignment for incremental updates and PCA fallback for < 5 points
- Added OKLab color generation from UMAP position — perceptually uniform on dark backgrounds with enforced minimum sibling distance
- Added LTTB downsampling for Q_system sparklines (preserves shape in ≤30 points) with OLS trend normalization
- Added Haiku-based 2–4 word cluster label generation from member text samples
- Added unified `PromptCluster` model — single entity with lifecycle states (candidate → active → mature → template → archived), self-join `parent_id`, L2-normalized centroid embedding, per-node metrics, intent/domain/task_type, usage counts, avg_score, preferred_strategy
- Added `MetaPattern` model — reusable technique extracted from cluster members with `cluster_id` FK, enriched on duplicate (cosine ≥0.82 pattern merge)
- Added `OptimizationPattern` join model linking `Optimization` → `PromptCluster` with similarity score and relationship type
- Added in-memory numpy `EmbeddingIndex` for O(1) cosine search across cluster centroids
- Added `PromptLifecycleService` — auto-curation (stale archival, quality pruning), state promotion (active → mature → template), temporal usage decay (0.9× after 30d inactivity), strategy affinity tracking, orphan backfill
- Added unified `/api/clusters/*` router — paginated list with state/domain filter, detail with children/breadcrumb/optimizations, paste-time similarity match, tree for 3D viz, stats with Q metrics + sparkline, proven templates, recluster trigger, rename/state override — with 301 legacy redirects for `/api/patterns/*` and `/api/taxonomy/*`
- Added `ClusterNavigator` with state filter tabs, domain filter, and Proven Templates section
- Added state-based chromatic encoding in `SemanticTopology` (opacity, size multiplier, color override per lifecycle state)
- Added template spawning — mature clusters promote to templates, "Use" button pre-fills editor
- Added auto-injection of cluster meta-patterns into optimizer pipeline (pre-phase context injection via `EmbeddingIndex` search)
- Added auto-suggestion banner on paste — detects similar clusters with 1-click apply/skip (50-char delta threshold, 300ms debounce, 10s auto-dismiss)
- Added Three.js 3D topology visualization (`SemanticTopology.svelte`) with LOD tiers (far/mid/near persistence thresholds), raycasting click-to-focus, billboard labels, and force-directed collision resolution
- Added `TopologyControls` overlay — Q_system badge, LOD tier indicator, Ctrl+F search, node counts
- Added canvas accessibility — `aria-label`, `tabindex`, `role="tooltip"` on hover, `role="alert" aria-live="polite"` on error
- Added `taxonomyColor()` and `qHealthColor()` to `colors.ts` — resolves hex, domain names, or null to fallback color
- Added cluster detail in Inspector — meta-patterns, linked optimizations, domain badge, usage stats, rename
- Added StatusBar breadcrumb segment showing `[domain] > intent_label` for the active optimization with domain color coding
- Added intent_label + domain badge display in History Navigator rows (falls back to truncated `raw_prompt` for pre-knowledge-graph optimizations)
- Added intent_label as editor tab title for result and diff tabs (falls back to existing word-based derivation from `raw_prompt`)
- Added live cluster link — `pattern_updated` SSE auto-refreshes current result to pick up async cluster assignment
- Added composite database index on `optimization_patterns(optimization_id, relationship)` for cluster lookup performance
- Added `intent_label` and `domain` to SSE `optimization_complete` event data for immediate breadcrumb display
- Added centralized intelligent routing service (`routing.py`) with pure 5-tier priority chain: force_passthrough > force_sampling > internal provider > auto sampling > passthrough fallback
- Added `routing` SSE event as first event in every optimize stream (tier, provider, reason, degraded_from)
- Added `routing_state_changed` ambient SSE event for real-time tier availability changes
- Added `RoutingManager` with in-memory live state, disconnect detection, MCP session file write-through for restart recovery, and SSE event broadcasting
- Added structured output via tool calling in MCP sampling pipeline — sends Pydantic-derived `Tool` schemas via `tools` + `tool_choice` on `create_message()`, falls back to text parsing when client doesn't support tools
- Added model preferences per sampling phase (analyze=Sonnet, optimize=Opus, score=Sonnet, suggest=Haiku) via `ModelPreferences` + `ModelHint`
- Added sampling fallback to `synthesis_analyze` — no longer requires a local LLM provider
- Added full feature parity in sampling pipeline: explore (via `SamplingLLMAdapter`), applied patterns, adaptation state, suggest phase, intent drift detection, z-score normalization
- Added `applied_pattern_ids` parameter to `synthesis_optimize` MCP tool — injects selected meta-patterns into optimizer context (mirrors REST API)
- Added PASSTHROUGH badge in Navigator Defaults section (amber warning color) when `force_passthrough` is active
- Added SvelteKit 2 frontend with VS Code workbench layout and industrial cyberpunk design system
- Added prompt editor with strategy picker, forge button, and SSE progress streaming
- Added result viewer with copy, diff toggle, and feedback (thumbs up/down)
- Added 5-dimension score card with deltas in Inspector panel
- Added side-by-side diff view with dimmed original
- Added command palette (Ctrl+K) with 6 actions
- Added refinement timeline with expandable turn cards, suggestion chips, and score sparkline
- Added branch switcher for refinement rollback navigation
- Added live history navigator with API data and auto-refresh
- Added GitHub navigator with repo browser and link management
- Added session persistence via localStorage — page refresh restores last optimization from DB
- Added toast notification system with chromatic action encoding
- Added landing page with hero, features grid, testimonials, CTA, and 15 content subpages
- Added CSS scroll-driven animations (`animation-timeline: view()`) with progressive enhancement fallback
- Added View Transitions API for cross-page navigation morphing
- Added GitHub Pages deployment via Actions artifacts (zero-footprint, no `gh-pages` branch)
- Added Docker single-container deployment (backend + frontend + MCP + nginx)
- Added init.sh service manager with PID tracking, process group kill, preflight checks, and log rotation
- Added version sync system (`version.json` → `scripts/sync-version.sh` propagates everywhere)
- Added `umap-learn` and `scipy` backend dependencies

### Changed
- Changed `domain` from `Literal` type to free-text `str` — analyzer writes unconstrained domain, taxonomy engine maps to canonical node
- Changed pattern matching to hierarchical cascade: nearest active node → walk parent chain → breadcrumb path
- Changed usage count propagation to walk up the taxonomy tree on each optimization
- Changed `model_used` in sampling pipeline from hardcoded `"ide_llm"` to actual model ID captured from `result.model` on each sampling response
- Changed `synthesis_optimize` MCP tool to 5 execution paths: force_passthrough → force_sampling → provider → sampling fallback → passthrough fallback
- Enforced `force_sampling` and `force_passthrough` as mutually exclusive — server-side (422) and client-side (radio toggle behavior)
- Disabled Force IDE sampling toggle when sampling is unavailable or passthrough is active
- Disabled Force passthrough toggle when sampling is available or `force_sampling` is active
- Changed `POST /api/optimize` to handle passthrough inline via SSE (no more 503 dead end when no provider)
- Changed frontend to be purely reactive for routing — backend owns all routing decisions via SSE events
- Changed health endpoint to read live routing state from `RoutingManager` instead of `mcp_session.json` file reads
- Changed provider set/delete endpoints to update `RoutingManager` state
- Changed refinement endpoint to use routing service (rejects passthrough tier with 503)
- Changed MCP server to use `RoutingManager` for all routing decisions
- Changed frontend health polling to fixed 60s interval (display only, no routing decisions)
- Enriched history API and single-optimization API with `intent_label`, `domain`, and `cluster_id` fields (batch lookup via IN query, not N+1)
- Added `intent_label` and `domain` to `_VALID_SORT_COLUMNS` in optimization service
- Extracted `sampling_pipeline.py` service module from `mcp_server.py` for maintainability

### Fixed
- Fixed process-wide taxonomy engine singleton with thread-safe double-checked locking (was creating multiple engine instances)
- Fixed task lifecycle — extraction tasks tracked in `set[Task]` with `add_done_callback` cleanup and 5s shutdown timeout
- Fixed usage propagation timing — split `_resolve_applied_patterns()` into read-only resolution + post-commit increment (avoids expired session)
- Fixed null label guard in `buildSceneData` — runtime `null` labels coerced to empty string
- Fixed `SemanticTopology` tooltip using non-existent CSS tokens (`--color-surface`, `--color-contour`)
- Fixed circular import between `forge.svelte.ts` and `clusters.svelte.ts` — `spawnTemplate()` returns data instead of writing to other stores
- Fixed dead `context_injected` SSE handler in `+page.svelte` — moved to `forge.svelte.ts` where optimization stream events are processed
- Fixed `pattern_updated` SSE event type missing from `connectEventStream` event types array — handler was dead code
- Fixed Inspector linked optimizations using `id` instead of `trace_id` for API fetch — would always 404
- Fixed `PipelineResult` schema missing `intent_label` and `domain` — SSE `optimization_complete` events now include analyzer output
- Fixed Inspector linked optimization display to use `intent_label` with fallback to truncated `raw_prompt`
- Fixed sampling pipeline missing confidence gate and semantic check — low-confidence strategy selections were applied without the safety override to "auto"
- Fixed sampling pipeline model hint presets using short names (`claude-sonnet`) instead of full model IDs from settings
- Fixed sampling pipeline `run_sampling_pipeline()` not returning `trace_id` in result dict
- Fixed sampling pipeline `run_sampling_analyze()` computing `heur_scores` twice
- Fixed internal pipeline path in `synthesis_optimize` not including `trace_id` in `OptimizeOutput`
- Fixed Docker healthcheck to validate `/api/health` (was hitting nginx root, always 200)
- Fixed Docker to add security headers (X-Content-Type-Options, X-Frame-Options, Referrer-Policy)
- Fixed Docker `text/event-stream` added to nginx gzip types
- Fixed Docker `.dockerignore` to correctly include prompt templates via `!prompts/**/*.md`
- Fixed Docker Alembic migration errors to fail hard instead of being silently ignored
- Fixed Docker entrypoint cleanup to propagate actual exit code
- Fixed CLI provider to remove invalid `--max-tokens` flag, uses native `--json-schema` instead
- Fixed pipeline scorer to use XML delimiters (`<prompt-a>`/`<prompt-b>`) preventing boundary corruption
- Fixed pipeline Phase 4 event keys to use consistent stage/state format
- Fixed pipeline refinement score events to only emit when scoring is enabled
- Fixed pipeline dynamic `max_tokens` to cap at 65536 to prevent timeout
- Fixed landing page route structure — landing at `/`, app at `/app` (fixes GitHub Pages routing)

### Removed
- Removed `auto_passthrough` preference toggle and frontend auto-passthrough logic (backend owns degradation)
- Removed `noProvider` state from forge store (replaced by routing SSE events)
- Removed frontend MCP disconnect/reconnect handlers (backend owns via SSE)
