# Changelog

All notable changes to Project Synthesis. Format follows [Keep a Changelog](https://keepachangelog.com/).

## Unreleased

### Added
- Added environment-gated MCP server authentication via bearer token (ADR-001)
- Added PBKDF2-SHA256 key derivation with context-specific salts (ADR-002)
- Added structured audit logging for sensitive operations (AuditLog model + service)
- Added Architecture Decision Record (ADR) directory at `docs/adr/`
- Added `DEVELOPMENT_MODE` config field for environment-gated security controls
- Added rate limiting on `/api/health`, `/api/settings`, `/api/clusters/{id}`, `/api/strategies`
- Added input validation: preferences schema, feedback comment limit, strategy file size cap, repo name format, sort column validator
- Added shared `backend/app/utils/crypto.py` with `derive_fernet()` and `decrypt_with_migration()`

### Changed
- Hardened cookie security: SameSite=Lax, environment-gated Secure flag, /api path scope, 14-day session lifetime
- Restricted CORS to explicit method/header allowlists
- Sanitized error messages across all routers (no exception detail leakage)
- Validated X-Forwarded-For IPs via `ipaddress` module
- Hardened SSE `format_sse()` to handle serialization failures gracefully
- Migrated Fernet encryption from SHA256 to PBKDF2 with transparent legacy fallback
- Extended API key validation to length check (>=40 chars)
- Pinned all Python and frontend dependencies to exact versions (ADR-003)

### Fixed
- Added `wss://` to CSP for secure WebSocket connections
- Enabled HSTS header in nginx (conditional on TLS)
- Tightened data directory permissions to 0700
- Scoped `init.sh` process discovery to current user
- Genericized nginx 50x error page (no branding/version leakage)
- Fixed logout cookie deletion to match path-scoped session cookie

## v0.3.2 — 2026-03-25

### Added
- Added `TierBadge` component with CLI/API sub-tier labels for internal tier (shows "CLI" or "API" instead of generic "INTERNAL")
- Added `models_by_phase` JSON column to Optimization model — persists per-phase model IDs for both internal and sampling pipelines
- Added per-phase model ID capture in SSE events (`model` field on phase-complete status events)
- Added `tierColor` and `tierColorRgb` getters to routing store — single source of truth for tier accent colors
- Added `--tier-accent` and `--tier-accent-rgb` CSS custom properties at layout level, inherited by all components
- Added tier-adaptive Provider/Connection/Routing section in Navigator (passthrough=Routing, sampling=Connection, internal=Provider)
- Added tier-adaptive System section in Navigator (reduced rows for passthrough/sampling, full for internal)
- Added IDE Model display section in Navigator for sampling tier — shows actual model IDs per phase in real time
- Added `.data-value.neon-green` CSS utility class
- Added shared `semantic_check()`, `apply_domain_gate()`, `resolve_effective_strategy()` helpers in `pipeline_constants.py`

### Changed
- Removed advisory MCP `ModelPreferences`/`ModelHint` from sampling pipeline — IDE selects model freely; actual model captured per phase and displayed in UI
- Total tier-aware accent branding across entire UI: SYNTHESIZE button, active tab underline, strategy list highlight, activity bar indicator, brand logo SVG, pattern suggestions, feedback buttons, refinement components, command palette, topology controls, score sparkline, markdown headings, global focus rings, selection highlight, and all action buttons adapt to tier color (cyan=CLI/API, green=sampling, yellow=passthrough)
- Navigator section headings use unified `sub-heading--tier` class (replaces per-tier `sub-heading--sampling`/`sub-heading--passthrough` classes)
- StatusBar shows CLI/API sub-tier badges instead of generic "INTERNAL" + separate "cli" text; version removed (displayed in System accordion)
- API key input redesigned as inline data-row with `pref-input`/`pref-btn` classes matching dropdown density
- SamplingGuide modal updated to remove hint/advisory language
- PassthroughView interactive elements (COPY button, focus rings) now correctly use yellow instead of cyan
- MCP disconnect detection reads `mcp_session.json` before disconnecting to detect cross-process activity the backend missed
- CLAUDE.md sampling detection section updated — replaced stale "optimistic strategy" with accurate "capability trust model" description
- RoutingManager: improved logging (session invalidation, stale capability recovery, disconnect checker fallback), type hints (`sync_from_event` signature), and docstrings (`_persist`, `RoutingState`)
- DRY: `prefs.resolve_model()` calls captured once and reused in `pipeline.py` and `tools/analyze.py`
- Replaced duplicated strategy resolution logic in `pipeline.py` and `sampling_pipeline.py` with shared helpers from `pipeline_constants.py`

### Fixed
- False MCP disconnect after 5 minutes in cross-process setup — backend disconnect checker now reads session file for fresh activity before clearing sampling state
- Missing `models_by_phase` in passthrough completion paths (REST save and MCP save_result)
- Missing `models_by_phase` in analyze tool's internal provider path
- PassthroughView COPY button and focus rings were incorrectly cyan (now yellow)
- Stale Navigator tests for removed UI elements (Model Hints, Effort Hints, "// via IDE", passthrough-mode class, "SET KEY"/"REMOVE" labels, version display)
- Activity throttle preventing routing state change broadcasts during MCP SSE reconnection
- Degradation messages hardcoding fallback tier

### Removed
- Removed `ModelPreferences`, `ModelHint`, `_resolve_model_preferences()`, `_PHASE_PRESETS`, `_PREF_TO_MODEL`, `_EFFORT_PRIORITIES` from sampling pipeline (~95 lines)
- Removed `.passthrough-mode` class from SYNTHESIZE button (tier accent handles all tiers)
- Removed per-component `style:--tier-accent` bindings (6 components) — replaced by single layout-level propagation
- Removed redundant version display from StatusBar (available in System accordion)
- Removed deprecated `preparePassthrough()` API function and `PassthroughPrepareResult` type from frontend client

## v0.3.1 — 2026-03-24

### Added
- Added unified `ContextEnrichmentService` replacing 5 scattered context resolution call sites with a single `enrich()` entry point
- Added `HeuristicAnalyzer` for zero-LLM passthrough classification (task_type, domain, weaknesses, strengths, strategy recommendation)
- Augmented `RepoIndexService` with type-aware structured file outlines and `query_curated_context()` for token-conscious codebase retrieval
- Added analysis summary, codebase context from pre-built index, applied meta-patterns, and task-specific adaptation state to passthrough tier
- Added config settings: `INDEX_OUTLINE_MAX_CHARS`, `INDEX_CURATED_MAX_CHARS`, `INDEX_CURATED_MIN_SIMILARITY`, `INDEX_CURATED_MAX_PER_DIR`, `INDEX_DOMAIN_BOOST`
- Enhanced `RootsScanner` with subdirectory discovery: `discover_project_dirs()` detects immediate subdirectories containing manifest files (`package.json`, `pyproject.toml`, `requirements.txt`, `Cargo.toml`, `go.mod`) and skips ignored dirs (`node_modules`, `.venv`, `__pycache__`, etc.)
- Expanded `GUIDANCE_FILES` list to include `GEMINI.md`, `.clinerules`, and `CONVENTIONS.md`
- Updated `RootsScanner.scan()` to scan root + manifest-detected subdirectories and deduplicate identical content by SHA256 hash (root copy wins)
- Added frontend tier resolver (`routing.svelte.ts`) — unified derived state mirroring the backend's 5-tier priority chain (force_passthrough > force_sampling > internal > auto_sampling > passthrough)
- Added tier-adaptive Navigator settings panel — Models, Effort, and pipeline feature toggles (Explore/Scoring/Adaptation) are hidden in passthrough mode since they are irrelevant without an LLM
- Added passthrough workflow guide modal — interactive stepper explaining the 6-step manual passthrough protocol, feature comparison matrix across all three execution tiers, and "don't show on toggle" preference. Triggered on passthrough toggle enable and via help button in PassthroughView header.
- Exposed `refine_rate_limit` and `database_engine` in `GET /api/settings` endpoint
- Added Version row to System section (sourced from health polling via `forgeStore.version`)
- Added Database, Refine rate rows to System section
- Added Score health (mean, stddev with clustering warning) and Phase durations to System section from health polling
- Added per-phase effort preferences: `pipeline.analyzer_effort`, `pipeline.scorer_effort` (default: `low`)
- Expanded `pipeline.optimizer_effort` to accept `low` and `medium` (was `high`/`max` only)
- Threaded `cache_ttl` parameter through full provider chain (base → API → CLI → pipeline → refinement)
- Added EFFORT section in settings panel with per-phase effort controls (low/medium/high/max)
- Included effort level in trace logger output for each phase
- Added streaming support for optimize/refine phases via `messages.stream()` + `get_final_message()` — prevents HTTP timeouts on long Opus outputs up to 128K tokens
- Added `complete_parsed_streaming()` to LLM provider interface with fallback default in base class
- Added `streaming` parameter to `call_provider_with_retry()` dispatcher
- Added `optimizer_effort` user preference (`"high"` | `"max"`) with validation and sanitization in `PreferencesService`
- Added 7 new MCP tools completing the autonomous LLM workflow: `synthesis_health`, `synthesis_strategies`, `synthesis_history`, `synthesis_get_optimization`, `synthesis_match`, `synthesis_feedback`, `synthesis_refine`
- Extracted MCP tool handlers into `backend/app/tools/` package (11 modules) — `mcp_server.py` is now a thin ~420-line registration layer
- Added `tools/_shared.py` for module-level state management (routing, taxonomy engine) with setter/getter pattern
- Added per-phase JSONL trace logging to the MCP sampling pipeline (`provider: "mcp_sampling"`, token counts omitted as MCP sampling does not expose them)
- Added optional `domain` and `intent_label` parameters to `synthesis_save_result` MCP tool (backward-compatible, defaults to `"general"`)
- Extracted shared `auto_inject_patterns()` into `services/pattern_injection.py` and `compute_optimize_max_tokens()` into `pipeline_constants.py` — eliminates duplication between internal and sampling pipelines
- Added optional `domain` and `intent_label` fields to REST `PassthroughSaveRequest` for parity with MCP `synthesis_save_result`
- Added adaptation state injection to all passthrough prepare paths (REST inline, REST dedicated, MCP `synthesis_prepare_optimization`)

### Changed
- Shared `EmbeddingService` singleton across taxonomy engine and `ContextEnrichmentService` in both FastAPI and MCP lifespans (was creating duplicate instances)
- Changed `EnrichedContext.context_sources` to use `MappingProxyType` for runtime immutability (callers convert to `dict()` at DB boundary)
- Changed `HeuristicAnalyzer._score_category()` to use word-boundary regex matching instead of substring search (prevents false positives like "class" matching "classification")
- Removed unused `prompt_lower` parameter from `_classify_domain()` helper
- Updated `ContextEnrichmentService.enrich()` to respect `preferences_snapshot["enable_adaptation"]` to skip adaptation state resolution when disabled
- Improved error logging when `ContextEnrichmentService` init fails — now explicitly warns that passthrough and pattern resolution will be unavailable
- Persisted `task_type`, `domain`, `intent_label`, and `context_sources` from heuristic analysis for passthrough optimizations (previously hardcoded "general")
- Added `EnrichedContext` accessor properties (`task_type`, `domain_value`, `intent_label`, `analysis_summary`, `context_sources_dict`) eliminating 20+ repeated null-guard expressions across call sites
- Added content capping to `ContextEnrichmentService`: codebase context capped at `MAX_CODEBASE_CONTEXT_CHARS` and wrapped in `<untrusted-context>`, adaptation state capped at `MAX_ADAPTATION_CHARS`
- Corrected `HeuristicAnalyzer` keyword signals to match spec: added 8 missing keywords (`database`, `create`, `data`, `pipeline`, `query`, `setup`, `auth`), corrected 5 weights (`write` 0.5→0.6, `design` 0.5→0.7, `API` 0.7→0.8, `index` 0.5→0.6, `deploy` 0.7→0.8)
- Pre-compiled word-boundary regex patterns at module load time (was recompiling ~100+ patterns per analysis call)
- Updated `_detect_weaknesses` and `_detect_strengths` to receive pre-computed `has_constraints`/`has_outcome`/`has_audience` flags instead of re-scanning keyword sets
- Used `is_question` structural signal to influence analysis classification (boosts analysis type when question form detected)
- Updated intent labels for non-general domains to include trailing "task" suffix per spec (e.g. "implement backend coding task")
- Changed intent label verb fallback to produce `"{task_type} optimization"` per spec (was `"optimize {task_type} task"`)
- Added "target audience unclear" weakness check for writing/creative prompts (spec compliance)
- Raised underspecification threshold from 15 to 50 words per spec
- Added optional `repo_full_name` field to REST `OptimizeRequest` — enables curated codebase context for web UI passthrough optimizations
- Removed unused `_prompts_dir` and `_data_dir` instance attributes from `ContextEnrichmentService`
- Updated `WorkspaceIntelligence._detect_stack()` to use `discover_project_dirs()` for monorepo subdirectory scanning
- Expanded `passthrough.md` template with `{{analysis_summary}}`, `{{codebase_context}}`, and `{{applied_patterns}}` sections
- Migrated all optimize/prepare/refine call sites to use unified `ContextEnrichmentService.enrich()` instead of inline context resolution
- Suppressed refinement timeline for passthrough results — refinement requires a local provider and would 503
- Hid stale phase durations from Navigator System section in passthrough mode
- Changed hardcoded "hybrid" scoring label to dynamic — shows "heuristic" in passthrough mode
- Hid internal provider jargon (`web_passthrough`) in Inspector for passthrough results
- Added "(passthrough)" suffix to heuristic scoring label in Inspector for passthrough results
- Increased passthrough scoring rubric cap from 2000 to 4000 chars (all 5 dimension definitions now included)
- Replaced vague JSON output instruction in passthrough template with structured schema example
- Added rate limiting to `POST /api/optimize/passthrough/save` (was unprotected)
- Added `max_context_tokens` validation in prepare handler (rejects non-positive values)
- Added `workspace_path` directory validation (skips non-existent paths instead of scanning arbitrary locations)
- Added `codebase_context`, `domain`, `intent_label` fields to `SaveResultInput` schema (matches tool wrapper)
- Removed unused `detect_divergence()` from `HeuristicScorer` (dead code; `blend_scores` has its own inline check)
- Standardized heuristic scorer rounding to 2 decimal places across all dimensions
- Added `DimensionScores.from_dict()` / `.to_dict()` helpers — eliminated 11 repeated dict↔model conversion patterns across passthrough code paths
- Used `DimensionScores.compute_deltas()` and `.overall` instead of manual computation in passthrough save handlers
- Extracted strategy normalization into `StrategyLoader.normalize_strategy()` — removed duplicated fuzzy matching logic from `save_result.py` and `optimize.py`
- Changed pipeline analyze and score phases to use `effort="low"` (was `"medium"`), reducing latency 30-40%
- Reduced analyze and score max_tokens from 16384 to 4096 (matching actual output size)
- Extended scoring system prompt cache TTL from 5min to 1h (fewer cache writes)
- Expanded system prompt (`agent-guidance.md`) to 5000+ tokens for cache activation across all providers
- Raised optimize/refine `max_tokens` cap from 65,536 to 131,072 (safe with streaming)
- Refactored `anthropic_api.py` — extracted `_build_kwargs()`, `_track_usage()`, `_raise_provider_error()` helpers, eliminating ~70 lines of duplicated error handling
- Rewrote all 11 MCP tool descriptions for LLM-first consumption with chaining hints (When → Returns → Chain)
- Removed prompt echo from `AnalyzeOutput.optimization_ready` to eliminate token waste on large prompts
- Extracted shared `build_scores_dict()` helper into `tools/_shared.py` (eliminates duplication in get_optimization + refine handlers)
- Moved inline imports to module level in health, history, and optimize handlers for consistency
- Imported `VALID_SORT_COLUMNS` from `OptimizationService` in history handler (single source of truth, no divergence risk)
- Renamed `_VALID_SORT_COLUMNS` to `VALID_SORT_COLUMNS` in optimization_service.py (public API for cross-module use)
- Replaced `hasattr` checks with direct attribute access on ORM columns in get_optimization and match handlers

### Removed
- Removed `resolve_workspace_guidance()` from `tools/_shared.py` (replaced by `ContextEnrichmentService`)

### Fixed
- Wrapped `_resolve_workspace_guidance` call to `WorkspaceIntelligence.analyze()` in try/except — unguarded call could crash the entire enrichment request on unexpected errors
- Fixed `test_prune_weekly_best_retention` — used hour offsets instead of day offsets so 3 test snapshots always land in the same ISO week regardless of test execution date
- Removed double-correction (bias + z-score) from passthrough hybrid scoring that systematically deflated passthrough scores vs internal pipeline
- Fixed asymmetric delta computation in MCP `save_result` — original scores now use the same blending pipeline as optimized scores
- Fixed heuristic-only passthrough path running through `blend_scores()` z-score normalization (designed for LLM scores only)
- Guarded `_recover_state()` in routing against corrupt `mcp_session.json` (non-dict JSON crashed MCP server startup)
- Fixed `available_tiers` truthiness check inconsistency with `resolve_route()` identity check
- Fixed SSE error/end handlers not recognizing passthrough mode — UI no longer gets stuck in "analyzing" on connection drop
- Added passthrough session persistence to localStorage — page refresh no longer loses assembled prompt and trace state
- Wired `check_degenerate()` into `FeedbackService.create_feedback()` — degenerate feedback (>90% same rating over 10+ feedbacks) now skips affinity updates to freeze saturated counters
- Added analyzer strategy validation against disk in both `pipeline.py` and `sampling_pipeline.py` — hallucinated strategy names now fall back to validated fallback instead of silently polluting the DB
- Added orphaned strategy affinity cleanup at startup — removes `StrategyAffinity` rows for strategies no longer on disk
- Made confidence gate fallback resilient — `resolve_fallback_strategy()` validates "auto" exists on disk, falls back to first available strategy if not. No more hardcoded `"auto"` assumption
- Added programmatic adaptation enforcement — strategies with approval_rate < 0.3 and ≥5 feedbacks are filtered from the analyzer's available list and overridden post-selection. Adaptation is no longer advisory-only
- Wired file watcher to sanitize preferences on strategy deletion — when a strategy file is deleted, the persisted default preference is immediately reset if it references the deleted strategy
- Changed event bus overflow strategy — full subscriber queues now drop oldest event instead of killing the subscriber connection, preventing silent SSE disconnections
- Added sequence numbers and replay buffer (200 events) to event bus — enables `Last-Event-ID` reconnection replay in SSE endpoint
- Added SSE reconnection reconciliation — frontend refetches health, strategies, and cluster tree after EventSource reconnects to cover any missed events
- Added `preferences_changed` event — `PATCH /api/preferences` now publishes to event bus; frontend preferences store updates reactively via SSE
- Added visibility-change fallback for strategy dropdown — re-fetches strategy list when browser tab becomes visible, defense-in-depth against missed SSE events
- Added cluster detail refresh on taxonomy change — `invalidateClusters()` now also refreshes the Inspector detail view when a cluster is selected
- Added toast notification on failed session restore — users now see "Previous session could not be restored" instead of silent empty state
- Changed taxonomy engine to use lazy provider resolution — `_provider` is now a property that resolves via callable, ensuring hot-reloaded providers (API key change) are picked up automatically
- Added 5-minute TTL to workspace intelligence cache — workspace profiles now expire and re-scan manifest files instead of caching indefinitely until restart
- Added `invalidate_all()` method to explore cache for manual full flush
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
