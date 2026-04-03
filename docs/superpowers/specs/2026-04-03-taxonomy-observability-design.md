# Taxonomy Engine Observability

**Date:** 2026-04-03
**Status:** Design
**Scope:** Backend structured event logging + frontend activity panel

## Problem

The taxonomy engine runs 3 paths (hot/warm/cold) with 22 files, ~10K lines, and dozens of thresholds. Critical decisions — multi-signal assignment scoring, split HDBSCAN parameters, merge coherence gates, phase Q-gate acceptance — execute silently. Current logging is sparse, string-based, and decision-agnostic. When behavior is unexpected, there's no way to reconstruct WHY a decision was made without reading source code and adding temporary debug prints.

## Solution

A dedicated `TaxonomyEventLogger` service that captures structured decision events at ~17 strategic instrumentation points across all three paths. Events dual-write to JSONL files (persistence) and an in-memory ring buffer (real-time). A collapsible frontend Activity panel displays events in real-time via SSE.

## Architecture

### Event Logger Service

**File:** `backend/app/services/taxonomy/event_logger.py`

Singleton service (initialized in lifespan, accessed via module-level getter). Mirrors `TraceLogger` pattern.

**Components:**
- **JSONL writer:** Daily rotation to `data/taxonomy_events/decisions-YYYY-MM-DD.jsonl`. One JSON object per line. 30-day retention with `rotate()`.
- **Ring buffer:** `collections.deque(maxlen=500)`. Serves real-time reads for API and seeds frontend on mount.
- **Event bus bridge:** After writing, publishes `taxonomy_activity` to existing event bus for SSE streaming.

**API:**
```python
# Write
log_decision(event: dict) -> None  # dual-write to JSONL + ring buffer + event bus

# Read
get_recent(limit: int = 50, path: str | None = None, op: str | None = None) -> list[dict]
get_history(date: str, limit: int = 100, offset: int = 0) -> list[dict]

# Maintenance
rotate(retention_days: int = 30) -> int  # returns files deleted
```

### Event Schema

All events share a base structure:

```python
{
    "ts": "2026-04-03T14:22:00.123Z",   # ISO 8601 UTC
    "path": "hot",                        # hot | warm | cold
    "op": "assign",                       # operation type (see table)
    "decision": "merge_into",             # outcome
    "cluster_id": "abc-123",              # affected cluster (nullable)
    "optimization_id": "opt-456",         # triggering optimization (nullable)
    "duration_ms": 12,                    # wall-clock (nullable)
    "context": { ... }                    # operation-specific (see below)
}
```

**Operation types and context schemas:**

| Op | Path | Context keys |
|---|---|---|
| `assign` | hot | `candidates` [{id, label, raw_score, penalties {coherence, output_coh, task_type, size_pressure}, threshold, effective_score}], `winner_id` (or null if new), `new_cluster` (bool), `prompt_domain`, `prompt_task_type` |
| `split` | warm/cold | `trigger` (coherence_floor/mega_cluster), `coherence`, `floor`, `hdbscan_clusters`, `noise_count`, `silhouette`, `children` [{id, label, members, coherence}], `fallback` (kmeans/none) |
| `merge` | warm | `pair` [id_a, id_b], `labels` [label_a, label_b], `similarity`, `threshold`, `gate` (passed/coherence_floor/output_floor/merge_protected/split_protected), `survivor_id`, `combined_members` |
| `retire` | warm | `node_label`, `member_count_before`, `sibling_target_id`, `sibling_label`, `families_reparented`, `optimizations_reassigned` |
| `phase` | warm | `phase_name`, `phase_idx`, `q_before`, `q_after`, `delta`, `accepted`, `operations` (summary list), `rejection_count` |
| `refit` | cold | `clusters_input`, `hdbscan_clusters`, `mega_splits`, `blended_weights` {raw, optimized, transform}, `q_before`, `q_after`, `accepted` |
| `emerge` | warm | `family_id`, `member_count`, `coherence`, `domain`, `parent_id` |
| `discover` | warm | `domain_label`, `seed_cluster_id`, `consistency_pct`, `members_reparented`, `total_domains_after` |
| `error` | any | `source` (assign/split/merge/refit/phase/...), `error_type` (hdbscan_failure/llm_timeout/db_error/embedding_error/label_generation), `error_message`, `recovery` (skipped/fallback_kmeans/rollback/none), `stack_trace` (first 500 chars) |

### Instrumentation Points

**Hot path** (engine.py + family_ops.py) — 3 points:
1. `assign_cluster()` final decision — single event with all candidate evaluations (scores, penalties, thresholds) bundled in `candidates` array, plus final outcome (merge_into winner or create_new). Cross-domain blocks included as candidates with `gate: "cross_domain"`
2. `process_optimization()` error catch — embedding failure, assignment failure → error event
3. `process_optimization()` success — log optimization_id, cluster assignment, meta-pattern extraction count

**Warm path** (warm_phases.py + warm_path.py) — 8 points:
5. `_run_speculative_phase()` Q-gate result — q_before, q_after, accepted, epsilon, rejection_count
6. `phase_split_emerge()` per split — coherence vs floor, HDBSCAN result, children
7. `phase_merge()` per merge candidate — similarity, threshold, gate outcome
8. `phase_merge()` merge execution — survivor, combined metrics
9. `phase_retire()` per retirement — sibling selection, reassignment counts
10. `phase_discover()` domain creation — seed, consistency, reparented
11. `phase_reconcile()` zombie archival — count, node IDs
12. `phase_refresh()` stale re-extraction — count

**Cold path** (cold_path.py + split.py) — 6 points:
13. `execute_cold_path()` HDBSCAN refit result — input/output cluster counts, Q delta
14. `execute_cold_path()` mega-cluster detection — cluster IDs, member counts
15. `split_cluster()` HDBSCAN member result — n_clusters, noise, fallback trigger
16. `split_cluster()` per-child creation — label, members, coherence, protection timestamp
17. `split_cluster()` noise reassignment — count per child
18. `split_cluster()` error — HDBSCAN failure, LLM timeout → error event with recovery action

### API Endpoints

Added to `routers/clusters.py`:

**GET /api/clusters/activity**
```
Query: limit (default 50, max 200), path (hot|warm|cold), op (assign|split|...), errors_only (bool)
Response: { events: [...], total_in_buffer: int, oldest_ts: str | null }
```
Reads from ring buffer. Sub-millisecond response.

**GET /api/clusters/activity/history**
```
Query: date (YYYY-MM-DD, required), limit (default 100, max 500), offset (default 0)
Response: { events: [...], total: int, has_more: bool }
```
Reads from JSONL file. Paginated.

### SSE Integration

New event type `taxonomy_activity` on existing `/api/events` stream. Payload is the full event dict. Frontend SSE handler dispatches to ActivityPanel component.

### Frontend Activity Panel

**New component:** `frontend/src/lib/components/taxonomy/ActivityPanel.svelte`

Collapsible bottom panel below the 3D topology view. Follows existing Svelte 5 runes patterns.

**Features:**
- Filter chips: path (hot/warm/cold), operation type, errors-only toggle
- Scrollable event list (newest first), capped at 200 client-side
- Each event row: timestamp, colored operation badge, decision badge, cluster label, key metric
- Expandable detail: full context dict as key-value grid
- Click cluster label → select in topology (dispatches custom event)
- Auto-scroll toggle ("pin to bottom")
- Color coding: green (accept/merge_into), blue (create_new), amber (reject/skip), red (error)

**Data flow:**
1. On mount: `GET /api/clusters/activity` seeds initial events
2. SSE `taxonomy_activity` → prepend to `$state` array (cap at 200)
3. Filters via `$derived` computed from state array
4. Cluster click → `window.dispatchEvent(new CustomEvent('select-cluster', {detail: {id}}))`

**Integration point:** `SemanticTopology.svelte` or its parent adds the ActivityPanel below the canvas, toggled by a button in `TopologyControls.svelte`.

## Files Modified

| File | Change |
|---|---|
| `backend/app/services/taxonomy/event_logger.py` | **New** — TaxonomyEventLogger service |
| `backend/app/services/taxonomy/family_ops.py` | Instrument assign_cluster (1 bundled decision event) |
| `backend/app/services/taxonomy/warm_phases.py` | Instrument phases (6 log points) |
| `backend/app/services/taxonomy/warm_path.py` | Instrument Q-gate + reconcile/refresh (2 log points) |
| `backend/app/services/taxonomy/cold_path.py` | Instrument refit + mega detection (2 log points) |
| `backend/app/services/taxonomy/split.py` | Instrument split decisions (4 log points) |
| `backend/app/services/taxonomy/engine.py` | Initialize logger in lifespan, instrument error catch |
| `backend/app/routers/clusters.py` | Add /activity and /activity/history endpoints |
| `backend/app/main.py` | Initialize event_logger in lifespan, add rotation |
| `frontend/src/lib/components/taxonomy/ActivityPanel.svelte` | **New** — collapsible event panel |
| `frontend/src/lib/components/taxonomy/TopologyControls.svelte` | Add activity toggle button |
| `frontend/src/routes/app/+page.svelte` | Handle taxonomy_activity SSE event |

## Verification

1. **Backend event capture:** Start services, optimize a prompt, verify JSONL file created in `data/taxonomy_events/` with assign event containing all penalty fields
2. **Ring buffer:** Hit `GET /api/clusters/activity` — should return the assign event
3. **SSE streaming:** Open browser, watch Network tab for `taxonomy_activity` events on `/api/events` stream
4. **Warm path events:** Trigger recluster, verify phase events with q_before/q_after and split/merge events with full context
5. **Error events:** Simulate failure (e.g., corrupt embedding), verify error event with recovery action
6. **Frontend panel:** Open topology view, toggle Activity panel, verify events appear in real-time with correct color coding
7. **Filtering:** Apply path/op filters, verify list updates reactively
8. **Cluster navigation:** Click cluster label in event, verify topology selects that cluster
9. **History:** Check `GET /api/clusters/activity/history?date=2026-04-03` returns persisted events
10. **Rotation:** Verify `rotate(retention_days=1)` cleans old files
