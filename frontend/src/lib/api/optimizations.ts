/**
 * Optimization REST client — delete surface.
 *
 * Two distinct HTTP endpoints:
 *
 *   DELETE /api/optimizations/{id}       — shipped in v0.4.2, always available.
 *                                          Hit by `deleteOptimization(id)`.
 *   POST   /api/optimizations/delete     — shipped in v0.4.3, bulk endpoint.
 *                                          Hit by `deleteOptimizations(ids)`.
 *
 * The single-id call does NOT proxy through the bulk endpoint — if it did,
 * a backend that predates v0.4.3 would 404 on both calls and the delete
 * UX would break. Keeping them on separate endpoints lets the UI degrade
 * gracefully: when the bulk POST 404s, HistoryPanel falls back to per-id
 * DELETEs via `deleteOptimization`, which hits the always-available route.
 *
 * Both functions go through `apiFetch` so `BASE_URL` (which resolves to
 * `http://localhost:8000/api` in dev and relative `/api` in prod) is
 * applied uniformly. Writing `fetch('/api/...')` directly would bypass
 * the dev-time backend target and hit the frontend port (5199) instead,
 * which silently 404s with no proxy configured in `vite.config.ts`.
 *
 * Preflight validation mirrors the backend Pydantic constraints on the
 * bulk endpoint so the UI shows a friendly error before any HTTP call.
 */

import { ApiError, apiFetch } from './client';

export type DeleteResult = {
  deleted: number;
  requested: number;
  affected_cluster_ids: string[];
  affected_project_ids: string[];
};

export { ApiError };

// Keep in sync with backend/app/routers/history.py :: BulkDeleteRequest.ids
// (min_length=1, max_length=100). Changing the backend constraint requires
// mirroring here so the preflight error message stays accurate.
const MAX_BULK = 100;
const MIN_BULK = 1;

/**
 * Delete a single optimization via `DELETE /api/optimizations/{id}`.
 *
 * Uses the v0.4.2 per-id endpoint directly (not the v0.4.3 bulk POST), so
 * the call succeeds on backends that haven't been restarted with the newer
 * bulk handler registered. Response mirrors the bulk envelope with
 * `requested: 1`. 404 means the id doesn't exist in the DB — callers
 * interpret that as "already deleted elsewhere".
 */
export async function deleteOptimization(
  id: string,
  _reason?: string,
): Promise<DeleteResult> {
  return apiFetch<DeleteResult>(
    `/optimizations/${encodeURIComponent(id)}`,
    { method: 'DELETE' },
  );
}

/**
 * Delete up to 100 optimizations in a single call via
 * `POST /api/optimizations/delete`.
 *
 * Emits one aggregated `taxonomy_changed` SSE event per call (vs one per
 * id if we looped single-deletes), so taxonomy reconciliation is
 * efficient. Returns 422 preflight-locally when outside the 1..100 range.
 * The callers (HistoryPanel) fall back to per-id `deleteOptimization` on
 * 404, which covers the case where the bulk endpoint isn't registered on
 * the running backend (deployment hasn't been restarted).
 */
export async function deleteOptimizations(
  ids: string[],
  reason: string = 'user_request',
): Promise<DeleteResult> {
  if (ids.length < MIN_BULK || ids.length > MAX_BULK) {
    throw new ApiError(
      422,
      `Bulk delete requires ${MIN_BULK}-${MAX_BULK} ids at a time.`,
    );
  }
  return apiFetch<DeleteResult>('/optimizations/delete', {
    method: 'POST',
    body: JSON.stringify({ ids, reason }),
  });
}
