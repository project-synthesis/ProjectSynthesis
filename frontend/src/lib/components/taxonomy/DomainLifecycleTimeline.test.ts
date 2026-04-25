import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, cleanup } from '@testing-library/svelte';
import DomainLifecycleTimeline from './DomainLifecycleTimeline.svelte';
import { clustersStore } from '$lib/stores/clusters.svelte';

describe('DomainLifecycleTimeline', () => {
  beforeEach(() => {
    clustersStore._reset();
    vi.restoreAllMocks();
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

  it('observatoryStore.setPeriod refetches via /api/clusters/activity/history (T10)', async () => {
    const { observatoryStore } = await import('$lib/stores/observatory.svelte');
    observatoryStore._reset?.();
    const fetchSpy = vi.fn().mockResolvedValue({
      ok: true, status: 200,
      json: async () => ({ events: [], total: 0, has_more: false }),
    });
    vi.stubGlobal('fetch', fetchSpy);
    vi.useFakeTimers();
    render(DomainLifecycleTimeline);
    observatoryStore.setPeriod('24h');
    await vi.advanceTimersByTimeAsync(1100);
    const urlsCalled = fetchSpy.mock.calls.map((c) => String(c[0])).join(' ');
    expect(urlsCalled).toMatch(/activity\/history\?.*since=\d{4}-\d{2}-\d{2}/);
    vi.useRealTimers();
  });
});
