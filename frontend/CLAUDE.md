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
| `github.svelte.ts` | GitHub OAuth auth + repo link state |
| `refinement.svelte.ts` | Refinement sessions: turns, branches, suggestions, score progression |
| `preferences.svelte.ts` | Persistent user preferences loaded from backend |
| `toast.svelte.ts` | Toast notification queue with `addToast()` API |
| `routing.svelte.ts` | Derived routing state mirroring backend 5-tier priority chain. Reactive tier resolver |
| `clusters.svelte.ts` | Cluster state: paste detection (50-char delta, 300ms debounce), suggestion lifecycle (10s auto-dismiss), tree/stats, detail, template spawning, `StateFilter` + `filteredTaxonomyTree`, SSE invalidation. Activity panel state: `activityEvents`, `activityOpen`, `pushActivityEvent()`, `toggleActivity()`, `loadActivity()` with JSONL history fallback |
| `domains.svelte.ts` | API-driven domain palette. `colorFor()` resolves domain→hex with keyword fallback. Invalidated on `domain_created`/`taxonomy_changed` SSE |
| `passthrough-guide.svelte.ts` | Passthrough workflow guide modal (visibility, "don't show again") |
| `sampling-guide.svelte.ts` | Sampling tier guide modal state |
| `internal-guide.svelte.ts` | Internal tier guide modal state |
| `guide-factory.svelte.ts` | Tier guide factory |
| `tier-onboarding.svelte.ts` | Tier onboarding flow state |

## Component layout

```
src/lib/components/
  layout/       # ActivityBar, Navigator, ClusterNavigator, EditorGroups, Inspector, StatusBar
  editor/       # PromptEdit, ForgeArtifact, PatternSuggestion, PassthroughView
  taxonomy/     # SemanticTopology, TopologyControls, TopologyRenderer, TopologyData,
                # TopologyInteraction, TopologyLabels, TopologyWorker (5-force simulation),
                # ActivityPanel (collapsible bottom panel — decision event feed),
                # SeedModal (batch seeding modal — agent selector, progress bar, result card)
                # Candidate nodes: 40% opacity, no billboard labels, filter tab with count badge
  refinement/   # RefinementTimeline, RefinementTurnCard, SuggestionChips,
                # BranchSwitcher, ScoreSparkline, RefinementInput
  shared/       # CommandPalette, DiffView, Logo, MarkdownRenderer, PassthroughGuide,
                # SamplingGuide, InternalGuide, TierGuide, TierBadge,
                # ProviderBadge, ScoreCard, Toast
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
| `optimization_created/analyzed/failed` | History auto-refresh, toast notifications |
| `feedback_submitted` | Inspector feedback state sync |
| `refinement_turn` | Refinement timeline update |
| `strategy_changed` | Strategy list refresh |
| `taxonomy_changed` | Cluster/domain store invalidation, topology re-render |
| `taxonomy_activity` | `clustersStore.pushActivityEvent()` — real-time feed to ActivityPanel |
| `routing_state_changed` | Routing store update, tier availability toasts |
| `domain_created` | Domain store invalidation |
| `seed_batch_progress` | Dispatched as `seed-batch-progress` DOM CustomEvent for SeedModal progress bar |

Fixed 60s health polling for StatusBar display only — no routing decisions from frontend.

## Key patterns

- **Svelte 5 runes**: `$state`, `$derived`, `$effect` for reactive state throughout
- **Store pattern**: files export reactive class instances (not Svelte 4 writable stores)
- **Session persistence**: forge store saves `last_trace_id` to `localStorage`; page refresh restores from DB
- **Paste detection**: clusters store watches 50-char deltas with 300ms debounce, auto-dismisses suggestions after 10s
- **Toggle safety**: disabled conditions prefixed with `!currentValue &&` — toggle already ON is always interactive
- **Routing reactivity**: frontend is purely reactive — receives `routing_state_changed` SSE, never makes routing decisions
- **Version**: `src/lib/version.ts` imports from root `version.json` — auto-synced by `scripts/sync-version.sh`, never edit manually
