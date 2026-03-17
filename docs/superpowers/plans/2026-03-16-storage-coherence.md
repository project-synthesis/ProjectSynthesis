# Storage Architecture Coherence Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development to implement.

**Goal:** Unify the data contract between History list, detail view, and forge store. Add session persistence and enriched history previews.

**Architecture:** Three changes: (1) enrich history API with `optimized_prompt` preview, (2) persist last-active optimization ID in localStorage, (3) unify HistoryItem and OptimizationResult into one shape.

---

## Task 1: Enrich History API

**Files:**
- Modify: `backend/app/routers/history.py` — add `optimized_prompt` (truncated 100 chars) to list response
- Modify: `frontend/src/lib/api/client.ts` — add `optimized_prompt` to HistoryItem interface

The history list currently returns `raw_prompt[:100]` but no preview of the output. Add `optimized_prompt[:100]` so the sidebar can show what was produced.

---

## Task 2: Unified Data Contract

**Files:**
- Modify: `frontend/src/lib/api/client.ts` — merge HistoryItem fields into OptimizationResult (make shared fields optional for list context)
- Modify: `frontend/src/lib/stores/forge.svelte.ts` — add `loadFromRecord(opt: OptimizationResult)` method to centralize the mapping from API response → store state
- Modify: `frontend/src/lib/components/layout/Navigator.svelte` — use `loadFromRecord` instead of manual field-by-field assignment in `loadHistoryItem`

The goal: one `OptimizationResult` interface used everywhere. HistoryItem becomes `Partial<OptimizationResult>` effectively — same fields, some optional/truncated in list context.

---

## Task 3: Session Persistence (localStorage)

**Files:**
- Modify: `frontend/src/lib/stores/forge.svelte.ts` — on optimization_complete, save `result.trace_id` to `localStorage`. On init, check localStorage and restore from API.
- Modify: `frontend/src/routes/+layout.svelte` — call forge store session restore on mount

When the user refreshes the page, the forge store reads `localStorage.getItem('synthesis:last_trace_id')`, fetches `GET /api/optimize/{trace_id}`, and restores the full result. The user sees their last optimization immediately.

---

## Verification

1. `GET /api/history` now includes `optimized_prompt` field (truncated)
2. Page refresh → last optimization restored automatically
3. `forgeStore.loadFromRecord()` used consistently (no manual mapping)
4. All tests pass, svelte-check clean
