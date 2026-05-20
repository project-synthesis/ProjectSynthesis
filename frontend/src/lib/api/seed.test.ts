import { describe, it, expect, vi, beforeEach } from 'vitest';
import { seedTaxonomy, listSeedAgents, type SeedOutput, type SeedAgent } from './seed';

const SEED_OK: SeedOutput = {
  status: 'completed',
  batch_id: 'batch-1',
  tier: 'internal',
  prompts_generated: 10,
  prompts_optimized: 8,
  prompts_failed: 2,
  estimated_cost_usd: 0.42,
  domains_touched: ['backend'],
  // T3.3 (v0.4.30) additive field; default to empty in fixtures.
  clusters: [],
  clusters_created: 3,
  summary: 'ok',
  duration_ms: 5000,
  run_id: 'run-uuid',
};

function mockJsonFetch<T>(body: T) {
  const fetchMock = vi.fn().mockResolvedValue(
    new Response(JSON.stringify(body), {
      status: 200,
      headers: { 'content-type': 'application/json' },
    }),
  );
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

describe('api/seed', () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
  });

  it('seedTaxonomy POSTs to /seed with the request body and decodes SeedOutput', async () => {
    const fetchMock = mockJsonFetch(SEED_OK);
    const result = await seedTaxonomy({
      project_description: 'demo',
      prompt_count: 10,
    });

    expect(result.status).toBe('completed');
    expect(result.run_id).toBe('run-uuid');
    expect(result.prompts_optimized).toBe(8);

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toMatch(/\/seed$/);
    expect(init.method).toBe('POST');
    expect(JSON.parse(init.body as string)).toMatchObject({
      project_description: 'demo',
      prompt_count: 10,
    });
  });

  it('seedTaxonomy preserves null run_id and null batch_id from degraded path', async () => {
    // Regression: orchestrator-unavailable path returns null batch_id/tier/run_id;
    // SeedModal renders these conditionally, so the type contract matters.
    mockJsonFetch({ ...SEED_OK, status: 'failed', batch_id: null, tier: null, run_id: null });
    const result = await seedTaxonomy({ project_description: 'd' });
    expect(result.batch_id).toBeNull();
    expect(result.tier).toBeNull();
    expect(result.run_id).toBeNull();
  });

  it('listSeedAgents GETs /seed/agents and returns the agent list', async () => {
    const agents: SeedAgent[] = [
      {
        name: 'code-explorer',
        description: 'codebase',
        task_types: ['coding'],
        prompts_per_run: 10,
        enabled: true,
      },
    ];
    const fetchMock = mockJsonFetch(agents);
    const result = await listSeedAgents();

    expect(result).toEqual(agents);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit | undefined];
    expect(url).toMatch(/\/seed\/agents$/);
    // Default fetch is GET
    expect(init?.method ?? 'GET').toBe('GET');
  });
});
