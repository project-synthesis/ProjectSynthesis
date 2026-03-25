/**
 * Tier onboarding coordinator.
 *
 * Single entry point for all automatic guide triggers — startup detection
 * and runtime tier transitions.  Maps the resolved tier to the correct
 * guide store and calls ``show(true)`` (respectDismiss) so first-time
 * users see onboarding while returning users are not interrupted.
 *
 * Copyright 2025-2026 Project Synthesis contributors.
 */

import type { EffectiveTier } from './routing.svelte';
import { internalGuide } from './internal-guide.svelte';
import { samplingGuide } from './sampling-guide.svelte';
import { passthroughGuide } from './passthrough-guide.svelte';

/** Last tier for which a guide was triggered — prevents redundant opens. */
let lastTriggeredTier: EffectiveTier | null = null;

/** Tier → guide store lookup.  O(1) dispatch, no switch statement. */
const GUIDE_MAP: Record<EffectiveTier, { show(respectDismiss?: boolean): void }> = {
  internal: internalGuide,
  sampling: samplingGuide,
  passthrough: passthroughGuide,
};

/**
 * Show the onboarding guide for the given tier (respectDismiss = true).
 *
 * Skips if the tier matches the last triggered tier — prevents duplicate
 * opens when health poll and SSE both fire for the same state.
 *
 * Call after the first health check and on every ``routing_state_changed``
 * SSE event.
 */
export function triggerTierGuide(tier: EffectiveTier): void {
  if (tier === lastTriggeredTier) return;
  lastTriggeredTier = tier;
  GUIDE_MAP[tier].show(true);
}

/** @internal Test-only: reset the last triggered tier for isolation. */
export function _resetOnboarding(): void {
  lastTriggeredTier = null;
}
