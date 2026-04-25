import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, cleanup } from '@testing-library/svelte';
import PatternDensityHeatmap from './PatternDensityHeatmap.svelte';
import { observatoryStore } from '$lib/stores/observatory.svelte';
import type { PatternDensityRow } from '$lib/api/observatory';

function makeRow(overrides: Partial<PatternDensityRow> = {}): PatternDensityRow {
  return {
    domain_id: 'd1',
    domain_label: 'backend',
    cluster_count: 3,
    meta_pattern_count: 5,
    meta_pattern_avg_score: 7.8,
    global_pattern_count: 1,
    cross_cluster_injection_rate: 0.25,
    period_start: '2026-04-17T00:00:00Z',
    period_end: '2026-04-24T00:00:00Z',
    ...overrides,
  };
}

describe('PatternDensityHeatmap', () => {
  beforeEach(() => {
    observatoryStore.patternDensity = null;
    observatoryStore.patternDensityLoading = false;
    observatoryStore.patternDensityError = null;
    vi.restoreAllMocks();
  });
  afterEach(() => cleanup());

  it('renders the canonical column headers (H1)', () => {
    observatoryStore.patternDensity = [makeRow()];
    render(PatternDensityHeatmap);
    for (const header of ['clusters', 'meta', 'avg score', 'global', 'x-cluster inj. rate']) {
      expect(screen.getByText(header)).toBeTruthy();
    }
  });

  it('renders one data row per density entry (H2)', () => {
    observatoryStore.patternDensity = [
      makeRow({ domain_id: 'd1', domain_label: 'backend' }),
      makeRow({ domain_id: 'd2', domain_label: 'frontend', meta_pattern_count: 3 }),
      makeRow({ domain_id: 'd3', domain_label: 'database', meta_pattern_count: 1 }),
    ];
    const { container } = render(PatternDensityHeatmap);
    expect(container.querySelectorAll('[data-test="density-row"]').length).toBe(3);
  });

  it('empty cells render "—" glyph (H3)', () => {
    observatoryStore.patternDensity = [makeRow({ meta_pattern_avg_score: null })];
    const { container } = render(PatternDensityHeatmap);
    const row = container.querySelector('[data-test="density-row"]') as HTMLElement;
    expect(row.textContent).toContain('—');
  });

  it('row background opacity scales with meta_pattern_count (H4)', () => {
    observatoryStore.patternDensity = [
      makeRow({ domain_id: 'd-hi', meta_pattern_count: 10 }),
      makeRow({ domain_id: 'd-lo', meta_pattern_count: 1 }),
    ];
    const { container } = render(PatternDensityHeatmap);
    const rows = Array.from(container.querySelectorAll('[data-test="density-row"]')) as HTMLElement[];
    // Top row's inline style carries a higher percent token than the bottom row.
    const topStyle = rows[0].getAttribute('style') || '';
    const bottomStyle = rows[1].getAttribute('style') || '';
    const topPct = Number((topStyle.match(/(\d+)%/) || [])[1] || 0);
    const bottomPct = Number((bottomStyle.match(/(\d+)%/) || [])[1] || 0);
    expect(topPct).toBeGreaterThan(bottomPct);
  });

  it('rows are read-only — no role/tabindex/cursor (H5)', () => {
    observatoryStore.patternDensity = [makeRow()];
    const { container } = render(PatternDensityHeatmap);
    const row = container.querySelector('[data-test="density-row"]') as HTMLElement;
    expect(row.getAttribute('role')).not.toBe('button');
    expect(row.getAttribute('tabindex')).toBeNull();
    const cs = getComputedStyle(row);
    expect(cs.cursor).not.toBe('pointer');
  });

  it('loading state dims body opacity to 0.5 (H6)', () => {
    observatoryStore.patternDensity = [makeRow()];
    observatoryStore.patternDensityLoading = true;
    const { container } = render(PatternDensityHeatmap);
    const body = container.querySelector('[data-test="heatmap-body"]') as HTMLElement;
    const inline = body.getAttribute('style') || '';
    expect(inline).toMatch(/opacity:\s*0\.5/);
  });

  it('error state renders retry button (H7)', () => {
    observatoryStore.patternDensityError = 'fetch-failed';
    const { container } = render(PatternDensityHeatmap);
    const err = container.querySelector('[data-test="heatmap-error"]') as HTMLElement;
    expect(err).not.toBeNull();
    expect(screen.getByRole('button', { name: /retry/i })).toBeTruthy();
  });

  it('empty state renders factual no-action copy (H8)', () => {
    observatoryStore.patternDensity = [];
    render(PatternDensityHeatmap);
    expect(screen.getByText(/pattern library is empty/i)).toBeTruthy();
  });

  it('zero counts render as "0", not "—" (REFACTOR regression)', () => {
    // Schema: cluster_count, meta_pattern_count, global_pattern_count are
    // non-nullable numbers. 0 means "none observed yet" — a meaningful
    // signal — and must render explicitly. '—' is reserved for null
    // (avg-score not yet computed) and would mislead the reader.
    observatoryStore.patternDensity = [
      makeRow({
        cluster_count: 0,
        meta_pattern_count: 0,
        global_pattern_count: 0,
        cross_cluster_injection_rate: 0,
        meta_pattern_avg_score: null,
      }),
    ];
    const { container } = render(PatternDensityHeatmap);
    const cells = container.querySelectorAll('[data-test="density-row"] .col-n');
    // 5 numeric cells: clusters, meta, avg score, global, x-cluster rate.
    expect(cells.length).toBe(5);
    expect(cells[0].textContent).toBe('0');       // cluster_count
    expect(cells[1].textContent).toBe('0');       // meta_pattern_count
    expect(cells[2].textContent).toBe('—');       // null avg score
    expect(cells[3].textContent).toBe('0');       // global_pattern_count
    expect(cells[4].textContent).toBe('0%');      // x-cluster rate
  });
});
