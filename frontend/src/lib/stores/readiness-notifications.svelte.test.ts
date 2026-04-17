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

  it('fires exactly one info-styled toast when enabled and domain not muted (neutral transition)', () => {
    preferencesStore.prefs.domain_readiness_notifications = {
      enabled: true,
      muted_domain_ids: [],
    };
    // inert -> warming is a neutral (non-guarded, non-critical) transition.
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

  it('dispatchReadinessCrossing is a no-op on repeated calls after muting', () => {
    // Reactivity to pref changes: first call fires, then muting the
    // domain suppresses subsequent calls without a re-subscribe.
    preferencesStore.prefs.domain_readiness_notifications = {
      enabled: true,
      muted_domain_ids: [],
    };
    dispatchReadinessCrossing(makePayload({ domain_id: 'dom-1' }));
    expect(toastStore.toasts.length).toBe(1);

    // User mutes the domain (e.g. via panel control).
    preferencesStore.prefs.domain_readiness_notifications = {
      enabled: true,
      muted_domain_ids: ['dom-1'],
    };
    dispatchReadinessCrossing(makePayload({ domain_id: 'dom-1' }));
    expect(toastStore.toasts.length).toBe(1); // no new toast
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

  // --- Severity-aware dispatch (Cycle 5 followup #1) ------------------------

  it('uses deleted (red) severity when to_tier is critical', () => {
    preferencesStore.prefs.domain_readiness_notifications = {
      enabled: true,
      muted_domain_ids: [],
    };
    dispatchReadinessCrossing(
      makePayload({
        axis: 'stability',
        from_tier: 'guarded',
        to_tier: 'critical',
      }),
    );
    expect(toastStore.toasts.length).toBe(1);
    expect(toastStore.toasts[0].symbol).toBe('-');
    expect(toastStore.toasts[0].color).toBe('var(--color-neon-red)');
  });

  it('uses deleted (red) severity when would_dissolve is true, even if to_tier is not critical', () => {
    preferencesStore.prefs.domain_readiness_notifications = {
      enabled: true,
      muted_domain_ids: [],
    };
    dispatchReadinessCrossing(
      makePayload({
        axis: 'stability',
        from_tier: 'guarded',
        to_tier: 'healthy', // not critical
        would_dissolve: true,
      }),
    );
    expect(toastStore.toasts.length).toBe(1);
    expect(toastStore.toasts[0].symbol).toBe('-');
    expect(toastStore.toasts[0].color).toBe('var(--color-neon-red)');
  });

  it('uses modified (yellow) severity when to_tier is guarded', () => {
    preferencesStore.prefs.domain_readiness_notifications = {
      enabled: true,
      muted_domain_ids: [],
    };
    dispatchReadinessCrossing(
      makePayload({
        axis: 'stability',
        from_tier: 'healthy',
        to_tier: 'guarded',
      }),
    );
    expect(toastStore.toasts.length).toBe(1);
    expect(toastStore.toasts[0].symbol).toBe('~');
    expect(toastStore.toasts[0].color).toBe('var(--color-neon-yellow)');
  });

  it.each(['healthy', 'ready', 'warming', 'inert'])(
    'falls back to info (cyan) severity for neutral to_tier=%s',
    (tier) => {
      preferencesStore.prefs.domain_readiness_notifications = {
        enabled: true,
        muted_domain_ids: [],
      };
      dispatchReadinessCrossing(
        makePayload({
          from_tier: 'inert',
          to_tier: tier,
        }),
      );
      expect(toastStore.toasts.length).toBe(1);
      expect(toastStore.toasts[0].symbol).toBe('i');
      expect(toastStore.toasts[0].color).toBe('var(--color-neon-cyan)');
    },
  );
});

describe('formatCrossingMessage', () => {
  it('emergence inert->warming on backend includes both tiers and the arrow', () => {
    const msg = formatCrossingMessage(
      makePayload({
        domain_label: 'backend',
        axis: 'emergence',
        from_tier: 'inert',
        to_tier: 'warming',
      }),
    );
    expect(msg).toBe('backend: emergence inert \u2192 warming');
  });

  it('stability healthy->guarded on security includes both tiers and the arrow', () => {
    const msg = formatCrossingMessage(
      makePayload({
        domain_label: 'security',
        axis: 'stability',
        from_tier: 'healthy',
        to_tier: 'guarded',
      }),
    );
    expect(msg).toBe('security: stability healthy \u2192 guarded');
  });

  it('would_dissolve=true appends the dissolution suffix after the target tier', () => {
    const msg = formatCrossingMessage(
      makePayload({
        domain_label: 'frontend',
        axis: 'stability',
        from_tier: 'guarded',
        to_tier: 'critical',
        would_dissolve: true,
      }),
    );
    expect(msg).toBe('frontend: stability guarded \u2192 critical (will dissolve)');
  });

  it('lowercases all payload strings in the rendered message', () => {
    const msg = formatCrossingMessage(
      makePayload({
        domain_label: 'Backend',
        axis: 'emergence',
        from_tier: 'Inert',
        to_tier: 'Warming',
      }),
    );
    expect(msg).toBe('backend: emergence inert \u2192 warming');
    expect(msg).toBe(msg.toLowerCase());
  });
});
