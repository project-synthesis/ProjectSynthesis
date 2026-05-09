/**
 * Tier onboarding mux.
 *
 * Thin pass-through that maps an effective tier to the matching guide
 * store and calls ``show(true)`` (respectDismiss). Dispatch only —
 * no state.
 *
 * The same-tier dedup contract (don't re-pop a guide on the same
 * effective tier) lives in the consuming ``$effect`` in
 * ``frontend/src/routes/app/+page.svelte``. Keeping dedup at the
 * consumer (component-scoped ``$state``) eliminates the module-level
 * mutable that was previously here, removing an HMR-corruption risk
 * (Svelte 5 dev mode preserves module-scope ``let`` across hot
 * reloads in some configurations, leaving stale ``lastTriggeredTier``
 * blocking legitimate triggers).
 *
 * Copyright 2025-2026 Project Synthesis contributors.
 */

import type { EffectiveTier } from './routing.svelte';
import { internalGuide } from './internal-guide.svelte';
import { samplingGuide } from './sampling-guide.svelte';
import { passthroughGuide } from './passthrough-guide.svelte';

/** Tier → guide store lookup. O(1) dispatch, no switch. */
const GUIDE_MAP: Record<EffectiveTier, { show(respectDismiss?: boolean): void }> = {
  internal: internalGuide,
  sampling: samplingGuide,
  passthrough: passthroughGuide,
};

/**
 * Show the onboarding guide for the given tier (respectDismiss = true).
 *
 * Idempotent against the same-tier flip via the consumer-side
 * dedup; safe to call repeatedly.
 *
 * Call from the single tier-watching ``$effect`` in +page.svelte.
 */
export function triggerTierGuide(tier: EffectiveTier): void {
  GUIDE_MAP[tier].show(true);
}
