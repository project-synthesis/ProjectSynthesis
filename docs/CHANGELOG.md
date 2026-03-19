# Changelog

All notable changes to Project Synthesis. Format follows [Keep a Changelog](https://keepachangelog.com/).

## Unreleased

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
- Added `pipeline.force_passthrough` preference toggle — forces passthrough mode (assembled template for external LLM) in both MCP and frontend, mutually exclusive with `force_sampling`
- Added ASGI middleware on MCP server that detects sampling capability at `initialize` handshake — writes `mcp_session.json` before any tool call, enabling instant detection when an MCP client connects
- Added runtime MCP sampling capability detection via `data/mcp_session.json` — all 4 MCP tools refresh client capabilities on every call; health endpoint reads with 30-minute staleness window; ASGI middleware detects capabilities at `initialize` handshake (before any tool call); optimistic strategy prevents multi-session flicker (False never overwrites fresh True)
- Added `sampling_capable` field to `/api/health` response
- Added PASSTHROUGH badge in Navigator Defaults section (amber warning color) when force_passthrough is active
- Added `pipeline.force_sampling` preference toggle — forces `synthesis_optimize` through the MCP sampling pipeline (IDE's LLM) even when a local provider is detected; gracefully falls through to the local provider if sampling fails
- 3-phase pipeline orchestrator (analyze → optimize → score) with independent subagent context windows
- Hybrid scoring engine — blends LLM scores with model-independent heuristics via score_blender.py
- Z-score normalization against historical distribution to prevent score clustering
- Scorer A/B randomization to prevent position and verbosity bias
- Provider error hierarchy with typed exceptions (RateLimitError, AuthError, BadRequestError, OverloadedError)
- Shared retry utility (call_provider_with_retry) with smart retryable/non-retryable classification
- Token usage tracking with prompt cache hit/miss stats
- 3-tier provider layer (Claude CLI, Anthropic API, MCP passthrough) with auto-detection
- Claude CLI provider: native --json-schema structured output, --effort flag, subprocess timeout with zombie reaping
- Anthropic API provider: typed SDK exception mapping, prompt cache logging
- Prompt template system with {{variable}} substitution, manifest validation, and hot-reload
- 6 optimization strategies with YAML frontmatter (tagline, description) for adaptive discovery
- Context resolver with per-source character caps and `<untrusted-context>` injection hardening
- Workspace roots scanning for agent guidance files (CLAUDE.md, AGENTS.md, .cursorrules, etc.)
- SHA-based explore caching with TTL and LRU eviction
- Startup template validation against manifest.json
- MCP server with 4 tools (synthesis_optimize, synthesis_analyze, synthesis_prepare_optimization, synthesis_save_result)
- GitHub OAuth integration with Fernet-encrypted token storage
- Codebase explorer with semantic retrieval + single-shot Haiku synthesis
- Sentence-transformers embedding service (all-MiniLM-L6-v2, 384-dim) with async wrappers
- Heuristic scorer with 5-dimension analysis (clarity, specificity, structure, faithfulness, conciseness)
- Passthrough bias correction (default 15% discount) for MCP self-rated scores
- Optimization CRUD with sort/filter, pagination envelope, and score distribution tracking
- Feedback CRUD with synchronous adaptation tracker update
- Strategy affinity tracking with degenerate pattern detection
- Conversational refinement with version history, branching/rollback, and 3 suggestions per turn
- API key management (GET/PATCH/DELETE) with Fernet encryption at rest
- Health endpoint with score clustering detection, recent error counts, and per-phase duration metrics
- Trace logger writing per-phase JSONL to data/traces/ with daily rotation
- In-memory rate limiting (optimize 10/min, refine 10/min, feedback 30/min, default 60/min)
- Real-time event bus — SSE stream with optimization, feedback, refinement, and strategy events
- Persistent user preferences (model selection, pipeline toggles, default strategy)
- SvelteKit 2 frontend with VS Code workbench layout and industrial cyberpunk design system
- Prompt editor with strategy picker, forge button, and SSE progress streaming
- Result viewer with copy, diff toggle, and feedback (thumbs up/down)
- 5-dimension score card with deltas in inspector panel
- Side-by-side diff view with dimmed original
- Command palette (Ctrl+K) with 6 actions
- Refinement timeline with expandable turn cards, suggestion chips, and score sparkline
- Branch switcher for refinement rollback navigation
- Live history navigator with API data and auto-refresh
- GitHub navigator with repo browser and link management
- Session persistence via localStorage — page refresh restores last optimization from DB
- Toast notification system with chromatic action encoding
- Landing page with hero, features grid, testimonials, CTA, and 15 content subpages
- CSS scroll-driven animations (animation-timeline: view()) with progressive enhancement fallback
- View Transitions API for cross-page navigation morphing
- GitHub Pages deployment via Actions artifacts (zero-footprint, no gh-pages branch)
- Docker single-container deployment (backend + frontend + MCP + nginx)
- init.sh service manager with PID tracking, process group kill, preflight checks, and log rotation
- Version sync system (version.json → scripts/sync-version.sh propagates everywhere)

### Changed
- `synthesis_optimize` MCP tool now has 5 execution paths: force_passthrough → force_sampling → provider → sampling fallback → passthrough fallback
- `force_sampling` and `force_passthrough` are mutually exclusive — enforced server-side (422) and client-side (radio toggle behavior)
- Force IDE sampling toggle disabled when sampling is unavailable or passthrough is active
- Force passthrough toggle disabled when sampling is available or force_sampling is active
- Frontend health polling uses fast 10s interval for first 2 minutes, then 60s steady-state — detects MCP client connections within seconds of handshake
- All 4 MCP tools (`synthesis_optimize`, `synthesis_analyze`, `synthesis_prepare_optimization`, `synthesis_save_result`) now write session capabilities on every invocation

### Fixed
- Docker: healthcheck validates /api/health (was hitting nginx root, always 200)
- Docker: added security headers (X-Content-Type-Options, X-Frame-Options, Referrer-Policy)
- Docker: text/event-stream added to nginx gzip types
- Docker: .dockerignore correctly includes prompt templates via !prompts/**/*.md
- Docker: Alembic migration errors fail hard instead of being silently ignored
- Docker: entrypoint cleanup propagates actual exit code
- CLI provider: removed invalid --max-tokens flag, uses native --json-schema instead
- Pipeline: scorer uses XML delimiters (<prompt-a>/<prompt-b>) preventing boundary corruption
- Pipeline: Phase 4 event keys use consistent stage/state format
- Pipeline: refinement score events only emitted when scoring is enabled
- Pipeline: dynamic max_tokens capped at 65536 to prevent timeout
- Landing: route restructure — landing at /, app at /app (fixes GitHub Pages routing)
