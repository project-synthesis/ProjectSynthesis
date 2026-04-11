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
| `forge.svelte.ts` | Pipeline state (prompt, strategy, SSE, result, feedback). `localStorage` persistence via `synthesis:last_trace_id` — refresh restores last optimization |
| `editor.svelte.ts` | Tab management (prompt/result/diff/mindmap types) |
| `github.svelte.ts` | GitHub Device Flow auth + token refresh, `connectionState` getter (5 states), `reconnect()`, repo picker, file browser (tree/content), branch list, index status, project selection. `_handleAuthError()` centralizes 401 detection across all methods |
| `refinement.svelte.ts` | Refinement sessions: turns, branches, suggestions, score progression |
| `preferences.svelte.ts` | Persistent user preferences loaded from backend |
| `toast.svelte.ts` | Toast notification queue with `addToast()` API |
| `routing.svelte.ts` | Derived routing state mirroring backend 5-tier priority chain. Reactive tier resolver |
| `clusters.svelte.ts` | Cluster state: paste detection (50-char delta, 300ms debounce), suggestion lifecycle (10s auto-dismiss), tree/stats, detail, template spawning, `StateFilter` + `filteredTaxonomyTree`, async `invalidateClusters()` with ghost-selection guard, seed batch progress (`seedBatchActive`/`seedBatchProgress`). Activity panel state: `activityEvents`, `activityOpen`, `pushActivityEvent()`, `toggleActivity()`, `loadActivity()` with JSONL history fallback |
| `domains.svelte.ts` | API-driven domain palette. `colorFor()` resolves domain→hex with keyword fallback. Invalidated on `domain_created`/`taxonomy_changed` SSE |
| `passthrough-guide.svelte.ts` | Passthrough workflow guide modal (visibility, "don't show again") |
| `sampling-guide.svelte.ts` | Sampling tier guide modal state |
| `internal-guide.svelte.ts` | Internal tier guide modal state |
| `guide-factory.svelte.ts` | Tier guide factory |
| `tier-onboarding.svelte.ts` | Tier onboarding flow state |
| `pattern-graph-guide.svelte.ts` | Pattern graph keyboard shortcuts/interaction hints modal |
| `update.svelte.ts` | Auto-update state: available version, changelog, dialog, restart progress, health polling. SSE-driven via `update_available`/`update_complete` events. `localStorage` persistence for detached HEAD warning dismissal |
| `sse-health.svelte.ts` | SSE connection health: owns EventSource lifecycle, latency tracking (rolling 100-event window, p50/p95/p99), 3-state degradation detection (healthy/degraded/disconnected), exponential backoff reconnection (1s-16s cap, 10 attempts, ±20% jitter), 90s staleness detection. StatusBar indicator reads `connectionState` and `tooltipText` |

## Component layout

```
src/lib/components/
  layout/       # ActivityBar, Navigator, ClusterNavigator, EditorGroups, Inspector, StatusBar
  editor/       # PromptEdit, ForgeArtifact, PatternSuggestion, PassthroughView
  taxonomy/     # SemanticTopology, TopologyControls (diegetic UI — auto-hide controls,
                # right-edge hover zone, Q key metrics, inline hint card),
                # TopologyRenderer, TopologyData (state filter graph dimming),
                # TopologyInteraction, TopologyLabels, TopologyWorker (5-force simulation),
                # ActivityPanel (mission control terminal — severity-driven rows, path
                # accent rails, auto-hide cluster links, expandable context cards),
                # SeedModal (batch seeding modal — agent selector, progress bar, result card)
  refinement/   # RefinementTimeline, RefinementTurnCard, SuggestionChips,
                # BranchSwitcher, ScoreSparkline, RefinementInput
  shared/       # CommandPalette, DiffView, Logo, MarkdownRenderer, PassthroughGuide,
                # SamplingGuide, InternalGuide, TierGuide, TierBadge,
                # ProviderBadge, ScoreCard, Toast, UpdateBadge
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
| `optimization_status/score_card/start` | Routed through `forgeStore.handleExternalEvent()` (single code path for MCP + web) |
| `feedback_submitted` | Inspector feedback state sync + `editorStore.cacheFeedback()` |
| `refinement_turn` | Refinement timeline update + `refinementStore.reloadTurns()` for cross-tab sync |
| `strategy_changed` | Strategy list refresh |
| `taxonomy_changed` | Cluster/domain store invalidation, topology re-render. Also fires with `trigger: "project_created"` on repo link |
| `taxonomy_activity` | `clustersStore.pushActivityEvent()` — real-time feed to ActivityPanel |
| `routing_state_changed` | Routing store update, tier availability toasts |
| `domain_created` | Domain store invalidation |
| `seed_batch_progress` | `clustersStore.updateSeedProgress()` (persistent) + DOM CustomEvent for SeedModal. StatusBar shows progress when modal closed |
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
- **Paste detection**: clusters store watches 50-char deltas with 300ms debounce, auto-dismisses suggestions after 10s
- **Toggle safety**: disabled conditions prefixed with `!currentValue &&` — toggle already ON is always interactive
- **Routing reactivity**: frontend is purely reactive — receives `routing_state_changed` SSE, never makes routing decisions
- **GitHub connection state**: `githubStore.connectionState` getter (5 states) replaces ad-hoc null checks. `reconnect()` clears `linkedRepo` before Device Flow so template falls to auth branch. `_handleAuthError()` centralizes 401 detection. Tab selection persisted to `localStorage` key `synthesis:github_tab`
- **Cross-component SSE**: MCP pipeline events route through `forgeStore.handleExternalEvent()` (single code path). Refinement turns propagated to `refinementStore.reloadTurns()`. Seed batch progress persisted in `clustersStore` (survives modal close)
- **Per-tab feedback caching**: `editorStore.cacheFeedback()`/`activeFeedback` getter prevents feedback state loss on tab switch
- **Version**: `src/lib/version.ts` imports from root `version.json` — auto-synced by `scripts/sync-version.sh`, never edit manually
