import { describe, it, expect, beforeEach, vi } from 'vitest';
import { triggerTierGuide } from './tier-onboarding.svelte';
import { internalGuide } from './internal-guide.svelte';
import { samplingGuide } from './sampling-guide.svelte';
import { passthroughGuide } from './passthrough-guide.svelte';

/**
 * Tier-onboarding mux contract (post-2026-05-09 simplification).
 *
 * The mux is now a pure pass-through: tier → guide store → ``show(true)``.
 * Same-tier dedup lives in the consuming ``$effect`` in
 * ``frontend/src/routes/app/+page.svelte``, NOT here. End-to-end dedup
 * + transition coverage is pinned in ``+page`` regression tests.
 */
describe('tier-onboarding mux', () => {
  beforeEach(() => {
    internalGuide.close();
    internalGuide.resetDismissal();
    samplingGuide.close();
    samplingGuide.resetDismissal();
    passthroughGuide.close();
    passthroughGuide.resetDismissal();
  });

  it('triggers internal guide for internal tier', () => {
    const spy = vi.spyOn(internalGuide, 'show');
    triggerTierGuide('internal');
    expect(spy).toHaveBeenCalledWith(true);
    spy.mockRestore();
  });

  it('triggers sampling guide for sampling tier', () => {
    const spy = vi.spyOn(samplingGuide, 'show');
    triggerTierGuide('sampling');
    expect(spy).toHaveBeenCalledWith(true);
    spy.mockRestore();
  });

  it('triggers passthrough guide for passthrough tier', () => {
    const spy = vi.spyOn(passthroughGuide, 'show');
    triggerTierGuide('passthrough');
    expect(spy).toHaveBeenCalledWith(true);
    spy.mockRestore();
  });

  it('mux is stateless — every call dispatches (dedup moved to consumer)', () => {
    // Pre-2026-05-09 the mux carried a module-level ``lastTriggeredTier``
    // and silently swallowed same-tier re-triggers. That mutable was an
    // HMR-corruption risk and made the cold-boot pathway brittle. The
    // dedup contract moved to the consumer (component-scoped ``$state``)
    // — see ``+page.svelte``'s ``lastEffectiveTier`` watching
    // ``routing.tier``. Confirm the mux no longer dedups.
    const spy = vi.spyOn(internalGuide, 'show');
    triggerTierGuide('internal');
    triggerTierGuide('internal');
    expect(spy).toHaveBeenCalledTimes(2);
    spy.mockRestore();
  });

  it('different tiers in succession each dispatch independently', () => {
    const internalSpy = vi.spyOn(internalGuide, 'show');
    const passthroughSpy = vi.spyOn(passthroughGuide, 'show');
    triggerTierGuide('internal');
    triggerTierGuide('passthrough');
    expect(internalSpy).toHaveBeenCalledTimes(1);
    expect(passthroughSpy).toHaveBeenCalledTimes(1);
    internalSpy.mockRestore();
    passthroughSpy.mockRestore();
  });
});
