// frontend/src/lib/api/suites.ts
//
// Topic Probe Tier 2 (v0.4.22): client surface for the ValidationSuite REST
// API. Per-domain module — keeps the suite save/replay/retire/list calls
// off the cross-cutting `client.ts` typedef wall and lets test files stub
// `$lib/api/suites` directly without affecting other API surfaces.
//
// Backend reference:
//   - schemas/validation_suite.py — Pydantic shapes
//   - routers/suites.py — endpoint handlers
//
// Pagination envelope matches `RunListResponse` in api/runs.ts. Status
// strings ('running'/'completed'/'failed'/'partial') match the unified
// `RunRow.status` enum so replay rows surface through GET /api/runs too.

import { apiFetch } from './client';
import type { RunListResponse } from './runs';

// ---- Types --------------------------------------------------------------

/** Frozen prompt snapshot row — one per saved prompt in the suite. */
export interface PromptSnapshotItem {
  raw_prompt: string;
  intent_label: string | null;
  original_optimization_id: string | null;
  /** v0.4.37 — full-length baseline optimized output (≤20,000 chars);
   *  null/absent for pre-v0.4.37 suites and sources without output. */
  baseline_optimized_prompt?: string | null;
}

/** Per-prompt scoring snapshot stored on the suite at save time. */
export interface PerPromptScore {
  raw_prompt_idx: number;
  overall: number;
  dimensions: Record<string, number>;
}

/**
 * Shape of `validation_suite.baseline_scores`. Key names match the canonical
 * `compute_run_aggregate` output verbatim — `p5_overall` / `p50_overall` /
 * `p95_overall` (NOT `p5`/`p50`/`p95`) so downstream consumers reading
 * `RunRow.aggregate` stay compatible without a key-shape adapter.
 */
export interface BaselineScoresPayload {
  mean_overall: number;
  p5_overall: number;
  p50_overall: number;
  p95_overall: number;
  per_prompt: PerPromptScore[];
  task_type_distribution: Record<string, number>;
}

/** POST /api/probes/{run_id}/save-as-suite body. */
export interface SaveSuiteRequest {
  label: string;
  tolerance_abs?: number;
}

/** POST /api/suites/{id}/retire body. */
export interface RetireSuiteRequest {
  reason: string;
}

/** Full ValidationSuite read response. Backs GET /api/suites/{id} +
 *  POST /api/probes/{run_id}/save-as-suite. */
export interface ValidationSuiteOut {
  id: string;
  source_run_id: string | null;
  label: string;
  tolerance_abs: number;
  project_id: string | null;
  repo_full_name: string | null;
  created_at: string;
  retired_at: string | null;
  retired_reason: string | null;
  prompts_snapshot: PromptSnapshotItem[];
  baseline_scores: BaselineScoresPayload;
}

/** Abbreviated list-row view — backs GET /api/suites items. */
export interface ValidationSuiteListItem {
  id: string;
  source_run_id: string | null;
  label: string;
  tolerance_abs: number;
  project_id: string | null;
  repo_full_name: string | null;
  created_at: string;
  retired_at: string | null;
  prompts_count: number;
  baseline_mean: number;
}

/** Pagination envelope mirroring `RunListResponse`. */
export interface ValidationSuiteListResponse {
  total: number;
  count: number;
  offset: number;
  items: ValidationSuiteListItem[];
  has_more: boolean;
  next_offset: number | null;
}

/** Returned by POST /api/suites/{id}/replay at 202-Accepted time. */
export interface ReplayRunOut {
  run_id: string;
  suite_id: string;
  mode?: 'replay_run';
  status: 'running' | 'completed' | 'failed' | 'partial';
  started_at?: string;
  poll_url: string;
}

export interface ListSuitesParams {
  project_id?: string;
  include_retired?: boolean;
  limit?: number;
  offset?: number;
}

export interface ListReplaysParams {
  limit?: number;
  offset?: number;
}

// ---- Endpoints ----------------------------------------------------------

/**
 * POST /api/probes/{run_id}/save-as-suite — freeze a completed topic-probe
 * run's prompts + aggregate scores as a regression-testable ValidationSuite.
 * Idempotent on (source_run_id, label) — re-saving the same run with the
 * same label returns the existing row.
 */
export async function saveSuite(
  runId: string,
  body: SaveSuiteRequest,
): Promise<ValidationSuiteOut> {
  return apiFetch<ValidationSuiteOut>(
    `/probes/${encodeURIComponent(runId)}/save-as-suite`,
    { method: 'POST', body: JSON.stringify(body) },
  );
}

/**
 * Alias for `saveSuite` matching the test-mock surface name. The
 * TopicProbeReportCard tests stub `$lib/api/suites.saveAsSuite` — both names
 * resolve to the same network call so call sites can pick the more
 * grammatically natural form.
 */
export async function saveAsSuite(
  runId: string,
  body: SaveSuiteRequest,
): Promise<ValidationSuiteOut> {
  return saveSuite(runId, body);
}

/**
 * POST /api/suites/{id}/replay — dispatch a ReplayRunGenerator against the
 * frozen suite. Returns 202 with a poll_url; the run completes asynchronously
 * via WriteQueue + RunOrchestrator and is observable through GET /api/runs
 * filtered by mode='replay_run'.
 */
export async function replaySuite(suiteId: string): Promise<ReplayRunOut> {
  return apiFetch<ReplayRunOut>(
    `/suites/${encodeURIComponent(suiteId)}/replay`,
    { method: 'POST' },
  );
}

/**
 * GET /api/suites — paginated suite list. `include_retired=false` (default)
 * surfaces only `retired_at IS NULL` rows; pass `true` for the operator/
 * audit view that includes retired suites.
 */
export async function getSuites(
  params?: ListSuitesParams,
): Promise<ValidationSuiteListResponse> {
  const qs = new URLSearchParams();
  if (params?.project_id) qs.set('project_id', params.project_id);
  if (params?.include_retired !== undefined) {
    qs.set('include_retired', String(params.include_retired));
  }
  if (params?.limit !== undefined) qs.set('limit', String(params.limit));
  if (params?.offset !== undefined) qs.set('offset', String(params.offset));
  const suffix = qs.toString() ? `?${qs.toString()}` : '';
  return apiFetch<ValidationSuiteListResponse>(`/suites${suffix}`);
}

/** GET /api/suites/{id} — full suite read (prompts + baseline scores). */
export async function getSuite(suiteId: string): Promise<ValidationSuiteOut> {
  return apiFetch<ValidationSuiteOut>(
    `/suites/${encodeURIComponent(suiteId)}`,
  );
}

/**
 * GET /api/suites/{id}/replays — paginated list of replay runs against this
 * suite, ordered started_at desc. Re-uses `RunListResponse` envelope since
 * replay rows live in the unified `RunRow` substrate.
 */
export async function getSuiteReplays(
  suiteId: string,
  params?: ListReplaysParams,
): Promise<RunListResponse> {
  const qs = new URLSearchParams();
  if (params?.limit !== undefined) qs.set('limit', String(params.limit));
  if (params?.offset !== undefined) qs.set('offset', String(params.offset));
  const suffix = qs.toString() ? `?${qs.toString()}` : '';
  return apiFetch<RunListResponse>(
    `/suites/${encodeURIComponent(suiteId)}/replays${suffix}`,
  );
}

/**
 * POST /api/suites/{id}/retire — flag a suite as retired (no destructive
 * delete). `reason` is required and persisted to `retired_reason`. Replay
 * dispatch against a retired suite returns 410 Gone.
 */
export async function retireSuite(
  suiteId: string,
  reason: string,
): Promise<ValidationSuiteOut> {
  const body: RetireSuiteRequest = { reason };
  return apiFetch<ValidationSuiteOut>(
    `/suites/${encodeURIComponent(suiteId)}/retire`,
    { method: 'POST', body: JSON.stringify(body) },
  );
}
