# History–Patterns Bidirectional Integration

**Date:** 2026-03-19
**Status:** Approved
**Scope:** Backend API enrichment, frontend store binding, Navigator/Inspector/StatusBar/Editor UI changes

## Problem

History and Patterns are two isolated views of the same data. The history panel shows truncated raw prompts (`raw_prompt[:100]`) with no semantic labeling. The patterns panel shows intent labels, domain badges, and family groupings. There is no cross-navigation — clicking an optimization in history doesn't reveal its pattern family, and clicking a linked optimization in a family detail doesn't sync with history.

`intent_label` and `domain` are generated on every optimization but only surfaced in the knowledge graph half of the UI.

## Design

### 1. Backend — Enrich History API

**`GET /api/history`** adds three fields to each item:

| Field | Type | Source |
|-------|------|--------|
| `intent_label` | `string \| null` | `Optimization.intent_label` column |
| `domain` | `string \| null` | `Optimization.domain` column |
| `family_id` | `string \| null` | LEFT JOIN `optimization_patterns` WHERE `relationship='source'` LIMIT 1 |

The `family_id` JOIN is done in a single SQL query using a correlated subquery or lateral join — not N+1. An optimization has at most one "source" family (the family it was clustered into by the extractor).

**`GET /api/optimize/{trace_id}`** also adds `family_id` via the same pattern. The full optimization response already includes `intent_label` and `domain`; only `family_id` is new.

**`_VALID_SORT_COLUMNS`** in `optimization_service.py` adds `intent_label` and `domain`.

### 2. Frontend — Bidirectional Store Binding

**Forge store** (`forge.svelte.ts`):
- New field: `familyId: string | null = null`
- `loadFromRecord(opt)`: sets `familyId` from the API response. If non-null, calls `patternsStore.selectFamily(familyId)` — Inspector auto-shows family detail.
- `forge()`: clears `familyId`, calls `patternsStore.selectFamily(null)` (already exists).
- `reset()`: clears `familyId`.

**Patterns store** (`patterns.svelte.ts`):
- No new fields needed. `selectFamily(id)` already loads family detail into Inspector.
- Inspector's "linked optimizations" list already has click handlers that call `forgeStore.loadFromRecord()` → which sets `familyId` → which calls `selectFamily()` — the loop is closed.

**Deselection rules:**
- Starting a new forge run clears the family link (optimization in progress has no family yet).
- Selecting a family directly in PatternNavigator does NOT clear the forge result — the user can browse families while viewing a result.

### 3. Frontend — History Navigator Enrichment

History rows in `Navigator.svelte` change layout:

**Before:**
```
[Write a Python function that implements dep...]
[auto]  [7.2]
```

**After:**
```
[dependency injection refactoring]        ← intent_label (falls back to raw_prompt[:60])
[backend]  [auto]  [7.2]                  ← domain badge + strategy + score
```

Domain badge uses the shared `domainColor()` from `constants/patterns.ts` — same color coding as PatternNavigator and RadialMindmap.

`intent_label` fallback: if null (pre-knowledge-graph optimizations or failed analysis), display `raw_prompt[:60]` truncated with "..".

### 4. Frontend — Editor Tab Titles

Result and diff tabs use `intent_label` as the tab title when available:
- Result tab: `intent_label` (fallback: `"Result"`)
- Diff tab: `intent_label + " diff"` (fallback: `"Diff"`)

Updated in `editorStore.cacheResult()` which already syncs tab metadata when optimization data loads.

### 5. Frontend — StatusBar Breadcrumb

StatusBar adds a breadcrumb segment showing the active optimization's context, matching VS Code's file path pattern:

```
[domain] › intent_label
```

Example: `backend › dependency injection refactoring`

- Domain rendered in `domainColor()`, dim weight.
- Intent label in `text-primary`.
- Clears when no optimization is active (`forgeStore.result === null`).
- Replaces the simple pattern count (which moves to a secondary position or is removed if redundant with the Patterns panel).

### 6. Reactive Event Sync

**Existing (no changes needed):**
- `optimization_created` SSE → Navigator refreshes history list
- `pattern_updated` SSE → `patternsStore.invalidateGraph()`

**New:**
- When `pattern_updated` SSE fires with `optimization_id` matching the current `forgeStore.result.id`, the forge store fetches the updated optimization to pick up the newly assigned `family_id`. This handles the async gap: user optimizes → sees result → background extractor links it to a family → family link appears in Inspector automatically.

Implementation: `+page.svelte` event handler checks `pattern_updated` data for `optimization_id`, compares to `forgeStore.result?.id`, and calls a lightweight refresh if matched.

## Data Flow

```
User clicks optimization in History
  → forgeStore.loadFromRecord(opt)
    → forgeStore.familyId = opt.family_id
    → patternsStore.selectFamily(familyId)
      → Inspector shows family detail
      → PatternNavigator highlights family

User clicks linked optimization in Inspector family detail
  → forgeStore.loadFromRecord(opt)
    → forgeStore.familyId = opt.family_id (same family)
    → Editor opens result tab with intent_label title

User starts new optimization (forge)
  → forgeStore.familyId = null
  → patternsStore.selectFamily(null)
  → Inspector shows forge progress

Background extractor finishes
  → SSE pattern_updated { optimization_id, family_id }
  → If matches current result: forge store picks up family_id
  → Inspector auto-shows family detail

User clicks family in PatternNavigator
  → patternsStore.selectFamily(familyId)
  → Inspector shows family detail (forge result stays in editor)
```

## Files Changed

### Backend
| File | Change |
|------|--------|
| `routers/history.py` | Add `intent_label`, `domain`, `family_id` to serialization |
| `services/optimization_service.py` | Add `intent_label`, `domain` to `_VALID_SORT_COLUMNS`; add `family_id` subquery to history query |
| `routers/optimize.py` | Add `family_id` to trace response |

### Frontend
| File | Change |
|------|--------|
| `stores/forge.svelte.ts` | Add `familyId` field, auto-select family on load, clear on forge/reset |
| `stores/editor.svelte.ts` | Update tab titles from `intent_label` in `cacheResult()` |
| `components/layout/Navigator.svelte` | Display `intent_label` + domain badge in history rows |
| `components/layout/StatusBar.svelte` | Add breadcrumb segment for active optimization |
| `components/layout/Inspector.svelte` | No changes — already shows family detail when `selectedFamilyId` is set |
| `routes/app/+page.svelte` | Handle `pattern_updated` with `optimization_id` match for live family link |
| `api/client.ts` | Update `HistoryItem` type with new fields |

## Non-Goals

- History search/filter by intent_label (deferred — can be added later with the sort columns in place).
- Backfilling `intent_label`/`domain` on pre-knowledge-graph optimizations (they display with raw_prompt fallback).
- Changing the PatternNavigator layout (already well-integrated).
