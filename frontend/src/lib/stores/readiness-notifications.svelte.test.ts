import { describe, it, expect, beforeEach } from 'vitest';
import {
  dispatchReadinessCrossing,
  formatCrossingMessage,
  type ReadinessCrossingPayload,
} from './readiness-notifications.svelte';
import { toastStore } from './toast.svelte';
import { preferencesStore } from './preferences.svelte';

function makePayload(overrides: Partial<ReadinessCrossingPayload> = {}): ReadinessCrossingPayload {
  return {
    domain_id: 'dom-1',
    domain_label: 'backend',
    axis: 'emergence',
    from_tier: 'inert',
    to_tier: 'warming',
    consistency: 0.52,
    gap_to_threshold: 0.08,
    would_dissolve: false,
    ts: '2026-04-17T12:00:00Z',
    ...overrides,
  };
}

describe('dispatchReadinessCrossing', () => {
  beforeEach(() => {
    toastStore._reset();
    preferencesStore._reset();
  });

  it('fires no toast when notifications are disabled (enabled=false)', () => {
    // DEFAULTS should already be disabled, but assert explicitly.
    preferencesStore.prefs.domain_readiness_notifications = {
      enabled: false,
      muted_domain_ids: [],
    };
    dispatchReadinessCrossing(makePayload());
    expect(toastStore.toasts.length).toBe(0);
  });

  it('fires exactly one info-styled toast when enabled and domain not muted', () => {
    preferencesStore.prefs.domain_readiness_notifications = {
      enabled: true,
      muted_domain_ids: [],
    };
    dispatchReadinessCrossing(makePayload());
    expect(toastStore.toasts.length).toBe(1);
    expect(toastStore.toasts[0].symbol).toBe('i');
    expect(toastStore.toasts[0].color).toBe('var(--color-neon-cyan)');
  });

  it('fires no toast when enabled but the domain ID is muted', () => {
    preferencesStore.prefs.domain_readiness_notifications = {
      enabled: true,
      muted_domain_ids: ['dom-1'],
    };
    dispatchReadinessCrossing(makePayload({ domain_id: 'dom-1' }));
    expect(toastStore.toasts.length).toBe(0);
  });

  it('does not throw on malformed payloads and fires no toast', () => {
    preferencesStore.prefs.domain_readiness_notifications = {
      enabled: true,
      muted_domain_ids: [],
    };
    // Cast to bypass TS — we are simulating a malformed SSE event body.
    const bad = {
      domain_id: 'x',
      // missing domain_label, axis
      from_tier: null,
      to_tier: null,
      consistency: null,
      gap_to_threshold: null,
      would_dissolve: null,
      ts: null,
    } as unknown as ReadinessCrossingPayload;
    expect(() => dispatchReadinessCrossing(bad)).not.toThrow();
    expect(toastStore.toasts.length).toBe(0);
  });
});

describe('formatCrossingMessage', () => {
  it('emergence inert->warming on backend mentions both label and target tier', () => {
    const msg = formatCrossingMessage(
      makePayload({
        domain_label: 'backend',
        axis: 'emergence',
        from_tier: 'inert',
        to_tier: 'warming',
      }),
    );
    expect(msg.toLowerCase()).toContain('backend');
    expect(msg.toLowerCase()).toContain('warming');
  });

  it('stability healthy->guarded on security mentions both label and target tier', () => {
    const msg = formatCrossingMessage(
      makePayload({
        domain_label: 'security',
        axis: 'stability',
        from_tier: 'healthy',
        to_tier: 'guarded',
      }),
    );
    expect(msg.toLowerCase()).toContain('security');
    expect(msg.toLowerCase()).toContain('guarded');
  });

  it('would_dissolve=true message references dissolution', () => {
    const msg = formatCrossingMessage(
      makePayload({
        domain_label: 'frontend',
        axis: 'stability',
        from_tier: 'guarded',
        to_tier: 'critical',
        would_dissolve: true,
      }),
    );
    expect(msg).toMatch(/dissolv/i);
  });
});
