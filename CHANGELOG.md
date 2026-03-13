# Changelog

## Unreleased

- Added quality feedback loops with thumbs up/down, dimension overrides, and issue corrections
- Added adaptive RetryOracle replacing fixed 5.0 threshold with 7-gate decision algorithm
- Added user adaptation engine tuning validator weights, strategy selection, and retry thresholds per-user
- Added session resumption with unified refinement service and parallel branching
- Added feedback, refinement, and branch API endpoints (`/api/feedback`, `/api/refinement`)
- Added 3 MCP tools (`submit_feedback`, `get_branches`, `get_adaptation_state`) bringing total to 18
- Added frontend inline feedback, refinement input, branch management, and adaptation transparency
- Added Inspector panels for feedback verdict, refinement history, branch tree, and adaptation weights
- Changed retry logic from fixed threshold to adaptive oracle with best-of-N selection
- Added `pause_turn` stop reason handling in agentic loop — re-sends instead of terminating when server-side tool hits iteration limit
- Added model-family effort parameter to `_make_extra()` — Opus `high`, Sonnet `medium`, Haiku `low` via `output_config.effort`
- Added row version guard (`row_version == 0`) on all pipeline DB updates to prevent concurrent PATCH overwrites
- Added compaction beta support (`COMPACTION_ENABLED` config flag) for automatic context summarization in agentic loops
- Added `pre-pr-svelte.sh` hook — runs `svelte-check` before `git push` and `gh pr create`
- Added `code-reviewer` subagent definition for architecture, brand, and consistency review
- Added Claude Code automation section to `CLAUDE.md` documenting MCP, hooks, settings, and subagents
- Changed 13 logger call sites from f-string interpolation to parameterized `%s`/`%d` format
- Fixed `_run_optimize_validate()` retry path bypassing dynamic model routing — now receives per-stage models instead of raw `model_override`
- Fixed `_compute_cost()` producing negative cost when cache tokens exceed total input tokens — normal input clamped to zero
- Fixed missing `model_*` columns in SQLite ALTER TABLE migration — `model_explore`, `model_analyze`, `model_strategy`, `model_optimize`, `model_validate` now added to existing databases on startup
- Added cost tracking with `CompletionUsage` dataclass — real token counts from Anthropic API, estimated for CLI, per-stage usage in SSE events, accumulated totals in `complete` event and DB columns
- Added dynamic model routing via `select_model()` — downgrades Opus to Sonnet for simple prompts after Analyze, emits `model_selection` SSE event for observability
- Added 1M context window beta support — `CONTEXT_1M_ENABLED` config flag passes `anthropic-beta` header and expands explore limits (80 files, 50K lines, 2M chars)
- Added parallel Explore + Analyze execution — runs concurrently via `asyncio.wait` with buffered deterministic event ordering, saving ~min(explore, analyze) seconds
- Added SDK session resumption support — captures `session_id` from `ResultMessage` on `AgenticResult`, `resume_session_id` parameter on `complete_agentic()`
- Added MCP tool category registry (`TOOL_CATEGORIES`) — structured metadata for all 15 tools enabling future tool search integration
- Added `_MODEL_PRICING` table for Claude 4.x models with cache-aware cost computation
- Added cost aggregation to `compute_stats()` — `total_input_tokens`, `total_output_tokens`, `total_cost_usd`
- Fixed `ClaudeCLIProvider.complete_json()` silently ignoring schema parameter — now injects schema instruction into system prompt for best-effort compliance
- Fixed `ClaudeCLIProvider` `ExceptionGroup` unwrapping with dead code and missing error logging on non-ExceptionGroup failures
- Fixed detector not falling through to `AnthropicAPIProvider` when `ClaudeCLIProvider` instantiation fails with non-ImportError exceptions
- Fixed inaccurate comment claiming Haiku 4.5 has "no thinking support" — it supports manual thinking, just not adaptive
- Improved `LLMProvider.complete_json()` docstring to accurately document provider-specific schema enforcement capabilities
- Improved `on_agent_text` callback docstring with per-provider granularity semantics
- Changed SDK version constraints in `requirements.txt` — pinned `anthropic>=1.45.0,<2.0`, `mcp>=1.0,<2.0`, `claude-agent-sdk>=0.1.46,<1.0` to prevent breaking changes

## 0.7.0

- Added intent-aware pipeline — pre-explore classification adapts codebase observations, strategy hints, optimizer weaving, and validator scoring per intent category (refactoring, api_design, testing, debugging, etc.)
- Added codebase-aware scoring calibration across all pipeline stages — specificity rewards code navigation precision, conciseness ignores earned length, faithfulness accepts informed scope narrowing
- Added `stage_durations` persistence — per-stage timing and token counts saved to database, TraceView shows real durations
- Improved explore synthesis prompt with behavioral specificity, cross-cutting pattern tracing, and quantitative metadata
- Improved validator scoring rubrics — distinguish earned precision from bloat, protective constraints from restrictive ones, proportional structure from over-engineering
- Changed context builder caps: observations 8→12, grounding notes 8→12, snippets 5→10, content 600→1200 chars, key files 10→20, tech stack 10→15
- Changed CLI provider from simulated chunking to true token-level streaming via `claude` subprocess with `--include-partial-messages`
- Changed optimizer output format from pure JSON to plain text with `<optimization_meta>` block — new `OptimizeStreamParser` extracts prompt from metadata in real-time
- Improved streaming robustness with cross-boundary marker detection, multi-line SSE data field concatenation, and JSON fallback
- Added wiring for all 6 pipeline settings — `default_model`, `pipeline_timeout`, `max_retries`, `default_strategy`, `stream_optimize`, and `auto_validate` now control pipeline behavior
- Added strategy dropdown to Settings panel with 10 known frameworks and `KNOWN_STRATEGIES` validation
- Changed `pipeline_timeout` default from 120s to 300s, capped by `config.py` ceiling (900s)
- Added DNA helix brand mark (`HelixMark.svelte`) with Canvas 2D parametric rendering and organic animations — replaces text marks in ActivityBar, editor watermark, and favicon
- Changed keyboard shortcuts to avoid browser conflicts — `Alt` modifier for navigation, `Ctrl` reserved for actions, `Alt+←/→` directional panel focus
- Changed StatusBar to 3-zone semantic layout (Health, Context, Workspace) with progressive disclosure
- Changed Welcome tab to adaptive 3-column grid layout — checklist collapses when complete, sample prompts in compact clickable card grid
- Added indeterminate progress bar and tooltip to Stream optimize toggle in batch mode
- Added in-app Anthropic API key management — configure, update, or remove via Settings UI without editing `.env`
- Added bootstrap mode — app starts without an LLM provider and guides first-time setup through the UI
- Added auto-generated crypto secrets (`SECRET_KEY`, `JWT_SECRET`, `JWT_REFRESH_SECRET`) persisted to `data/.app_secrets`
- Added auto-generated Redis password via `secrets-init` init container (zero-config Docker deployment)
- Added Fernet encryption for GitHub App credentials at rest with automatic plaintext migration
- Added single-source-of-truth versioning via `backend/app/_version.py` with status bar and MCP display
- Fixed 6 cache invalidation bugs — history auto-refresh, editor tab sync, inline edit propagation, delete/restore/batch-delete eviction, and stale artifact cleanup
- Fixed Analyze and Strategy cache keys ignoring system prompt content — template edits now invalidate cached results
- Fixed Settings panel `$effect` infinite fetch loop, save error handling, and `unlinkRepo()` silent failures
- Fixed `save_settings` to use atomic temp-file + `os.replace` pattern (prevents corruption on crash)
- Changed `sort` and `order` query parameters to return 400 on invalid values instead of silently defaulting
- Changed error responses to structured `{code, message}` format via centralized error factories
- Changed startup behavior from fatal crash to graceful degradation when no LLM provider is available

## 0.5.0

- Added intelligence-layer boundary enforcement across all pipeline stages
- Added boundary tests to verify explore, analyze, strategy, optimize, and validate respect layer separation
- Fixed downstream stages receiving raw LLM artifacts instead of normalized pipeline data

## 0.4.0

- Added JWT-based authentication with GitHub OAuth login flow
- Added audit logging for auth events and token refresh
- Added `POST /auth/github/token/refresh` for manual token rotation
- Added user profile identity — `fetchAuthMe`, avatar in status bar, profile section in settings
- Added onboarding flow — new-user detection, welcome modal, 4-step wizard, strategy explainer
- Added rate limiting on all auth endpoints via `limits` library with `RateLimit` dependency
- Added `SameSite=Strict` on JWT refresh cookies
- Added CRITICAL warning when `JWT_COOKIE_SECURE=False` on non-localhost origins
- Fixed unified auth error messages to prevent user-enumeration oracle attacks
- Fixed 401 cascade on hard refresh causing premature logout
- Fixed workbench flash, API spam, and toast storm on reload/HMR

## 0.3.0

- Added batch delete endpoint with confirmation UI in history sidebar
- Added `batch_delete_optimizations` MCP tool
- Added trash/restore system — `list_trash` and `restore_optimization` MCP tools
- Added advanced history filters — score range, repo, task type, status
- Added soft-delete and `user_id` scoping on all optimization endpoints
- Added composite database index `(user_id, deleted_at, created_at DESC)`
- Added optimistic locking and input validation on optimization writes
- Fixed MCP `merge()` antipattern and error handling in pipeline event mapping

## 0.2.0

- Added semantic codebase exploration — replaced 25-turn agentic loop with retrieval + single-shot synthesis
- Added background repo indexing with `all-MiniLM-L6-v2` embeddings (384-dim, CPU)
- Added auto-refresh on branch changes — HEAD SHA detection triggers background reindex
- Added 3-tier file merge (prompt-referenced > deterministic anchors > semantic-ranked)
- Added dynamic line budget, context overflow guard, and post-LLM output validation
- Added pipeline stage caching — Strategy (24h) and Analyze (24h) with prompt-aware cache keys
- Added Redis service with graceful degradation to in-memory fallback
- Added `CacheService` with bounded LRU eviction and JSON type normalization
- Added trusted proxy validation for `X-Forwarded-For` rate-limit bypass prevention
- Fixed strategy selector bias toward CO-STAR framework
- Fixed score distribution compression — widened range with decimal precision
- Fixed cache key ignoring prompt content (all same-type prompts got identical strategy)

## 0.1.0

- Added five-stage optimization pipeline — Explore, Analyze, Strategy, Optimize, Validate
- Added real-time SSE streaming for pipeline progress
- Added GitHub OAuth integration with Fernet-encrypted token storage
- Added MCP server with 15 tools (streamable HTTP + WebSocket transports)
- Added Docker Compose deployment with nginx reverse proxy
- Added container hardening — non-root, `no-new-privileges`, `cap_drop: ALL`, read-only filesystems
- Added Playwright E2E test suite and 49-test backend integration suite
- Added command palette with unified command registration
- Added `@` context popup with caret-anchored positioning
