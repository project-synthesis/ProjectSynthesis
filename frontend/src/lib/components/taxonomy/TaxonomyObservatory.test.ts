import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, cleanup } from '@testing-library/svelte';
import TaxonomyObservatory from './TaxonomyObservatory.svelte';
import componentSource from './TaxonomyObservatory.svelte?raw';
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

  /**
   * Brand-audit lock (TO8): the shell header allows the legend to wrap
   * onto a second line on narrow widths rather than getting clipped at
   * the right edge. Plan #5 shipped with `height: 28px` + `align-items:
   * center` which forced single-line layout and hid the legend on the
   * 1280px workbench width. `flex-wrap: wrap` + `min-height` (vs fixed
   * `height`) is the canonical fix.
   */
  it('shell header allows legend to wrap on narrow widths (TO8 brand audit)', () => {
    expect(componentSource).toMatch(/\.observatory-shell-header[\s\S]*?flex-wrap:\s*wrap/);
    expect(componentSource).toMatch(/\.observatory-shell-header[\s\S]*?min-height:\s*24px/);
    // Negative assertion: no fixed height that would prevent wrap.
    expect(componentSource).not.toMatch(/\.observatory-shell-header[\s\S]*?\n\s+height:\s*28px/);
  });

  /**
   * Brand-audit lock (TO9): the legend is visually subordinated to the
   * Syne title via 10px font, dim color, and `flex: 1 1 auto` + `min-width:
   * 0` so it consumes remaining horizontal space when wide and shrinks
   * gracefully when narrow.
   */
  it('legend is brand-subordinated to title (TO9 brand audit)', () => {
    expect(componentSource).toMatch(/\.observatory-legend[\s\S]*?font-size:\s*10px/);
    expect(componentSource).toMatch(/\.observatory-legend[\s\S]*?color:\s*var\(--color-text-dim\)/);
    expect(componentSource).toMatch(/\.observatory-legend[\s\S]*?min-width:\s*0/);
  });

  it('routes domain:select CustomEvent from Aggregate panel to clustersStore.selectCluster (TO7)', () => {
    const selectSpy = vi
      .spyOn(clustersStore, 'selectCluster')
      .mockImplementation(() => {});
    const { container } = render(TaxonomyObservatory);
    const root = container.querySelector(
      '[data-test="taxonomy-observatory"]',
    ) as HTMLElement;
    root.dispatchEvent(
      new CustomEvent('domain:select', {
        detail: { domain_id: 'd-test-id' },
        bubbles: true,
      }),
    );
    expect(selectSpy).toHaveBeenCalledWith('d-test-id');
  });
});
