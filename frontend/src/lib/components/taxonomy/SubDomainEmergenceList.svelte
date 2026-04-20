<script lang="ts">
  /**
   * SubDomainEmergenceList — ranked qualifier candidates with per-row gap
   * gauges, dominant-source badges, and threshold context.
   *
   * Mirrors the engine's three-source cascade: domain_raw > intent_label >
   * tf_idf. Tier chromatic encoding: green=ready, cyan=warming, dim=inert.
   */
  import type {
    QualifierCandidate,
    QualifierSource,
    SubDomainEmergenceReport,
  } from '$lib/api/readiness';
  import { tooltip } from '$lib/actions/tooltip';

  interface Props {
    report: SubDomainEmergenceReport;
  }

  let { report }: Props = $props();

  const thresholdPct = $derived(Math.round(report.threshold * 100));

  const tierColor = $derived.by(() => {
    switch (report.tier) {
      case 'ready':
        return 'var(--color-neon-green)';
      case 'warming':
        return 'var(--color-neon-cyan)';
      case 'inert':
        return 'var(--color-text-dim)';
    }
  });

  const tierLabel = $derived(report.tier.toUpperCase());

  const blockedLabel = $derived.by(() => {
    switch (report.blocked_reason) {
      case 'no_candidates':
        return 'No qualifier candidates.';
      case 'insufficient_members':
        return `Top candidate below member floor (${report.min_member_count}).`;
      case 'below_threshold':
        return `Top candidate below threshold (${thresholdPct}%).`;
      case 'single_cluster':
        return 'Top candidate concentrated in a single cluster.';
      default:
        return null;
    }
  });

  function sourceShort(source: QualifierSource): string {
    switch (source) {
      case 'domain_raw':
        return 'RAW';
      case 'intent_label':
        return 'INT';
      case 'tf_idf':
        return 'TFI';
    }
  }

  function sourceColor(source: QualifierSource): string {
    switch (source) {
      case 'domain_raw':
        return 'var(--color-neon-cyan)';
      case 'intent_label':
        return 'var(--color-neon-pink)';
      case 'tf_idf':
        return 'var(--color-neon-indigo)';
    }
  }

  function consistencyPct(c: QualifierCandidate): number {
    return Math.round(c.consistency * 100);
  }

  function formatGap(gap: number | null): string {
    if (gap == null) return '—';
    const pts = gap * 100;
    if (pts <= 0) return `ready (+${(-pts).toFixed(1)}pts)`;
    return `+${pts.toFixed(1)}pts to threshold`;
  }

  function rowFillPct(c: QualifierCandidate): number {
    // Visualize consistency against threshold — full bar at 1.5× threshold.
    const target = Math.max(report.threshold * 1.5, 0.01);
    return Math.min(100, Math.round((c.consistency / target) * 100));
  }

  function rowThresholdMarkerPct(): number {
    const target = Math.max(report.threshold * 1.5, 0.01);
    return Math.min(100, Math.round((report.threshold / target) * 100));
  }
</script>

<div class="sel">
  <div class="sel-header">
    <span class="sel-title">EMERGENCE</span>
    <span class="sel-tier" style="color: {tierColor}">{tierLabel}</span>
  </div>

  <div class="sel-threshold" use:tooltip={report.threshold_formula}>
    <span class="sel-threshold-label">THRESHOLD</span>
    <span class="sel-threshold-value">{thresholdPct}%</span>
    <span class="sel-threshold-meta">· min {report.min_member_count}m · {report.total_opts} opts</span>
  </div>

  {#if report.top_candidate}
    {@const top = report.top_candidate}
    <div
      class="sel-row sel-row-top"
      style="--row-accent: {tierColor}"
      use:tooltip={`Dominant source: ${top.dominant_source} · cluster breadth: ${top.cluster_breadth}`}
    >
      <div class="sel-row-head">
        <span class="sel-qualifier">{top.qualifier}</span>
        <span
          class="sel-source-badge"
          style="color: {sourceColor(top.dominant_source)}; border-color: color-mix(in srgb, {sourceColor(top.dominant_source)} 40%, transparent)"
        >
          {sourceShort(top.dominant_source)}
        </span>
        <span class="sel-count">{top.count}m</span>
      </div>
      <div
        class="sel-meter"
        role="meter"
        aria-valuemin="0"
        aria-valuemax="100"
        aria-valuenow={consistencyPct(top)}
        aria-label="Consistency {consistencyPct(top)}% vs threshold {thresholdPct}%"
      >
        <div class="sel-fill" style="width: {rowFillPct(top)}%; background: {tierColor}"></div>
        <div class="sel-marker" style="left: {rowThresholdMarkerPct()}%" aria-hidden="true"></div>
      </div>
      <div class="sel-numerics">
        <span class="sel-consistency">{consistencyPct(top)}%</span>
        <span class="sel-gap" style="color: {tierColor}">{formatGap(report.gap_to_threshold)}</span>
      </div>
    </div>
  {/if}

  {#if blockedLabel}
    <div class="sel-blocked">{blockedLabel}</div>
  {/if}

  {#if report.runner_ups.length > 0}
    <div class="sel-runners">
      {#each report.runner_ups as c (c.qualifier)}
        <div
          class="sel-runner"
          use:tooltip={`${c.qualifier} · source ${c.dominant_source} · breadth ${c.cluster_breadth}`}
        >
          <span class="sel-runner-name">{c.qualifier}</span>
          <span
            class="sel-runner-source"
            style="color: {sourceColor(c.dominant_source)}"
          >{sourceShort(c.dominant_source)}</span>
          <span class="sel-runner-pct">{consistencyPct(c)}%</span>
          <span class="sel-runner-count">{c.count}m</span>
        </div>
      {/each}
    </div>
  {/if}
</div>

<style>
  .sel {
    display: flex;
    flex-direction: column;
    gap: 3px;
  }

  .sel-header {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
  }

  .sel-title {
    font-family: var(--font-display);
    font-size: 10px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    color: var(--color-text-dim);
  }

  .sel-tier {
    font-family: var(--font-mono);
    font-size: 9px;
    font-weight: 700;
    letter-spacing: 0.05em;
  }

  .sel-threshold {
    display: flex;
    align-items: baseline;
    gap: 4px;
    font-family: var(--font-mono);
    font-size: 9px;
    color: var(--color-text-dim);
  }

  .sel-threshold-label {
    letter-spacing: 0.05em;
  }

  .sel-threshold-value {
    color: var(--color-text-primary);
    font-weight: 700;
    font-size: 10px;
  }

  .sel-threshold-meta {
    color: var(--color-text-dim);
    font-size: 9px;
  }

  .sel-row {
    display: flex;
    flex-direction: column;
    gap: 2px;
    padding: 3px 0;
  }

  .sel-row-top {
    border-top: 1px solid var(--color-border-subtle);
    border-bottom: 1px solid var(--color-border-subtle);
    padding: 4px 0;
  }

  .sel-row-head {
    display: flex;
    align-items: baseline;
    gap: 4px;
  }

  .sel-qualifier {
    font-family: var(--font-mono);
    font-size: 11px;
    font-weight: 700;
    color: var(--color-text-primary);
    flex: 1;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .sel-source-badge {
    font-family: var(--font-mono);
    font-size: 8px;
    font-weight: 500;
    padding: 0 3px;
    border: 1px solid;
    letter-spacing: 0.05em;
  }

  .sel-count {
    font-family: var(--font-mono);
    font-size: 9px;
    color: var(--color-text-dim);
    flex-shrink: 0;
  }

  .sel-meter {
    position: relative;
    height: 3px;
    width: 100%;
    background: var(--color-bg-input);
    box-shadow: inset 0 0 0 1px var(--color-border-subtle);
    overflow: hidden;
  }

  .sel-fill {
    position: absolute;
    left: 0;
    top: 0;
    height: 100%;
    transition: width var(--duration-progress) var(--ease-spring),
      background-color var(--duration-progress) var(--ease-spring);
  }

  .sel-marker {
    position: absolute;
    top: 0;
    height: 100%;
    width: 1px;
    background: var(--color-text-primary);
    opacity: 0.6;
    pointer-events: none;
  }

  .sel-numerics {
    display: flex;
    justify-content: space-between;
    font-family: var(--font-mono);
    font-size: 10px;
  }

  .sel-consistency {
    font-weight: 700;
    color: var(--color-text-primary);
  }

  .sel-gap {
    font-weight: 500;
  }

  .sel-blocked {
    font-family: var(--font-sans);
    font-size: 10px;
    color: var(--color-text-dim);
    padding: 2px 0;
  }

  .sel-runners {
    display: flex;
    flex-direction: column;
  }

  .sel-runner {
    display: grid;
    grid-template-columns: 1fr auto auto auto;
    gap: 4px;
    align-items: baseline;
    height: 18px;
    padding: 0 2px;
    border-top: 1px solid color-mix(in srgb, var(--color-border-subtle) 50%, transparent);
  }

  .sel-runner-name {
    font-family: var(--font-sans);
    font-size: 10px;
    color: var(--color-text-secondary);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    min-width: 0;
  }

  .sel-runner-source {
    font-family: var(--font-mono);
    font-size: 8px;
    letter-spacing: 0.05em;
  }

  .sel-runner-pct {
    font-family: var(--font-mono);
    font-size: 9px;
    color: var(--color-text-secondary);
  }

  .sel-runner-count {
    font-family: var(--font-mono);
    font-size: 9px;
    color: var(--color-text-dim);
  }

  @media (prefers-reduced-motion: reduce) {
    .sel-fill {
      transition: none;
    }
  }
</style>
