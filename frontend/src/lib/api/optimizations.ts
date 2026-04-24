/**
 * Optimization REST client — delete surface only (v0.4.3).
 *
 * The UI always uses the bulk endpoint. Single-row callers go through
 * the `deleteOptimization(id)` shim (ids=[id]) for a single codepath.
 * Preflight validation catches the backend's min/max constraints before
 * the HTTP call, so the UI can show a friendly error immediately.
 */

import { ApiError } from './client';

export type DeleteResult = {
  deleted: number;
  requested: number;
  affected_cluster_ids: string[];
  affected_project_ids: string[];
};

export { ApiError };

const MAX_BULK = 100;
const MIN_BULK = 1;

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
  const res = await fetch('/api/optimizations/delete', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ ids, reason }),
  });
  if (!res.ok) {
    let body: unknown = null;
    try {
      body = await res.json();
    } catch {
      body = null;
    }
    const detail =
      typeof body === 'object' && body !== null && 'detail' in body
        ? String((body as { detail: unknown }).detail)
        : res.statusText;
    throw new ApiError(res.status, detail);
  }
  return (await res.json()) as DeleteResult;
}

export const deleteOptimization = (id: string, reason?: string) =>
  deleteOptimizations([id], reason);
