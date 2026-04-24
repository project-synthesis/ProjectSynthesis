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
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          deleted: 2,
          requested: 2,
          affected_cluster_ids: ['c1'],
          affected_project_ids: [],
        }),
        { status: 200, headers: { 'content-type': 'application/json' } },
      ),
    ));

    const result = await deleteOptimizations(['a', 'b']);
    expect(result.deleted).toBe(2);
    expect(result.requested).toBe(2);
    expect(result.affected_cluster_ids).toEqual(['c1']);

    expect(fetch).toHaveBeenCalledWith(
      '/api/optimizations/delete',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ ids: ['a', 'b'], reason: 'user_request' }),
      }),
    );
  });

  it('deleteOptimization is a single-id shim going through bulk', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({ deleted: 1, requested: 1, affected_cluster_ids: [], affected_project_ids: [] }),
        { status: 200, headers: { 'content-type': 'application/json' } },
      ),
    );
    vi.stubGlobal('fetch', fetchMock);

    await deleteOptimization('xyz');
    expect(fetchMock).toHaveBeenCalledOnce();
    const body = JSON.parse(fetchMock.mock.calls[0][1].body);
    expect(body.ids).toEqual(['xyz']);
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
