/// <reference types="vitest/globals" />
import { render, screen, cleanup, within } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';

export { render, screen, cleanup, within, userEvent };

// ── Mock response factories ──────────────────────────────────────

export function mockHealthResponse(overrides: Record<string, unknown> = {}) {
  return {
    status: 'ok',
    version: '0.2.0',
    provider: 'claude-cli',
    score_health: { last_n_mean: 7.5, last_n_stddev: 0.8, count: 10, clustering_warning: false },
    avg_duration_ms: 3200,
    phase_durations: { analyzing: 800, optimizing: 1800, scoring: 600 },
    recent_errors: { last_hour: 0, last_24h: 2 },
    sampling_capable: null,
    mcp_disconnected: false,
    available_tiers: ['internal', 'passthrough'],
    ...overrides,
  };
}

export function mockDimensionScores(overrides: Record<string, number> = {}) {
  return {
    clarity: 7.5,
    specificity: 8.0,
    structure: 7.0,
    faithfulness: 9.0,
    conciseness: 6.5,
    ...overrides,
  };
}

export function mockOptimizationResult(overrides: Record<string, unknown> = {}) {
  return {
    id: 'opt-1',
    trace_id: 'trace-1',
    raw_prompt: 'Write a hello world',
    optimized_prompt: 'Craft a concise hello world program',
    task_type: 'coding',
    strategy_used: 'chain-of-thought',
    changes_summary: 'Added specificity',
    scores: mockDimensionScores(),
    original_scores: mockDimensionScores({ clarity: 5.0, specificity: 4.5 }),
    score_deltas: { clarity: 2.5, specificity: 3.5, structure: 0, faithfulness: 0, conciseness: 0 },
    overall_score: 7.6,
    provider: 'claude-cli',
    scoring_mode: 'hybrid',
    duration_ms: 3200,
    status: 'complete',
    created_at: '2026-03-20T12:00:00Z',
    model_used: 'claude-sonnet-4-6',
    context_sources: null,
    intent_label: 'Hello world program',
    domain: 'backend',
    family_id: null,
    ...overrides,
  };
}

export function mockHistoryItem(overrides: Record<string, unknown> = {}) {
  return {
    id: 'opt-1',
    trace_id: 'trace-1',
    created_at: '2026-03-20T12:00:00Z',
    task_type: 'coding',
    strategy_used: 'chain-of-thought',
    overall_score: 7.6,
    status: 'complete',
    duration_ms: 3200,
    provider: 'claude-cli',
    raw_prompt: 'Write a hello world',
    optimized_prompt: 'Craft a concise hello world program',
    model_used: 'claude-sonnet-4-6',
    scoring_mode: 'hybrid',
    intent_label: 'Hello world program',
    domain: 'backend',
    family_id: null,
    ...overrides,
  };
}

export function mockPatternFamily(overrides: Record<string, unknown> = {}) {
  return {
    id: 'fam-1',
    intent_label: 'API endpoint patterns',
    domain: 'backend',
    task_type: 'coding',
    usage_count: 5,
    member_count: 3,
    avg_score: 7.8,
    created_at: '2026-03-15T10:00:00Z',
    ...overrides,
  };
}

export function mockMetaPattern(overrides: Record<string, unknown> = {}) {
  return {
    id: 'mp-1',
    pattern_text: 'Include error handling for edge cases',
    source_count: 3,
    ...overrides,
  };
}

export function mockPatternMatch(overrides: Record<string, unknown> = {}) {
  return {
    family: mockPatternFamily(),
    meta_patterns: [mockMetaPattern()],
    similarity: 0.85,
    match_level: 'family' as const,
    taxonomy_node_id: 'node-1',
    taxonomy_label: 'Test Node',
    taxonomy_color: '#00e5ff',
    taxonomy_breadcrumb: ['Root', 'Test Node'],
    ...overrides,
  };
}

export function mockRefinementTurn(overrides: Record<string, unknown> = {}) {
  return {
    id: 'turn-1',
    optimization_id: 'opt-1',
    version: 1,
    branch_id: 'branch-main',
    parent_version: null,
    refinement_request: 'Make it more concise',
    prompt: 'Refined prompt text',
    scores: { clarity: 8.0, specificity: 8.5, structure: 7.5, faithfulness: 9.0, conciseness: 7.0 },
    deltas: { clarity: 0.5, specificity: 0.5, structure: 0.5, faithfulness: 0, conciseness: 0.5 },
    deltas_from_original: { clarity: 3.0, specificity: 4.0, structure: 0.5, faithfulness: 0, conciseness: 0.5 },
    strategy_used: 'chain-of-thought',
    suggestions: [{ text: 'Try adding examples', source: 'model' }],
    created_at: '2026-03-20T12:05:00Z',
    ...overrides,
  };
}

export function mockRefinementBranch(overrides: Record<string, unknown> = {}) {
  return {
    id: 'branch-main',
    optimization_id: 'opt-1',
    parent_branch_id: null,
    forked_at_version: null,
    created_at: '2026-03-20T12:00:00Z',
    ...overrides,
  };
}

export function mockStrategyInfo(overrides: Record<string, unknown> = {}) {
  return {
    name: 'chain-of-thought',
    tagline: 'Step-by-step reasoning',
    description: 'Breaks down the task into logical steps',
    ...overrides,
  };
}

// ── Fetch mock helper ────────────────────────────────────────────

type FetchHandler = { match: string | RegExp; response: unknown; status?: number };

/**
 * Set up a mock fetch that matches URL patterns and returns canned responses.
 * Call in beforeEach; automatically restored in afterEach by vitest.
 */
export function mockFetch(handlers: FetchHandler[]) {
  const mock = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
    const url = typeof input === 'string' ? input : input.toString();
    for (const h of handlers) {
      const matches = typeof h.match === 'string' ? url.includes(h.match) : h.match.test(url);
      if (matches) {
        return new Response(JSON.stringify(h.response), {
          status: h.status ?? 200,
          headers: { 'Content-Type': 'application/json' },
        });
      }
    }
    return new Response('Not Found', { status: 404 });
  });
  vi.stubGlobal('fetch', mock);
  return mock;
}
