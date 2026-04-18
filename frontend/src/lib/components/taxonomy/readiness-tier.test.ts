import { describe, it, expect } from 'vitest';
import {
  composeReadinessTier,
  readinessTierColor,
  stabilityTierVar,
  emergenceTierVar,
  emergenceTierBadge,
  type ReadinessTier,
} from './readiness-tier';
import type {
  DomainReadinessReport,
  StabilityTier,
  EmergenceTier,
} from '$lib/api/readiness';

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

describe('stabilityTierVar', () => {
  // Centralizes the panel-side CSS-var color table that previously lived in
  // DomainReadinessPanel.svelte. Kept next to `TIER_COLORS` (hex palette for
  // topology rings) so semantic-brand changes touch one file.
  it('healthy → neon-green', () => {
    expect(stabilityTierVar('healthy')).toBe('var(--color-neon-green)');
  });
  it('guarded → neon-yellow', () => {
    expect(stabilityTierVar('guarded')).toBe('var(--color-neon-yellow)');
  });
  it('critical → neon-red', () => {
    expect(stabilityTierVar('critical')).toBe('var(--color-neon-red)');
  });

  it('is total over the StabilityTier union', () => {
    const all: StabilityTier[] = ['healthy', 'guarded', 'critical'];
    for (const t of all) {
      expect(stabilityTierVar(t)).toMatch(/^var\(--color-neon-/);
    }
  });
});

describe('emergenceTierVar', () => {
  it('ready → neon-green', () => {
    expect(emergenceTierVar('ready')).toBe('var(--color-neon-green)');
  });
  it('warming → neon-cyan', () => {
    expect(emergenceTierVar('warming')).toBe('var(--color-neon-cyan)');
  });
  it('inert → text-dim', () => {
    expect(emergenceTierVar('inert')).toBe('var(--color-text-dim)');
  });

  it('is total over the EmergenceTier union', () => {
    const all: EmergenceTier[] = ['ready', 'warming', 'inert'];
    for (const t of all) {
      expect(emergenceTierVar(t)).toMatch(/^var\(--/);
    }
  });
});

describe('emergenceTierBadge', () => {
  it('ready → RDY', () => {
    expect(emergenceTierBadge('ready')).toBe('RDY');
  });
  it('warming → WRM', () => {
    expect(emergenceTierBadge('warming')).toBe('WRM');
  });
  it('inert → em-dash', () => {
    expect(emergenceTierBadge('inert')).toBe('—');
  });
});

describe('readinessTierColor', () => {
  // Exhaustive tier list — `satisfies Record<ReadinessTier, ...>` would fail
  // to compile if a tier were added/removed without updating this test.
  const ALL_TIERS = {
    healthy: true, warming: true, guarded: true, critical: true, ready: true,
  } as const satisfies Record<ReadinessTier, true>;
  const TIERS = Object.keys(ALL_TIERS) as ReadinessTier[];

  it('returns a valid 6-digit hex for every tier', () => {
    for (const tier of TIERS) {
      expect(readinessTierColor(tier)).toMatch(/^#[0-9a-f]{6}$/i);
    }
  });

  it('assigns a unique color to every tier (palette has no collisions)', () => {
    const colors = TIERS.map((tier) => readinessTierColor(tier));
    expect(new Set(colors).size).toBe(TIERS.length);
  });
});
