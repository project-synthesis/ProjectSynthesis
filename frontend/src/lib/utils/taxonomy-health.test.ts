import { describe, it, expect } from 'vitest';
import {
  assessTaxonomyHealth,
  generatePanelInsight,
  type PanelInsightInput,
} from './taxonomy-health';
import type { ClusterStats } from '$lib/api/clusters';

// Build a ClusterStats stub. All fields nullable except `nodes`/`q_trend`/etc;
// callers override only what their test cares about.
function stats(over: Partial<ClusterStats> = {}): ClusterStats {
  return {
    q_system: 0.5,
    q_coherence: 0.5,
    q_separation: 0.5,
    q_coverage: 0.5,
    q_dbcv: 0,
    q_health: null,
    q_health_coherence_w: null,
    q_health_separation_w: null,
    q_health_weights: null,
    q_health_total_members: null,
    q_health_cluster_count: null,
    total_clusters: 0,
    nodes: { active: 0, candidate: 0, mature: 0, template: 0, archived: 0, max_depth: 0, leaf_count: 0 },
    last_warm_path: null,
    last_cold_path: null,
    warm_path_age: null,
    q_history: null,
    q_sparkline: null,
    q_trend: 0,
    q_current: null,
    q_min: null,
    q_max: null,
    q_point_count: 0,
    ...over,
  };
}

describe('assessTaxonomyHealth — early-state branches', () => {
  it('returns null when stats is null', () => {
    expect(assessTaxonomyHealth(null)).toBeNull();
  });

  it('returns null when both q_health and q_system are missing', () => {
    expect(assessTaxonomyHealth(stats({ q_health: null, q_system: null }))).toBeNull();
  });

  it('"No patterns yet" when total nodes = 0', () => {
    const r = assessTaxonomyHealth(stats({ q_system: 0.5 }))!;
    expect(r.headline).toBe('No patterns yet');
    expect(r.severity).toBe('info');
  });

  it('"Just getting started" with 1–3 total nodes (singular form)', () => {
    const r = assessTaxonomyHealth(
      stats({ q_system: 0.5, nodes: { active: 1, candidate: 0, mature: 0, template: 0, archived: 0, max_depth: 0, leaf_count: 1 } }),
    )!;
    expect(r.headline).toBe('Just getting started');
    expect(r.detail).toMatch(/1 group /);
    expect(r.severity).toBe('info');
  });

  it('"Just getting started" with 1–3 total nodes (plural form)', () => {
    const r = assessTaxonomyHealth(
      stats({ q_system: 0.5, nodes: { active: 2, candidate: 1, mature: 0, template: 0, archived: 0, max_depth: 0, leaf_count: 3 } }),
    )!;
    expect(r.detail).toMatch(/3 groups /);
  });
});

describe('assessTaxonomyHealth — critical sub-metric override', () => {
  // The composite Q_system is high but a sub-metric is critically low —
  // override branch must fire regardless of composite score.
  const baseLargeNodes = { active: 10, candidate: 0, mature: 0, template: 0, archived: 0, max_depth: 0, leaf_count: 10 };

  it('"Groups need reorganizing" when both separation AND coherence are critically low', () => {
    const r = assessTaxonomyHealth(
      stats({ q_health: 0.9, q_health_coherence_w: 0.1, q_health_separation_w: 0.1, nodes: baseLargeNodes }),
    )!;
    expect(r.headline).toBe('Groups need reorganizing');
    expect(r.severity).toBe('warning');
  });

  it('"Groups are too similar" when only separation is critically low', () => {
    const r = assessTaxonomyHealth(
      stats({ q_health: 0.9, q_health_coherence_w: 0.7, q_health_separation_w: 0.1, nodes: baseLargeNodes }),
    )!;
    expect(r.headline).toBe('Groups are too similar');
  });

  it('"Groups are unfocused" when only coherence is critically low', () => {
    const r = assessTaxonomyHealth(
      stats({ q_health: 0.9, q_health_coherence_w: 0.1, q_health_separation_w: 0.7, nodes: baseLargeNodes }),
    )!;
    expect(r.headline).toBe('Groups are unfocused');
  });
});

describe('assessTaxonomyHealth — composite score bands', () => {
  const fineNodes = { active: 10, candidate: 0, mature: 0, template: 0, archived: 0, max_depth: 0, leaf_count: 10 };

  it('"Looking great, getting better" — high Q + improving trend', () => {
    const r = assessTaxonomyHealth(
      stats({
        q_health: 0.8, q_health_coherence_w: 0.7, q_health_separation_w: 0.7,
        q_trend: 0.2, q_point_count: 5, nodes: fineNodes,
      }),
    )!;
    expect(r.headline).toBe('Looking great, getting better');
    expect(r.severity).toBe('good');
  });

  it('"Good, but slipping" — high Q + declining trend', () => {
    const r = assessTaxonomyHealth(
      stats({
        q_health: 0.8, q_health_coherence_w: 0.7, q_health_separation_w: 0.7,
        q_trend: -0.2, q_point_count: 5, nodes: fineNodes,
      }),
    )!;
    expect(r.headline).toBe('Good, but slipping');
  });

  it('"Well organized" — high Q + flat trend', () => {
    const r = assessTaxonomyHealth(
      stats({
        q_health: 0.8, q_health_coherence_w: 0.7, q_health_separation_w: 0.7,
        nodes: fineNodes,
      }),
    )!;
    expect(r.headline).toBe('Well organized');
    expect(r.severity).toBe('good');
  });

  it('"Getting better" — mid Q + improving', () => {
    const r = assessTaxonomyHealth(
      stats({
        q_health: 0.5, q_health_coherence_w: 0.5, q_health_separation_w: 0.5,
        q_trend: 0.2, q_point_count: 5, nodes: fineNodes,
      }),
    )!;
    expect(r.headline).toBe('Getting better');
  });

  it('"Losing organization" — mid Q + declining', () => {
    const r = assessTaxonomyHealth(
      stats({
        q_health: 0.5, q_health_coherence_w: 0.5, q_health_separation_w: 0.5,
        q_trend: -0.2, q_point_count: 5, nodes: fineNodes,
      }),
    )!;
    expect(r.headline).toBe('Losing organization');
  });

  it('"Could be better" — mid Q + flat', () => {
    const r = assessTaxonomyHealth(
      stats({
        q_health: 0.5, q_health_coherence_w: 0.5, q_health_separation_w: 0.5,
        nodes: fineNodes,
      }),
    )!;
    expect(r.headline).toBe('Could be better');
  });

  it('"Rebuilding" — low Q + improving', () => {
    const r = assessTaxonomyHealth(
      stats({
        q_health: 0.3, q_health_coherence_w: 0.5, q_health_separation_w: 0.5,
        q_trend: 0.2, q_point_count: 5, nodes: fineNodes,
      }),
    )!;
    expect(r.headline).toBe('Rebuilding');
    expect(r.severity).toBe('critical');
  });

  it('"Needs a recluster" — low Q + flat/declining', () => {
    const r = assessTaxonomyHealth(
      stats({
        q_health: 0.3, q_health_coherence_w: 0.5, q_health_separation_w: 0.5,
        nodes: fineNodes,
      }),
    )!;
    expect(r.headline).toBe('Needs a recluster');
    expect(r.severity).toBe('critical');
  });
});

describe('assessTaxonomyHealth — detail synthesis', () => {
  it('detail enumerates active / forming / reusable counts', () => {
    const r = assessTaxonomyHealth(
      stats({
        q_health: 0.7, q_health_coherence_w: 0.7, q_health_separation_w: 0.7,
        nodes: { active: 5, candidate: 2, mature: 0, template: 1, archived: 0, max_depth: 0, leaf_count: 8 },
      }),
    )!;
    expect(r.detail).toMatch(/5 active/);
    expect(r.detail).toMatch(/2 forming/);
    expect(r.detail).toMatch(/1 reusable/);
  });

  it('detail recommends template promotion when q is high and templates=0', () => {
    const r = assessTaxonomyHealth(
      stats({
        q_health: 0.8, q_health_coherence_w: 0.7, q_health_separation_w: 0.7,
        nodes: { active: 6, candidate: 0, mature: 0, template: 0, archived: 0, max_depth: 0, leaf_count: 6 },
      }),
    )!;
    expect(r.detail).toMatch(/promote your best groups/);
  });

  it('falls back to q_system when q_health is null', () => {
    const r = assessTaxonomyHealth(
      stats({
        q_system: 0.8, q_health: null, q_coherence: 0.7, q_separation: 0.7,
        nodes: { active: 6, candidate: 0, mature: 0, template: 0, archived: 0, max_depth: 0, leaf_count: 6 },
      }),
    )!;
    expect(r.severity).toBe('good');
  });
});

describe('generatePanelInsight', () => {
  it('mode=system: includes active count and silhouette', () => {
    const out = generatePanelInsight({
      mode: 'system',
      stats: stats({
        q_dbcv: 0.42,
        nodes: { active: 12, candidate: 0, mature: 0, template: 0, archived: 0, max_depth: 0, leaf_count: 12 },
      }),
      detail: null,
    });
    expect(out).toMatch(/12 active clusters/);
    expect(out).toMatch(/silhouette 0\.42/);
  });

  it('mode=system: notes silhouette is pending when q_dbcv is missing/zero', () => {
    const out = generatePanelInsight({
      mode: 'system',
      stats: stats({ q_dbcv: 0, nodes: { active: 5, candidate: 0, mature: 0, template: 0, archived: 0, max_depth: 0, leaf_count: 5 } }),
      detail: null,
    });
    expect(out).toMatch(/silhouette pending/);
  });

  it('mode=system: appends formatted last_cold_path "ago"', () => {
    const justNow = new Date().toISOString();
    const out = generatePanelInsight({
      mode: 'system',
      stats: stats({
        last_cold_path: justNow, q_dbcv: 0.42,
        nodes: { active: 1, candidate: 0, mature: 0, template: 0, archived: 0, max_depth: 0, leaf_count: 1 },
      }),
      detail: null,
    });
    expect(out).toMatch(/last recluster /);
  });

  it('mode=cluster well-focused — high coherence, healthy output coherence', () => {
    const input: PanelInsightInput = {
      mode: 'cluster',
      stats: null,
      detail: {
        coherence: 0.85, separation: null, output_coherence: 0.6, blend_w_optimized: 0.2,
        member_count: 8, split_failures: 0, label: 'foo', state: 'active',
      },
    };
    const out = generatePanelInsight(input);
    expect(out).toMatch(/Well-focused group/);
    expect(out).toMatch(/all embedding signals contribute/);
  });

  it('mode=cluster divergent outputs — low output_coherence', () => {
    const input: PanelInsightInput = {
      mode: 'cluster',
      stats: null,
      detail: {
        coherence: 0.8, separation: null, output_coherence: 0.1, blend_w_optimized: 0.05,
        member_count: 5, split_failures: 0, label: 'foo', state: 'active',
      },
    };
    const out = generatePanelInsight(input);
    expect(out).toMatch(/divergent outputs/);
    expect(out).toMatch(/optimized signal reduced/);
  });

  it('mode=cluster low coherence — split exhausted', () => {
    const out = generatePanelInsight({
      mode: 'cluster',
      stats: null,
      detail: {
        coherence: 0.3, separation: null, output_coherence: null, blend_w_optimized: null,
        member_count: 5, split_failures: 5, label: 'foo', state: 'active',
      },
    });
    expect(out).toMatch(/Low coherence/);
    expect(out).toMatch(/split attempts exhausted/);
  });

  it('mode=cluster default branch — moderate metrics, prints member count', () => {
    const out = generatePanelInsight({
      mode: 'cluster',
      stats: null,
      detail: {
        coherence: 0.6, separation: null, output_coherence: 0.4, blend_w_optimized: null,
        member_count: 11, split_failures: 0, label: 'foo', state: 'active',
      },
    });
    expect(out).toMatch(/11 members/);
    expect(out).toMatch(/moderate output diversity/);
  });

  it('mode=domain: includes child count, below-floor count, and truncated top pattern', () => {
    const out = generatePanelInsight({
      mode: 'domain',
      stats: null,
      detail: null,
      domainChildCount: 7,
      domainBelowFloor: 2,
      topPattern: 'a'.repeat(60),
      topPatternCount: 4,
    });
    expect(out).toMatch(/7 clusters/);
    expect(out).toMatch(/2 below coherence floor/);
    // Truncation: 37 chars + ellipsis
    expect(out).toMatch(/a{37}\.\.\./);
    expect(out).toMatch(/\(x4\)/);
  });

  it('mode=project: returns the static workspace headline', () => {
    expect(generatePanelInsight({ mode: 'project', stats: null, detail: null })).toMatch(
      /Project workspace/,
    );
  });

  it('returns empty string for unknown mode (defensive default)', () => {
    expect(
      // @ts-expect-error -- defensive coverage of impossible enum value
      generatePanelInsight({ mode: 'unknown', stats: null, detail: null }),
    ).toBe('');
  });
});
