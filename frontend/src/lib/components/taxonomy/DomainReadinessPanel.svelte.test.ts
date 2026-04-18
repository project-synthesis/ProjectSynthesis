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

  it('pressing Space on the mute button does NOT activate the row (a11y)', async () => {
    // When a keyboard user tabs to the mute button and presses Space, the
    // browser synthesises a click on the button, but the keydown also
    // bubbles to the row handler (`onRowKey`). Without a propagation guard,
    // `select(report)` would fire on top of `toggleDomainMute`. Lock the
    // row-level handler to no-op when the keydown originated on a nested
    // control.
    const toggleSpy = vi
      .spyOn(preferencesStore, 'toggleDomainMute')
      .mockImplementation(async () => {});
    const onSelect = vi.fn();
    const { container } = render(DomainReadinessPanel, { props: { onSelect } });
    const selectHandler = vi.fn();
    container.addEventListener('domain:select', selectHandler as EventListener);

    const muteBtn = container.querySelector(
      'button.drp-mute[aria-label="Mute notifications for backend"]',
    ) as HTMLButtonElement;
    expect(muteBtn).not.toBeNull();

    await fireEvent.keyDown(muteBtn, { key: ' ' });
    await fireEvent.keyDown(muteBtn, { key: 'Enter' });

    // Row-level handler must NOT promote the keydown into a selection —
    // the event originated inside the nested mute control.
    expect(onSelect).not.toHaveBeenCalled();
    expect(selectHandler).not.toHaveBeenCalled();
    // Sanity: spy exists so we don't mask true negatives.
    expect(toggleSpy).not.toHaveBeenCalled();
  });

  it('pressing Space on the row (not a nested control) DOES activate selection', async () => {
    // Regression guard: the propagation fix must NOT break the main-path
    // keyboard activation on the row itself. Space on the row still selects.
    const onSelect = vi.fn();
    const { container } = render(DomainReadinessPanel, { props: { onSelect } });
    const row = container.querySelector(
      'div.drp-row[role="button"]',
    ) as HTMLDivElement;
    expect(row).not.toBeNull();
    await fireEvent.keyDown(row, { key: ' ' });
    expect(onSelect).toHaveBeenCalled();
  });

  it('row exposes an aria-label combining domain, stability, and emergence state', () => {
    // Without an accessible name, a div[role="button"] reads only its inner
    // text — which on this panel is a grid of numbers. Lock the label format
    // so screen-reader users get the same summary sighted users get via the
    // tooltip.
    preferencesStore.prefs.domain_readiness_notifications = {
      enabled: true,
      muted_domain_ids: ['dom-a'],
    };
    const { container } = render(DomainReadinessPanel);
    const rows = container.querySelectorAll<HTMLDivElement>('div.drp-row[role="button"]');
    expect(rows.length).toBe(2);
    const labels = Array.from(rows).map((r) => r.getAttribute('aria-label') ?? '');
    // Muted domain announces the mute state; unmuted does not.
    expect(labels.some((l) => l.includes('backend') && l.includes('notifications muted'))).toBe(true);
    expect(labels.some((l) => l.includes('security') && !l.includes('notifications muted'))).toBe(true);
  });

  it('Space on a row activates select() and preventDefaults the page scroll', async () => {
    const onSelect = vi.fn();
    const { container } = render(DomainReadinessPanel, { props: { onSelect } });
    const row = container.querySelector<HTMLDivElement>('div.drp-row[role="button"]');
    expect(row).not.toBeNull();

    // Directly dispatch a KeyboardEvent so we can inspect defaultPrevented.
    const spaceEvent = new KeyboardEvent('keydown', {
      key: ' ',
      bubbles: true,
      cancelable: true,
    });
    row!.dispatchEvent(spaceEvent);
    expect(spaceEvent.defaultPrevented).toBe(true);
    expect(onSelect).toHaveBeenCalledTimes(1);
  });

  it('Enter on a row activates select() without calling preventDefault', async () => {
    const onSelect = vi.fn();
    const { container } = render(DomainReadinessPanel, { props: { onSelect } });
    const row = container.querySelector<HTMLDivElement>('div.drp-row[role="button"]');
    expect(row).not.toBeNull();

    const enterEvent = new KeyboardEvent('keydown', {
      key: 'Enter',
      bubbles: true,
      cancelable: true,
    });
    row!.dispatchEvent(enterEvent);
    expect(enterEvent.defaultPrevented).toBe(false);
    expect(onSelect).toHaveBeenCalledTimes(1);
  });

  it('mute button glyph is aria-hidden so the accessible name is not duplicated', () => {
    preferencesStore.prefs.domain_readiness_notifications = {
      enabled: true,
      muted_domain_ids: [],
    };
    const { container } = render(DomainReadinessPanel);
    const btn = container.querySelector<HTMLButtonElement>('button.drp-mute');
    expect(btn).not.toBeNull();
    // Glyph container is aria-hidden whether the glyph is an emoji span or
    // an inline SVG — locking the contract, not the rendering.
    const glyph = btn!.querySelector('[aria-hidden="true"]');
    expect(glyph).not.toBeNull();
  });

  it('mute button renders a 1px-stroke SVG bell, not an emoji (brand spec)', () => {
    // PR #27 follow-up: the raw \u{1F515}/\u{1F514} emoji violates the
    // "no glyph noise" brand rule and renders inconsistently across OSes.
    // The bell must be an inline SVG styled with `currentColor` so it
    // inherits the active/hover color tokens and matches the 1px-contour
    // visual language of the rest of the app.
    const { container } = render(DomainReadinessPanel);
    const btn = container.querySelector<HTMLButtonElement>('button.drp-mute');
    expect(btn).not.toBeNull();

    const svg = btn!.querySelector('svg');
    expect(svg, 'mute button should render an <svg> glyph').not.toBeNull();
    expect(svg!.getAttribute('aria-hidden')).toBe('true');
    expect(svg!.getAttribute('stroke')).toBe('currentColor');

    // Belt-and-braces: neither emoji codepoint may leak into the rendered
    // output. This is what actually protects against drift.
    const text = btn!.textContent ?? '';
    expect(text).not.toContain('\u{1F514}');
    expect(text).not.toContain('\u{1F515}');
  });
});
