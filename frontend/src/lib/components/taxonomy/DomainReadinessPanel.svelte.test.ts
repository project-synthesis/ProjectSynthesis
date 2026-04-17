/**
 * DomainReadinessPanel — per-row mute toggle (Plan C Cycle 6).
 *
 * Verifies that the panel exposes a mute toggle per row, that clicking the
 * button toggles `preferencesStore.toggleDomainMute(domainId)` without
 * triggering row selection, and that the accessible name + aria-pressed
 * state reflect the current `muted_domain_ids` membership.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render } from '@testing-library/svelte';
import type {
  DomainReadinessReport,
  DomainStabilityReport,
  SubDomainEmergenceReport,
} from '$lib/api/readiness';
import DomainReadinessPanel from './DomainReadinessPanel.svelte';
import { readinessStore } from '$lib/stores/readiness.svelte';
import { preferencesStore } from '$lib/stores/preferences.svelte';

const baseStability = (over: Partial<DomainStabilityReport> = {}): DomainStabilityReport => ({
  consistency: 0.45,
  dissolution_floor: 0.15,
  hysteresis_creation_threshold: 0.6,
  age_hours: 72,
  min_age_hours: 48,
  member_count: 12,
  member_ceiling: 5,
  sub_domain_count: 2,
  total_opts: 40,
  guards: {
    general_protected: false,
    has_sub_domain_anchor: true,
    age_eligible: true,
    above_member_ceiling: true,
    consistency_above_floor: true,
  },
  tier: 'healthy',
  dissolution_risk: 0.1,
  would_dissolve: false,
  ...over,
});

const baseEmergence = (over: Partial<SubDomainEmergenceReport> = {}): SubDomainEmergenceReport => ({
  threshold: 0.432,
  threshold_formula: 'max(0.40, 0.60 - 0.004 * 42) = 0.432',
  min_member_count: 5,
  total_opts: 42,
  top_candidate: null,
  gap_to_threshold: null,
  ready: false,
  blocked_reason: 'no_candidates',
  runner_ups: [],
  tier: 'inert',
  ...over,
});

const baseReport = (over: Partial<DomainReadinessReport> = {}): DomainReadinessReport => ({
  domain_id: 'dom-1',
  domain_label: 'backend',
  member_count: 12,
  stability: baseStability(),
  emergence: baseEmergence(),
  computed_at: '2026-04-16T00:00:00Z',
  ...over,
});

describe('DomainReadinessPanel — mute toggle (Cycle 6)', () => {
  beforeEach(() => {
    readinessStore._reset();
    preferencesStore._reset();
    preferencesStore.prefs.domain_readiness_notifications = {
      enabled: true,
      muted_domain_ids: [],
    };
    readinessStore.reports = [
      baseReport({
        domain_id: 'dom-a',
        domain_label: 'backend',
        stability: baseStability({ tier: 'healthy' }),
        emergence: baseEmergence({ tier: 'inert', gap_to_threshold: 0.3 }),
      }),
      baseReport({
        domain_id: 'dom-b',
        domain_label: 'security',
        stability: baseStability({ tier: 'critical', would_dissolve: true, consistency: 0.1 }),
        emergence: baseEmergence({ tier: 'warming', gap_to_threshold: 0.05 }),
      }),
    ];
    readinessStore.loaded = true;
  });

  afterEach(() => {
    cleanup();
    readinessStore._reset();
    preferencesStore._reset();
    vi.restoreAllMocks();
  });

  it('renders a mute toggle per row with aria-pressed reflecting muted_domain_ids', () => {
    preferencesStore.prefs.domain_readiness_notifications = {
      enabled: true,
      muted_domain_ids: ['dom-a'],
    };
    const { container } = render(DomainReadinessPanel);
    const buttons = container.querySelectorAll('button.drp-mute');
    // One toggle button per rendered row.
    expect(buttons.length).toBe(2);

    // Collect the aria-pressed state keyed by accessible name.
    const pressedByName = new Map<string, string | null>();
    for (const btn of buttons) {
      const name = btn.getAttribute('aria-label') ?? '';
      pressedByName.set(name, btn.getAttribute('aria-pressed'));
    }
    // 'dom-a' is backend and muted; 'dom-b' is security and unmuted.
    expect(pressedByName.get('Unmute notifications for backend')).toBe('true');
    expect(pressedByName.get('Mute notifications for security')).toBe('false');
  });

  it('accessible name switches between "Mute…" and "Unmute…" based on state', () => {
    // Start unmuted.
    preferencesStore.prefs.domain_readiness_notifications = {
      enabled: true,
      muted_domain_ids: [],
    };
    const { container } = render(DomainReadinessPanel);
    const btn = container.querySelector(
      'button.drp-mute[aria-label="Mute notifications for backend"]',
    ) as HTMLButtonElement | null;
    expect(btn).not.toBeNull();
    expect(btn!.getAttribute('aria-pressed')).toBe('false');
  });

  it('clicking the mute button toggles state and does NOT trigger domain:select / onSelect', async () => {
    // Spy on the store method.
    const toggleSpy = vi
      .spyOn(preferencesStore, 'toggleDomainMute')
      .mockImplementation(async (id: string) => {
        const cur = preferencesStore.prefs.domain_readiness_notifications.muted_domain_ids;
        const next = cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id];
        preferencesStore.prefs.domain_readiness_notifications = {
          ...preferencesStore.prefs.domain_readiness_notifications,
          muted_domain_ids: next,
        };
      });

    const onSelect = vi.fn();
    const { container } = render(DomainReadinessPanel, { props: { onSelect } });
    const selectHandler = vi.fn();
    container.addEventListener('domain:select', selectHandler as EventListener);

    const muteBtn = container.querySelector(
      'button.drp-mute[aria-label="Mute notifications for backend"]',
    ) as HTMLButtonElement;
    expect(muteBtn).not.toBeNull();

    await fireEvent.click(muteBtn);

    expect(toggleSpy).toHaveBeenCalledWith('dom-a');
    // Critically: clicking the mute button must NOT propagate to row selection.
    expect(onSelect).not.toHaveBeenCalled();
    expect(selectHandler).not.toHaveBeenCalled();
  });

  it('clicking a muted row\'s button flips aria-pressed back to "false"', async () => {
    preferencesStore.prefs.domain_readiness_notifications = {
      enabled: true,
      muted_domain_ids: ['dom-a'],
    };
    vi.spyOn(preferencesStore, 'toggleDomainMute').mockImplementation(async (id: string) => {
      const cur = preferencesStore.prefs.domain_readiness_notifications.muted_domain_ids;
      const next = cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id];
      preferencesStore.prefs.domain_readiness_notifications = {
        ...preferencesStore.prefs.domain_readiness_notifications,
        muted_domain_ids: next,
      };
    });

    const { container } = render(DomainReadinessPanel);
    const muteBtn = container.querySelector(
      'button.drp-mute[aria-label="Unmute notifications for backend"]',
    ) as HTMLButtonElement;
    expect(muteBtn).not.toBeNull();
    expect(muteBtn.getAttribute('aria-pressed')).toBe('true');

    await fireEvent.click(muteBtn);

    // After toggle, the corresponding button should re-render as unmuted.
    const nextBtn = container.querySelector(
      'button.drp-mute[aria-label="Mute notifications for backend"]',
    ) as HTMLButtonElement | null;
    expect(nextBtn).not.toBeNull();
    expect(nextBtn!.getAttribute('aria-pressed')).toBe('false');
  });

  it('applies the drp-mute--active class when the domain is muted', () => {
    preferencesStore.prefs.domain_readiness_notifications = {
      enabled: true,
      muted_domain_ids: ['dom-a'],
    };
    const { container } = render(DomainReadinessPanel);
    const mutedBtn = container.querySelector(
      'button.drp-mute[aria-label="Unmute notifications for backend"]',
    ) as HTMLButtonElement;
    expect(mutedBtn.classList.contains('drp-mute--active')).toBe(true);

    const unmutedBtn = container.querySelector(
      'button.drp-mute[aria-label="Mute notifications for security"]',
    ) as HTMLButtonElement;
    expect(unmutedBtn.classList.contains('drp-mute--active')).toBe(false);
  });
});
