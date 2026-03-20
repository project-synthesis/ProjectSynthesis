# Changelog

All notable changes to Project Synthesis. Format follows [Keep a Changelog](https://keepachangelog.com/).

## Unreleased

### Added (Intelligent Routing)
- Added centralized intelligent routing service (`routing.py`) with pure 5-tier priority chain: force_passthrough > force_sampling > internal provider > auto sampling > passthrough fallback
- Added `routing` SSE event as first event in every optimize stream (tier, provider, reason, degraded_from)
- Added `routing_state_changed` ambient SSE event for real-time tier availability changes
- Added `available_tiers` field to `/api/health` response
- Added `RoutingManager` with in-memory live state, disconnect detection, MCP session file write-through for restart recovery, and SSE event broadcasting

### Changed (Intelligent Routing)
- Changed `POST /api/optimize` to handle passthrough inline via SSE (no more 503 dead end when no provider)
- Changed frontend to be purely reactive for routing — backend owns all routing decisions via SSE events
- Changed health endpoint to read live routing state from `RoutingManager` instead of `mcp_session.json` file reads
- Changed provider set/delete endpoints to update `RoutingManager` state
- Changed refinement endpoint to use routing service (rejects passthrough tier with 503)
- Changed MCP server to use `RoutingManager` for all routing decisions (replaced 200-line if/elif chain)
- Changed `mcp_session_changed` SSE event to `routing_state_changed`
- Changed frontend health polling from adaptive (10s/60s) to fixed 60s interval (display only)

### Removed (Intelligent Routing)
- Removed `auto_passthrough` preference toggle and frontend auto-passthrough logic (backend owns degradation)
- Removed `_write_mcp_session_caps()` function from MCP server (replaced by `RoutingManager.on_mcp_initialize()`)
- Removed `noProvider` state from forge store (replaced by routing SSE events)
- Removed `preparePassthrough()` method from forge store (passthrough now handled by backend)
- Removed `handleMcpDisconnect()` and `handleMcpReconnect()` from frontend (backend owns via SSE)

### Added
- Added prompt knowledge graph — auto-extracts reusable meta-patterns from optimizations into pattern families clustered by semantic similarity
- Added auto-suggestion banner on paste — detects similar pattern families with 1-click apply/skip (50-char delta threshold, 300ms debounce, 10s auto-dismiss)
- Added radial mindmap visualization — interactive D3.js graph of pattern portfolio with domain-coded arcs, family nodes, edges, zoom/pan
- Added pattern navigator compact view in ActivityBar sidebar — families grouped by domain with inline meta-pattern expansion
- Added pattern family detail in Inspector — meta-patterns, linked optimizations, domain badge, usage stats
- Added pattern knowledge graph API — `GET /api/patterns/graph`, `POST /api/patterns/match`, `GET /api/patterns/families`, `GET /api/patterns/search`, `GET /api/patterns/stats`, `PATCH /api/patterns/families/{id}`
- Added `intent_label` and `domain` fields to optimization analysis (extracted by analyzer, persisted to DB)
- Added `extract_patterns.md` Haiku prompt template for meta-pattern extraction
- Added `applied_patterns` parameter to optimization pipeline — injects user-selected meta-patterns into optimizer context
- Added background pattern extraction listener on event bus (`optimization_created` → async extraction)
- Added bidirectional forge–patterns store binding — loading an optimization auto-selects its pattern family in Inspector; clicking a linked optimization in a family loads it in the editor
- Added `intent_label`, `domain`, and `family_id` fields to history API and single-optimization API responses
- Added intent_label + domain badge display in History Navigator rows (falls back to truncated `raw_prompt` for pre-knowledge-graph optimizations)
- Added intent_label as editor tab title for result and diff tabs (falls back to existing word-based derivation from raw_prompt)
- Added StatusBar breadcrumb segment showing `[domain] › intent_label` for the active optimization with domain color coding
- Added live family link — `pattern_updated` SSE auto-refreshes current result to pick up async family assignment when background extractor finishes
- Added composite database index on `optimization_patterns(optimization_id, relationship)` for family lookup performance
- Added `intent_label` and `domain` to SSE `optimization_complete` event data for immediate breadcrumb display without page refresh
- Added `pipeline.force_passthrough` preference toggle — forces passthrough mode (assembled template for external LLM) in both MCP and frontend, mutually exclusive with `force_sampling`
- Added ASGI middleware on MCP server that detects sampling capability at `initialize` handshake — writes `mcp_session.json` before any tool call, enabling instant detection when an MCP client connects
- Added runtime MCP sampling capability detection via `data/mcp_session.json` — all 4 MCP tools refresh client capabilities on every call; health endpoint reads with 30-minute staleness window; ASGI middleware detects capabilities at `initialize` handshake (before any tool call); optimistic strategy prevents multi-session flicker (False never overwrites fresh True)
- Added `sampling_capable` field to `/api/health` response
- Added PASSTHROUGH badge in Navigator Defaults section (amber warning color) when force_passthrough is active
- Added `pipeline.force_sampling` preference toggle — forces `synthesis_optimize` through the MCP sampling pipeline (IDE's LLM) even when a local provider is detected; gracefully falls through to the local provider if sampling fails
- Added 3-phase pipeline orchestrator (analyze → optimize → score) with independent subagent context windows
- Added hybrid scoring engine — blends LLM scores with model-independent heuristics via score_blender.py
- Added Z-score normalization against historical distribution to prevent score clustering
- Added scorer A/B randomization to prevent position and verbosity bias
- Added provider error hierarchy with typed exceptions (RateLimitError, AuthError, BadRequestError, OverloadedError)
- Added shared retry utility (call_provider_with_retry) with smart retryable/non-retryable classification
- Added token usage tracking with prompt cache hit/miss stats
- Added 3-tier provider layer (Claude CLI, Anthropic API, MCP passthrough) with auto-detection
- Added Claude CLI provider — native --json-schema structured output, --effort flag, subprocess timeout with zombie reaping
- Added Anthropic API provider — typed SDK exception mapping, prompt cache logging
- Added prompt template system with {{variable}} substitution, manifest validation, and hot-reload
- Added 6 optimization strategies with YAML frontmatter (tagline, description) for adaptive discovery
- Added context resolver with per-source character caps and `<untrusted-context>` injection hardening
- Added workspace roots scanning for agent guidance files (CLAUDE.md, AGENTS.md, .cursorrules, etc.)
- Added SHA-based explore caching with TTL and LRU eviction
- Added startup template validation against manifest.json
- Added MCP server with 4 tools (synthesis_optimize, synthesis_analyze, synthesis_prepare_optimization, synthesis_save_result)
- Added GitHub OAuth integration with Fernet-encrypted token storage
- Added codebase explorer with semantic retrieval + single-shot Haiku synthesis
- Added sentence-transformers embedding service (all-MiniLM-L6-v2, 384-dim) with async wrappers
- Added heuristic scorer with 5-dimension analysis (clarity, specificity, structure, faithfulness, conciseness)
- Added passthrough bias correction (default 15% discount) for MCP self-rated scores
- Added optimization CRUD with sort/filter, pagination envelope, and score distribution tracking
- Added feedback CRUD with synchronous adaptation tracker update
- Added strategy affinity tracking with degenerate pattern detection
- Added conversational refinement with version history, branching/rollback, and 3 suggestions per turn
- Added API key management (GET/PATCH/DELETE) with Fernet encryption at rest
- Added health endpoint with score clustering detection, recent error counts, and per-phase duration metrics
- Added trace logger writing per-phase JSONL to data/traces/ with daily rotation
- Added in-memory rate limiting (optimize 10/min, refine 10/min, feedback 30/min, default 60/min)
- Added real-time event bus — SSE stream with optimization, feedback, refinement, and strategy events
- Added persistent user preferences (model selection, pipeline toggles, default strategy)
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
- Added CSS scroll-driven animations (animation-timeline: view()) with progressive enhancement fallback
- Added View Transitions API for cross-page navigation morphing
- Added GitHub Pages deployment via Actions artifacts (zero-footprint, no gh-pages branch)
- Added Docker single-container deployment (backend + frontend + MCP + nginx)
- Added init.sh service manager with PID tracking, process group kill, preflight checks, and log rotation
- Added version sync system (version.json → scripts/sync-version.sh propagates everywhere)

### Added (Sampling Pipeline Parity)
- Added structured output via tool calling in MCP sampling pipeline — sends Pydantic-derived `Tool` schemas via `tools` + `tool_choice` on `create_message()`, falls back to text parsing when client doesn't support tools
- Added model preferences per sampling phase (analyze=Sonnet, optimize=Opus, score=Sonnet, suggest=Haiku) via `ModelPreferences` + `ModelHint`
- Added sampling fallback to `synthesis_analyze` — no longer requires a local LLM provider
- Added explore, suggest, applied patterns, adaptation state, intent drift detection, and z-score normalization to the sampling pipeline (full feature parity with internal CLI/API pipeline)
- Added `structured_output=True` to all 4 MCP tool definitions — tools return Pydantic models and expose `outputSchema` to MCP clients
- Added `applied_pattern_ids` parameter to `synthesis_optimize` MCP tool — injects selected meta-patterns into optimizer context (mirrors REST API)
- Added `SamplingLLMAdapter` — minimal `LLMProvider` wrapper for `CodebaseExplorer` to use MCP sampling as its LLM backend
- Extracted `sampling_pipeline.py` service module from `mcp_server.py` (was 1305 lines, over 800-line guideline)

### Changed
- Changed `model_used` in sampling pipeline from hardcoded `"ide_llm"` to actual model ID captured from `result.model` on each sampling response
- Changed all 4 MCP tool return types from `dict` to typed Pydantic models (`OptimizeOutput`, `AnalyzeOutput`, `PrepareOutput`, `SaveResultOutput`)
- Changed `synthesis_optimize` internal pipeline path to pass `applied_pattern_ids` through to `orchestrator.run()` (was previously omitted)
- Changed `synthesis_optimize` MCP tool to 5 execution paths: force_passthrough → force_sampling → provider → sampling fallback → passthrough fallback
- Enforced `force_sampling` and `force_passthrough` as mutually exclusive — server-side (422) and client-side (radio toggle behavior)
- Disabled Force IDE sampling toggle when sampling is unavailable or passthrough is active
- Disabled Force passthrough toggle when sampling is available or force_sampling is active
- Changed frontend health polling to fast 10s interval for first 2 minutes, then 60s steady-state — detects MCP client connections within seconds of handshake
- Changed all 4 MCP tools (`synthesis_optimize`, `synthesis_analyze`, `synthesis_prepare_optimization`, `synthesis_save_result`) to write session capabilities on every invocation
- Enriched history API and single-optimization API with `intent_label`, `domain`, and `family_id` fields (batch family lookup via IN query, not N+1)
- Added `intent_label` and `domain` to `_VALID_SORT_COLUMNS` in optimization service

### Fixed
- Fixed sampling pipeline missing confidence gate and semantic check — low-confidence strategy selections were applied without the safety override to "auto" (parity with internal pipeline)
- Fixed sampling pipeline model hint presets using short names (`claude-sonnet`) instead of full model IDs from `settings` — could cause model resolution mismatches
- Fixed sampling pipeline `run_sampling_pipeline()` not returning `trace_id` in result dict — downstream `OptimizeOutput` had null `trace_id` for sampling path
- Fixed sampling pipeline `run_sampling_analyze()` computing `heur_scores` twice (once in try, once in except) — now computed once before the try/except
- Fixed internal pipeline path in `synthesis_optimize` not including `trace_id` in `OptimizeOutput` for completed results
- Fixed `pattern_updated` SSE event type missing from `connectEventStream` event types array — handler in +page.svelte was dead code
- Fixed Inspector linked optimizations using `id` instead of `trace_id` for API fetch — would always 404
- Fixed `PipelineResult` schema missing `intent_label` and `domain` — SSE `optimization_complete` events now include analyzer output
- Fixed Inspector linked optimization display to use `intent_label` with fallback to truncated `raw_prompt`
- Fixed Docker healthcheck to validate /api/health (was hitting nginx root, always 200)
- Fixed Docker to add security headers (X-Content-Type-Options, X-Frame-Options, Referrer-Policy)
- Fixed Docker text/event-stream added to nginx gzip types
- Fixed Docker .dockerignore to correctly include prompt templates via !prompts/**/*.md
- Fixed Docker Alembic migration errors to fail hard instead of being silently ignored
- Fixed Docker entrypoint cleanup to propagate actual exit code
- Fixed CLI provider to remove invalid --max-tokens flag, uses native --json-schema instead
- Fixed pipeline scorer to use XML delimiters (<prompt-a>/<prompt-b>) preventing boundary corruption
- Fixed pipeline Phase 4 event keys to use consistent stage/state format
- Fixed pipeline refinement score events to only emit when scoring is enabled
- Fixed pipeline dynamic max_tokens to cap at 65536 to prevent timeout
- Fixed landing page route structure — landing at /, app at /app (fixes GitHub Pages routing)
