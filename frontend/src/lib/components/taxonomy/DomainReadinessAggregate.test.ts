import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, cleanup } from '@testing-library/svelte';
import DomainReadinessAggregate from './DomainReadinessAggregate.svelte';
import componentSource from './DomainReadinessAggregate.svelte?raw';
import { readinessStore } from '$lib/stores/readiness.svelte';
import type {
  DomainReadinessReport,
  DomainStabilityReport,
  SubDomainEmergenceReport,
} from '$lib/api/readiness';

/**
 * Build a complete `DomainReadinessReport` matching the backend contract in
 * `backend/app/schemas/sub_domain_readiness.py`. Tests can shallow-merge any
 * field via `overrides`; nested overrides for `stability` / `emergence` are
 * spread on top of the defaults so callers only specify what they care about.
 *
 * Keeping fixtures contract-complete lets the panel mount the real
 * `DomainStabilityMeter` + `SubDomainEmergenceList` children rather than
 * stubbing them — a regression here will surface as a runtime prop mismatch
 * rather than a silently-empty card.
 */
type ReportOverrides = Omit<Partial<DomainReadinessReport>, 'stability' | 'emergence'> & {
  stability?: Partial<DomainStabilityReport>;
  emergence?: Partial<SubDomainEmergenceReport>;
};

function makeReport(overrides: ReportOverrides = {}): DomainReadinessReport {
  const baseStability: DomainStabilityReport = {
    consistency: 0.65,
    dissolution_floor: 0.15,
    hysteresis_creation_threshold: 0.6,
    age_hours: 72,
    min_age_hours: 48,
    member_count: 20,
    member_ceiling: 5,
    sub_domain_count: 0,
    total_opts: 80,
    guards: {
      general_protected: false,
      has_sub_domain_anchor: false,
      age_eligible: true,
      above_member_ceiling: true,
      consistency_above_floor: true,
    },
    tier: 'healthy',
    dissolution_risk: 0.05,
    would_dissolve: false,
  };
  const baseEmergence: SubDomainEmergenceReport = {
    threshold: 0.55,
    threshold_formula: 'max(0.40, 0.60 - 0.004 * 20) = 0.52',
    min_member_count: 5,
    total_opts: 80,
    top_candidate: null,
    gap_to_threshold: 0.15,
    ready: false,
    blocked_reason: 'no_candidates',
    runner_ups: [],
    tier: 'warming',
  };
  const { stability: stabilityOverrides, emergence: emergenceOverrides, ...rest } = overrides;
  return {
    domain_id: 'd1',
    domain_label: 'backend',
    member_count: 20,
    stability: { ...baseStability, ...stabilityOverrides },
    emergence: { ...baseEmergence, ...emergenceOverrides },
    computed_at: '2026-04-25T00:00:00Z',
    ...rest,
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
    ];
    const { container } = render(DomainReadinessAggregate);
    expect(container.querySelectorAll('.readiness-card').length).toBe(3);
  });

  it('sorts by stability tier — critical first, then guarded, then healthy (R3)', () => {
    readinessStore.reports = [
      makeReport({ domain_id: 'd-h', domain_label: 'healthy-one', stability: { tier: 'healthy', consistency: 0.7, age_hours: 200, member_count: 50 } }),
      makeReport({ domain_id: 'd-c', domain_label: 'critical-one', stability: { tier: 'critical', consistency: 0.1, age_hours: 10, member_count: 3 } }),
      makeReport({ domain_id: 'd-g', domain_label: 'guarded-one', stability: { tier: 'guarded', consistency: 0.4, age_hours: 60, member_count: 12 } }),
    ];
    const { container } = render(DomainReadinessAggregate);
    const firstCard = container.querySelector('.readiness-card') as HTMLElement;
    expect(firstCard.getAttribute('data-tier')).toBe('critical');
  });

  it('card click dispatches domain:select CustomEvent with domain_id (R4)', async () => {
    const userEvent = (await import('@testing-library/user-event')).default;
    readinessStore.reports = [makeReport({ domain_id: 'd-abc', domain_label: 'X' })];
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
        emergence: { tier: 'inert', total_opts: 5, gap_to_threshold: 0.5, blocked_reason: 'no_candidates', runner_ups: [] },
      }),
    ];
    const { container } = render(DomainReadinessAggregate);
    const card = container.querySelector('.readiness-card');
    expect(card?.querySelector('.emergence-empty-placeholder')).toBeNull();
  });

  it('mid-session dissolution: click on a card whose domain is gone is a no-op (R6)', async () => {
    const userEvent = (await import('@testing-library/user-event')).default;
    readinessStore.reports = [makeReport({ domain_id: 'd-gone', domain_label: 'was-here' })];
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
    readinessStore.reports = [makeReport()];
    render(DomainReadinessAggregate);
    // Source-locked: assert the @media block lives in the .svelte file (Svelte's
    // scoped CSS isn't injected at test time — same C8/C23 strategy from Plan #4).
    expect(componentSource).toContain('prefers-reduced-motion: reduce');
  });

  /**
   * Spec lock (R8): each card header carries a 6px chromatic dot resolved
   * via `taxonomyColor(domain_label)`. The dot is the chromatic channel
   * for the domain identity in the brand grammar — without it, the
   * Observatory's readiness panel diverges from the navigator + topology
   * surfaces that all show domain identity through colour.
   */
  it('renders a 6px chromatic dot per card via taxonomyColor (R8)', () => {
    readinessStore.reports = [
      makeReport({ domain_id: 'd1', domain_label: 'backend' }),
      makeReport({ domain_id: 'd2', domain_label: 'frontend' }),
    ];
    const { container } = render(DomainReadinessAggregate);
    const dots = container.querySelectorAll('[data-test="domain-dot"]');
    expect(dots.length).toBe(2);
    for (const dot of Array.from(dots) as HTMLElement[]) {
      const style = dot.getAttribute('style') || '';
      // Resolved colour must be inline-set (taxonomyColor returns hex/rgb).
      expect(style).toMatch(/background-color:\s*(#|rgb)/i);
    }
    // Source-locked: 6px solid contour in the brand grammar.
    expect(componentSource).toMatch(
      /\.domain-dot\s*\{[^}]*width:\s*6px[^}]*height:\s*6px/,
    );
  });
});
