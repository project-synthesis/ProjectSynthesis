// frontend/src/lib/api/runs.ts
//
// Foundation P3 (v0.4.18): client surface for the unified RunRow substrate.
// Backs the inline "Recent Runs" hint inside SeedModal. A dedicated Runs
// panel is T4 scope — this module is intentionally minimal.
//
// Backend reference: backend/app/routers/runs.py + backend/app/routers/seed.py
// (extended). Both endpoints share the RunListResponse pagination envelope.

import { apiFetch } from './client';

/** Mirrors backend `RunSummary` (compact list view). */
export interface RunSummary {
  id: string;
  mode: 'topic_probe' | 'seed_agent';
  status: 'running' | 'completed' | 'failed' | 'partial';
  started_at: string;
  completed_at: string | null;
  project_id: string | null;
  repo_full_name: string | null;
  topic: string | null;
  intent_hint: string | null;
  prompts_generated: number;
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
  mode?: 'topic_probe' | 'seed_agent';
  status?: string;
  project_id?: string;
  limit?: number;
  offset?: number;
}

/**
 * GET /api/runs — paginated, filterable list of run summaries ordered by
 * `started_at desc`. Mode-agnostic (returns both topic_probe + seed_agent
 * when unfiltered). Used by the Recent Runs counter in SeedModal.
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
