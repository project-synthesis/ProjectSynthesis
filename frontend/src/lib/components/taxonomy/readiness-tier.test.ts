import { describe, it, expect } from 'vitest';
import {
  composeReadinessTier,
  readinessTierColor,
  type ReadinessTier,
} from './readiness-tier';
import type { DomainReadinessReport } from '$lib/api/readiness';

function build(
  stability: 'healthy' | 'guarded' | 'critical',
  emergence: 'ready' | 'warming' | 'inert',
): DomainReadinessReport {
  return {
    domain_id: 'd1', domain_label: 'backend', member_count: 30,
    stability: {
      consistency: 0.5, dissolution_floor: 0.15, hysteresis_creation_threshold: 0.6,
      age_hours: 72, min_age_hours: 48, member_count: 30, member_ceiling: 5,
      sub_domain_count: 0, total_opts: 100,
      guards: {
        general_protected: false, has_sub_domain_anchor: false,
        age_eligible: true, above_member_ceiling: true, consistency_above_floor: true,
      },
      tier: stability, dissolution_risk: 0.5, would_dissolve: false,
    },
    emergence: {
      threshold: 0.5, threshold_formula: 'x', min_member_count: 8,
      total_opts: 100, top_candidate: null, gap_to_threshold: null,
      ready: false, blocked_reason: 'none', runner_ups: [], tier: emergence,
    },
    computed_at: '2026-04-17T12:00:00Z',
  };
}

describe('composeReadinessTier', () => {
  it('emergence ready overrides healthy stability → ready', () => {
    expect(composeReadinessTier(build('healthy', 'ready'))).toBe('ready');
  });

  it('emergence warming overrides healthy stability → warming', () => {
    expect(composeReadinessTier(build('healthy', 'warming'))).toBe('warming');
  });

  it('inert emergence + healthy stability → healthy', () => {
    expect(composeReadinessTier(build('healthy', 'inert'))).toBe('healthy');
  });

  it('inert emergence + critical stability → critical', () => {
    expect(composeReadinessTier(build('critical', 'inert'))).toBe('critical');
  });

  it('inert emergence + guarded stability → guarded', () => {
    expect(composeReadinessTier(build('guarded', 'inert'))).toBe('guarded');
  });

  it('emergence ready + critical stability → ready (more actionable)', () => {
    expect(composeReadinessTier(build('critical', 'ready'))).toBe('ready');
  });

  it('emergence warming + critical stability → warming (emergence dominates)', () => {
    expect(composeReadinessTier(build('critical', 'warming'))).toBe('warming');
  });

  it('emergence warming + guarded stability → warming (emergence dominates)', () => {
    expect(composeReadinessTier(build('guarded', 'warming'))).toBe('warming');
  });

  it('emergence ready + guarded stability → ready (emergence dominates)', () => {
    expect(composeReadinessTier(build('guarded', 'ready'))).toBe('ready');
  });
});

describe('readinessTierColor', () => {
  it('returns brand-defined hex per tier (conflict-free palette)', () => {
    const expected: Record<ReadinessTier, string> = {
      healthy: '#16a34a',  // forest green
      warming: '#0ea5e9',  // sky blue
      guarded: '#eab308',  // gold
      critical: '#dc2626', // crimson
      ready: '#f97316',    // orange — split-ready
    };
    for (const [tier, hex] of Object.entries(expected)) {
      expect(readinessTierColor(tier as ReadinessTier)).toBe(hex);
    }
  });
});
