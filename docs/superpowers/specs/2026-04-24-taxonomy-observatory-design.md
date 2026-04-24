# Taxonomy Observatory — First Iteration (Design Spec)

**Date:** 2026-04-24
**Status:** Draft r2 — independent spec-reviewer pass complete (2026-04-24). All 3 BLOCKERs (path-color contract, GlobalPattern containment strategy, domain_color_hex sourcing) and 5 MAJORs (period-selector UX, SSE row-animation, activity history range, UTC consistency, mid-session dissolution) addressed inline; 7 MINORs addressed inline. Pending user approval.
**Scope:** Frontend (one new view + stores) + two backend read-only endpoint changes: one new (`GET /api/taxonomy/pattern-density`) and one extension (`since`/`until` range variant on `GET /api/clusters/activity/history`). No schema migration. No service-layer changes.
**ROADMAP link:** `docs/ROADMAP.md` → "Taxonomy observatory — live domain & sub-domain lifecycle dashboard" (currently in the Immediate bucket, marked Exploring)

---

## Problem

The taxonomy engine generates a rich signal stream: domain creation / dissolution decisions, sub-domain discovery, readiness cascades, pattern promotion / demotion / retirement, cluster split / merge / retire events. All of it flows through the `TaxonomyEventLogger` singleton (ring buffer + JSONL persistence + SSE bridge). All of it is surfaced in narrow slices today:

- `ActivityPanel.svelte` (823 lines) — real-time event feed with severity rows and path accent rails.
- `DomainReadinessPanel.svelte` (556 lines) — per-domain stability + emergence widgets.
- `DomainReadinessSparkline.svelte` — hourly-bucketed readiness trendline.
- `StatusBar` — a single Q_system digit.

What's missing is the **cross-cutting view**: a single surface where a user can see (a) the evolutionary history of their taxonomy over time, (b) which domains are healthy and which are in transition, and (c) where the pattern library is dense vs thin. This is the Taxonomy Observatory the ROADMAP has listed in Immediate since v0.3.38.

Every backend signal needed to build it is shipped. This spec scopes the **first iteration** — three panels that compose existing data sources with a brand-compliant visual treatment. Deferred for v2 include the Tamagotchi-style steering suggestions, cross-domain pattern-flow visualization, and dynamic-vocabulary transparency views.

---

## Goals

1. **Single-surface view** — `/app` route gains a new tab or modal that renders three panels at once without page navigation.
2. **Historical lens on the taxonomy** — a timeline of lifecycle decisions sourced from `data/taxonomy_events/*.jsonl` files + live ring buffer. The canonical `(op, decision)` vocabulary lives in `ActivityPanel.svelte:32-67` — the Timeline reuses it (see §Panel 1 filter bar for the op-family groupings).
3. **Current-state lens** — per-domain readiness aggregate (reuses `DomainStabilityMeter` + `SubDomainEmergenceList` with minimal wrapping).
4. **Pattern-density lens** — visualize where the pattern library is concentrated vs sparse so users see where their taxonomy has learned the most.
5. **Brand-guideline compliance** — no ambient animation, 1 px contours only, chromatic encoding per the palette, ultra-compact density (20 px data rows, 24 px section headers).
6. **Read-only surface** — no actions mutate state. The Observatory is a lens, not a control panel. Future iterations may add `steering_suggestion_dismiss` write actions.

## Non-goals

- **Tamagotchi / buddy framing** — the ROADMAP's v2 vision includes "the taxonomy as a living system you cultivate." Deferred. The v1 visual language is cyberpunk-instrument, not pet-care.
- **Dynamic steering suggestions** — "SaaS pricing at 17% — needs 40% to form a sub-domain, 23 more prompts to get there." Deferred. The readiness data is already surfaced via `SubDomainEmergenceList`; explicit call-to-action copy belongs in v2.
- **Cross-domain pattern flow visualization** — how GlobalPatterns propagate between domains. Deferred — the inline topology's injection edges already convey the shape, and a dedicated view needs its own interaction model.
- **Vocabulary transparency** — showing which qualifier vocabularies are active per domain (static / Haiku-generated / TF-IDF) + refresh timestamps. The `qualifier_vocab` health field exposes the aggregate; per-domain inspection is v2 scope.
- **Write actions** — no "dismiss this steering tip" or "pin this panel" actions. Read-only surface.
- **Mobile / narrow viewport** — desktop-first per `frontend/CLAUDE.md`.

---

## Architecture

### High-level composition

```
                       TaxonomyObservatory.svelte  (route tab: /app → Observatory)
                                      │
        ┌─────────────────────────────┼─────────────────────────────┐
        │                             │                             │
        ▼                             ▼                             ▼
DomainLifecycleTimeline.svelte  DomainReadinessAggregate.svelte  PatternDensityHeatmap.svelte
        │                             │                             │
        ▼                             ▼                             ▼
  GET /api/clusters/           GET /api/domains/             GET /api/taxonomy/
    activity (ring buffer)       readiness (existing)           pattern-density (NEW)
  GET /api/clusters/           DomainStabilityMeter
    activity/history             SubDomainEmergenceList
    (JSONL by date)
        │
        └── subscribes to
            taxonomy_activity
            SSE event (live feed)
```

Each panel is a self-contained Svelte component with its own store binding. The Observatory root is a thin layout shell — no cross-panel state, no shared reducers. Panels can be relocated into other views later (for instance, the Timeline could be embedded in Inspector's cluster-detail view) without refactor.

### Data sources

**All from existing surfaces except the new pattern-density aggregator.**

| Panel | Data source | Status |
|-------|-------------|--------|
| Lifecycle Timeline | `GET /api/clusters/activity` (live ring buffer, 500 events) + `GET /api/clusters/activity/history?date=YYYY-MM-DD` (JSONL-backed) + `taxonomy_activity` SSE | Shipped |
| Readiness Aggregate | `GET /api/domains/readiness` + re-rendered `DomainStabilityMeter` and `SubDomainEmergenceList` | Shipped; component reuse |
| Pattern Density Heatmap | `GET /api/taxonomy/pattern-density` (NEW; one aggregated query) | Backend delta (see §API Contract) |

### Components

| File | Role |
|------|------|
| `frontend/src/lib/components/taxonomy/TaxonomyObservatory.svelte` | **new** — layout shell: three-panel grid + a period selector (24h / 7d / 30d) shared across panels |
| `frontend/src/lib/components/taxonomy/DomainLifecycleTimeline.svelte` | **new** — vertical time-stamped event list with chromatic path/op encoding |
| `frontend/src/lib/components/taxonomy/DomainReadinessAggregate.svelte` | **new** (thin wrapper) — composes `DomainStabilityMeter` + `SubDomainEmergenceList` per domain in a grid layout |
| `frontend/src/lib/components/taxonomy/PatternDensityHeatmap.svelte` | **new** — 2D grid: domain rows × pattern-tier columns (MetaPattern count, MetaPattern avg score, GlobalPattern count, cross-cluster injection rate) with chromatic cell fills |
| `frontend/src/lib/stores/observatory.svelte.ts` | **new** — shared period selector state + SSE subscription dispatcher. No derived state beyond what the three panels already compute. |
| `frontend/src/lib/api/observatory.ts` | **new** — typed `fetchPatternDensity()` wrapper |
| `backend/app/routers/taxonomy_insights.py` | **new** — `GET /api/taxonomy/pattern-density` aggregator |
| `backend/app/schemas/taxonomy_insights.py` | **new** — Pydantic models: `PatternDensityRow`, `PatternDensityResponse` |
| `frontend/src/routes/app/+page.svelte` | Modified — adds an Observatory tab to the existing editor-layout tablist |

### Where the Observatory lives in the UI

The `/app` route today is a workbench with tabs (prompt editor, result, diff, mindmap). Adding a new persistent top-level view via the existing tablist keeps the interaction model consistent — users click a tab, see the Observatory, click back to the prompt editor. No full-screen modal, no side-panel overlay.

Alternative considered: a dedicated `/app/observatory` route. Rejected because (a) the taxonomy surface is cross-cutting to the user's current prompt, not a separate product area, and (b) the `/app` route already manages the SSE subscription — routing away and back adds re-subscription churn.

### Period selector

A period selector (`24h / 7d / 30d`) is rendered **inside each panel header that actually consumes it** — not on the Observatory shell. State lives in `observatory.svelte.ts`, persisted to `localStorage['synthesis:observatory_period']`, default `7d`. A single selection still applies to all period-consuming panels (single source of truth); the UI change is placement, not scope.

- **Timeline** — period selector in its header, bound to `since` on the activity history fetch.
- **Readiness Aggregate** — **no period selector in this panel's header**. The readiness data is a current-state snapshot. A one-line legend in the Observatory shell header ("Readiness reflects current state — period applies to Timeline and Pattern Density") covers the consistency story. This avoids a control that visibly does nothing in one of the three panels (UX debt flagged in the first-round review).
- **Pattern Density** — period selector in its header, bound to the aggregator query's window.

All dates are computed in **UTC**. The frontend derives the date strings via `new Date().toISOString().slice(0, 10)` and always emits UTC ISO dates to the backend. JSONL files under `data/taxonomy_events/` are already named `decisions-YYYY-MM-DD.jsonl` in UTC per `event_logger.py`.

---

## API Contract Delta

### Extension to the activity history endpoint

`GET /api/clusters/activity/history` (`backend/app/routers/clusters.py:413-438`) currently accepts `?date=YYYY-MM-DD` plus `limit` + `offset`. One call = one day of JSONL. The Observatory's 30d period selector would fan-out to 30 round-trips per toggle — guaranteed to trip the existing rate limit on first use.

**Additive change** — the endpoint gains two optional query params:

- `since: str | None` (UTC ISO date, `YYYY-MM-DD`)
- `until: str | None` (UTC ISO date, `YYYY-MM-DD`, inclusive)

When both are present, the handler fans out internally over the JSONL files between those dates (inclusive) and concatenates in reverse chronological order. `limit` + `offset` continue to operate on the flattened result. The existing `date` param is retained — present → single-day mode (backwards compatible).

**Validation rules**:
- Mutually exclusive with `date` — 422 if both `date` and `since`/`until` are set.
- `until - since` ≤ 30 days — 422 otherwise.
- If only `since` is set → `until = today_utc`.

Fan-out is **sequential** inside the handler (N ≤ 30 file reads, local disk, typical < 50 ms total). No concurrency needed. Each missing JSONL file is skipped (not an error — pre-install days have no events).

Test coverage: add 5 cases to `test_clusters_router.py` — range happy-path, edge dates, missing-file handling, mutually-exclusive-with-date, oversized-window.

### New pattern-density endpoint

**`GET /api/taxonomy/pattern-density`**

Returns per-domain pattern-density metrics as a flat list. Aggregates at request time — no caching layer (query is fast: `COUNT(*) GROUP BY cluster.domain` against `MetaPattern` + `GlobalPattern` tables, typical < 20 ms).

```python
# backend/app/schemas/taxonomy_insights.py
class PatternDensityRow(BaseModel):
    domain_id: str                           # cluster node ID (domain_label alone is non-unique across projects)
    domain_label: str
    # NB: no domain_color_hex — frontend resolves via domainStore.colorFor() per frontend/CLAUDE.md.
    #     Sending hex from the backend would duplicate the client-side resolution and diverge on re-theme.
    cluster_count: int                       # live active+mature+candidate clusters in this domain
    meta_pattern_count: int                  # MetaPattern rows under this domain's clusters
    meta_pattern_avg_score: float | None     # mean of MetaPattern.avg_cluster_score or null
    global_pattern_count: int                # GlobalPattern rows with any of this domain's cluster IDs in source_cluster_ids
    cross_cluster_injection_rate: float      # 0.0–1.0: injection events in window / total injections
    period_start: datetime                   # window lower bound (UTC)
    period_end: datetime                     # window upper bound (UTC)

class PatternDensityResponse(BaseModel):
    rows: list[PatternDensityRow]
    total_domains: int
    total_meta_patterns: int
    total_global_patterns: int
```

**Query**: `?period=24h|7d|30d` (default `7d`). Rate-limited via the canonical `RateLimit(lambda: settings.DEFAULT_RATE_LIMIT)` pattern used by `/api/clusters/stats` + siblings (`clusters.py:367,451`) — not a bespoke `30/min` idiom.

**Aggregation logic** (`services/taxonomy_insights.py` sibling service, single function — not worth a class):

1. For each active domain node (`state='domain'` AND `archived_at IS NULL`):
   - Count children with `state IN ('active', 'mature', 'candidate')` → `cluster_count`.
   - Count `MetaPattern` rows whose `cluster_id` points to a child → `meta_pattern_count`.
   - Compute `MetaPattern.avg_cluster_score` mean or `None` if empty.
   - Count `GlobalPattern` rows with any of this domain's cluster IDs in `source_cluster_ids` — **Python-side containment**, not a SQLite JSON-operator query. Fetch all `(id, source_cluster_ids)` tuples from `GlobalPattern` (≤ 500 rows per `GlobalPattern` cap) into a list, then walk it once per domain intersecting against that domain's cluster-ID set. The spec explicitly rejects SQLite's `JSON_EACH` / `JSON_CONTAINS` path — those operators aren't used anywhere else in the codebase, aren't indexed, and don't portably survive a future PostgreSQL migration.
   - Compute `cross_cluster_injection_rate`: within `[period_start, period_end]` UTC, count `OptimizationPattern` rows where `relationship IN ('injected', 'global_injected')` (note: column is `relationship`, not `relationship_type` — see `models.py:265`) and `cluster_id` belongs to this domain; divide by all injection-relationship events in the window. Returns `0.0` when the window has no injection events.
2. Sort by `meta_pattern_count` desc, then `cluster_count` desc.
3. Return.

**Performance.** The Python-side containment is O(D × G) where D = domain count (≤ 30 per `DOMAIN_COUNT_CEILING`) and G ≤ 500. 15,000 set-membership checks per request. Verified during implementation: should stay under 50 ms for a typical deployment. The verification plan includes an explicit measurement gate — if profiling shows this exceeds 100 ms, replace with an eagerly-built `dict[cluster_id → GlobalPattern[]]` cache invalidated on `GlobalPattern` state-change events.

**Error handling**: `503` on `OperationalError` (matches the activity router pattern). `500` on anything else with server-log capture.

### No other backend changes

The domain readiness router is already present. The activity router is present but gains the `since` / `until` range query-params above. No schema migration, no new SQLAlchemy models.

---

## UI Design

### Brand compliance

Every design choice below maps to a brand rule from `.claude/skills/brand-guidelines/SKILL.md`.

| Brand rule | Application |
|------------|-------------|
| Zero-effects directive | No `box-shadow` with blur/spread; no `text-shadow`; no pulse animations. Active/hovered cells use 1 px inset contours (`inset 0 0 0 1px`). |
| Neon Tube Model | All borders 1 px uniform. Heatmap cell fills are opaque at their tier's opacity tier — never gradients, never radial fades. |
| Chromatic encoding | Lifecycle Timeline: path color matches the shipped `ActivityPanel.svelte:70-75` contract — `hot=neon-red, warm=neon-yellow, cold=neon-cyan`. Readiness Aggregate: tier color (critical=red, guarded=yellow, healthy=green). Pattern Density: density tier (low=dim, medium=cyan, high=purple for elevated/GlobalPattern). Reuse `pathColor()` from ActivityPanel — do not re-derive. |
| Ultra-compact density | Observatory header 24 px; panel headers 24 px; data rows 20 px; padding `p-1.5` (6 px). No `gap-2`, no `p-2` anywhere. |
| Space Grotesk / Geist Mono discipline | Headings Syne 11 px 700 uppercase 0.1em tracking. Timeline timestamps Geist Mono 10 px. Heatmap cell values Geist Mono 10 px (tabular figures). Descriptive text Space Grotesk 11 px. |
| Motion | Panel entry uses `navFade` preset (180 ms, spring). Period-selector button toggle is instant. SSE-driven Timeline row prepend uses `fade-in` 150 ms — bounded to 1 animated row at a time (the newest). |
| Reduced motion | `@media (prefers-reduced-motion: reduce)` collapses all transitions to 0.01 ms. |

### Layout

Three-panel grid, sized for a 1400 px wide workbench:

```
┌────────────────────────────────────────────────────────────────────────────────┐
│ OBSERVATORY                                   [ 24h ] [ 7d ] [ 30d ]           │  ← 28 px shell header
├────────────────────────────────────────┬───────────────────────────────────────┤
│ LIFECYCLE TIMELINE                     │  DOMAIN READINESS                     │
│                                        │                                       │
│ 18:42  cluster_split  backend          │  ● backend   ████▒▒▒▒  healthy / warm │
│ 17:11  global_pattern_promoted         │  ● frontend  ████████  healthy / emer │
│ 14:03  sub_domain_created  saas:pricing│  ● security  ██▒▒▒▒▒▒  critical / gua │
│ …                                      │  …                                    │
│                                        │                                       │
├────────────────────────────────────────┴───────────────────────────────────────┤
│ PATTERN DENSITY                                                                │
│                                                                                │
│             clusters  meta  avg score  global  x-cluster inj. rate             │
│ backend       12      28    7.8          3       31%                           │
│ frontend       8      19    7.6          2       22%                           │
│ database       5       9    7.1          1       12%                           │
│ security       4       7    7.3          0        8%                           │
│ general        2       1    6.5          0        2%                           │
│                                                                                │
└────────────────────────────────────────────────────────────────────────────────┘
```

- **Shell header** (28 px): Observatory title + period selector (three 20 px buttons, active state = 1 px neon-cyan contour + `color-mix(cyan 8%, transparent)` background, inactive = 1 px `border-subtle` + no background).
- **Left column** (60% width): Lifecycle Timeline. Full-height vertical scrolling list.
- **Right column top half** (40% width): Readiness Aggregate — one row per domain, sorted by stability tier (critical → guarded → healthy).
- **Bottom full-width strip**: Pattern Density — 5-column data grid. Column headers in Syne 10 px uppercase.

### Panel 1: Lifecycle Timeline

Vertical list of taxonomy lifecycle events ordered newest-first. Each row is 20 px with:

- **Left**: 60 px timestamp (Geist Mono 10 px, `HH:MM` or `Apr 22 14:03` for events past today).
- **Middle**: 80 px path badge (hot / warm / cold, chromatic encoding per the brand palette).
- **Right**: event description — 11 px Space Grotesk — combining `op` + `decision` + primary context field (e.g. "sub_domain_created · saas:pricing"; "cluster_split · 8 members → 2×4"). Truncated with ellipsis if over 60 chars.
- **Hover**: 1 px neon-cyan left contour + 18 px `:after` expand glyph ("…") reveals the full context payload in an inline expansion row (matching `ActivityPanel.svelte`'s existing pattern — reuse, don't reinvent).

**SSE hookup**: subscribes to `taxonomy_activity` events via `clustersStore.activityEvents` (already wired by `routes/app/+page.svelte:132-133`, single EventSource managed by `sseHealthStore`) — the new component reads the store directly, **no duplicate EventSource**. Events prepend instantly without a per-row fade animation. Rationale: `ActivityPanel.svelte` (the precedent the Timeline mirrors) has no row-entry animation either — it fades the *expansion card* on hover (`ctx-enter` at `ActivityPanel.svelte:777`) but never the rows themselves. Reusing that pattern avoids an animation-queue engineering problem under warm-path flood, and keeps brand motion discipline.

**Filter bar** (second row of the panel header, 24 px tall): three toggle chips matching `ActivityPanel.svelte:32-67`'s canonical filter grammar — path (`hot` / `warm` / `cold`), op-family (canonical pairings: `domain lifecycle` = `{op:"discover", decision ∈ {"domains_created","domains_dissolved"}}`; `cluster lifecycle` = `{op ∈ {"split","merge","retire"}}`; `pattern lifecycle` = `{op ∈ {"promote","demote","re_promote","retired","global_pattern","meta_pattern"}}`; `readiness` = `{op:"readiness"}` + `{op:"signal_adjuster"}`), and `errors_only`. Active state matches the period-selector chip grammar. Implementers: read the canonical mapping from `ActivityPanel.svelte` before duplicating — do not re-invent the taxonomy.

**Period filter applies.** Requires a **new range variant** of the history endpoint — see §API Contract Delta "Extension to the activity history endpoint" below — so that 30d selection is one request, not 30 round-trips (the original design had this as "future optimization"; live-blocker per first-round review, pulled into scope). Live ring-buffer events prepend from the SSE store.

### Panel 2: Domain Readiness Aggregate

A grid layout wrapping the existing components:

```
┌──────────────────────────────────────┐
│ ● backend                            │
│   Stability:  ████▒▒▒▒ (guarded)    │  ← DomainStabilityMeter (reused)
│   Emergence:  ██▒▒▒▒▒▒ 22% · 18/82  │  ← SubDomainEmergenceList header row
│     pricing   ███▒▒▒▒▒ (warming)    │     + first 3 emerging sub-domains
│     auth      ████▒▒▒▒ (warming)    │
│     api       ██▒▒▒▒▒▒ (cold)       │
└──────────────────────────────────────┘
```

One card per domain, ordered by `DomainReadinessPanel.sorted` derivation (critical stability first; within stability tier, smallest emergence gap). Card = 1 px `border-subtle` + 6 px domain-color dot in the header row. **No new primitives** — `DomainStabilityMeter.svelte` and `SubDomainEmergenceList.svelte` already render the meter / list rows; this panel just lays out one per domain in a CSS grid (`grid-template-columns: repeat(auto-fill, minmax(280px, 1fr))`).

Click a card → dispatches the existing `domain:select` `CustomEvent` so topology / inspector views can focus. Keeps parity with `DomainReadinessPanel`'s current behavior.

### Panel 3: Pattern Density Heatmap

Despite the name, this is rendered as a **data grid** — not a spatial heatmap. The "heat" channel is the opacity of the cell background, ranging from `/0` (empty) to `/22` (heavy density) proportional to the metric's percentile across all domains. The component file opens with an `@file` docstring noting `"Heatmap" = data grid with opacity-scaled domain-color row backgrounds.` so future contributors aren't misled by the name.

- **Columns** (fixed width, right-aligned values): `clusters`, `meta`, `avg score`, `global`, `x-cluster inj. rate`.
- **Rows**: one per domain, ordered by `meta_pattern_count` desc.
- **Row background**: `color-mix(in srgb, var(--domain-color) X%, transparent)` where X scales by the row's `meta_pattern_count / max_meta_pattern_count * 22`.
- **Cell value**: Geist Mono 10 px, tabular-figures. Empty cells show `—` in `text-dim`.
- **Hover row**: 1 px inset cyan contour + tooltip with the absolute counts + timestamp of the last update.

Empty states:
- All domains empty (fresh install) → centered Syne 11 px "Pattern library is empty. Run `POST /api/seed` or start optimizing prompts."
- Fetch failure → 1 px neon-red inset contour + error message + retry button.

---

## State model

```ts
// frontend/src/lib/stores/observatory.svelte.ts
period = $state<'24h' | '7d' | '30d'>('7d')              // persisted to localStorage
patternDensity = $state<PatternDensityRow[] | null>(null)
patternDensityLoading = $state(false)
patternDensityError = $state<string | null>(null)

async function refreshPatternDensity() { ... }            // debounced 1s on period change
$effect.root subscribes to period changes and invalidates

// Panels reuse existing stores — no duplication:
// - clustersStore.activityEvents (live SSE + initial fetch)
// - readinessStore.reports (shared with the existing DomainReadinessPanel)
```

The Observatory's own store is tiny — just the period selector + the one new data feed. Everything else reuses existing stores, which keeps the cross-panel SSE + invalidation story unchanged.

---

## Files touched

| File | Change | Rationale |
|------|--------|-----------|
| `frontend/src/lib/components/taxonomy/TaxonomyObservatory.svelte` | **new** (~160 lines) | Layout shell + period selector |
| `frontend/src/lib/components/taxonomy/DomainLifecycleTimeline.svelte` | **new** (~280 lines) | Timeline panel |
| `frontend/src/lib/components/taxonomy/DomainReadinessAggregate.svelte` | **new** (~120 lines) | Thin wrapper around existing components |
| `frontend/src/lib/components/taxonomy/PatternDensityHeatmap.svelte` | **new** (~200 lines) | Heatmap grid |
| `frontend/src/lib/components/taxonomy/TaxonomyObservatory.test.ts` | **new** (~80 lines, ≥ 6 cases) | Shell + period-selector coverage |
| `frontend/src/lib/components/taxonomy/DomainLifecycleTimeline.test.ts` | **new** (~160 lines, ≥ 10 cases) | Timeline behaviors |
| `frontend/src/lib/components/taxonomy/DomainReadinessAggregate.test.ts` | **new** (~80 lines, ≥ 5 cases) | Grid layout + store-driven rendering |
| `frontend/src/lib/components/taxonomy/PatternDensityHeatmap.test.ts` | **new** (~160 lines, ≥ 8 cases) | Heat-scaling + empty/error states |
| `frontend/src/lib/stores/observatory.svelte.ts` | **new** (~90 lines) | Period state + pattern-density fetch |
| `frontend/src/lib/stores/observatory.svelte.test.ts` | **new** (~120 lines, ≥ 7 cases) | Store invariants |
| `frontend/src/lib/api/observatory.ts` | **new** (~40 lines) | Typed client wrapper |
| `frontend/src/routes/app/+page.svelte` | Add Observatory tab + `TaxonomyObservatory` mount | Route integration |
| `backend/app/routers/taxonomy_insights.py` | **new** (~120 lines) | `GET /api/taxonomy/pattern-density` |
| `backend/app/schemas/taxonomy_insights.py` | **new** (~60 lines) | Pydantic models |
| `backend/app/services/taxonomy_insights.py` | **new** (~140 lines) | Aggregator function (Python-side GlobalPattern containment) |
| `backend/app/routers/clusters.py:413-438` | Extend `GET /api/clusters/activity/history` with `since` / `until` range params; retain `date` for backwards-compat | M3 launch-blocker fix |
| `backend/app/main.py` | Register the new `taxonomy_insights` router | Route wire-up |
| `backend/tests/test_taxonomy_insights_router.py` | **new** (~180 lines, ≥ 8 cases) | Endpoint + aggregator behavior |
| `backend/tests/test_clusters_router.py` | Extend with 5 new cases covering the `since` / `until` range variant (happy-path, edge dates, missing-file handling, mutually-exclusive-with-date, oversized-window) | Activity-history range regression guard |
| `docs/ROADMAP.md` | Move item from Immediate/Exploring to Planned (with spec link) | Progress tracking |

---

## Testing strategy

### Backend — `test_taxonomy_insights_router.py`

1. `test_pattern_density_returns_row_per_domain`: seed 3 domains with varying MetaPattern counts; assert 3 rows, each with correct `meta_pattern_count`.
2. `test_pattern_density_includes_domain_color`: seed ensures `domain_color_hex` is populated from `PromptCluster.color_hex`.
3. `test_pattern_density_global_pattern_counts_match_source_cluster_ids`: seed GlobalPattern with a specific domain's cluster IDs → assert `global_pattern_count` for that domain only.
4. `test_pattern_density_cross_cluster_injection_rate_respects_period`: seed injection events inside and outside the 7d window; assert rate reflects only in-window events.
5. `test_pattern_density_empty_taxonomy_returns_empty_rows`: fresh DB, no domains → `rows: []`, `total_domains: 0`.
6. `test_pattern_density_period_parsing`: invalid period → 422.
7. `test_pattern_density_ordered_by_meta_pattern_count_desc`: seeded rows; assert ordering.
8. `test_pattern_density_rate_limit`: 31st call in a minute → 429.

### Frontend — component tests

**DomainLifecycleTimeline (≥ 10 cases)**: mounts with empty state; renders 20 px rows with correct chromatic path badge; period selector changes fetch URL; SSE event prepends with fade; filter chip toggles hide events; expand-row reveals context payload; reduced-motion disables animation; error state renders retry; 24h / 7d / 30d fetches correct date ranges.

**DomainReadinessAggregate (≥ 7 cases)**: mounts with 0 domains → empty state; mounts with 3 domains → 3 cards rendered; cards sorted by stability tier; clicking a card dispatches `domain:select`; reduced-motion respected; mounts with 1 domain that has zero sub-domains → card renders without SubDomainEmergenceList empty-row churn; mid-session dissolution (domain becomes null between fetches) → clicking the stale card is a no-op (no dispatch).

**PatternDensityHeatmap (≥ 8 cases)**: renders header row; data rows present; empty value renders `—`; heat scaling proportional to `meta_pattern_count`; hover row shows tooltip; rows have no `role="button"` / `tabindex` / `cursor: pointer` (read-only assertion — concrete, not "does nothing"); error state renders; loading state has dim opacity on prior data.

**TaxonomyObservatory shell (≥ 6 cases)**: three panels mount; period selector toggles persist to localStorage; period change cascades to pattern-density fetch; mounts inside the existing tablist; Escape closes (if modal variant) / Tab closes (if tab variant).

**observatory.svelte.ts store (≥ 7 cases)**: initial period is `7d`; localStorage override on init; period change invalidates cache; fetch debounced 1 s; error state captured; `patternDensity` cleared on period change start; race protection (late responses from prior periods discarded).

### Manual verification

Preconditions: taxonomy has at least 3 active domains with members; 10+ MetaPatterns across clusters; 1+ GlobalPattern.

1. Open `/app` → click Observatory tab.
2. Confirm three panels render within 2 s.
3. Switch period to 24h → lifecycle timeline re-fetches; heatmap updates.
4. Pan a real prompt in another tab → SSE event prepends to timeline with fade.
5. Click a domain card in readiness aggregate → topology in other views focuses on it.
6. Hover a heatmap row → tooltip shows absolute counts.
7. Reload page → period stays at whatever was selected.
8. Turn on `prefers-reduced-motion` in dev tools → all transitions collapse to 0.01 ms.

---

## Rollout

Single PR. No feature flag. Rationale:

- All three panels degrade gracefully (empty states present for every "no data" path).
- The new `/api/taxonomy/pattern-density` endpoint is read-only and rate-limited — no write risk.
- Observatory tab can be closed by users who don't want it — existing tabs unaffected.

### Pre-merge gates

Same as spec #4:
1. `pytest --cov=app -v` green; coverage ≥ 90%.
2. `npm run test && npm run check` green.
3. `ruff check` + frontend lint clean.
4. Brand-guideline grep: no `box-shadow` with blur; no `glow`/`radiance`/`bloom`; no `border: 2px`; no `animation: pulse`.
5. Pre-PR hook passes.

---

## Risks + mitigations

| Risk | Mitigation |
|------|------------|
| Pattern-density endpoint slow at scale (10k+ MetaPattern rows) | Query uses `GROUP BY` with indexed FK; typical <20 ms. Add index on `MetaPattern.cluster_id` if profiling shows regression. Rate-limited 30/min. |
| JSONL activity history fetch churn when period = 30d | 30 days × 1 fetch per day = 30 round-trips. Mitigation: add a `since` query param to `/api/clusters/activity/history` that spans multiple days in one request. Optional — implement only if frontend profiling shows regression. |
| Three-panel layout breaks at narrow viewports | Desktop-first per `frontend/CLAUDE.md`. Below 1200 px the grid stacks vertically (pure CSS, no component branching). |
| SSE event flood during warm-path Phase 5 → timeline animation churn | Mirror `ActivityPanel.svelte`'s existing pattern: no per-row entry animation. Rows prepend instantly; only the expansion card on hover uses `fade-in` (per `ActivityPanel.svelte:777`). Keeps brand motion discipline without the CSS-alone-can't-gate-keyframes engineering problem. |
| Mid-session domain dissolution (Phase 5 `_dissolve_node()` while the Observatory is open) | `readinessStore` and the new `observatory.patternDensity` store both invalidate on `taxonomy_changed` SSE (already wired at `routes/app/+page.svelte:118-123` for `readinessStore`; add the same handler for the new pattern-density store). The Timeline's `taxonomy_activity` subscription surfaces the dissolution event itself. Before dispatching `domain:select` on a readiness-card click, the handler checks `readinessStore.byDomain(id)` returns a non-null report; if null (already dissolved), the click is a no-op with a console-debug log. |
| Pattern-density heat scaling misleading when one domain dominates | Heat opacity is relative to the max across visible rows. If `max / min > 20x`, log-scale the opacity instead of linear. Documented in `PatternDensityHeatmap.svelte` as a one-line switch. |
| Cross-process `TaxonomyEventLogger` events miss the ring buffer (MCP → backend HTTP POST forwarding) | Already live and tested — the backend process's ring buffer receives MCP events via `event_notification.py` cross-process forwarding. No additional risk. |
| Tab addition breaks existing `/app` layout in narrow use | Tablist already manages overflow-ellipsis and scrolling. Visual regression test: mount the editor groups with the new tab in `+page.svelte.test` and assert no layout shift on the existing tabs. |

---

## Locked design decisions

1. **Tab vs route vs modal (locked: tab).** The `/app` route manages SSE subscription lifecycle; a dedicated route would re-subscribe on navigation. A modal would lose the persistent-surface property users expect from an observability view.
2. **Period selector scope (locked: global to all three panels).** Simpler than per-panel period. The Readiness Aggregate ignores the selector (current-state snapshot), which is acknowledged in the UI with a disabled-looking chip + tooltip.
3. **Heatmap rendering (locked: data grid with opacity-scaled backgrounds, not a spatial heatmap).** Spatial heatmap with domain × time dimensions requires a second data axis the v1 doesn't collect. Grid form conveys the information Signal-Over-Noise.
4. **Timeline event filters (locked: path + op-family + errors_only, mirroring the existing `ActivityPanel`).** Don't re-invent the filter grammar — users already know it from the existing panel.
5. **Empty-state CTAs (locked: factual, no actions).** Read-only surface; pointing users to seed / optimize in copy only.

---

## Verification plan (post-implementation)

1. `cd backend && source .venv/bin/activate && pytest tests/test_taxonomy_insights_router.py -v`
2. `cd frontend && npm run test -- taxonomy/Observatory taxonomy/DomainLifecycleTimeline taxonomy/PatternDensityHeatmap taxonomy/DomainReadinessAggregate observatory.svelte`
3. Manual (preconditions: at least 3 active domains with ≥ 1 cluster each, ≥ 10 MetaPatterns across them, ≥ 1 GlobalPattern; run `POST /api/seed` with one of the shipped seed agents to prime if needed):
   - Open `/app` → click Observatory tab.
   - Confirm three panels render within 2 s.
   - Switch period to 24h → timeline + heatmap re-fetch.
   - Open another tab, optimize a prompt → watch lifecycle event prepend to timeline with fade.
   - Hover a heatmap row → tooltip shows absolute counts.
   - Click a readiness card → topology view focuses that domain.
   - Reload page → period selector state persists.
   - DevTools → enable `prefers-reduced-motion` → confirm transitions collapse to 0.01 ms.
