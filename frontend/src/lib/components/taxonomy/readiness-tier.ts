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
import type {
  DomainReadinessReport,
  EmergenceTier,
  StabilityTier,
} from '$lib/api/readiness';

export type ReadinessTier = 'healthy' | 'warming' | 'guarded' | 'critical' | 'ready';

/**
 * Panel-side CSS-var color table for axis tiers. Kept next to `TIER_COLORS`
 * (topology ring hex palette) so any semantic brand change touches a single
 * file. Previously duplicated as private helpers in
 * `DomainReadinessPanel.svelte` → drift risk.
 *
 * NB: these map onto the app-wide neon CSS variables (`--color-neon-*`) so
 * the panel composes with hover/focus states; the topology overlay uses the
 * hex palette in `TIER_COLORS` because Three.js materials need literal hex.
 */
const STABILITY_TIER_VAR = {
  healthy: 'var(--color-neon-green)',
  guarded: 'var(--color-neon-yellow)',
  critical: 'var(--color-neon-red)',
} as const satisfies Record<StabilityTier, string>;

const EMERGENCE_TIER_VAR = {
  ready: 'var(--color-neon-green)',
  warming: 'var(--color-neon-cyan)',
  inert: 'var(--color-text-dim)',
} as const satisfies Record<EmergenceTier, string>;

const EMERGENCE_TIER_BADGE = {
  ready: 'RDY',
  warming: 'WRM',
  inert: '—',
} as const satisfies Record<EmergenceTier, string>;

/** Resolve the panel CSS var for a stability tier (text + rail colour). */
export function stabilityTierVar(tier: StabilityTier): string {
  return STABILITY_TIER_VAR[tier];
}

/** Resolve the panel CSS var for an emergence tier (badge + gap colour). */
export function emergenceTierVar(tier: EmergenceTier): string {
  return EMERGENCE_TIER_VAR[tier];
}

/** Resolve the 3-char badge string for an emergence tier (RDY/WRM/—). */
export function emergenceTierBadge(tier: EmergenceTier): string {
  return EMERGENCE_TIER_BADGE[tier];
}

/**
 * Brand-defined hex palette per composite tier. Conflict-free against the
 * domain palette (`$lib/stores/domains.svelte`) and lifecycle state palette
 * (`$lib/utils/colors`).
 *
 * `as const` preserves literal hex types; `satisfies` enforces exhaustive
 * coverage of `ReadinessTier` without widening the value type to `string`.
 */
const TIER_COLORS = {
  healthy: '#16a34a',  // forest green — stable + inert
  warming: '#0ea5e9',  // sky blue — emergence approaching threshold
  guarded: '#eab308',  // gold — stability degrading
  critical: '#dc2626', // crimson — would dissolve next cycle
  ready: '#f97316',    // orange — sub-domain ready to split
} as const satisfies Record<ReadinessTier, string>;

/** Resolve the brand-aligned hex for a composite tier (used as ring color). */
export function readinessTierColor(tier: ReadinessTier): string {
  return TIER_COLORS[tier];
}

/**
 * Compose stability + emergence into a single actionable tier.
 *
 * Priority rules (emergence dominates stability — splits and forming
 * sub-domains are more user-actionable than a degrading parent):
 *   1. emergence === 'ready'   → 'ready'   (split is most-actionable signal)
 *   2. emergence === 'warming' → 'warming' (sub-domain forming — still actionable)
 *   3. else the stability tier passes through unchanged
 *      (healthy | guarded | critical)
 *
 * Because `ReadinessTier = StabilityTier ∪ {'warming', 'ready'}`, the
 * passthrough branch is total — every `StabilityTier` is a valid
 * `ReadinessTier`, so no default case is required.
 *
 * Precondition: `report` MUST be a fully-populated `DomainReadinessReport`
 * — `report.emergence.tier` and `report.stability.tier` must both be
 * present. Malformed payloads (missing nested objects) will throw
 * `TypeError`. This matches sibling pure transforms in `$lib/utils/`
 * (e.g. `parsePrimaryDomain`, `stateSizeMultiplier`) which trust their
 * typed inputs rather than silently masking upstream data corruption.
 * Callers should validate API responses at the fetch boundary
 * (`$lib/api/readiness.ts`) rather than here.
 */
export function composeReadinessTier(report: DomainReadinessReport): ReadinessTier {
  const emergenceTier = report.emergence.tier;
  if (emergenceTier === 'ready') return 'ready';
  if (emergenceTier === 'warming') return 'warming';
  return report.stability.tier;
}
