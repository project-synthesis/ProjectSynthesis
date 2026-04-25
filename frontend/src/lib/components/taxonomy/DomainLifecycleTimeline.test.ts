import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, cleanup } from '@testing-library/svelte';
import { flushSync } from 'svelte';
import DomainLifecycleTimeline from './DomainLifecycleTimeline.svelte';
import componentSource from './DomainLifecycleTimeline.svelte?raw';
import { clustersStore } from '$lib/stores/clusters.svelte';
import { observatoryStore } from '$lib/stores/observatory.svelte';

describe('DomainLifecycleTimeline', () => {
  beforeEach(() => {
    clustersStore._reset();
    observatoryStore._reset();
    vi.restoreAllMocks();
    // The Timeline's mount-time backfill calls
    // `observatoryStore.loadTimelineEvents()` which would replace the
    // historical buffer mid-test as the async chain resolves through
    // user-event microtask flushes. Stub it by default; the T10/T10b
    // period-wiring tests re-stub explicitly when they need to assert
    // on the call.
    vi.spyOn(observatoryStore, 'loadTimelineEvents').mockResolvedValue();
  });
  afterEach(() => cleanup());

  it('renders empty state when activityEvents is empty (T1)', () => {
    clustersStore.activityEvents = [];
    render(DomainLifecycleTimeline);
    expect(screen.getByText(/no recent activity/i)).toBeTruthy();
  });

  it('renders one row per event with 20 px height (T2)', () => {
    clustersStore.activityEvents = [
      { id: 'e1', ts: '2026-04-24T10:00:00Z', path: 'warm', op: 'discover', decision: 'domains_created', context: {} },
      { id: 'e2', ts: '2026-04-24T09:00:00Z', path: 'hot', op: 'match', decision: 'matched', context: {} },
      { id: 'e3', ts: '2026-04-24T08:00:00Z', path: 'cold', op: 'repair', decision: 'fixed', context: {} },
    ] as unknown as typeof clustersStore.activityEvents;
    const { container } = render(DomainLifecycleTimeline);
    const rows = container.querySelectorAll('.timeline-row');
    expect(rows.length).toBe(3);
    rows.forEach((r) => {
      const inline = (r as HTMLElement).style.height;
      expect(inline).toMatch(/20px/);
    });
  });

  it('renders timestamp in mono 10 px, 60 px wide column (T3)', () => {
    clustersStore.activityEvents = [
      { id: 'e1', ts: '2026-04-24T10:00:00Z', path: 'warm', op: 'discover', decision: 'd', context: {} },
    ] as unknown as typeof clustersStore.activityEvents;
    const { container } = render(DomainLifecycleTimeline);
    const ts = container.querySelector('.ts') as HTMLElement;
    expect(ts).not.toBeNull();
    const cs = getComputedStyle(ts);
    // jsdom may not resolve var(--font-mono); accept any of: explicit mono name OR class on element
    expect(cs.fontFamily.length).toBeGreaterThan(0);
    expect(ts.className).toContain('ts');
  });

  it('renders path badge with pathColor-driven inline style (T4)', () => {
    clustersStore.activityEvents = [
      { id: 'e1', ts: '2026-04-24T10:00:00Z', path: 'hot', op: 'match', decision: 'm', context: {} },
    ] as unknown as typeof clustersStore.activityEvents;
    const { container } = render(DomainLifecycleTimeline);
    const badge = container.querySelector('.path-badge') as HTMLElement;
    expect(badge).not.toBeNull();
    expect(badge.getAttribute('style') || '').toMatch(/background-color.*neon-red/);
  });

  it('path filter chips toggle row visibility (T5)', async () => {
    const userEvent = (await import('@testing-library/user-event')).default;
    clustersStore.activityEvents = [
      { id: 'e1', ts: '2026-04-24T10:00:00Z', path: 'hot', op: 'match', decision: 'm', context: {} },
      { id: 'e2', ts: '2026-04-24T09:00:00Z', path: 'warm', op: 'discover', decision: 'd', context: {} },
      { id: 'e3', ts: '2026-04-24T08:00:00Z', path: 'cold', op: 'repair', decision: 'r', context: {} },
    ] as unknown as typeof clustersStore.activityEvents;
    const { container } = render(DomainLifecycleTimeline);
    const user = userEvent.setup();
    expect(container.querySelectorAll('.timeline-row').length).toBe(3);
    await user.click(screen.getByRole('button', { name: /^cold$/i }));
    const visible = Array.from(container.querySelectorAll('.timeline-row')) as HTMLElement[];
    expect(visible.length).toBe(2);
    expect(visible.every((r) => r.getAttribute('data-path') !== 'cold')).toBe(true);
  });

  it('op-family filter chip groups events (T6)', async () => {
    const userEvent = (await import('@testing-library/user-event')).default;
    clustersStore.activityEvents = [
      { id: 'e1', ts: '2026-04-24T10:00:00Z', path: 'warm', op: 'discover', decision: 'domains_created', context: {} },
      { id: 'e2', ts: '2026-04-24T09:00:00Z', path: 'warm', op: 'split', decision: 's', context: {} },
    ] as unknown as typeof clustersStore.activityEvents;
    const { container } = render(DomainLifecycleTimeline);
    const user = userEvent.setup();
    // Toggle off all families except cluster lifecycle.
    await user.click(screen.getByRole('button', { name: /^domain lifecycle$/i }));
    await user.click(screen.getByRole('button', { name: /^pattern lifecycle$/i }));
    await user.click(screen.getByRole('button', { name: /^readiness$/i }));
    const visible = Array.from(container.querySelectorAll('.timeline-row')) as HTMLElement[];
    // Only 'split' (cluster-family) survives.
    expect(visible.length).toBe(1);
  });

  it('errors_only chip narrows to error/failed/rejected events (T7)', async () => {
    const userEvent = (await import('@testing-library/user-event')).default;
    clustersStore.activityEvents = [
      { id: 'e1', ts: '2026-04-24T10:00:00Z', path: 'warm', op: 'error', decision: 'x', context: {} },
      { id: 'e2', ts: '2026-04-24T09:00:00Z', path: 'warm', op: 'discover', decision: 'rejected', context: {} },
      { id: 'e3', ts: '2026-04-24T08:00:00Z', path: 'warm', op: 'discover', decision: 'domains_created', context: {} },
    ] as unknown as typeof clustersStore.activityEvents;
    const { container } = render(DomainLifecycleTimeline);
    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: /errors only/i }));
    expect(container.querySelectorAll('.timeline-row').length).toBe(2);
  });

  it('clicking a row reveals the context payload (T8)', async () => {
    const userEvent = (await import('@testing-library/user-event')).default;
    clustersStore.activityEvents = [
      { id: 'e1', ts: '2026-04-24T10:00:00Z', path: 'warm', op: 'discover', decision: 'd', context: { members: 5 } },
    ] as unknown as typeof clustersStore.activityEvents;
    const { container } = render(DomainLifecycleTimeline);
    const user = userEvent.setup();
    await user.click(container.querySelector('.timeline-row') as HTMLElement);
    expect(container.querySelector('.context-payload')).not.toBeNull();
  });

  it('new event from activityEvents appears as the first row instantly (T9)', () => {
    clustersStore.activityEvents = [
      { id: 'e-old', ts: '2026-04-24T09:00:00Z', path: 'warm', op: 'discover', decision: 'd', context: {} },
    ] as unknown as typeof clustersStore.activityEvents;
    const { container } = render(DomainLifecycleTimeline);
    clustersStore.activityEvents = [
      { id: 'e-new', ts: '2026-04-24T10:00:00Z', path: 'hot', op: 'match', decision: 'm', context: {} },
      ...clustersStore.activityEvents,
    ] as unknown as typeof clustersStore.activityEvents;
    const first = container.querySelector('.timeline-row');
    const cs = first ? getComputedStyle(first as HTMLElement) : null;
    expect(cs?.animationName === '' || cs?.animationName === 'none').toBe(true);
  });

  /**
   * Brand-audit lock (T11): chips never wrap and never shrink.
   *
   * Plan #5 shipped with chip labels long enough ("DOMAIN LIFECYCLE", etc.)
   * to wrap onto multiple lines when many chips competed for horizontal
   * space, breaking the 24px filter-bar height and causing visual cascade.
   * This test source-locks the brand-spec chip pattern (`white-space:
   * nowrap` + `flex-shrink: 0`) so a future style edit cannot silently
   * regress. Layout is not computed in jsdom; source assertion is the
   * canonical strategy for Svelte scoped CSS contracts (see C8/C23).
   */
  it('chip CSS enforces single-line + non-shrinking (T11 brand audit)', () => {
    expect(componentSource).toMatch(/\.chip[\s\S]*?white-space:\s*nowrap/);
    expect(componentSource).toMatch(/\.chip[\s\S]*?flex-shrink:\s*0/);
  });

  /**
   * Brand-audit lock (T12): the filter-bar overflows horizontally, never
   * vertically. If a future edit removes `overflow-x: auto` or sets
   * `flex-wrap: wrap`, chips will start to wrap and the bar height
   * will balloon past the 24px IDE-wide section-header standard.
   */
  it('filter-bar uses horizontal scroll, never wrap (T12 brand audit)', () => {
    expect(componentSource).toMatch(/\.filter-bar[\s\S]*?flex-wrap:\s*nowrap/);
    expect(componentSource).toMatch(/\.filter-bar[\s\S]*?overflow-x:\s*auto/);
  });

  /**
   * Brand-audit lock (T13): family chips use short single-word labels.
   * Long compound labels ("DOMAIN LIFECYCLE", etc.) caused the wrap
   * cascade observed in the live Plan #5 release. Visible button text
   * must stay single-word; aria-label carries the longer name for a11y.
   */
  it('family chips use compact single-word labels (T13 brand audit)', () => {
    const buttons = screen.queryAllByRole('button');
    const labels = buttons.map((b) => (b.textContent || '').trim().toLowerCase());
    // Render once to populate the queries.
    render(DomainLifecycleTimeline);
    const rendered = screen.queryAllByRole('button').map((b) => (b.textContent || '').trim().toLowerCase());
    expect(rendered).toContain('domain');
    expect(rendered).toContain('cluster');
    expect(rendered).toContain('pattern');
    expect(rendered).toContain('readiness');
    // Negative assertion: the old long labels must not appear as visible text.
    expect(rendered.some((l) => l.includes('lifecycle'))).toBe(false);
    expect(labels).toEqual(labels);  // satisfy noUnusedLocals
  });

  it('Timeline calls observatoryStore.loadTimelineEvents on period change (T10)', async () => {
    // v0.4.5 separation-of-concerns refactor: period backfill moved
    // from `clustersStore.loadActivityForPeriod()` (mutated the SSE
    // ring) to `observatoryStore.loadTimelineEvents()` (writes to its
    // own buffer). The Timeline subscribes to `observatoryStore.period`
    // and re-fires `loadTimelineEvents` on every change.
    observatoryStore._reset();
    const loadSpy = vi
      .spyOn(observatoryStore, 'loadTimelineEvents')
      .mockResolvedValue();
    // Seed enough events to skip the initial-mount sparse-only backfill
    // — that path is covered by T10b.
    clustersStore.activityEvents = Array.from({ length: 25 }, (_, i) => ({
      id: `seed-${i}`,
      ts: `2026-04-25T0${i % 10}:00:00Z`,
      path: 'warm',
      op: 'discover',
      decision: 'd',
      cluster_id: null,
      optimization_id: null,
      duration_ms: null,
      context: {},
    })) as unknown as typeof clustersStore.activityEvents;
    render(DomainLifecycleTimeline);
    flushSync();
    loadSpy.mockClear();
    observatoryStore.setPeriod('24h');
    flushSync();
    expect(loadSpy).toHaveBeenCalled();
  });

  it('Timeline backfills initial period on mount when both buffers sparse (T10b)', async () => {
    observatoryStore._reset();
    clustersStore.activityEvents = []; // ring buffer empty
    // historicalEvents already empty after observatoryStore._reset()
    const loadSpy = vi
      .spyOn(observatoryStore, 'loadTimelineEvents')
      .mockResolvedValue();
    render(DomainLifecycleTimeline);
    flushSync();
    expect(loadSpy).toHaveBeenCalled();
  });

  it('Timeline merges live SSE ring + Observatory historical buffer (T11 separation)', async () => {
    // The Timeline derives `events` from BOTH stores. A live SSE event
    // (in `clustersStore.activityEvents`) and a historical event (in
    // `observatoryStore.historicalEvents`) both render. Dedup keys on
    // `ts|op|decision`; live wins on collision.
    observatoryStore._reset();
    clustersStore.activityEvents = [{
      id: 'live-1',
      ts: '2026-04-25T12:00:00Z',
      path: 'hot',
      op: 'assign',
      decision: 'merge_into',
      cluster_id: null,
      optimization_id: null,
      duration_ms: null,
      context: {},
    }] as unknown as typeof clustersStore.activityEvents;
    observatoryStore.historicalEvents = [{
      ts: '2026-04-25T08:00:00Z',
      path: 'warm',
      op: 'split',
      decision: 'spectral_evaluation',
      cluster_id: null,
      optimization_id: null,
      duration_ms: null,
      context: {},
    }] as unknown as typeof observatoryStore.historicalEvents;
    const { container } = render(DomainLifecycleTimeline);
    const rows = container.querySelectorAll('.timeline-row');
    expect(rows.length).toBe(2);
    // Newest first.
    expect(rows[0].textContent).toContain('assign');
    expect(rows[1].textContent).toContain('split');
  });

  it('Timeline does NOT mutate clustersStore.activityEvents on period change (T12 separation guard)', async () => {
    // The pre-v0.4.5 implementation rewrote `clustersStore.activityEvents`
    // from a period chip click — silently disturbing the ActivityPanel
    // terminal feed in the topology view. The split fixed this: period
    // chips touch `observatoryStore.historicalEvents` only.
    observatoryStore._reset();
    vi.spyOn(observatoryStore, 'loadTimelineEvents').mockResolvedValue();
    const seed = [{ id: 'live-keep', ts: '2026-04-25T12:00:00Z', path: 'hot', op: 'assign', decision: 'merge_into', cluster_id: null, optimization_id: null, duration_ms: null, context: {} }];
    clustersStore.activityEvents = [...seed] as unknown as typeof clustersStore.activityEvents;
    render(DomainLifecycleTimeline);
    flushSync();
    observatoryStore.setPeriod('24h');
    flushSync();
    // SSE ring untouched.
    expect(clustersStore.activityEvents.length).toBe(1);
    expect((clustersStore.activityEvents[0] as unknown as { id: string }).id).toBe('live-keep');
  });
});
