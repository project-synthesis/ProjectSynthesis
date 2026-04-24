import { describe, it, expect, vi, beforeEach } from 'vitest';
import {
  deleteOptimization,
  deleteOptimizations,
  ApiError,
} from './optimizations';

describe('api/optimizations', () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
  });

  it('deleteOptimizations posts ids and returns typed envelope', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          deleted: 2,
          requested: 2,
          affected_cluster_ids: ['c1'],
          affected_project_ids: [],
        }),
        { status: 200, headers: { 'content-type': 'application/json' } },
      ),
    );
    vi.stubGlobal('fetch', fetchMock);

    const result = await deleteOptimizations(['a', 'b']);
    expect(result.deleted).toBe(2);
    expect(result.requested).toBe(2);
    expect(result.affected_cluster_ids).toEqual(['c1']);

    // URL resolved through BASE_URL (apiFetch) — suffix `/optimizations/delete`
    // is what matters; the host prefix depends on build env.
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toMatch(/\/optimizations\/delete$/);
    expect(init).toMatchObject({
      method: 'POST',
      body: JSON.stringify({ ids: ['a', 'b'], reason: 'user_request' }),
    });
  });

  it('deleteOptimization hits the v0.4.2 per-id DELETE endpoint (not bulk)', async () => {
    // Regression for the delete-fallback bug: deleteOptimization MUST hit
    // `DELETE /api/optimizations/{id}` directly. Proxying through the bulk
    // POST would defeat the fallback — if the bulk endpoint 404s on a
    // backend that predates v0.4.3, per-id calls would 404 too.
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({ deleted: 1, requested: 1, affected_cluster_ids: [], affected_project_ids: [] }),
        { status: 200, headers: { 'content-type': 'application/json' } },
      ),
    );
    vi.stubGlobal('fetch', fetchMock);

    const result = await deleteOptimization('xyz');
    expect(result.deleted).toBe(1);
    expect(fetchMock).toHaveBeenCalledOnce();
    const [url, init] = fetchMock.mock.calls[0];
    // URL resolved through BASE_URL (apiFetch) — suffix must be the per-id
    // DELETE path so we hit the v0.4.2 endpoint, not the v0.4.3 bulk POST.
    expect(url).toMatch(/\/optimizations\/xyz$/);
    expect(init).toMatchObject({ method: 'DELETE' });
  });

  it('deleteOptimization url-encodes the id to handle edge-case characters', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({ deleted: 1, requested: 1, affected_cluster_ids: [], affected_project_ids: [] }),
        { status: 200, headers: { 'content-type': 'application/json' } },
      ),
    );
    vi.stubGlobal('fetch', fetchMock);

    await deleteOptimization('abc/xyz?weird');
    expect(fetchMock.mock.calls[0][0]).toMatch(/\/optimizations\/abc%2Fxyz%3Fweird$/);
  });

  it('deleteOptimization throws ApiError with status 404 when the row is gone', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ detail: 'Optimization not found' }), {
        status: 404, headers: { 'content-type': 'application/json' },
      }),
    ));
    await expect(deleteOptimization('missing')).rejects.toMatchObject({ status: 404 });
    await expect(deleteOptimization('missing')).rejects.toBeInstanceOf(ApiError);
  });

  it('throws ApiError with status on non-2xx', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ detail: 'not found' }), {
        status: 404, headers: { 'content-type': 'application/json' },
      }),
    ));

    await expect(deleteOptimizations(['missing'])).rejects.toMatchObject({
      status: 404,
    });
    await expect(deleteOptimizations(['missing'])).rejects.toBeInstanceOf(ApiError);
  });

  it('preflights oversized ids before calling fetch', async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);

    const oversized = Array.from({ length: 101 }, (_, i) => `id-${i}`);

    await expect(deleteOptimizations(oversized)).rejects.toMatchObject({
      status: 422,
    });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('preflights empty ids before calling fetch', async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);

    await expect(deleteOptimizations([])).rejects.toMatchObject({ status: 422 });
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
