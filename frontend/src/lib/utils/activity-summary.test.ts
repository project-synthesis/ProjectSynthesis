/**
 * Per-op summary formatter — covers the canonical backend op vocabulary.
 *
 * Pre-extraction this lived inline in `ActivityPanel.svelte` (220+ lines).
 * Now shared with `DomainLifecycleTimeline.svelte` so the Observatory's
 * lifecycle rows surface a meaningful one-liner per event instead of
 * just `op + decision`.
 *
 * Tests cover one representative case per op family. Pure-function
 * formatter — no DOM, no store.
 */
import { describe, it, expect } from 'vitest';
import { keyMetric } from './activity-summary';
import type { TimelineEvent } from './activity-filters';

function ev(op: string, decision: string, context: Record<string, unknown> = {}): TimelineEvent {
  return {
    ts: '2026-04-25T08:00:00Z',
    path: 'warm',
    op,
    decision,
    cluster_id: null,
    optimization_id: null,
    duration_ms: null,
    context,
  };
}

describe('keyMetric', () => {
  it('returns "" for unknown ops', () => {
    expect(keyMetric(ev('completely_unknown', 'whatever'))).toBe('');
  });

  it('returns "" for events with empty context', () => {
    expect(keyMetric(ev('phase', 'complete'))).toBe('');
  });

  describe('assign', () => {
    it('formats merge_into with winner + score + member count', () => {
      const out = keyMetric(ev('assign', 'merge_into', {
        winner_label: 'API Endpoints',
        prompt_label: 'JWT validation',
        member_count: 12,
        candidates: [{ effective_score: 0.876 }],
      }));
      expect(out).toContain('API Endpoints');
      expect(out).toContain('[12m]');
      expect(out).toContain('0.876');
    });

    it('formats create_new with new label + domain', () => {
      const out = keyMetric(ev('assign', 'create_new', {
        new_label: 'JWT Validation',
        prompt_domain: 'security',
      }));
      expect(out).toBe('new: JWT Validation [security]');
    });
  });

  describe('phase + refit', () => {
    it('formats Q before/after for phase events', () => {
      expect(keyMetric(ev('phase', 'complete', { q_before: 0.512, q_after: 0.643 })))
        .toBe('Q 0.512→0.643');
    });

    it('formats Q before/after for refit', () => {
      expect(keyMetric(ev('refit', 'completed', { q_before: 0.5, q_after: 0.6 })))
        .toBe('Q 0.500→0.600');
    });
  });

  describe('split + candidate', () => {
    it('formats spectral evaluation with k + silhouette + acceptance', () => {
      const out = keyMetric(ev('split', 'spectral_evaluation', {
        best_k: 3,
        best_silhouette: 0.612,
        accepted: true,
      }));
      expect(out).toContain('k=3');
      expect(out).toContain('sil=0.612');
      expect(out).toContain('accepted');
    });

    it('formats candidate_promoted with label + coherence', () => {
      const out = keyMetric(ev('candidate', 'candidate_promoted', {
        cluster_label: 'API Endpoints',
        coherence: 0.823,
      }));
      expect(out).toContain('API Endpoints');
      expect(out).toContain('coh=0.823');
    });
  });

  describe('merge + retire', () => {
    it('formats merge with similarity + gate', () => {
      expect(keyMetric(ev('merge', 'merged', { similarity: 0.91, gate: 'mega-prevention' })))
        .toContain('sim=0.910 [mega-prevention]');
    });

    it('formats retire/dissolved with label + coherence + member count', () => {
      const out = keyMetric(ev('retire', 'dissolved', {
        cluster_label: 'API endpoints',
        coherence: 0.31,
        member_count: 4,
      }));
      expect(out).toContain('API endpoints');
      expect(out).toContain('coh=0.310');
      expect(out).toContain('4m');
      expect(out).toContain('reassigned');
    });
  });

  describe('score', () => {
    it('formats score with overall + label + divergences', () => {
      const out = keyMetric(ev('score', 'scored', {
        overall: 8.4,
        intent_label: 'Validate JWT input',
        divergence: ['structure', 'specificity'],
      }));
      expect(out).toContain('8.4');
      expect(out).toContain('Validate JWT input');
      expect(out).toContain('[structure,specificity]');
    });
  });

  describe('extract', () => {
    it('formats meta-patterns added count', () => {
      expect(keyMetric(ev('extract', 'meta_patterns_added', { meta_patterns_added: 3 })))
        .toBe('3 patterns');
    });
  });

  describe('discover', () => {
    it('formats domain creation with count + first 3 names', () => {
      const out = keyMetric(ev('discover', 'domains_created', {
        count: 5,
        domains: ['security', 'devops', 'data', 'ml', 'frontend'],
      }));
      expect(out).toContain('5');
      expect(out).toContain('security');
      expect(out).toContain('devops');
      expect(out).toContain('data');
      // Only first 3 names surface.
      expect(out).not.toContain('ml');
    });
  });

  describe('global_pattern', () => {
    it('formats action + truncated text + score', () => {
      const out = keyMetric(ev('global_pattern', 'promoted', {
        pattern_text: 'Always specify the target audience',
        avg_score: 7.8,
      }));
      expect(out).toContain('promoted');
      expect(out).toContain('Always specify the target audience');
      expect(out).toContain('score=7.8');
    });
  });

  describe('readiness', () => {
    it('formats sub_domain readiness with domain + tier + top + gap', () => {
      const out = keyMetric(ev('readiness', 'sub_domain_readiness_computed', {
        domain: 'backend',
        tier: 'warming',
        top_qualifier: 'auth',
        gap_to_threshold: 0.083,
      }));
      expect(out).toContain('backend');
      expect(out).toContain('[warming]');
      expect(out).toContain('top="auth"');
      expect(out).toContain('gap=0.083');
    });

    it('formats domain stability with consistency + risk', () => {
      const out = keyMetric(ev('readiness', 'domain_stability_computed', {
        domain: 'security',
        tier: 'guarded',
        consistency: 0.42,
        dissolution_risk: 0.31,
      }));
      expect(out).toContain('security');
      expect(out).toContain('[guarded]');
      expect(out).toContain('cons=0.42');
      expect(out).toContain('risk=0.31');
    });
  });

  describe('seed', () => {
    it('formats seed completed with prompt + cluster counts', () => {
      const out = keyMetric(ev('seed', 'seed_completed', {
        prompts_optimized: 27,
        clusters_created: 4,
      }));
      expect(out).toBe('27 done, 4 clusters');
    });

    it('formats seed_prompt_scored with score + label', () => {
      expect(keyMetric(ev('seed', 'seed_prompt_scored', { overall_score: 7.2, intent_label: 'API endpoint' })))
        .toContain('7.2 API endpoint');
    });
  });

  describe('error', () => {
    it('truncates long error messages to 60 chars', () => {
      const longMsg = 'x'.repeat(200);
      const out = keyMetric(ev('error', 'whatever', { error_message: longMsg }));
      expect(out.length).toBe(60);
    });
  });

  describe('injection_effectiveness', () => {
    it('formats lift with sample size', () => {
      const out = keyMetric(ev('injection_effectiveness', 'computed', {
        lift: 0.42,
        injected_n: 150,
        baseline_n: 80,
      }));
      expect(out).toContain('lift=+0.42');
      expect(out).toContain('(n=150+80)');
    });

    it('shows negative lift with sign preserved', () => {
      expect(keyMetric(ev('injection_effectiveness', 'computed', { lift: -0.15, injected_n: 50, baseline_n: 100 })))
        .toContain('lift=-0.15');
    });
  });

  describe('recovery', () => {
    it('formats scan with orphan count', () => {
      expect(keyMetric(ev('recovery', 'scan', { orphan_count: 3 }))).toBe('3 orphans found');
    });

    it('formats success with cluster id prefix', () => {
      expect(keyMetric(ev('recovery', 'success', { cluster_id: 'abcd1234efgh5678' })))
        .toBe('recovered → abcd1234');
    });
  });

  describe('reconcile', () => {
    it('formats repaired with non-zero fix counts', () => {
      const out = keyMetric(ev('reconcile', 'repaired', {
        member_counts_fixed: 3,
        coherence_updated: 0,
        scores_reconciled: 7,
      }));
      expect(out).toContain('3 counts');
      expect(out).toContain('7 scores');
      expect(out).not.toContain('coh');
    });
  });
});
