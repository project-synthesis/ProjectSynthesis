/**
 * Readiness UI components — brand compliance + behavior contract tests.
 *
 * Coverage target: chromatic tier encoding, ARIA meter contract, sort order
 * match with backend, and (critically) zero glow/shadow regressions per
 * Project Synthesis brand guidelines.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/svelte';
import type {
  DomainReadinessReport,
  DomainStabilityReport,
  SubDomainEmergenceReport,
} from '$lib/api/readiness';
import DomainStabilityMeter from './DomainStabilityMeter.svelte';
import SubDomainEmergenceList from './SubDomainEmergenceList.svelte';
import DomainReadinessPanel from './DomainReadinessPanel.svelte';
import DomainReadinessSparkline from './DomainReadinessSparkline.svelte';
import { readinessStore } from '$lib/stores/readiness.svelte';
import * as readinessApi from '$lib/api/readiness';

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

/**
 * Brand guard: parses every style attribute on rendered element tree and asserts
 * no `box-shadow` uses spread or blur (only `inset 0 0 0 Npx` contours allowed).
 */
function assertNoGlowShadow(container: HTMLElement): void {
  const all = container.querySelectorAll('[style]');
  for (const el of all) {
    const style = el.getAttribute('style') ?? '';
    // Any box-shadow with a non-zero blur/spread (i.e. not `inset 0 0 0 Npx`) is banned.
    if (/box-shadow:/i.test(style)) {
      expect(style).toMatch(/inset\s+0\s+0\s+0\s+\d+px/);
    }
    expect(style).not.toMatch(/text-shadow:/i);
    expect(style).not.toMatch(/filter:\s*drop-shadow/i);
  }
}

describe('DomainStabilityMeter', () => {
  afterEach(() => cleanup());

  it('renders healthy tier in neon green', () => {
    const { container } = render(DomainStabilityMeter, {
      props: { report: baseStability({ tier: 'healthy', consistency: 0.55 }) },
    });
    const tier = container.querySelector('.dsm-tier') as HTMLElement;
    expect(tier.textContent?.trim()).toBe('HEALTHY');
    expect(tier.getAttribute('style')).toContain('--color-neon-green');
  });

  it('renders guarded tier in neon yellow', () => {
    const { container } = render(DomainStabilityMeter, {
      props: { report: baseStability({ tier: 'guarded', consistency: 0.2 }) },
    });
    const tier = container.querySelector('.dsm-tier') as HTMLElement;
    expect(tier.getAttribute('style')).toContain('--color-neon-yellow');
  });

  it('renders critical tier in neon red', () => {
    const { container } = render(DomainStabilityMeter, {
      props: {
        report: baseStability({
          tier: 'critical',
          consistency: 0.1,
          would_dissolve: true,
          dissolution_risk: 0.9,
          guards: {
            general_protected: false,
            has_sub_domain_anchor: false,
            age_eligible: true,
            above_member_ceiling: false,
            consistency_above_floor: false,
          },
        }),
      },
    });
    const tier = container.querySelector('.dsm-tier') as HTMLElement;
    expect(tier.getAttribute('style')).toContain('--color-neon-red');
  });

  it('exposes ARIA meter contract with correct values', () => {
    render(DomainStabilityMeter, {
      props: { report: baseStability({ consistency: 0.45 }) },
    });
    const meter = screen.getByRole('meter');
    expect(meter.getAttribute('aria-valuenow')).toBe('45');
    expect(meter.getAttribute('aria-valuemin')).toBe('0');
    expect(meter.getAttribute('aria-valuemax')).toBe('100');
    expect(meter.getAttribute('aria-label')).toContain('Consistency');
  });

  it('gauge fill width matches consistency percentage', () => {
    const { container } = render(DomainStabilityMeter, {
      props: { report: baseStability({ consistency: 0.72 }) },
    });
    const fill = container.querySelector('.dsm-fill') as HTMLElement;
    expect(fill.getAttribute('style')).toContain('width: 72%');
  });

  it('surfaces failing guards as yellow chips when age-eligible + below ceiling', () => {
    const { container } = render(DomainStabilityMeter, {
      props: {
        report: baseStability({
          tier: 'critical',
          would_dissolve: true,
          guards: {
            general_protected: false,
            has_sub_domain_anchor: false,
            age_eligible: true,
            above_member_ceiling: false,
            consistency_above_floor: false,
          },
        }),
      },
    });
    const chips = container.querySelectorAll('.dsm-guard-chip');
    expect(chips.length).toBeGreaterThan(0);
  });

  it('contains no glow, drop-shadow, or text-shadow', () => {
    const { container } = render(DomainStabilityMeter, {
      props: { report: baseStability({ tier: 'critical', would_dissolve: true }) },
    });
    assertNoGlowShadow(container);
  });
});

describe('SubDomainEmergenceList', () => {
  afterEach(() => cleanup());

  it('renders empty-state copy when no candidates', () => {
    render(SubDomainEmergenceList, {
      props: { report: baseEmergence({ blocked_reason: 'no_candidates' }) },
    });
    expect(screen.getByText('No qualifier candidates.')).toBeInTheDocument();
  });

  it('renders ready tier in green with negative gap', () => {
    const { container } = render(SubDomainEmergenceList, {
      props: {
        report: baseEmergence({
          tier: 'ready',
          ready: true,
          gap_to_threshold: -0.05,
          blocked_reason: 'none',
          top_candidate: {
            qualifier: 'auth',
            count: 8,
            consistency: 0.48,
            dominant_source: 'domain_raw',
            source_breakdown: { domain_raw: 8, intent_label: 0, tf_idf: 0 },
            cluster_breadth: 3,
          },
        }),
      },
    });
    const tier = container.querySelector('.sel-tier') as HTMLElement;
    expect(tier.textContent?.trim()).toBe('READY');
    expect(tier.getAttribute('style')).toContain('--color-neon-green');
    expect(screen.getByText('auth')).toBeInTheDocument();
  });

  it('renders warming tier in cyan', () => {
    const { container } = render(SubDomainEmergenceList, {
      props: {
        report: baseEmergence({
          tier: 'warming',
          gap_to_threshold: 0.05,
          blocked_reason: 'below_threshold',
          top_candidate: {
            qualifier: 'auth',
            count: 6,
            consistency: 0.38,
            dominant_source: 'intent_label',
            source_breakdown: { domain_raw: 2, intent_label: 4, tf_idf: 0 },
            cluster_breadth: 2,
          },
        }),
      },
    });
    const tier = container.querySelector('.sel-tier') as HTMLElement;
    expect(tier.getAttribute('style')).toContain('--color-neon-cyan');
  });

  it('exposes ARIA meter on top candidate row', () => {
    render(SubDomainEmergenceList, {
      props: {
        report: baseEmergence({
          tier: 'warming',
          top_candidate: {
            qualifier: 'auth',
            count: 6,
            consistency: 0.4,
            dominant_source: 'domain_raw',
            source_breakdown: { domain_raw: 6, intent_label: 0, tf_idf: 0 },
            cluster_breadth: 2,
          },
        }),
      },
    });
    const meter = screen.getByRole('meter');
    expect(meter.getAttribute('aria-valuenow')).toBe('40');
  });

  it('renders runner-ups when provided', () => {
    render(SubDomainEmergenceList, {
      props: {
        report: baseEmergence({
          tier: 'warming',
          blocked_reason: 'below_threshold',
          top_candidate: {
            qualifier: 'auth',
            count: 6,
            consistency: 0.4,
            dominant_source: 'domain_raw',
            source_breakdown: { domain_raw: 6, intent_label: 0, tf_idf: 0 },
            cluster_breadth: 2,
          },
          runner_ups: [
            {
              qualifier: 'api',
              count: 4,
              consistency: 0.25,
              dominant_source: 'tf_idf',
              source_breakdown: { domain_raw: 0, intent_label: 0, tf_idf: 4 },
              cluster_breadth: 1,
            },
          ],
        }),
      },
    });
    expect(screen.getByText('api')).toBeInTheDocument();
    expect(screen.getByText('TFI')).toBeInTheDocument();
  });

  it('contains no glow, drop-shadow, or text-shadow', () => {
    const { container } = render(SubDomainEmergenceList, {
      props: {
        report: baseEmergence({
          tier: 'ready',
          top_candidate: {
            qualifier: 'auth',
            count: 8,
            consistency: 0.55,
            dominant_source: 'domain_raw',
            source_breakdown: { domain_raw: 8, intent_label: 0, tf_idf: 0 },
            cluster_breadth: 3,
          },
        }),
      },
    });
    assertNoGlowShadow(container);
  });
});

describe('DomainReadinessPanel', () => {
  beforeEach(() => {
    readinessStore._reset();
    // Seed the store synchronously — avoid hitting the network.
    readinessStore.reports = [
      baseReport({
        domain_id: 'a',
        domain_label: 'aaa',
        stability: baseStability({ tier: 'healthy' }),
        emergence: baseEmergence({ tier: 'inert', gap_to_threshold: 0.3 }),
      }),
      baseReport({
        domain_id: 'b',
        domain_label: 'bbb',
        stability: baseStability({ tier: 'critical', would_dissolve: true, consistency: 0.1 }),
        emergence: baseEmergence({ tier: 'warming', gap_to_threshold: 0.05 }),
      }),
      baseReport({
        domain_id: 'c',
        domain_label: 'ccc',
        stability: baseStability({ tier: 'guarded', consistency: 0.25 }),
        emergence: baseEmergence({
          tier: 'ready',
          ready: true,
          gap_to_threshold: -0.02,
          blocked_reason: 'none',
        }),
      }),
    ];
    readinessStore.loaded = true;
  });
  afterEach(() => {
    cleanup();
    readinessStore._reset();
    vi.restoreAllMocks();
  });

  it('sorts critical first, then guarded, then healthy', () => {
    const { container } = render(DomainReadinessPanel);
    const names = Array.from(container.querySelectorAll('.drp-name-text')).map((n) =>
      n.textContent?.trim(),
    );
    expect(names).toEqual(['bbb', 'ccc', 'aaa']);
  });

  it('fires onSelect + dispatches domain:select custom event on row click', async () => {
    const onSelect = vi.fn();
    const { container } = render(DomainReadinessPanel, { props: { onSelect } });
    const selectHandler = vi.fn();
    container.addEventListener('domain:select', selectHandler as EventListener);
    const firstRow = container.querySelector('.drp-row') as HTMLElement;
    await fireEvent.click(firstRow);
    expect(onSelect).toHaveBeenCalledWith('b');
    expect(selectHandler).toHaveBeenCalled();
  });

  it('contains no glow, drop-shadow, or text-shadow', () => {
    const { container } = render(DomainReadinessPanel);
    assertNoGlowShadow(container);
  });

  it('renders a master mute toggle in the header reflecting enabled state', async () => {
    const { preferencesStore } = await import('$lib/stores/preferences.svelte');
    preferencesStore._reset();
    // Defaults to enabled=true after the PR #27 follow-up flip.
    const { container } = render(DomainReadinessPanel);
    const master = container.querySelector('.drp-master-mute') as HTMLButtonElement;
    expect(master).toBeTruthy();
    // Icon-only button: aria-label conveys state; aria-pressed reflects muted.
    expect(master.getAttribute('aria-pressed')).toBe('false');
    expect(master.getAttribute('aria-label')).toMatch(/mute all readiness/i);
    // Renders an SVG bell, not an emoji.
    const svg = master.querySelector('svg');
    expect(svg).toBeTruthy();
    expect(master.textContent ?? '').not.toMatch(/[\u{1F514}\u{1F515}]/u);
  });

  it('master toggle calls preferencesStore.update with enabled flipped', async () => {
    const { preferencesStore } = await import('$lib/stores/preferences.svelte');
    preferencesStore._reset();
    const updateSpy = vi
      .spyOn(preferencesStore, 'update')
      .mockResolvedValue(undefined);
    const { container } = render(DomainReadinessPanel);
    const master = container.querySelector('.drp-master-mute') as HTMLButtonElement;
    await fireEvent.click(master);
    expect(updateSpy).toHaveBeenCalledWith({
      domain_readiness_notifications: { enabled: false },
    });
  });

  it('master toggle reflects muted (aria-pressed=true) when enabled=false', async () => {
    const { preferencesStore } = await import('$lib/stores/preferences.svelte');
    preferencesStore._reset();
    preferencesStore.prefs.domain_readiness_notifications = {
      enabled: false,
      muted_domain_ids: [],
    };
    const { container } = render(DomainReadinessPanel);
    const master = container.querySelector('.drp-master-mute') as HTMLButtonElement;
    expect(master.getAttribute('aria-pressed')).toBe('true');
    expect(master.getAttribute('aria-label')).toMatch(/unmute all readiness/i);
  });
});

describe('DomainReadinessSparkline', () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it('renders consistency sparkline when history points are available', async () => {
    vi.spyOn(readinessApi, 'getDomainReadinessHistory').mockResolvedValue({
      domain_id: 'd1', domain_label: 'backend', window: '24h', bucketed: false,
      points: [
        { ts: '2026-04-17T11:00:00Z', consistency: 0.40, dissolution_risk: 0.5,
          top_candidate_gap: 0.10, stability_tier: 'guarded',
          emergence_tier: 'warming', is_bucket_mean: false },
        { ts: '2026-04-17T12:00:00Z', consistency: 0.42, dissolution_risk: 0.5,
          top_candidate_gap: 0.08, stability_tier: 'guarded',
          emergence_tier: 'warming', is_bucket_mean: false },
      ],
    });
    const { findByLabelText } = render(DomainReadinessSparkline, {
      props: { domainId: 'd1', domainLabel: 'backend', metric: 'consistency' },
    });
    const sparkline = await findByLabelText(/consistency 24h sparkline/i);
    expect(sparkline).toBeTruthy();
  });

  it('renders gap trendline when metric="gap"', async () => {
    vi.spyOn(readinessApi, 'getDomainReadinessHistory').mockResolvedValue({
      domain_id: 'd1', domain_label: 'backend', window: '24h', bucketed: false,
      points: [
        { ts: '2026-04-17T11:00:00Z', consistency: 0.4, dissolution_risk: 0.5,
          top_candidate_gap: 0.12, stability_tier: 'guarded',
          emergence_tier: 'warming', is_bucket_mean: false },
        { ts: '2026-04-17T12:00:00Z', consistency: 0.42, dissolution_risk: 0.5,
          top_candidate_gap: 0.08, stability_tier: 'guarded',
          emergence_tier: 'warming', is_bucket_mean: false },
      ],
    });
    const { findByLabelText } = render(DomainReadinessSparkline, {
      props: { domainId: 'd1', domainLabel: 'backend', metric: 'gap', baseline: 0 },
    });
    const gapline = await findByLabelText(/gap to threshold 24h trendline/i);
    expect(gapline).toBeTruthy();
  });

  it('forwards window="7d" to the API and labels the sparkline with "7d"', async () => {
    const historySpy = vi
      .spyOn(readinessApi, 'getDomainReadinessHistory')
      .mockResolvedValue({
        domain_id: 'd1', domain_label: 'backend', window: '7d', bucketed: true,
        points: [
          { ts: '2026-04-10T00:00:00Z', consistency: 0.40, dissolution_risk: 0.5,
            top_candidate_gap: 0.10, stability_tier: 'guarded',
            emergence_tier: 'warming', is_bucket_mean: true },
          { ts: '2026-04-17T00:00:00Z', consistency: 0.48, dissolution_risk: 0.4,
            top_candidate_gap: 0.02, stability_tier: 'healthy',
            emergence_tier: 'warming', is_bucket_mean: true },
        ],
      });
    const { findByLabelText } = render(DomainReadinessSparkline, {
      props: { domainId: 'd1', domainLabel: 'backend', metric: 'consistency', window: '7d' },
    });
    const sparkline = await findByLabelText(/consistency 7d sparkline/i);
    expect(sparkline).toBeTruthy();
    expect(historySpy).toHaveBeenCalledWith('d1', '7d');
  });

  it('forwards window="30d" for the gap metric', async () => {
    const historySpy = vi
      .spyOn(readinessApi, 'getDomainReadinessHistory')
      .mockResolvedValue({
        domain_id: 'd1', domain_label: 'backend', window: '30d', bucketed: true,
        points: [
          { ts: '2026-03-17T00:00:00Z', consistency: 0.40, dissolution_risk: 0.5,
            top_candidate_gap: 0.20, stability_tier: 'guarded',
            emergence_tier: 'warming', is_bucket_mean: true },
          { ts: '2026-04-17T00:00:00Z', consistency: 0.52, dissolution_risk: 0.4,
            top_candidate_gap: 0.05, stability_tier: 'healthy',
            emergence_tier: 'warming', is_bucket_mean: true },
        ],
      });
    const { findByLabelText } = render(DomainReadinessSparkline, {
      props: { domainId: 'd1', domainLabel: 'backend', metric: 'gap', baseline: 0, window: '30d' },
    });
    const trendline = await findByLabelText(/gap to threshold 30d trendline/i);
    expect(trendline).toBeTruthy();
    expect(historySpy).toHaveBeenCalledWith('d1', '30d');
  });

  it('defaults to window="24h" when prop is omitted', async () => {
    const historySpy = vi
      .spyOn(readinessApi, 'getDomainReadinessHistory')
      .mockResolvedValue({
        domain_id: 'd1', domain_label: 'backend', window: '24h', bucketed: false,
        points: [
          { ts: '2026-04-17T11:00:00Z', consistency: 0.40, dissolution_risk: 0.5,
            top_candidate_gap: 0.10, stability_tier: 'guarded',
            emergence_tier: 'warming', is_bucket_mean: false },
          { ts: '2026-04-17T12:00:00Z', consistency: 0.42, dissolution_risk: 0.5,
            top_candidate_gap: 0.08, stability_tier: 'guarded',
            emergence_tier: 'warming', is_bucket_mean: false },
        ],
      });
    render(DomainReadinessSparkline, {
      props: { domainId: 'd1', domainLabel: 'backend', metric: 'consistency' },
    });
    await Promise.resolve();
    expect(historySpy).toHaveBeenCalledWith('d1', '24h');
  });

  it('re-fetches history when readinessStore.invalidate() fires', async () => {
    // Prevent the invalidate() → loadAll(force=true) call from hitting the
    // network — we only care that the sparkline's own effect re-runs.
    vi.spyOn(readinessApi, 'getAllDomainReadiness').mockResolvedValue([]);
    const historySpy = vi
      .spyOn(readinessApi, 'getDomainReadinessHistory')
      .mockResolvedValue({
        domain_id: 'd1', domain_label: 'backend', window: '24h', bucketed: false,
        points: [
          { ts: '2026-04-17T11:00:00Z', consistency: 0.4, dissolution_risk: 0.5,
            top_candidate_gap: 0.10, stability_tier: 'guarded',
            emergence_tier: 'warming', is_bucket_mean: false },
          { ts: '2026-04-17T12:00:00Z', consistency: 0.42, dissolution_risk: 0.5,
            top_candidate_gap: 0.08, stability_tier: 'guarded',
            emergence_tier: 'warming', is_bucket_mean: false },
        ],
      });

    const { findByLabelText } = render(DomainReadinessSparkline, {
      props: { domainId: 'd1', domainLabel: 'backend', metric: 'consistency' },
    });
    await findByLabelText(/consistency 24h sparkline/i);
    expect(historySpy).toHaveBeenCalledTimes(1);

    // Tier-crossing SSE invalidates the store; the sparkline must refresh.
    readinessStore.invalidate();
    // Allow the $effect to re-run.
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(historySpy).toHaveBeenCalledTimes(2);

    readinessStore._reset();
  });

  it('drops null gap points before passing scores to sparkline', async () => {
    vi.spyOn(readinessApi, 'getDomainReadinessHistory').mockResolvedValue({
      domain_id: 'd1', domain_label: 'backend', window: '24h', bucketed: false,
      points: [
        { ts: '2026-04-17T11:00:00Z', consistency: 0.4, dissolution_risk: 0.5,
          top_candidate_gap: null, stability_tier: 'guarded',
          emergence_tier: 'inert', is_bucket_mean: false },
        { ts: '2026-04-17T12:00:00Z', consistency: 0.42, dissolution_risk: 0.5,
          top_candidate_gap: 0.08, stability_tier: 'guarded',
          emergence_tier: 'warming', is_bucket_mean: false },
      ],
    });
    // With only 1 non-null gap point, the sparkline should not render
    // (ScoreSparkline requires scores.length >= 2).
    const { queryByLabelText } = render(DomainReadinessSparkline, {
      props: { domainId: 'd1', domainLabel: 'backend', metric: 'gap', baseline: 0 },
    });
    // Give the $effect a tick to resolve the mocked fetch
    await Promise.resolve();
    expect(queryByLabelText(/gap to threshold 24h trendline/i)).toBeNull();
  });
});
