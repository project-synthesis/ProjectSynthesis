import { describe, it, expect, beforeEach, vi } from 'vitest';
import { triggerTierGuide, _resetOnboarding } from './tier-onboarding.svelte';
import { internalGuide } from './internal-guide.svelte';
import { samplingGuide } from './sampling-guide.svelte';
import { passthroughGuide } from './passthrough-guide.svelte';

describe('tier onboarding coordinator', () => {
  beforeEach(() => {
    _resetOnboarding();
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

  it('deduplicates: same tier called twice only triggers once', () => {
    const spy = vi.spyOn(internalGuide, 'show');
    triggerTierGuide('internal');
    triggerTierGuide('internal');
    expect(spy).toHaveBeenCalledTimes(1);
    spy.mockRestore();
  });

  it('triggers guide when tier changes', () => {
    const internalSpy = vi.spyOn(internalGuide, 'show');
    const passthroughSpy = vi.spyOn(passthroughGuide, 'show');

    triggerTierGuide('internal');
    triggerTierGuide('passthrough');

    expect(internalSpy).toHaveBeenCalledTimes(1);
    expect(passthroughSpy).toHaveBeenCalledTimes(1);

    internalSpy.mockRestore();
    passthroughSpy.mockRestore();
  });

  it('_resetOnboarding allows re-triggering the same tier', () => {
    const spy = vi.spyOn(internalGuide, 'show');
    triggerTierGuide('internal');
    _resetOnboarding();
    triggerTierGuide('internal');
    expect(spy).toHaveBeenCalledTimes(2);
    spy.mockRestore();
  });
});
