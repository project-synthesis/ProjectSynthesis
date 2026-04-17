/**
 * Composite readiness tier for topology node decoration.
 *
 * Maps a DomainReadinessReport's stability + emergence tiers into a single
 * 5-state tier that drives the per-domain-node contour ring color in
 * SemanticTopology. Priority: emergence > inert wins (more actionable);
 * otherwise stability tier passes through.
 *
 * Pure function — no IO, no side effects. Safe to call inside render loops.
 *
 * Palette is conflict-free vs domain palette (domains.svelte.ts) and state
 * palette (utils/colors.ts) — see plan "Color Palette" section.
 */
import type { DomainReadinessReport } from '$lib/api/readiness';

export type ReadinessTier = 'healthy' | 'warming' | 'guarded' | 'critical' | 'ready';

const TIER_COLORS: Record<ReadinessTier, string> = {
  healthy: '#16a34a',  // forest green — stable + inert
  warming: '#0ea5e9',  // sky blue — emergence approaching threshold
  guarded: '#eab308',  // gold — stability degrading
  critical: '#dc2626', // crimson — would dissolve next cycle
  ready: '#f97316',    // orange — sub-domain ready to split
};

/** Brand-aligned hex per composite tier. Used by SemanticTopology ring color. */
export function readinessTierColor(tier: ReadinessTier): string {
  return TIER_COLORS[tier];
}

/**
 * Compose stability + emergence into a single actionable tier.
 *
 * Priority rules:
 *   1. emergence === 'ready'   → 'ready'   (split is most-actionable signal)
 *   2. emergence === 'warming' → 'warming' (sub-domain forming — still actionable)
 *   3. else stability tier passes through (healthy | guarded | critical)
 */
export function composeReadinessTier(report: DomainReadinessReport): ReadinessTier {
  const emergenceTier = report.emergence.tier;
  if (emergenceTier === 'ready') return 'ready';
  if (emergenceTier === 'warming') return 'warming';
  return report.stability.tier;
}
