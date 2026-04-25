import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, cleanup } from '@testing-library/svelte';
import DomainReadinessAggregate from './DomainReadinessAggregate.svelte';
import componentSource from './DomainReadinessAggregate.svelte?raw';
import { readinessStore } from '$lib/stores/readiness.svelte';

function makeReport(overrides: Record<string, unknown> = {}) {
  return {
    domain_id: 'd1',
    domain_label: 'backend',
    stability: { tier: 'healthy', consistency: 0.65, age_hours: 72, member_count: 20 },
    emergence: { tier: 'warming', total_opts: 80, gap_to_threshold: 15, consistency_pct: 25, emerging_sub_domains: [] },
    ...overrides,
  };
}

describe('DomainReadinessAggregate', () => {
  beforeEach(() => {
    readinessStore.reports = [];
    vi.restoreAllMocks();
  });
  afterEach(() => cleanup());

  it('renders empty-state copy when readinessStore.reports is empty (R1)', () => {
    render(DomainReadinessAggregate);
    expect(screen.getByText(/no domains yet/i)).toBeTruthy();
  });

  it('renders one card per domain report (R2)', () => {
    readinessStore.reports = [
      makeReport({ domain_id: 'd1', domain_label: 'backend' }),
      makeReport({ domain_id: 'd2', domain_label: 'frontend' }),
      makeReport({ domain_id: 'd3', domain_label: 'database' }),
    ] as unknown as typeof readinessStore.reports;
    const { container } = render(DomainReadinessAggregate);
    expect(container.querySelectorAll('.readiness-card').length).toBe(3);
  });

  it('sorts by stability tier — critical first, then guarded, then healthy (R3)', () => {
    readinessStore.reports = [
      makeReport({ domain_id: 'd-h', domain_label: 'healthy-one', stability: { tier: 'healthy', consistency: 0.7, age_hours: 200, member_count: 50 } }),
      makeReport({ domain_id: 'd-c', domain_label: 'critical-one', stability: { tier: 'critical', consistency: 0.1, age_hours: 10, member_count: 3 } }),
      makeReport({ domain_id: 'd-g', domain_label: 'guarded-one', stability: { tier: 'guarded', consistency: 0.4, age_hours: 60, member_count: 12 } }),
    ] as unknown as typeof readinessStore.reports;
    const { container } = render(DomainReadinessAggregate);
    const firstCard = container.querySelector('.readiness-card') as HTMLElement;
    expect(firstCard.getAttribute('data-tier')).toBe('critical');
  });

  it('card click dispatches domain:select CustomEvent with domain_id (R4)', async () => {
    const userEvent = (await import('@testing-library/user-event')).default;
    readinessStore.reports = [makeReport({ domain_id: 'd-abc', domain_label: 'X' })] as unknown as typeof readinessStore.reports;
    const { container } = render(DomainReadinessAggregate);
    const user = userEvent.setup();
    let receivedId: string | null = null;
    container.addEventListener('domain:select', ((e: Event) => {
      const ev = e as CustomEvent<{ domain_id: string }>;
      receivedId = ev.detail.domain_id;
    }) as EventListener);
    await user.click(container.querySelector('.readiness-card') as HTMLElement);
    expect(receivedId).toBe('d-abc');
  });

  it('domain with zero sub-domains renders card without empty-row churn (R5)', () => {
    readinessStore.reports = [
      makeReport({
        domain_id: 'd1', domain_label: 'solo',
        emergence: { tier: 'cold', total_opts: 5, gap_to_threshold: 50, consistency_pct: 0, emerging_sub_domains: [] },
      }),
    ] as unknown as typeof readinessStore.reports;
    const { container } = render(DomainReadinessAggregate);
    const card = container.querySelector('.readiness-card');
    expect(card?.querySelector('.emergence-empty-placeholder')).toBeNull();
  });

  it('mid-session dissolution: click on a card whose domain is gone is a no-op (R6)', async () => {
    const userEvent = (await import('@testing-library/user-event')).default;
    readinessStore.reports = [makeReport({ domain_id: 'd-gone', domain_label: 'was-here' })] as unknown as typeof readinessStore.reports;
    const { container } = render(DomainReadinessAggregate);
    const user = userEvent.setup();
    vi.spyOn(readinessStore, 'byDomain').mockReturnValue(null);
    let receivedId: string | null = null;
    container.addEventListener('domain:select', ((e: Event) => {
      const ev = e as CustomEvent<{ domain_id: string }>;
      receivedId = ev.detail.domain_id;
    }) as EventListener);
    await user.click(container.querySelector('.readiness-card') as HTMLElement);
    expect(receivedId).toBeNull();
  });

  it('respects prefers-reduced-motion (R7)', () => {
    readinessStore.reports = [makeReport()] as unknown as typeof readinessStore.reports;
    render(DomainReadinessAggregate);
    // Source-locked: assert the @media block lives in the .svelte file (Svelte's
    // scoped CSS isn't injected at test time — same C8/C23 strategy from Plan #4).
    expect(componentSource).toContain('prefers-reduced-motion: reduce');
  });
});
