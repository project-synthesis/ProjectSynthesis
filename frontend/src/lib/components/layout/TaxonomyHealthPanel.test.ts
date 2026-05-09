import { describe, it, expect, afterEach, vi } from 'vitest';
import { render, screen, cleanup } from '@testing-library/svelte';

vi.mock('$lib/actions/tooltip', () => ({
  tooltip: () => ({ destroy() {} }),
}));

import TaxonomyHealthPanel from './TaxonomyHealthPanel.svelte';
import type { ClusterStats } from '$lib/api/clusters';

function stats(over: Partial<ClusterStats> = {}): ClusterStats {
  return {
    q_system: 0.55, q_coherence: 0.55, q_separation: 0.55,
    q_coverage: 0.55, q_dbcv: 0,
    q_health: 0.55, q_health_coherence_w: 0.55, q_health_separation_w: 0.55,
    q_health_weights: null, q_health_total_members: null, q_health_cluster_count: null,
    total_clusters: 10,
    nodes: { active: 6, candidate: 2, mature: 0, template: 0, archived: 0, max_depth: 0, leaf_count: 8 },
    last_warm_path: null, last_cold_path: null, warm_path_age: null,
    q_history: null, q_sparkline: null, q_trend: 0, q_current: null,
    q_min: null, q_max: null, q_point_count: 0,
    ...over,
  };
}

describe('TaxonomyHealthPanel', () => {
  afterEach(() => cleanup());

  it('renders the title and the three metric rows', () => {
    render(TaxonomyHealthPanel, {
      props: { stats: stats(), activeCount: 6, candidateCount: 2 },
    });
    expect(screen.getByText('TAXONOMY HEALTH')).toBeInTheDocument();
    expect(screen.getByText('Q_health')).toBeInTheDocument();
    expect(screen.getByText('Coherence')).toBeInTheDocument();
    expect(screen.getByText('Separation')).toBeInTheDocument();
  });

  it('renders the active + candidate counts', () => {
    render(TaxonomyHealthPanel, {
      props: { stats: stats(), activeCount: 6, candidateCount: 2 },
    });
    expect(screen.getByText('6 active')).toBeInTheDocument();
    expect(screen.getByText('2 candidate')).toBeInTheDocument();
  });

  it('renders metric values to 3 decimal places', () => {
    render(TaxonomyHealthPanel, {
      props: {
        stats: stats({ q_health: 0.7321, q_health_coherence_w: 0.612, q_health_separation_w: 0.498 }),
        activeCount: 5, candidateCount: 0,
      },
    });
    expect(screen.getByText('0.732')).toBeInTheDocument();
    expect(screen.getByText('0.612')).toBeInTheDocument();
    expect(screen.getByText('0.498')).toBeInTheDocument();
  });

  it('falls back to em-dash when metrics are null', () => {
    render(TaxonomyHealthPanel, {
      props: {
        stats: stats({
          q_health: null, q_system: null,
          q_health_coherence_w: null, q_coherence: null,
          q_health_separation_w: null, q_separation: null,
        }),
        activeCount: 0, candidateCount: 0,
      },
    });
    // Three em-dashes for the three metric rows.
    const dashes = screen.getAllByText('—');
    expect(dashes.length).toBeGreaterThanOrEqual(3);
  });

  it('renders the sparkline + health headline when q_sparkline has 2+ points', () => {
    render(TaxonomyHealthPanel, {
      props: {
        stats: stats({
          q_sparkline: [0.4, 0.5, 0.55, 0.6],
          q_health: 0.6, q_health_coherence_w: 0.6, q_health_separation_w: 0.6,
          q_point_count: 4,
          nodes: { active: 6, candidate: 0, mature: 0, template: 0, archived: 0, max_depth: 0, leaf_count: 6 },
        }),
        activeCount: 6, candidateCount: 0,
      },
    });
    // Sparkline is an SVG with the role 'img' from ScoreSparkline.
    expect(screen.getByRole('img', { name: /sparkline/i })).toBeInTheDocument();
  });

  it('omits sparkline when q_sparkline has fewer than 2 points', () => {
    render(TaxonomyHealthPanel, {
      props: {
        stats: stats({ q_sparkline: [0.5] }),
        activeCount: 1, candidateCount: 0,
      },
    });
    expect(screen.queryByRole('img', { name: /sparkline/i })).toBeNull();
  });
});
