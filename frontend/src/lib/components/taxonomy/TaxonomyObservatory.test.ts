import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, cleanup } from '@testing-library/svelte';
import TaxonomyObservatory from './TaxonomyObservatory.svelte';
import { observatoryStore } from '$lib/stores/observatory.svelte';
import { clustersStore } from '$lib/stores/clusters.svelte';
import { readinessStore } from '$lib/stores/readiness.svelte';

// Stub heavy child components so the shell's own behavior is what we test.
vi.mock('./DomainLifecycleTimeline.svelte', () => ({
  default: () => ({ destroy: () => {} }),
}));
vi.mock('./DomainReadinessAggregate.svelte', () => ({
  default: () => ({ destroy: () => {} }),
}));
vi.mock('./PatternDensityHeatmap.svelte', () => ({
  default: () => ({ destroy: () => {} }),
}));

describe('TaxonomyObservatory', () => {
  beforeEach(() => {
    clustersStore._reset();
    readinessStore.reports = [];
    observatoryStore.patternDensity = null;
    observatoryStore._reset?.();
  });
  afterEach(() => cleanup());

  it('mounts all three panels (TO1)', () => {
    const { container } = render(TaxonomyObservatory);
    expect(container.querySelector('[data-test="observatory-timeline-slot"]')).not.toBeNull();
    expect(container.querySelector('[data-test="observatory-readiness-slot"]')).not.toBeNull();
    expect(container.querySelector('[data-test="observatory-heatmap-slot"]')).not.toBeNull();
  });

  it('Timeline slot carries the current period as data-period (TO2)', () => {
    const { container } = render(TaxonomyObservatory);
    const timelineSlot = container.querySelector('[data-test="observatory-timeline-slot"]') as HTMLElement;
    expect(timelineSlot.getAttribute('data-period')).toBe(observatoryStore.period);
  });

  it('period selector is NOT rendered in the shell header (TO3)', () => {
    const { container } = render(TaxonomyObservatory);
    const shellHeader = container.querySelector('[data-test="observatory-shell-header"]') as HTMLElement;
    expect(shellHeader).not.toBeNull();
    expect(shellHeader.querySelector('[data-test="period-chip"]')).toBeNull();
  });

  it('legend in shell header explains readiness asymmetry (TO4)', () => {
    const { container } = render(TaxonomyObservatory);
    const legend = container.querySelector('[data-test="observatory-legend"]') as HTMLElement;
    expect(legend).not.toBeNull();
    expect(legend.textContent || '').toMatch(/readiness reflects current state/i);
  });

  it('observatory root is role=tabpanel (TO5)', () => {
    const { container } = render(TaxonomyObservatory);
    const root = container.querySelector('[data-test="taxonomy-observatory"]') as HTMLElement;
    expect(root.getAttribute('role')).toBe('tabpanel');
  });

  it('mount + unmount does not corrupt clustersStore.activityEvents (TO6)', () => {
    const { unmount } = render(TaxonomyObservatory);
    unmount();
    expect(Array.isArray(clustersStore.activityEvents)).toBe(true);
  });
});
