# Live Pattern Intelligence — Tier 1 (Design Spec)

**Date:** 2026-04-24
**Status:** Draft r2 — independent spec-reviewer pass complete (2026-04-24). All 4 BLOCKERs and 5 MAJORs addressed inline; 6 MINORs addressed inline. Pending user approval.
**Scope:** Frontend primarily + two additive keys on the `POST /api/clusters/match` response dict. No schema migration. No service-layer changes. No new Pydantic models.
**ADR:** [ADR-007](../../adr/ADR-007-live-pattern-intelligence.md) — Tier 1 of the 3-tier progressive-intelligence system
**Roadmap link:** `docs/ROADMAP.md` → "Live pattern intelligence — real-time context awareness during prompt authoring"

---

## Problem

The current pattern-suggestion UX is a single-banner transient alert above the prompt textarea. It surfaces via `PatternSuggestion.svelte` when `clustersStore.suggestion` populates after a match. Two shapes of interaction exist today, driven by `clustersStore.checkForPatterns()`:

- **Paste event** (delta ≥ 30 chars, 300 ms debounce): one match call, one banner.
- **Typing event** (prompt ≥ 30 chars, 800 ms debounce): same path (already implemented by the F-series work that landed in v0.3.25).

The backend primitives are already in place and fast enough for live authoring (`POST /api/clusters/match` ≈ 200-400 ms). What's missing is the **authoring surface**: users never see the evolving landscape of similar clusters + patterns + recommended strategy as they type. They see a single-cluster banner that auto-replaces itself or hides on skip, and the store records `_skippedClusterId` so the same match doesn't resurface.

Consequence: the taxonomy's accumulated intelligence — matched cluster, top meta-patterns, cross-cluster universal patterns, strategy intelligence, classification drift signals — sits dormant until the user clicks SYNTHESIZE. By then the optimizer runs with whatever the enrichment pipeline decides, and the user receives post-hoc explanations in the ENRICHMENT panel of `ForgeArtifact.svelte`.

**Tier 1 closes the authoring-phase visibility gap.** Tier 2 (preview-enrichment endpoint) and Tier 3 (proactive inline hints) remain out of scope for this spec.

---

## Goals

1. **Persistent, non-modal context surface** as the user types — users see what the taxonomy knows about their emerging prompt without committing to SYNTHESIZE.
2. **Multi-pattern selection** — users can pick a subset of the returned patterns (e.g. two out of three meta-patterns + one cross-cluster pattern) rather than Apply-all / Skip-all.
3. **Continuous update** — the panel reflects the latest match. No auto-dismiss, no "you already skipped this cluster" state.
4. **Brand-guideline compliance** — the new surface honours the zero-effects directive, Tier-Small contour grammar, ultra-compact density, neon chromatic encoding, and spring motion. See §Brand Compliance below.
5. **Regression-safe rollout** — existing paste behaviour continues to work; the single-banner `PatternSuggestion.svelte` is retired cleanly, not toggled by a feature flag.

## Non-goals

- Tier 2 — the `POST /api/clusters/preview-enrichment` endpoint that streams analyze + strategy-intelligence previews alongside the match. Different endpoint, different backend work (orchestrator delta). Belongs in a separate spec.
- Tier 3 — proactive inline hints (tech-stack divergence alerts, strategy mismatches) that interrupt the flow. Also separate.
- Changes to `POST /api/clusters/match` rate limits, scoring logic, or hierarchical cascade thresholds. The endpoint contract gains two response fields but behavior is unchanged.
- Extending the panel with Inspector-level detail (member count history, coherence drift). That's the Taxonomy Observatory's scope.

---

## Architecture

### High-level data flow

```
                       <textarea> oninput
                              │
                              ▼
            clustersStore.checkForPatterns(text)      [existing]
                              │
               debounce (300 ms paste | 800 ms typing)
                              │
                              ▼
            matchPattern(text, signal, project_id)    [existing; response extended]
                              │
                              ▼
             clustersStore.suggestion (ClusterMatch)  [existing]
                              │
     ┌───────────────────────┬───────────────────────┐
     │                       │                       │
     ▼                       ▼                       ▼
ContextPanel.svelte    forgeStore.applied*     appliedPatternsChip  [existing]
 (new — sidebar)        (existing — selection)  (existing — chip)
```

### Component boundaries

The new surface is one Svelte component backed by the existing store. No new stores — `clustersStore` already owns the match state, and `forgeStore` already owns `appliedPatternIds` + `appliedPatternLabel`.

| Responsibility | Owner | Change |
|----------------|-------|--------|
| Debounce + API call + AbortController lifecycle | `clustersStore.checkForPatterns` | No change — the `_skippedClusterId` gate is dropped (see Store Delta) |
| Match state (current suggestion, match level, patterns) | `clustersStore.suggestion` | Extended type — see API Contract Delta |
| Pattern selection UI + apply/clear | `ContextPanel.svelte` | **new** |
| Apply-to-forge plumbing | `forgeStore.appliedPatternIds` | Expanded: now also handles cross-cluster pattern IDs |
| Docked sidebar layout | `EditorGroups.svelte` | Minor: one new slot next to the prompt editor |
| Legacy single-banner UI | `PatternSuggestion.svelte` + its tests | **deleted** |

### Where ContextPanel lives in the layout

The active Workbench layout (from `EditorGroups.svelte`) today is two columns: Editor on the left, Inspector on the right. The prompt editor (`PromptEdit.svelte`) is the top pane of the Editor column. Tier 1 adds a **third vertical strip** between the Editor column and the Inspector — a 240 px `ContextPanel` docked to the right edge of the Editor column. The panel:

- Collapses to a 28 px rail (header bar + domain dot only) when the user hides it.
- Expands back on click — no animation on first expansion except the `navSlide` preset at 300 ms.
- Persists its collapsed/expanded state to `localStorage['synthesis:context_panel_open']` so preferences survive reload.
- Hides entirely while `forgeStore.status` is mid-synthesis (no-op matches during pipeline execution — the ENRICHMENT panel in `ForgeArtifact.svelte` owns post-run display).

No responsive stacking — the Workbench is a desktop-first IDE surface; small viewports are out of scope per the existing `frontend/CLAUDE.md` posture.

### State model

```ts
// clustersStore — shape narrows; _skippedClusterId + dismissSuggestion() are removed
suggestion: ClusterMatch | null       // current match result (or null if no match)
suggestionVisible: boolean            // retained for PromptEdit chip gating

// forgeStore — appliedPatternIds expands to carry cross-cluster pattern IDs too
appliedPatternIds: string[] | null    // meta-pattern IDs from cluster meta + global sections
appliedPatternLabel: string | null    // "cluster label (N)" — retained shape, N now covers both sections

// ContextPanel.svelte — local state only
selectedPatternIds: Set<string>       // component-local; initialized from forgeStore.appliedPatternIds
                                       // on mount; no store mutation
```

The panel is a pure read-render over `clustersStore.suggestion`. The user's selection is component-local (`selectedPatternIds`), round-tripped to the pipeline through `forgeStore.appliedPatternIds` on APPLY. That keeps the pattern chip below the textarea (`PromptEdit.svelte:140-149`) always in sync without a second source of truth, and it keeps the store's API surface tight — `clustersStore` has no new fields.

---

## API Contract Delta

The `POST /api/clusters/match` handler in `backend/app/routers/clusters.py:684-725` currently returns `ClusterMatchResponse.match: dict | None` (see `backend/app/schemas/clusters.py:130-131`) — a loosely-typed dict the router assembles inline. Tier 1 adds **two new keys to that dict**, keeping the current untyped-dict shape to stay minimal:

```python
# In clusters.py:700-719 — existing dict-build is extended with two additive keys
match_dict["meta_patterns"] = [ ... ]               # existing
match_dict["similarity"] = result.similarity        # existing
# NEW — always populated (empty list allowed):
match_dict["cross_cluster_patterns"] = [ ... ]
# NEW — one of {"family", "cluster"} (the engine's internal "none" value short-circuits to match=None):
match_dict["match_level"] = result.match_level
```

**`cross_cluster_patterns`**: `match_prompt()` in `backend/app/services/taxonomy/matching.py:404-457` already queries `MetaPattern` rows with `global_source_count >= CROSS_CLUSTER_MIN_SOURCE_COUNT`, ranks them by `sim * log2(1 + global_source_count) * cluster_score_factor`, filters against `existing_ids` (the matched cluster's own meta-patterns — disjointness guaranteed at the engine layer), and applies `CROSS_CLUSTER_RELEVANCE_FLOOR`. Cross-cluster patterns are **global by construction** — there is no `project_filter` involved. The engine produces the list; Tier 1 just plumbs it into the response.

**`match_level`**: already a field on `engine.MatchResult.match_level` (`matching.py:99-107`) but currently dropped on the way to the response dict. Surfacing it lets the panel render a one-line context ("Matched via family similarity" vs "Matched within cluster").

Both keys are **additive with defaults** — no migration, no frontend break on rolling upgrades. The frontend decodes them defensively with `?? []` and `?? "cluster"`.

Promoting `match` to a typed `MatchPayload: BaseModel` is deliberately out of scope for Tier 1 — it would be a larger shape change with serialization-test ripple. Track it as a follow-up if future Tier 2/3 work needs the discipline.

---

## UI Design — ContextPanel.svelte

### Brand compliance (hard constraints)

All design choices below derive from `.claude/skills/brand-guidelines` loaded at spec time. Every interactive element passes through the 5-state lifecycle (Resting / Hover / Active / Focus / Disabled) defined there.

| Brand rule | Application in ContextPanel |
|------------|------------------------------|
| **Zero-effects directive** | No `box-shadow` with blur/spread. No `text-shadow`. No `filter: drop-shadow`. No pulse animations. Active/selected states use 1 px inset contours. |
| **Neon Tube Model** | Every visible border = 1 px uniform. Domain dot is 6 px solid chip in `taxonomyColor(domain)`. Similarity bar is a 1 px cyan border with opaque fill. |
| **Ultra-compact density** | Panel header 24 px (`px-1.5`, `text-[11px]` Syne uppercase). Pattern rows 20 px. Padding `p-1.5` everywhere. Gap `gap-1.5`. |
| **Chromatic encoding** | Domain color (`taxonomyColor(domain)`) drives the panel's left accent rail + domain dot. Match level uses chromatic encoding: `family` = dim text-secondary, `cluster` = neon-cyan. Cross-cluster patterns carry a 1 px neon-purple left-border indicator (the "elevated/universal" color). |
| **Space Grotesk + Geist Mono discipline** | Headings in Syne 11 px 700 uppercase + 0.1em tracking. Pattern body text in Space Grotesk 11 px. Similarity percentages + pattern source counts in Geist Mono 10 px (tabular figures). |
| **Motion** | Panel expansion uses the existing `navSlide` preset (`cubic-bezier(0.16, 1, 0.3, 1)`, 180 ms per `frontend/src/lib/utils/transitions.ts:37`). Pattern-row entry uses `fade-in` 150 ms with the brand spring curve. Checkbox toggle is instant — no transition on the selection state because the brand treats discrete binary states as non-animated. |
| **Reduced motion** | `@media (prefers-reduced-motion: reduce)` collapses all transitions to 0.01 ms (matches the `app.css` global rule). |

### Layout (expanded state)

```
┌──────────────────────────────────────┐   ← 240 px wide panel
│ CONTEXT                         ∨   │   ← 24 px header, Syne uppercase, ∨ = collapse
├──────────────────────────────────────┤
│ ● frontend · React Component Pat…   │   ← 20 px row: domain dot + cluster label
│   matched 84%  · cluster             │   ← 18 px subline: similarity + match_level
├──────────────────────────────────────┤
│ META-PATTERNS              3/3 ✔    │   ← 20 px sub-heading, "N/M ✔" selection count
│ ☒ Always specify return type and…   │   ← 20 px pattern row
│ ☒ Include example input/output…     │
│ ☐ Use JSDoc @param annotations…     │
├──────────────────────────────────────┤
│ GLOBAL                     0/2      │   ← 1 px neon-purple left border — "elevated / cross-cluster"
│ ☐ Break long tasks into numbered…   │
│ ☐ Specify tone and audience…        │
├──────────────────────────────────────┤
│                       [ APPLY 5 ]    │   ← 20 px Tier-Hero primary button
└──────────────────────────────────────┘
```

(ASCII glyphs `☒` / `☐` above are **documentation only** — the actual component renders native `<input type="checkbox">` elements styled per brand Tier-Small grammar: 1 px border at rest, 1 px neon-cyan on hover, 1 px neon-cyan + inset cyan fill `/12` when checked.)

- **Header**: 24 px tall, Syne `text-[11px]` 700 uppercase 0.1em tracking, `∨` collapse control right-aligned. Collapsing slides the panel to a 28 px rail showing only the domain dot + similarity percentage (so the chromatic channel stays visible).
- **Cluster identity row**: 20 px tall. 6 px domain dot, Space Grotesk 11 px cluster label truncated with `overflow: hidden; text-overflow: ellipsis`, similarity percentage in Geist Mono 10 px, and `match_level` as a subtle dim badge.
- **Section sub-headings**: 20 px, Syne `text-[10px]` 700 uppercase 0.1em tracking (per brand sub-section rule), right-aligned selection count `N/M ✔` — Geist Mono 10 px with a cyan check glyph when `N > 0`.
- **Pattern rows**: 20 px tall, 1 px top border between rows in `border-subtle`. Checkbox is a 10 px custom-styled input (hover → 1 px neon-cyan border; checked → 1 px neon-cyan + inset cyan fill at `/12`; per brand Tier-Small grammar). Pattern text in Space Grotesk 11 px, single line, truncated at 60 chars (similar to current banner which truncates at 80 chars, but tighter for the narrower panel).
- **Global section**: `border-left: 1px solid var(--color-neon-purple);` replacing the default subtle border to differentiate global patterns from cluster meta-patterns. Brand rationale: `neon-purple` is defined as "Processed, elevated" in the palette. Uniform 1 px border — zero spread, zero blur, aligned with the Neon Tube Model.
- **Apply button**: 20 px `button.action-btn.action-btn--primary` mirroring the existing paste banner's pattern-apply button. Disabled (`opacity: 0.4`) when selection is empty. Click → sets `forgeStore.appliedPatternIds` + `forgeStore.appliedPatternLabel` exactly like the banner does today, then the existing applied-chip in `PromptEdit.svelte:140-149` renders below the textarea.

### Layout (collapsed state)

28 px rail. Two elements stacked vertically in a 28 × 80 px region:
- Top: `∨` flipped to `∧` — expand control.
- Middle: 6 px domain dot — preserves chromatic channel.

The similarity percentage is intentionally dropped from the rail (the expanded panel still shows it). Rationale: the brand-consistency risk of rotated monospace text (`writing-mode: vertical-lr` has zero precedent in the codebase; every other numeric reads horizontally) outweighs the one extra data point on a 28 px rail.

No animation in or out of the collapsed state beyond the `navSlide` width transition.

### Empty states

| Condition | Rendering |
|-----------|-----------|
| Prompt < 30 chars (no match attempted yet) | Panel header "CONTEXT · waiting for prompt". Body dim: "Start typing to see related clusters and patterns." |
| Match returned `null` (no cluster above threshold) | Header "CONTEXT · no match". Body: "No similar clusters found — the optimizer will treat this prompt standalone." Offer: "Apply universal patterns anyway?" as a secondary CTA if `cross_cluster_patterns` is non-empty. |
| Match in-flight (request pending) | Header "CONTEXT · matching" (no trailing ellipsis — a perpetual ellipsis on the header line is a soft form of ambient animation the brand forbids). Previous match body remains visible at 0.5 opacity so the user can still interact with prior patterns; body is `pointer-events: none` until the new result lands. **No spinner**. |
| Synthesis in progress | Panel hidden entirely. Replaced by a single disabled rail "Synthesizing — context frozen." |

### Accessibility

- Panel wrapper: `role="complementary"` + `aria-label="Pattern context"` so screen readers can identify the region.
- Pattern rows: native `<input type="checkbox">` with `aria-describedby` pointing to the pattern text.
- Collapse/expand button: `aria-expanded={isOpen}` + `aria-controls="context-panel-body"`.
- Keyboard: Tab reaches the panel after the textarea. Space toggles checkboxes. `Cmd/Ctrl+Enter` on the panel triggers Apply.
- Focus ring: `1 px solid rgba(0, 229, 255, 0.3)` outline at `offset: 2px` — canonical brand focus treatment.

### Reduced motion / low-bandwidth

- `@media (prefers-reduced-motion: reduce)` → `transition-duration: 0.01 ms` on all property changes.
- Network errors (`matchPattern` throws non-Abort error): the panel shows the last successful match at full opacity + a 1 px neon-red inset contour around the header rail with the message "Match failed — retrying on next keystroke." No retry button; the next `checkForPatterns` cycle resolves.

---

## Store Delta — `clustersStore`

Two surgical changes to `frontend/src/lib/stores/clusters.svelte.ts`:

1. **Drop the skipped-cluster gate.** The existing `_skippedClusterId` state prevents re-showing a skipped single-cluster banner. Under the persistent panel, every fresh match should surface — the user no longer hits a skip action (they toggle checkboxes instead). Remove the field + the gate in `checkForPatterns`, and delete `dismissSuggestion()`.

2. **Extend `ClusterMatch` type** (re-exported from `frontend/src/lib/api/clusters.ts`) with `cross_cluster_patterns: MetaPatternItem[]` and `match_level: 'family' | 'cluster'`. Defensive defaults on decode.

`selectedPatternIds` stays **component-local** in `ContextPanel.svelte` — initialized from `forgeStore.appliedPatternIds` on mount via `$effect.root`, no store mutation. Rationale: the data's natural round-trip is `component → forgeStore → pipeline`; adding a third store field just to route through `clustersStore` would double the surface area for no cross-tab benefit. If a future need emerges, promote then.

`applySuggestion()` is retained (still returns `{ ids, clusterLabel }`); `dismissSuggestion()` is deleted — no skip action in the new panel. Tests referencing `dismissSuggestion()` or `_skippedClusterId` in `clusters.svelte.test.ts` are removed alongside the production code (see §Files touched).

---

## Files touched

| File | Change | Rationale |
|------|--------|-----------|
| `frontend/src/lib/components/editor/ContextPanel.svelte` | **new** (~220 lines) | Main panel component |
| `frontend/src/lib/components/editor/ContextPanel.test.ts` | **new** (~160 lines, ≥12 cases) | Behavioral coverage |
| `frontend/src/lib/components/editor/PatternSuggestion.svelte` | **delete** | Superseded by ContextPanel |
| `frontend/src/lib/components/editor/PatternSuggestion.test.ts` | **delete** | Superseded |
| `frontend/src/lib/components/editor/PromptEdit.svelte` | Drop the `<PatternSuggestion />` mount; applied-chip stays | Panel now owns suggestion UI |
| `frontend/src/lib/components/layout/EditorGroups.svelte` | Add a 240 px column slot for ContextPanel; reduce Inspector width by 240 px at >1400 px viewport | Layout accommodation |
| `frontend/src/lib/stores/clusters.svelte.ts` | Drop `_skippedClusterId` + `dismissSuggestion()`; extend `ClusterMatch` type | Behavior shift |
| `frontend/src/lib/stores/clusters.svelte.test.ts` | Remove the `dismissSuggestion` / `_skippedClusterId` describe blocks (around lines 158-162); add the re-match-surfaces coverage | Test parity with store delta |
| `frontend/src/lib/api/clusters.ts` | Add `cross_cluster_patterns` + `match_level` to the `match` object with optional-safe decode | API contract delta |
| `backend/app/routers/clusters.py:684-725` | Populate the two new keys in `match_dict` from `engine.MatchResult` (no new Pydantic model — keep `match: dict \| None` shape) | Wire new fields |
| `backend/tests/test_clusters_router.py` | Extend match tests to cover the two new keys (disjointness, match_level presence) | Backend regression guard |
| `frontend/src/lib/components/editor/PromptEdit.test.ts` | Delete the `PatternSuggestion` mount-assertion (retain applied-chip coverage) | Test parity |

---

## Testing strategy

### Backend

1. `test_clusters_router.py::test_match_includes_cross_cluster_patterns`: seed 1 target cluster + 2 clusters whose meta-patterns have `global_source_count >= 3`; assert both global patterns appear in `cross_cluster_patterns`, disjoint from `meta_patterns`.
2. `test_clusters_router.py::test_match_includes_match_level_field`: assert `match_level ∈ {"family", "cluster"}` for mocked matches.
3. Backwards-compat: existing match tests must continue to pass unchanged (the new fields are additive with defaults).

### Frontend — ContextPanel.test.ts (≥ 12 cases)

| Scenario | Assertion |
|----------|-----------|
| Mount with null suggestion → empty state "waiting for prompt" | DOM contains the empty-state copy |
| Mount with match → cluster label + similarity + match_level render | All three values visible |
| 3 meta-patterns → 3 rows rendered with truncated text | Exactly 3 `<input type="checkbox">` in meta section |
| 2 cross-cluster patterns → Universal section with purple left border | 2 additional rows, class carries the purple variant |
| Checkbox click toggles `selectedPatternIds` | After 2 clicks: 2 checked, apply button label = "APPLY 2" |
| Apply button disabled when selection is empty | `disabled` attribute present |
| Apply button click → `forgeStore.appliedPatternIds` populated | Store value is the union of checked IDs |
| Apply button click → panel remains visible with selection locked in | Checkboxes stay checked after apply |
| Collapse toggles to rail (28 px width) | Panel element width assertion |
| Collapsed state persists to `localStorage['synthesis:context_panel_open']` | localStorage value after collapse |
| `forgeStore.status = 'synthesizing'` → panel hides | Panel element not in DOM during synthesis |
| `prefers-reduced-motion: reduce` → `transition-duration: 0.01ms` | CSS computed-style assertion |

### Frontend — clusters.svelte.test.ts

1. `test_checkForPatterns_no_longer_tracks_skipped_cluster` — after match N, a subsequent re-match for the same cluster surfaces (assertion: `suggestionVisible === true` on the second call).
2. Type re-export regression: `ClusterMatch` shape includes `cross_cluster_patterns` and `match_level`; decoding an old response (no fields) defaults correctly.

### Manual verification

- Type 40 chars slowly → see panel populate within ~1 s of pause.
- Paste a 300-char prompt → see panel populate within ~500 ms.
- Toggle two patterns → click APPLY → see the applied-chip below the textarea update to "2 patterns from …".
- Click SYNTHESIZE → panel hides with the "context frozen" rail.
- Synthesis completes → panel re-renders with the (possibly-new) match state.
- Collapse → reload page → panel stays collapsed (localStorage persistence).

---

## Rollout

Single PR. No feature flag, no migration, no preference toggle. Rationale:

- The store's new typing-debounce path has been live since v0.3.25 (users already experience typing-triggered matches in the single-banner UI). Tier 1 replaces the surface, not the mechanism.
- `_skippedClusterId` removal is a pure behavior improvement — users previously had to clear the skip by refreshing; now every new match surfaces automatically.
- Brand-guideline review + independent spec-reviewer subagent validate before landing.

### Pre-merge gates

1. `cd backend && pytest --cov=app -v` — 0 failures, ≥ 90% line coverage preserved.
2. `cd frontend && npm run test && npm run check` — svelte-check clean, all Vitest suites green.
3. `ruff check app/ tests/` + frontend `eslint` clean.
4. Brand-guideline grep: no `box-shadow` with blur/spread in new files; no `glow` / `radiance` / `bloom` in code or comments; no `border: 2px` anywhere new.
5. Pre-PR hook passes (the existing `.claude/hooks/pre-pr-checks.sh`).

### Post-merge

- Existing `optimization_created` SSE flow, taxonomy events, and telemetry are unchanged — no monitoring deltas expected.
- If metrics show an uptick in `POST /api/clusters/match` rate >2x baseline, consider raising the typing debounce from 800 ms to 1200 ms. No action required if rate stays within `30/minute` per-IP (existing rate limit).

---

## Risks + mitigations

| Risk | Mitigation |
|------|------------|
| Match requests fire too frequently during fast typing → rate-limit exhaustion | Existing `30/minute` rate limit + AbortController cancels in-flight requests. Recovery: the next keystroke triggers a fresh debounce. |
| Panel redraws on every keystroke churn GPU / hurt perf on long prompts | Match response is the only thing that triggers re-render (Svelte reactive primitives are fine-grained). Panel body is memoized behind `suggestion` identity. |
| Cross-cluster patterns overlap with meta-patterns (same ID appears twice) | Backend guarantees disjointness: `auto_inject_patterns` excludes the match cluster's own meta-patterns when building `cross_cluster_patterns`. Covered by a disjointness test on the backend. |
| Collapsed-rail vertical text readability on some platforms | `writing-mode: vertical-lr` is well-supported in modern browsers (Chrome, Firefox, Safari 14+). Fallback: rail shows horizontal 10 px percentage with `overflow: clip`. |
| User selection lost across SYNTHESIZE / cancel cycles | The pattern chip below the textarea is the durable record — it reads from `forgeStore.appliedPatternIds`. Verified behavior (`forge.svelte.ts:110-130`, `:384-395`): `forge()` clears `appliedPatternIds` at invocation start (line 130); `cancel()` preserves them; `complete` clears them (line 511). ContextPanel re-initializes `selectedPatternIds` from `forgeStore.appliedPatternIds` on every (re-)mount, so a post-cancel panel re-render re-hydrates the checkboxes correctly. A post-`forge()` mount sees `null` → empty selection → user picks fresh patterns for the next run. This matches today's single-banner semantics. |
| Inspector column width reduction breaks existing Inspector layouts at narrow viewports | The Workbench is desktop-first (see `frontend/CLAUDE.md`) — viewports below 1400 px are out of scope across the whole app. When the panel can't fit, `EditorGroups.svelte` collapses it to the 28 px rail by default; no legacy shim, no viewport-gated fallback. Validated by running `npm run dev` at 1280 px and confirming the rail-only rendering is usable. |

---

## Out of scope / deferred to Tier 2 + 3

- Preview of analyze-phase output (strengths, weaknesses, recommended strategy) — Tier 2.
- Tech-stack divergence alerts inline in the panel — Tier 3.
- Showing applied-pattern provenance (which pattern got injected at pipeline time) — already live in `ForgeArtifact.svelte` ENRICHMENT panel.
- Cross-project pattern surfacing — current `project_id` scope via `projectStore.currentProjectId` is correct; a future "All projects" toggle is orthogonal.
- Pattern composition (ordering, combining multiple clusters' patterns) — post-v0.5 work.

---

## Locked design decisions

(The decisions below were locked during the initial spec-review pass. Listed for traceability — the original alternatives are on record should a future iteration revisit them.)

1. **Skipped-cluster state removal (locked: remove).** Persistent panel surfaces every new match. The pre-panel skip semantics don't survive the shift to an always-visible surface. Documented in `frontend/CLAUDE.md` on landing.
2. **Inspector width reduction (locked: third-column layout).** Alternative considered: floating overlay above the Inspector. Rejected because the column approach is more IDE-native and gives the panel its own spatial region for eyes to rest on.
3. **Section label: "GLOBAL" (locked).** Matches the `GlobalPattern` model name in the codebase so future contributors don't re-litigate naming. The brand-compliance rationale for the neon-purple left border ("Processed, elevated") doubles as the explanatory tooltip if needed in a later pass.
4. **match_level inline vs hover (locked: inline).** Data density aligns with brand Signal-Over-Noise. The one-line subline "matched 84% · cluster" costs 18 px vertical and tells the user why the match fired. If usability testing surfaces confusion, move to tooltip in a follow-up.
5. **Collapsed-rail content (locked: domain dot + expand glyph only).** Alternative considered: vertical percentage text via `writing-mode: vertical-lr`. Rejected on brand-consistency grounds — zero precedent in the codebase for rotated numerics.

---

## Verification plan (post-implementation)

1. `cd backend && source .venv/bin/activate && pytest tests/test_clusters_router.py::test_match_includes_cross_cluster_patterns tests/test_clusters_router.py::test_match_includes_match_level_field -v`
2. `cd frontend && npm run test -- ContextPanel.test.ts clusters.svelte.test.ts`
3. Manual (preconditions listed explicitly — the database must already contain at least one mature cluster in the `security` or `backend` domain; if empty, run `POST /api/seed` with the `coding-implementation` seed agent first):
   - Run `./init.sh restart`.
   - Load `/app` in a browser with a seeded taxonomy + an active project.
   - Type "Write a Python function that validates JWT tokens" (40 chars) and wait.
   - Confirm panel populates within 1.2 s with the `security` or `backend` cluster's patterns.
   - Toggle 2 checkboxes → click APPLY → see the applied-chip populate.
   - Click SYNTHESIZE → confirm panel hides.
   - Wait for completion → confirm panel re-renders with the post-run context.
