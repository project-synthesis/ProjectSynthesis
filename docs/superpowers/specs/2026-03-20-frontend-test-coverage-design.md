# Frontend Test Coverage — 90% Target

## Goal

Bring the SvelteKit frontend from near-zero test coverage (~1 test file, 84 lines) to ≥90% line coverage using Vitest + @testing-library/svelte, with co-located test files.

## Current State

- **1 test file**: `src/lib/components/patterns/utils/layout.test.ts` (84 lines)
- **Vitest + jsdom + @vitest/coverage-v8** already configured in `package.json`
- **~12K lines** across ~65 source files
- **0% coverage** on stores, API client, utilities, and all components

## Architecture

### Dependencies

Add to `devDependencies`:
- `@testing-library/svelte` — component rendering + user-centric queries (`getByRole`, `getByText`)
- `@testing-library/jest-dom` — DOM matchers (`toBeInTheDocument`, `toHaveTextContent`)
- `@testing-library/user-event` — realistic user interaction simulation

### Vitest Configuration

Update `vite.config.ts` test block:
- `environment: 'jsdom'`
- `setupFiles: ['./src/lib/test-setup.ts']`
- `include: ['src/**/*.test.ts']`
- `coverage.provider: 'v8'`
- `coverage.include: ['src/lib/**/*.ts', 'src/lib/**/*.svelte']`
- `coverage.exclude: ['**/*.test.ts', '**/test-*.ts', 'src/lib/content/**']`
- `coverage.thresholds.lines: 90`

### Shared Test Utilities

`src/lib/test-setup.ts`:
- Import `@testing-library/jest-dom`
- Mock `EventSource` (jsdom doesn't provide it)
- Mock `navigator.clipboard.writeText`

`src/lib/test-utils.ts`:
- Re-export `@testing-library/svelte` render/screen/cleanup
- Mock response factories: `mockHealthResponse()`, `mockOptimizationResult()`, `mockHistoryItem()`, `mockPatternFamily()`, `mockRefinementTurn()`, etc.
- `mockFetch(responses)` — configurable fetch mock that returns canned responses by URL pattern
- `createFreshStore()` helpers for stores that are exported as singletons

### Mocking Strategy

- **Mock at the boundary**: `fetch`, `EventSource`, `clipboard` — test everything above with real implementations
- **Stores**: test real instances with mocked API underneath. Fresh instance per test to avoid state leakage
- **Components**: render with real stores (pre-set state) + mocked fetch. Assert on DOM, not implementation
- **Cross-store coordination**: test with real stores imported together (forge ↔ editor ↔ patterns)
- **SSE streams**: mock `fetch` to return `ReadableStream` for `streamSSE` tests; mock `EventSource` class for `connectEventStream` tests

### File Organization

Co-located with source (consistent with existing `layout.test.ts`):
```
src/lib/
  utils/formatting.ts
  utils/formatting.test.ts        ← co-located
  stores/forge.svelte.ts
  stores/forge.svelte.test.ts     ← co-located
  components/shared/DiffView.svelte
  components/shared/DiffView.test.ts  ← co-located
```

## Test Tiers

### Tier 1 — Pure Utility Functions (~130 lines, ~98% target)

Trivial pure functions — full branch coverage.

| File | Key test cases |
|------|---------------|
| `utils/formatting.ts` | `formatScore` edge cases (null, 0, boundaries), `formatDelta` (+/-/zero), `truncateText` (under/at/over limit), `copyToClipboard` (clipboard API + fallback) |
| `utils/patterns.ts` | `domainColor` for each domain + unknown, `scoreColor` threshold boundaries |
| `utils/strategies.ts` | `strategyListToOptions` transforms correctly, prepends 'auto', handles empty list |
| `utils/dimensions.ts` | `DIMENSION_LABELS` completeness, `getPhaseLabel` for each phase + unknown |
| `utils/mcp-tooltips.ts` | Both tooltip functions with all disabled-state permutations |

### Tier 2 — Stores (~1,048 lines, ~92% target)

Reactive class-based stores with Svelte 5 runes. Test state transitions and side effects.

| Store | Key test cases |
|-------|---------------|
| `toast.svelte.ts` (55 lines) | `addToast` queuing, max 3 visible, auto-dismiss timeout, action type mapping |
| `editor.svelte.ts` (156 lines) | `openTab` (new + existing), `closeTab` (active vs inactive), `setActive`, result cache hit/miss, `openResult`/`openDiff`/`openMindmap` tab type creation |
| `preferences.svelte.ts` (86 lines) | `init` loads from API, `update` patches and persists, `setModel`/`setPipelineToggle`/`setDefaultStrategy` individual setters |
| `github.svelte.ts` (86 lines) | `checkAuth` sets state, `login` redirects, `logout` clears, `loadRepos`/`linkRepo`/`unlinkRepo` API interactions |
| `patterns.svelte.ts` (147 lines) | Paste detection (50-char delta threshold, debounce), suggestion auto-dismiss (10s), `applySuggestion`/`dismissSuggestion`, `loadGraph`/`selectFamily`/`invalidateGraph` |
| `refinement.svelte.ts` (137 lines) | `init` loads versions, `refine` SSE flow, `rollback` branch fork, `reset` clears, score progression getter computation |
| `forge.svelte.ts` (310 lines) | Status machine (idle→analyzing→optimizing→scoring→complete), `handleEvent` for each SSE event type, `reconnect` from localStorage, `submitFeedback` API call + state update, `submitPassthrough` flow, error state handling, cross-store coordination with editor/patterns |

### Tier 3 — API Client (~576 lines, ~90% target)

Mock `fetch` globally. Test request construction, response parsing, and error handling.

| File | Key test cases |
|------|---------------|
| `api/client.ts` | `apiFetch` success/error/network-failure, `tryFetch` returns null on error, `ApiError` construction, each endpoint function (correct URL + method + body), `streamSSE` event parsing (data/error/complete callbacks), `connectEventStream` EventSource lifecycle (open/message/error), `optimizeSSE`/`refineSSE` SSE wrappers |
| `api/patterns.ts` | Each function (`matchPattern`, `getPatternGraph`, `listFamilies`, `getFamilyDetail`, `renameFamily`, `searchPatterns`, `getPatternStats`) — correct request construction + response typing |

### Tier 4a — Complex Components (~3,200 lines, ~85% target)

Behavioral tests using `@testing-library/svelte`. Focus on user-visible behavior, not implementation.

| Component | Key test cases |
|-----------|---------------|
| `Toast.svelte` | Renders toast queue, dismiss click removes toast, action type styling |
| `ScoreCard.svelte` | Renders 5 dimensions, shows delta when provided, handles null scores |
| `ProviderBadge.svelte` | Displays provider name, tier badge, handles null provider |
| `DiffView.svelte` | Renders original + optimized side-by-side, highlights additions/deletions, copy button |
| `CommandPalette.svelte` | Opens on keyboard shortcut, filters items, executes action, closes on escape |
| `MarkdownRenderer.svelte` | Renders headings, code blocks, lists, inline formatting, handles empty input |
| `PatternSuggestion.svelte` | Displays family name + meta-patterns, Apply button dispatches event, Skip dismisses |
| `SuggestionChips.svelte` | Renders chip per suggestion, click dispatches selection event |
| `ScoreSparkline.svelte` | Renders SVG/canvas with score points, handles empty data |
| `BranchSwitcher.svelte` | Lists branches, active branch highlighted, selection dispatches event |
| `RefinementInput.svelte` | Text input binding, submit button calls refine, disabled during loading |
| `ActivityBar.svelte` | Renders activity icons, click changes active panel, highlights current |
| `StatusBar.svelte` | Shows provider name, MCP connection status, pattern count, error count, routing tier |
| `EditorGroups.svelte` | Renders tab bar, active tab content, close tab button, tab switching |
| `PromptEdit.svelte` | Textarea input, character count updates, submit button triggers forge, paste detection |

### Tier 4b — Navigator & Inspector (1,689 lines combined, proper behavioral tests)

These are the two largest components with significant branching logic. They get deeper testing beyond smoke tests.

**Navigator.svelte (984 lines):**
- History list rendering with optimization items
- Sort toggling (date, score, strategy)
- Filter by strategy/domain
- Pagination (load more)
- Preferences panel: model selection dropdowns, pipeline toggle switches
- API key management (set/delete flow)
- Real-time event handling (optimization_created refreshes list)
- Empty state rendering

**Inspector.svelte (705 lines):**
- Family detail display (name, domain, task type, member count, usage count, avg score)
- Meta-patterns list rendering
- Linked optimizations list with click-to-open
- Inline rename (edit mode toggle, save, cancel)
- Tab switching between graph view and family detail
- Empty state when no family selected

### Tier 4c — Smoke Tests for Remaining Components (~17 components)

Minimal "renders without crashing" + key element assertions (~5-10 lines each). Targets ~40-60% coverage per file.

Landing components: `Navbar`, `Footer`, `ContentPage`, `HeroSection`, `CardGrid`, `Timeline`, `MetricBar`, `CodeBlock`, `StepFlow`, `ProseSection`

Editor components: `ForgeArtifact`, `PassthroughView`

Refinement components: `RefinementTimeline`, `RefinementTurnCard`

Pattern components: `RadialMindmap`

Shared components: `Logo`

## Excluded from Coverage

- `src/lib/content/**` — static string exports (changelog, privacy, terms, security). Zero logic to test.
- `src/routes/**` — thin composition layers. The logic they delegate to is tested via stores and components.

## Estimated Test Count

| Tier | Est. Tests |
|------|-----------|
| Tier 1 — Utilities | ~50 |
| Tier 2 — Stores | ~120 |
| Tier 3 — API Client | ~60 |
| Tier 4a — Complex Components | ~80 |
| Tier 4b — Navigator + Inspector | ~40 |
| Tier 4c — Smoke Tests | ~20 |
| **Total** | **~370** |

## Success Criteria

- `npm run test` passes with 0 failures
- `npx vitest run --coverage` reports ≥90% line coverage on `src/lib/**`
- No test depends on implementation details (internal state, CSS classes for logic)
- All tests run in <30s on CI
- Mocking is confined to boundaries (fetch, EventSource, clipboard)
