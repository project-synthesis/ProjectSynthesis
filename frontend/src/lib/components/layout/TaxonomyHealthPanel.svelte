<script lang="ts">
  import type { ClusterStats } from '$lib/api/clusters';
  import { qHealthColor } from '$lib/utils/colors';
  import { TAXONOMY_TOOLTIPS } from '$lib/utils/metric-tooltips';
  import { assessTaxonomyHealth } from '$lib/utils/taxonomy-health';
  import { tooltip } from '$lib/actions/tooltip';
  import ScoreSparkline from '$lib/components/shared/ScoreSparkline.svelte';

  interface Props {
    stats: ClusterStats;
    activeCount: number;
    candidateCount: number;
  }

  const { stats, activeCount, candidateCount }: Props = $props();
  const health = $derived(assessTaxonomyHealth(stats));
</script>

<div class="health-panel">
  <div class="health-title">TAXONOMY HEALTH</div>
  <div class="health-metric" use:tooltip={TAXONOMY_TOOLTIPS.q_system}>
    <span class="metric-label">Q_health</span>
    <span class="metric-value" style="color: {qHealthColor(stats.q_health ?? stats.q_system)}">{(stats.q_health ?? stats.q_system)?.toFixed(3) ?? '—'}</span>
  </div>
  <div class="health-metric" use:tooltip={TAXONOMY_TOOLTIPS.coherence}>
    <span class="metric-label">Coherence</span>
    <span class="metric-value">{(stats.q_health_coherence_w ?? stats.q_coherence)?.toFixed(3) ?? '—'}</span>
  </div>
  <div class="health-metric" use:tooltip={TAXONOMY_TOOLTIPS.separation}>
    <span class="metric-label">Separation</span>
    <span class="metric-value">{(stats.q_health_separation_w ?? stats.q_separation)?.toFixed(3) ?? '—'}</span>
  </div>
  {#if stats.q_sparkline && stats.q_sparkline.length >= 2}
    <div class="health-sparkline">
      <ScoreSparkline scores={stats.q_sparkline} width={100} height={18} minRange={0.2} />
      {#if health}
        <span class="health-headline" style="color: {health.color}">{health.headline}</span>
      {/if}
    </div>
  {/if}
  {#if health}
    <div class="health-detail">{health.detail}</div>
  {/if}
  <div class="health-counts">
    <span use:tooltip={TAXONOMY_TOOLTIPS.active}>{activeCount} active</span>
    <span class="dot-sep">·</span>
    <span use:tooltip={TAXONOMY_TOOLTIPS.candidate}>{candidateCount} candidate</span>
  </div>
</div>

<style>
  .health-panel {
    display: flex;
    flex-direction: column;
    gap: 4px;
    padding: 6px 0;
  }

  .health-title {
    font-size: 9px;
    font-family: var(--font-mono);
    color: var(--color-text-dim);
    letter-spacing: 0.08em;
    padding: 0 6px;
  }

  .health-metric {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 2px 6px;
    background: var(--color-bg-card);
    border: 1px solid var(--color-border-subtle);
  }

  .metric-label {
    font-size: 10px;
    font-family: var(--font-sans);
    color: var(--color-text-dim);
  }

  .metric-value {
    font-size: 10px;
    font-family: var(--font-mono);
    color: var(--color-text-secondary);
  }

  .health-sparkline {
    display: flex;
    align-items: center;
    gap: 6px;
    margin-top: 2px;
  }

  .health-headline {
    font-family: var(--font-mono);
    font-size: 9px;
    white-space: nowrap;
    font-weight: 600;
  }

  .health-detail {
    font-family: var(--font-sans);
    font-size: 10px;
    color: var(--color-text-secondary);
    line-height: 1.4;
    margin-top: 2px;
  }

  .health-counts {
    display: flex;
    align-items: center;
    gap: 6px;
    padding: 2px 6px;
    font-size: 10px;
    font-family: var(--font-mono);
    color: var(--color-text-dim);
  }

  .dot-sep {
    color: var(--color-border-subtle);
  }
</style>
