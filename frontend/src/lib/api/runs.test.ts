import { describe, it, expect, vi, beforeEach } from 'vitest';
import { listRuns, type RunListResponse } from './runs';

const ENVELOPE: RunListResponse = {
  total: 0,
  count: 0,
  offset: 0,
  items: [],
  has_more: false,
  next_offset: null,
};

function mockFetch(envelope: Partial<RunListResponse> = {}) {
  const fetchMock = vi.fn().mockResolvedValue(
    new Response(JSON.stringify({ ...ENVELOPE, ...envelope }), {
      status: 200,
      headers: { 'content-type': 'application/json' },
    }),
  );
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

describe('api/runs.listRuns', () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
  });

  it('issues GET /runs with no querystring when no params supplied', async () => {
    const fetchMock = mockFetch();
    const result = await listRuns();
    expect(result).toMatchObject(ENVELOPE);

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toMatch(/\/runs$/);
    expect(init?.method ?? 'GET').toBe('GET');
  });

  it('threads each filter into the querystring exactly once', async () => {
    const fetchMock = mockFetch();
    await listRuns({
      mode: 'topic_probe',
      status: 'running',
      project_id: 'proj-123',
      limit: 10,
      offset: 20,
    });
    const [url] = fetchMock.mock.calls[0] as [string];
    expect(url).toMatch(/mode=topic_probe/);
    expect(url).toMatch(/status=running/);
    expect(url).toMatch(/project_id=proj-123/);
    expect(url).toMatch(/limit=10/);
    expect(url).toMatch(/offset=20/);
    // Querystring must start with '?'
    expect(url).toMatch(/\?[^?]+$/);
  });

  it('omits undefined params (does not produce empty key=)', async () => {
    const fetchMock = mockFetch();
    await listRuns({ mode: 'seed_agent' });
    const [url] = fetchMock.mock.calls[0] as [string];
    expect(url).toMatch(/mode=seed_agent/);
    expect(url).not.toMatch(/status=/);
    expect(url).not.toMatch(/project_id=/);
    expect(url).not.toMatch(/limit=/);
    expect(url).not.toMatch(/offset=/);
  });

  it('sends limit=0 explicitly when caller passes 0 (treats 0 as a real value)', async () => {
    // Regression: `if (params?.limit !== undefined)` — easy to mistake for
    // `if (params?.limit)` which would drop 0. 0 must reach the backend
    // because some callers want a count-only response.
    const fetchMock = mockFetch();
    await listRuns({ limit: 0, offset: 0 });
    const [url] = fetchMock.mock.calls[0] as [string];
    expect(url).toMatch(/limit=0/);
    expect(url).toMatch(/offset=0/);
  });

  it('decodes a populated envelope into typed RunSummary items', async () => {
    mockFetch({
      total: 1,
      count: 1,
      items: [
        {
          id: 'run-abc',
          mode: 'seed_agent',
          status: 'completed',
          started_at: '2026-05-09T00:00:00Z',
          completed_at: '2026-05-09T00:01:00Z',
          project_id: null,
          repo_full_name: null,
          topic: null,
          intent_hint: null,
          prompts_generated: 5,
        },
      ],
    });
    const result = await listRuns({ limit: 10 });
    expect(result.items).toHaveLength(1);
    expect(result.items[0].mode).toBe('seed_agent');
    expect(result.items[0].status).toBe('completed');
    expect(result.items[0].prompts_generated).toBe(5);
  });
});
