# Changelog

## Unreleased

- Added pre-explore intent classification to adapt codebase observations to prompt intent (refactoring, api_design, testing, debugging, etc.)
- Improved explore synthesis prompt with behavioral specificity guidance, cross-cutting pattern tracing, and quantitative metadata instructions
- Changed context builder caps: observations 8→12, grounding notes 8→12, snippets 5→10, snippet content 600→1200 chars, key files 10→20, tech stack 10→15
- Added intent-specific weaving guidance to optimizer codebase context injection (positive instructions per intent category)
- Added codebase-aware paragraphs to coding, analysis, reasoning, and general optimizer prompts
- Changed validator codebase summary cap from 2500 to 4000 chars
- Fixed streaming optimize showing raw JSON tokens instead of clean prompt text — new `OptimizeStreamParser` extracts prompt from metadata in real-time
- Fixed `streamingText` not clearing on optimize retry (stale text + new chunks concatenated)
- Changed optimizer prompt format from pure JSON to plain text with `<optimization_meta>` block for clean streaming
- Improved streaming robustness with cross-boundary marker detection and JSON fallback
- Changed event handling in `PromptEdit.svelte` from duplicated 140-line switch to delegating to `forge.handleSSEEvent()`
- Fixed Stream optimize tooltip to accurately describe behavior
- Changed keyboard shortcuts to avoid browser conflicts — `Alt` modifier for navigation (`Alt+N`, `Alt+W`, `Alt+↑↓`, `Alt+←→`, `Alt+1-8`), `Ctrl` reserved for actions
- Changed Templates panel shortcut from `Ctrl+Shift+T` to `Ctrl+Shift+Y` (browser conflict with reopen-closed-tab)
- Added `Alt+←` / `Alt+→` directional focus shortcuts for Navigator and Inspector panels
- Removed `F6` zone cycling (replaced by `Alt+←→` directional focus)
- Fixed double-fire bug where `Ctrl+S`, `Ctrl+W`, and `Ctrl+,` were handled by both `handleKeyboard` and the command palette dispatch
- Added DNA helix brand mark component (`HelixMark.svelte`) with Canvas 2D parametric rendering, organic animations, and adaptive level-of-detail
- Changed ActivityBar brand mark from "PS" text to animated DNA helix
- Changed empty editor watermark from "PF" text to animated DNA helix
- Changed favicon from fire emoji to DNA emoji
- Added DNA emoji to README header
- Removed phantom `anthropic` and `openai` provider values from workbench store type, StatusBar, NavigatorSettings, and health polling cast — backend only returns `claude_cli` or `anthropic_api`
- Fixed Settings panel `$effect` infinite fetch loop — replaced with `onMount` for single load
- Fixed Settings panel toggle/number save errors replacing entire UI with error box — now shows toast notification
- Added JS-side clamping on pipeline timeout (10–600) and max retries (0–5) number inputs in Settings panel
- Added `.catch()` error handler to walkthrough dynamic import in Settings panel
- Fixed `unlinkRepo()` silently swallowing errors during GitHub disconnect — now shows toast warning
- Removed unnecessary `as any` typecast on strategy setting update
- Fixed StatusBar provider fallback label from `???` to `--` and dot from yellow to red when provider is unknown
- Fixed SSE parser in `forge.svelte.ts` `_consumeSSEResponse` to concatenate multi-line `data:` fields (parity with `client.ts` fix — retry streams could silently drop data)
- Fixed `save_settings` to use atomic temp-file + `os.replace` pattern (prevents settings corruption on crash)
- Added indeterminate progress bar to `StageOptimize` when streaming is disabled (batch mode no longer shows bare spinner)
- Added tooltip to Stream optimize toggle explaining streaming vs batch mode trade-off
- Changed Welcome tab to adaptive layout — checklist collapses to single "All systems ready" line when 5/5 complete, reclaiming ~126px for returning users
- Removed decorative gradient header from Welcome tab (`bg-gradient-to-r bg-clip-text` violated zero-effects directive)
- Changed Welcome tab to 3-column grid layout — sample prompts in 3×3 card grid (9 cards), keyboard shortcuts grid aligned to same rhythm
- Changed sample prompt cards from horizontal scroll to compact clickable cards (whole card is the action, removed separate TRY THIS button and 2-line description)
- Changed Welcome tab container from `max-w-xl` to `max-w-2xl` to accommodate 3-column card grid
- Changed Keyboard Shortcuts heading to reference-tier dimming (`text-text-secondary`, `text-text-dim/50`)
- Removed dead `user` store import from `WelcomeTab.svelte`
- Changed StatusBar from flat pipe-separated list to 3-zone semantic layout (Health, Context, Workspace) with progressive disclosure and visual hierarchy
- Removed `ProviderBadge` component — logic inlined into StatusBar as flat label + dot (eliminates `rounded-md` brand violation)
- Changed `sort` and `order` query parameters to return 400 on invalid values instead of silently defaulting
- Improved SSE parser in `client.ts` to concatenate multi-line `data:` fields per SSE spec
- Added wiring for all 6 pipeline settings — `default_model`, `pipeline_timeout`, `max_retries`, `default_strategy`, `stream_optimize`, and `auto_validate` now control pipeline behavior
- Added `model` parameter to all 5 stage services (`run_analyze`, `run_strategy`, `run_optimize`, `run_validate`, `run_explore`)
- Added `streaming` parameter to `run_optimize` — when disabled, uses single `complete()` call instead of streaming
- Added strategy dropdown to Settings panel with 10 known frameworks
- Added `default_strategy` validation in settings router with `KNOWN_STRATEGIES` whitelist
- Changed settings PATCH endpoint from `exclude_none` to `exclude_unset` so `null` can clear nullable fields like `default_strategy`
- Changed `pipeline_timeout` default from 120s to 300s (realistic for 4-5 sequential LLM stages)
- Changed pipeline timeout to respect user setting capped by `config.py` ceiling (900s)
- Fixed README to clarify Claude Max subscription as the recommended (zero-cost) LLM provider
- Removed multi-provider roadmap item — Project Synthesis is built for Claude
- Added auto-generated Redis password via `secrets-init` init container (zero-config Docker deployment)
- Removed manual `REDIS_PASSWORD` requirement from `.env.docker.example`
- Added single-source-of-truth versioning via `backend/app/_version.py`
- Added version display in the status bar (far right, dim text)
- Added version to MCP server `initialize` response
- Removed hardcoded version strings from 5 files
- Added in-app Anthropic API key management — configure, update, or remove via Settings UI without editing `.env`
- Added bootstrap mode — app starts without an LLM provider and guides first-time setup through the UI
- Added auto-generated crypto secrets (`SECRET_KEY`, `JWT_SECRET`, `JWT_REFRESH_SECRET`) persisted to `data/.app_secrets`
- Added Fernet encryption for GitHub App credentials at rest with automatic plaintext migration
- Added `RATE_LIMIT_PROVIDER_READ` and `RATE_LIMIT_PROVIDER_WRITE` settings
- Changed startup behavior from fatal crash to graceful degradation when no LLM provider is available
- Changed `.env.docker.example` to reflect optional API key and auto-generated secrets

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
