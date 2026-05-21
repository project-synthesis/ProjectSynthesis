// frontend/src/lib/api/runs.ts
//
// Foundation P3 (v0.4.18): client surface for the unified RunRow substrate.
// Backs the inline "Recent Runs" hint inside SeedModal + the suites
// surface's "latest replay" warning resolver. A dedicated Runs panel is
// T4 scope — this module is intentionally minimal.
//
// Backend reference: backend/app/routers/runs.py + backend/app/routers/seed.py
// (extended). Both endpoints share the RunListResponse pagination envelope.

import { apiFetch } from './client';

/** Mirrors backend `RunSummary` (compact list view). */
export interface RunSummary {
  id: string;
  // v0.4.22 T2 ReplayRunGenerator extends the union with 'replay_run'. The
  // backend persists replay rows in the same RunRow substrate so the
  // existing /api/runs paginator surfaces them without a parallel envelope.
  mode: 'topic_probe' | 'seed_agent' | 'replay_run';
  status: 'running' | 'completed' | 'failed' | 'partial';
  started_at: string;
  completed_at: string | null;
  project_id: string | null;
  repo_full_name: string | null;
  topic: string | null;
  /** v0.4.32 — operator-writable label. Renders as `display_name ?? topic ?? id`. */
  display_name?: string | null;
  intent_hint: string | null;
  prompts_generated: number;
}

/**
 * Full RunRow detail view returned by `GET /api/runs/{run_id}` — mirrors
 * `schemas/runs.py::RunResult`.
 *
 * Used by the suites surface to surface `aggregate.replay_warnings`. Per
 * spec § 4 `suite_repo_drift` clarification + plan task 12.4 INTEGRATE A3:
 * the warning surfacing reads `aggregate.replay_warnings` from the polled
 * detail row — NOT from the immediate 202 dispatch response. This keeps
 * the warning truth source-of-record consistent between the SSE/poll
 * paths.
 */
export interface RunResult extends RunSummary {
  error: string | null;
  prompt_results: Array<{
    prompt_index: number;
    overall_score: number;
    raw_prompt?: string;
    [key: string]: unknown;
  }>;
  // The aggregate block is a dict on the backend (8-key block from
  // `compute_run_aggregate` plus replay-additive keys). The shape is
  // open-ended so consumers narrow with optional chaining rather than
  // brittle field assertions.
  aggregate: {
    mean_overall?: number;
    p5_overall?: number;
    p50_overall?: number;
    p95_overall?: number;
    completed_count?: number;
    failed_count?: number;
    // Replay-additive keys (additive contract per spec § 5):
    replay_warnings?: string[];
    replay_suite_id?: string | null;
    replay_n_completed?: number;
    replay_n_failed?: number;
    [key: string]: unknown;
  };
  taxonomy_delta: Record<string, unknown>;
  final_report: string;
  suite_id: string | null;
  topic_probe_meta: Record<string, unknown> | null;
  seed_agent_meta: Record<string, unknown> | null;
}

/** Paginated list envelope (matches `RunListResponse`). */
export interface RunListResponse {
  total: number;
  count: number;
  offset: number;
  items: RunSummary[];
  has_more: boolean;
  next_offset: number | null;
}

export interface ListRunsParams {
  // v0.4.31 T4: 3-mode union (add 'replay_run' so listRuns({mode:'replay_run'}) is type-safe)
  mode?: 'topic_probe' | 'seed_agent' | 'replay_run';
  // v0.4.31 T4: typed status enum matching RunSummary.status (was: string)
  status?: 'running' | 'completed' | 'partial' | 'failed';
  project_id?: string;
  limit?: number;
  offset?: number;
}

/**
 * GET /api/runs — paginated, filterable list of run summaries ordered by
 * `started_at desc`. Mode-agnostic (returns both topic_probe + seed_agent
 * + replay_run when unfiltered). Used by the Recent Runs counter in
 * SeedModal + the SuitesPanel replay-history table.
 */
export async function listRuns(
  params?: ListRunsParams,
): Promise<RunListResponse> {
  const qs = new URLSearchParams();
  if (params?.mode) qs.set('mode', params.mode);
  if (params?.status) qs.set('status', params.status);
  if (params?.project_id) qs.set('project_id', params.project_id);
  if (params?.limit !== undefined) qs.set('limit', String(params.limit));
  if (params?.offset !== undefined) qs.set('offset', String(params.offset));
  const suffix = qs.toString() ? `?${qs.toString()}` : '';
  return apiFetch<RunListResponse>(`/runs${suffix}`);
}

/**
 * GET /api/runs/{run_id} — full RunRow detail view including
 * `prompt_results` and the `aggregate` block (with replay-additive
 * `replay_warnings` / `replay_suite_id` / `replay_n_completed` /
 * `replay_n_failed` keys per spec § 5).
 *
 * The suites surface uses this to resolve the latest replay row, pair
 * its per-prompt scores against the suite baseline, and surface any
 * `aggregate.replay_warnings` (e.g. `suite_repo_drift`) per spec § 4.
 */
export async function getRun(runId: string): Promise<RunResult> {
  return apiFetch<RunResult>(`/runs/${encodeURIComponent(runId)}`);
}

// ---------------------------------------------------------------------------
// v0.4.32 — RunsPanel polish: delete, rename, bulk-delete, bulk-export
// ---------------------------------------------------------------------------

/**
 * DELETE /api/runs/{id} — hard-delete a RunRow. Cascade-safe: the suite's
 * source_run_id FK is `ondelete=SET NULL` so the suite is preserved.
 */
export async function deleteRun(id: string): Promise<void> {
  await apiFetch<void>(`/runs/${encodeURIComponent(id)}`, { method: 'DELETE' });
}

/**
 * PATCH /api/runs/{id} — set the operator-writable `display_name` label.
 * Empty string and `null` both clear the rename.
 */
export async function patchRun(
  id: string,
  patch: { display_name: string | null },
): Promise<RunSummary> {
  return apiFetch<RunSummary>(`/runs/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(patch),
  });
}

/**
 * POST /api/runs/bulk-delete — single-transaction bulk-delete.
 * Server caps at 200 ids per request.
 */
export async function bulkDeleteRuns(
  ids: string[],
): Promise<{ deleted: string[]; not_found: string[] }> {
  return apiFetch<{ deleted: string[]; not_found: string[] }>(`/runs/bulk-delete`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ids }),
  });
}

/**
 * POST /api/runs/bulk-export — read-only JSON export of full RunResult
 * shapes. Missing ids are silently omitted. Server caps at 200 ids.
 */
export async function bulkExportRuns(ids: string[]): Promise<RunResult[]> {
  return apiFetch<RunResult[]>(`/runs/bulk-export`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ids }),
  });
}
