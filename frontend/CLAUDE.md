# Frontend — Internal Reference

Everything frontend developers need. For project overview, see root `CLAUDE.md`. **Keep this file under 200 lines.**

## Framework and tooling

- **Framework**: SvelteKit 2 (Svelte 5 runes) + Tailwind CSS 4
- **Language**: TypeScript 5.9, Node >= 22
- **Build**: Vite 7 with `@sveltejs/adapter-static` (pre-renders to `/build`)
- **Key deps**: Three.js (topology 3D viz), D3 (data viz), diff (text diffing), marked (markdown)
- **Base path**: `/ProjectSynthesis` on GitHub Pages (`GITHUB_PAGES` env), empty otherwise
- **Brand**: industrial cyberpunk — see `brand-guidelines` skill for full reference

## Development

```bash
npm run dev        # dev server → port 5199
npm run build      # static build
npm run check      # svelte-check + tsc
npm run test       # vitest run
```

- **API proxy**: dev hits `http://localhost:8000/api` directly; prod uses relative `/api`. Override with `VITE_API_URL`
- **Test stack**: Vitest + @testing-library/svelte + jsdom. Setup: `src/lib/test-setup.ts`
- **Coverage**: `@vitest/coverage-v8`, 90% line threshold. Excludes test files and `src/lib/content/`

## API client (`src/lib/api/client.ts`)

All backend calls go through this module. Key helpers:
- `apiFetch<T>()` — throws `ApiError` on non-2xx
- `tryFetch<T>()` — returns `null` on non-2xx (optional checks)
- `streamSSE()` — manages `AbortController` + line-buffering for SSE streams

Key types: `HealthResponse`, `OptimizationResult`, `RefinementTurn`, `HistoryItem`, `DimensionScores`
- `seed.ts` — `seedTaxonomy()`, `listSeedAgents()`. Types: `SeedRequest`, `SeedOutput`, `SeedAgent`

## Stores (`src/lib/stores/`)

| Store | Purpose |
|-------|---------|
| `forge.svelte.ts` | Pipeline state (prompt, strategy, SSE, result, feedback). `localStorage` persistence via `synthesis:last_trace_id` — refresh restores last optimization. `OptimizationResult.optimized_scores: DimensionScores \| null` (legacy alias, narrowed — drops `as any` casts in favour of typed normalization) |
| `editor.svelte.ts` | Tab management (`prompt | result | diff | mindmap | observatory`). Two pinned tabs: `PROMPT_TAB_ID` + `OBSERVATORY_TAB_ID` (both `pinned: true`, never closeable; closing a result tab falls back to the Prompt tab). `closeTab()` cleans the result cache when no other tab references it |
| `github.svelte.ts` | GitHub Device Flow auth + token refresh, `connectionState` getter (7 states: `disconnected | expired | authenticated | linked | indexing | error | ready`), `phaseLabel` + `indexErrorText` for per-phase progress/error surfacing, `reconnect()`, repo picker, file browser (tree/content), branch list, index status, project selection, `uiTab` + `setUiTab()` for GitHubPanel tab persistence (migrated from ad-hoc `$effect` → `synthesis:github_tab` localStorage). `applyPhaseEvent()` consumes `index_phase_changed` SSE; `_handleAuthError()` centralizes 401 detection |
| `project.svelte.ts` | ADR-005 multi-project scope (F1–F5). `currentProjectId` rune (`null` = "All projects", `<uuid>` = scoped view, persisted to `localStorage['synthesis:current_project_id']` as JSON — survives repo unlink). `projects` list, `currentLabel`, `isLegacyScope` getters. `setCurrent()`, `refresh()` hits `GET /api/projects`. `applyLinkResponse(projectId, candidates)` auto-switches scope on `githubStore.linkRepo()` response and stashes `lastMigrationCandidates` for the F5 post-link migration toast (dismissed via `clearMigrationCandidates()`). F2 project selector subscribes; F3 threads explicit `project_id` into `/api/optimize`, `/api/refine`, `synthesis_optimize`; F4 tree/topology/pattern consumers scope by `currentProjectId`; F5 link/unlink transition toasts via `taxonomy_changed` SSE (`trigger: "project_created"`) and Inspector per-project breakdown |
| `refinement.svelte.ts` | Refinement sessions: turns, branches, suggestions, score progression |
| `preferences.svelte.ts` | Persistent user preferences loaded from backend |
| `toast.svelte.ts` | Toast notification queue with `addToast()` API. Severity helpers: `success()`, `info()`, `warning()`, `error()`. Push-and-dismiss primitive for ambient status feedback (taxonomy updates, optimization completion, etc.) |
| `toasts.svelte.ts` | **Distinct from `toast.svelte.ts`** — destructive-action primitive. `toastsStore` singleton exposes `push/dismiss/undo/pause/resume` on a capped stack (max 3). Toasts carry an optional `commit?: () => Promise<void>` hook fired on timer expiry (grace-window pattern — HistoryPanel's row × defers `deleteOptimization` until the 5 s undo window closes). Consumed by `UndoToast.svelte`; not a replacement for `toast.svelte.ts` |
| `readiness-notifications.svelte.ts` | SSE dispatcher for `domain_readiness_changed`. Maps tier transitions to toast severity via `readiness-tier.ts`. Gated by `preferences.domain_readiness_notifications`; per-row mute toggle stored in `localStorage` |
| `routing.svelte.ts` | Derived routing state mirroring backend 5-tier priority chain. Reactive tier resolver |
| `clusters.svelte.ts` | Cluster state: two-path pattern detection (typing 800ms + paste 300ms debounce, 30-char min, AbortController), persistent suggestion (no auto-dismiss). `ClusterMatch` carries `cross_cluster_patterns: MetaPatternItem[]` + `match_level: 'family'\|'cluster'` (defensively defaulted on decode for legacy responses). Public transient flags `_matchInFlight`/`_matchError`/`_lastMatchedText` drive `ContextPanel` empty-states. `applySuggestion()` returns `{ids, clusterLabel}`, tree/stats, detail, `StateFilter` + `filteredTaxonomyTree`, async `invalidateClusters()` with ghost-selection guard, seed batch progress. Activity (SSE-only ring): `activityEvents`, `activityOpen`, `pushActivityEvent()`, `toggleActivity()`, `loadActivity()` (single-day JSONL fallback when ring < 20). Period-scoped backfill moved to `observatoryStore.loadTimelineEvents()` in v0.4.5 — keeps the topology ActivityPanel feed isolated from Observatory period selection. (Template spawning moved to `templates.svelte.ts`) |
| `observatory.svelte.ts` | Pattern-density + lifecycle-timeline telemetry for the Observatory. Owns the period selector (`'24h'\|'7d'\|'30d'`, default `'7d'`, persisted to `synthesis:observatory_period`). `setPeriod()` debounces re-fetch by 1 s and fires BOTH `refreshPatternDensity()` (Heatmap) AND `loadTimelineEvents()` (Timeline JSONL backfill). Two race-guard counters (`_fetchGeneration` / `_timelineGeneration`) keep the panels' fetches independent. `historicalEvents` buffer is dedicated to the Timeline; the Timeline derives its render-side view by merging this with `clustersStore.activityEvents` (live SSE) at render time — neither store mutates the other. `_reset()` test helper |
| `templates.svelte.ts` | Proven-templates store: `load(projectId)`, `spawn(templateId)` (records a use and returns the spawned optimization), `retire(templateId)`. List grouped by frozen domain in PROVEN TEMPLATES navigator section. Reacts to `taxonomy_changed` SSE (template fork/retire triggers via warm path). Halo ring rendering uses `HIGHLIGHT_COLOR_HEX` from `colors.ts` |
| `domains.svelte.ts` | API-driven domain palette. `colorFor()` resolves domain→hex with keyword fallback. Invalidated on `domain_created`/`taxonomy_changed` SSE |
| `passthrough-guide.svelte.ts` / `sampling-guide.svelte.ts` / `internal-guide.svelte.ts` / `guide-factory.svelte.ts` / `tier-onboarding.svelte.ts` | Tier-specific onboarding guide modals + factory |
| `pattern-graph-guide.svelte.ts` | Pattern graph keyboard shortcuts/interaction hints modal |
| `update.svelte.ts` | Auto-update state: available version, changelog, dialog, restart progress, health polling. SSE-driven via `update_available`/`update_complete` events. `localStorage` persistence for detached HEAD warning dismissal |
| `sse-health.svelte.ts` | SSE connection health: owns EventSource lifecycle, latency tracking (rolling 100-event window, p50/p95/p99), 3-state degradation detection (healthy/degraded/disconnected), exponential backoff reconnection (1s-16s cap, 10 attempts, ±20% jitter), 90s staleness detection. StatusBar indicator reads `connectionState` and `tooltipText` |
| `readiness.svelte.ts` | Domain readiness cache with 30s stale window matching backend TTL. Invalidated on `taxonomy_changed`/`domain_created` SSE. Exposes `reports`, `byDomain(id)`, `refresh()`, `fresh()` (bypass server cache) |
| `readiness-window.svelte.ts` | Persistent time-window selector (24h/7d/30d) for DomainReadinessSparkline. Stored under `synthesis:readiness_window`; invalid/missing values fall back to `'24h'` |
| `nav_collapse.svelte.ts` | Persisted collapse state for sidebar sections. `isOpen(key)`, `toggle(key)`, `collapseAll(prefix?)`. Keys: `readiness`, `templates`, `domain:${name}`, `subdomain:${id}`. Persisted to `localStorage['synthesis:navigator_collapsed']` as JSON array; default-open policy |
| `hints.svelte.ts` | Persistent dismissal tracking for one-shot UI hints (e.g. pattern-graph onboarding). Keys: `pattern_graph_keyboard`, `pattern_graph_drag`. Persisted under `synthesis:ui_hints_dismissed`; one-shot migration from the legacy `synthesis:pattern_graph_hints_dismissed` key |
| `topology-cache.svelte.ts` | 60-iteration settled-position cache for `SemanticTopology` layout. Fingerprint-based single-entry staleness policy — swap on node-set change, reuse on tab switch. Persisted under `synthesis:topology_cache` |

## Component layout

```
src/lib/components/
  layout/       # ActivityBar (sliding tab indicator), Navigator (~182-line shell
                # delegating to 8 panels: StrategiesPanel, HistoryPanel [row ×
                # grace-window delete; multi-select keyboard grammar — Ctrl/Cmd+Click
                # auto-seed, Shift+Click range, Ctrl+A, Esc, Delete/Backspace;
                # bulk 404 fallback], GitHubPanel, SettingsPanel, ClusterRow,
                # DomainGroup, StateFilterTabs, TemplatesSection), ClusterNavigator
                # (reads templatesStore, renders PROVEN TEMPLATES grouped by domain),
                # EditorGroups (mounts ContextPanel beside the prompt editor + the
                # pinned Observatory tab via the editor TabType), Inspector
                # (phase-dot + Templates section + ClusterPatternsSection +
                # ClusterTemplatesSection + TaxonomyHealthPanel co-located here),
                # StatusBar
  editor/       # PromptEdit, ForgeArtifact (ENRICHMENT renders analyzer telemetry —
                # signal source [bootstrap/dynamic], TASK-TYPE SCORES, CONTEXT
                # INJECTION counts, DOMAIN SIGNALS + RETRIEVAL headings, per-layer
                # skip-reason tags; guards all-zero vectors), ContextPanel (ADR-007
                # Tier 1 — persistent sidebar mounted by EditorGroups; cluster
                # identity row + meta-patterns checkboxes + neon-purple-bordered
                # GLOBAL section; APPLY → forgeStore.appliedPatternIds; mount-gated
                # to prompt tab, hidden during synthesis; <1400px → 28px rail;
                # localStorage-persisted collapse state), PassthroughView
  taxonomy/     # SemanticTopology + TopologyControls/Renderer/Data/Interaction/Labels/Worker
                # (5-force sim, halo pool around clusters with template_count > 0,
                # state filter dimming), ActivityPanel (severity rows + path accent
                # rails + expandable context cards; recognizes readiness/* + vocab
                # ops), DomainReadinessPanel + DomainStabilityMeter + SubDomainEmergenceList
                # + DomainReadinessSparkline (hourly-bucket history; per-domain rings
                # overlaid on topology; `role="meter"`, zero-glow), SeedModal,
                # TaxonomyObservatory (v0.4.4 — three-panel shell on the pinned
                # Observatory tab; routes domain:select → clustersStore.selectCluster()):
                # DomainLifecycleTimeline (render-time merge of clustersStore.activityEvents
                # [live SSE ring] + observatoryStore.historicalEvents [JSONL period buffer],
                # deduped by ts|op|decision, cap 200 newest-first; period chips drive
                # observatoryStore.loadTimelineEvents(); shared isErrorEvent + opFamily +
                # keyMetric helpers in $lib/utils/activity-{filters,summary}.ts keep
                # ActivityPanel + Timeline aligned), DomainReadinessAggregate (auto-fill card
                # grid composing existing meter+emergence per domain; 6px chromatic
                # dot; sorted critical→guarded→healthy; click dispatches domain:select),
                # PatternDensityHeatmap (read-only grid; opacity-scaled tint with
                # HEAT_MAX_PCT=14 mixed INTO bg-card; hover 1px inset cyan + use:tooltip
                # with absolute counts + period_end UTC; mount-time backfill)
  refinement/   # RefinementTimeline, RefinementTurnCard, SuggestionChips,
                # BranchSwitcher, ScoreSparkline, RefinementInput
  shared/       # CommandPalette, CollapsibleSectionHeader (whole-bar/split modes,
                # Snippet-based slots, navCollapse-backed persistence),
                # DiffView, Logo, MarkdownRenderer (pseudo-XML sanitizer for
                # optimizer wrapper tags), PassthroughGuide, SamplingGuide,
                # InternalGuide, TierGuide, TierBadge, ProviderBadge, ScoreCard,
                # Toast, UpdateBadge, UndoToast (grace-window toast — RAF progress,
                # pause-on-hover, commit fires on timer expiry so Undo skips the
                # round-trip), DestructiveConfirmModal (`DELETE` literal gate)
  landing/      # Navbar, Footer, ContentPage, HeroSection, CardGrid, ProseSection,
                # CodeBlock, MetricBar, StepFlow, Timeline
```

## Routes

```
src/routes/
  +layout.svelte, +layout.ts     # Root layout + preload
  (landing)/                      # Public landing pages
    +page.svelte                  # Home (/)
    [slug]/+page.svelte           # Dynamic content pages
  app/                            # Authenticated workbench
    +layout.svelte, +page.svelte  # Main application (/app)
```

## Shared utilities (`src/lib/utils/`)

| File | Purpose |
|------|---------|
| `colors.ts` | `scoreColor()`, `taxonomyColor()` (delegates to `domainStore.colorFor()`), `qHealthColor()`, `stateColor()` (includes `domain`→amber). No hardcoded domain color maps |
| `dimensions.ts` | Score dimension label/description helpers |
| `formatting.ts` | Display formatting (numbers, dates, text truncation) |
| `strategies.ts` | Strategy display name/description helpers |
| `mcp-tooltips.ts` | MCP tool tooltip content for UI hints |
| `ui-tooltips.ts` | Structured tooltip builders (e.g., GitHub index file count) |
| `keyboard.ts` | Pure `nextTablistValue()` + `handleTablistArrowKeys()` for tablist arrow-key navigation (wrap, orientation, no-op branches; `preventDefault` + `onChange` wrapper) |
| `transitions.ts` | `navSlide`/`navFade` presets driven by an inline 8-iteration Newton-Raphson bezier solver matching `--ease-spring` (`cubic-bezier(0.16, 1, 0.3, 1)`) exactly — single source of truth for the brand spring across JS + CSS transitions (Svelte's built-in `cubicOut` drifted visibly) |

## Brand and design system

- **Theme**: dark backgrounds, 1px neon contours, no rounded corners, no shadows, no gradients
- **Color system**: all domain colors resolved from API via `domainStore.colorFor()` — never hardcoded
- **Domain palette**: backend=#b44aff violet, frontend=#ff4895 hot pink, database=#36b5ff steel blue, data=#b49982 warm taupe, security=#ff2255 red, devops=#6366f1 indigo, fullstack=#d946ef magenta, general=#7a7a9e gray
- **Topology**: Three.js `SemanticTopology` with LOD tiers (persistence thresholds: far=0.4, mid=0.2, near=0.0), state-based chromatic encoding (opacity, size, color per lifecycle state), raycasting interaction
- **Topology simulation**: `TopologyWorker` runs 5-force model: UMAP anchor, parent-child spring, same-domain affinity, universal repulsion, collision resolution

## SSE event handling

Events received at `/api/events` via `EventSource`. Types that drive UI reactivity:

| Event | UI Effect |
|-------|-----------|
| `optimization_created/analyzed/failed` | History auto-refresh, toast notifications. MCP events auto-load via `forgeStore.loadFromRecord()` |
| `optimization_deleted` | Root `+page.svelte` bridges to DOM `CustomEvent('optimization-deleted')`; `HistoryPanel` surgically removes the row (no full re-fetch). 2-s fallback timeout covers SSE reconnect gaps. Dedicated event (not bundled into `optimization-event`) so consumers target the specific `id` |
| `optimization_status/score_card/start` | Routed through `forgeStore.handleExternalEvent()` (single code path for MCP + web) |
| `feedback_submitted` | Inspector feedback state sync + `editorStore.cacheFeedback()` |
| `refinement_turn` | Refinement timeline update + `refinementStore.reloadTurns()` for cross-tab sync |
| `strategy_changed` | Strategy list refresh |
| `taxonomy_changed` | Cluster/domain store invalidation, topology re-render. Also fires with `trigger: "project_created"` on repo link |
| `taxonomy_activity` | `clustersStore.pushActivityEvent()` — real-time feed to ActivityPanel. `readiness/*` ops feed readiness panel; `vocab_generated_enriched` surfaces vocab quality score |
| `routing_state_changed` | Routing store update, tier availability toasts |
| `domain_created` | Domain store invalidation |
| `domain_readiness_changed` | `readinessNotificationsStore` dispatches severity-mapped toast (preference-gated, per-row mute respected); `readinessStore` invalidates cached report; SemanticTopology redraws the domain's readiness ring |
| `seed_batch_progress` | `clustersStore.updateSeedProgress()` (persistent) + DOM CustomEvent for SeedModal. StatusBar shows progress when modal closed |
| `index_phase_changed` | `githubStore.applyPhaseEvent()` — live per-phase index progress (`fetching_tree`/`embedding`/`synthesizing`/`ready`/`error`) drives Navigator badge pulse + error row + StatusBar state without waiting for the 2-minute poll |
| `preferences_changed` | Preferences store reload |
| `agent_changed` | Seed agent list refresh (hot-reload on file change) |
| `update_available` | Update store populated, StatusBar UpdateBadge badge displayed |
| `update_complete` | Update validation results, success/failure toast |

**Connection lifecycle**: `sseHealthStore` owns the single `EventSource`. It tracks delivery latency per event, detects degradation (p95 thresholds), and manages reconnection with exponential backoff (replaces browser auto-reconnect). On manual reconnect, sends `?last_event_id=N` query param for server-side replay. `+page.svelte` calls `sseHealthStore.connect(handler, onReconnect)` in `$effect` and `disconnect()` on cleanup.

Fixed 60s health polling for StatusBar display only — no routing decisions from frontend. Tightens to 2s during auto-update restart window (120s timeout).

## Key patterns

- **Svelte 5 runes**: `$state`, `$derived`, `$effect` for reactive state throughout
- **Store pattern**: files export reactive class instances (not Svelte 4 writable stores)
- **Session persistence**: forge store saves `last_trace_id` to `localStorage`; page refresh restores from DB
- **Pattern detection**: two-path — typing (800ms debounce, 30-char min) + paste (300ms, 30-char delta). AbortController cancels in-flight requests. No auto-dismiss. `applySuggestion()` returns `{ids, clusterLabel}` for persistent chip bar. `appliedPatternLabel` on forge store for UI confirmation
- **Toggle safety**: disabled conditions prefixed with `!currentValue &&` — toggle already ON is always interactive
- **Routing reactivity**: frontend is purely reactive — receives `routing_state_changed` SSE, never makes routing decisions
- **GitHub connection state**: `githubStore.connectionState` getter (7 states: `disconnected | expired | authenticated | linked | indexing | error | ready`) replaces ad-hoc null checks. `ready` requires `index_phase === 'ready'` AND `status === 'ready'` AND synthesis complete — no premature transitions while synthesis is still running. `phaseLabel`/`indexErrorText` render per-phase copy + error rows. `reconnect()` clears `linkedRepo` before Device Flow so template falls to auth branch. `_handleAuthError()` centralizes 401 detection. Tab selection persisted via `githubStore.uiTab` → `synthesis:github_tab` localStorage (store-owned, not component-owned)
- **UI persistence through stores**: localStorage access lives in store modules, not components. `githubStore.uiTab` / `stores/hints.svelte.ts` / `stores/topology-cache.svelte.ts` replace ad-hoc `$effect` + direct localStorage reads in `GitHubPanel`, `TopologyControls`, and `SemanticTopology` respectively. One-shot migration shims preserve user state across key renames
- **Module-level store imports retained (architectural decision)**: Svelte 5 runes stores (`.svelte.ts` modules exporting singleton class instances) are the idiomatic DI boundary. Extracted Navigator panels import stores at module level; smoke tests mutate singleton state in `beforeEach` and assert render output without touching panel imports — props-DI would add boilerplate with no testability or reuse payoff. See PR #33 audit + code-quality sweep phase 1
- **Brand-aligned animation tokens**: `--duration-skeleton` (1500ms) + `--duration-stagger` (350ms) in `app.css` replace hardcoded `1500ms` / `350ms` literals in `ClusterRow`, `HistoryPanel`, `TierGuide`. `ForgeArtifact` uses the shared `navSlide` preset instead of three `{duration: 200}` overrides
- **Cross-component SSE**: MCP pipeline events route through `forgeStore.handleExternalEvent()` (single code path). Refinement turns propagated to `refinementStore.reloadTurns()`. Seed batch progress persisted in `clustersStore` (survives modal close)
- **Per-tab feedback caching**: `editorStore.cacheFeedback()`/`activeFeedback` getter prevents feedback state loss on tab switch
- **Version**: `src/lib/version.ts` imports from root `version.json` — auto-synced by `scripts/sync-version.sh`, never edit manually
