<script lang="ts">
  import { clustersStore } from '$lib/stores/clusters.svelte';
  import { qHealthColor, taxonomyColor } from '$lib/utils/colors';
  import { assessTaxonomyHealth, generatePanelInsight } from '$lib/utils/taxonomy-health';
  import type { PanelMode } from '$lib/utils/taxonomy-health';
  import { TAXONOMY_TOOLTIPS, TOPOLOGY_PANEL_TOOLTIPS } from '$lib/utils/metric-tooltips';
  import { tooltip } from '$lib/actions/tooltip';
  import ScoreSparkline from '$lib/components/shared/ScoreSparkline.svelte';
  import DomainStabilityMeter from './DomainStabilityMeter.svelte';
  import SubDomainEmergenceList from './SubDomainEmergenceList.svelte';
  import DomainReadinessSparkline from './DomainReadinessSparkline.svelte';
  import { readinessStore } from '$lib/stores/readiness.svelte';
  import type { ReadinessWindow } from '$lib/api/readiness';

  // Shared window state drives BOTH sparklines below — keeps consistency +
  // gap trendlines on the same x-axis so the operator can compare them.
  // Persisted across domain selections within a session but defaults back to
  // 24h on reload (matches backend bucketing default).
  let readinessWindow = $state<ReadinessWindow>('24h');
  const READINESS_WINDOWS: readonly ReadinessWindow[] = ['24h', '7d', '30d'];

  interface Props {
    hideInsight?: boolean;
  }

  let { hideInsight = false }: Props = $props();

  const stats = $derived(clustersStore.taxonomyStats);
  const detail = $derived(clustersStore.clusterDetail);
  const selectedId = $derived(clustersStore.selectedClusterId);

  // Determine panel mode
  const mode: PanelMode = $derived.by(() => {
    if (!selectedId || !detail) return 'system';
    if (detail.state === 'project') return 'project';
    if (detail.state === 'domain') return 'domain';
    return 'cluster';
  });

  // System mode data
  const qSystem = $derived(stats?.q_health ?? stats?.q_system ?? null);
  const qColor = $derived(qHealthColor(qSystem));
  const health = $derived(stats ? assessTaxonomyHealth(stats) : null);
  const sparkline = $derived(stats?.q_sparkline ?? []);
  const hasSparkline = $derived(sparkline.length >= 2);
  const silhouette = $derived(stats?.q_dbcv ?? null);
  const coverage = $derived(stats?.q_coverage ?? null);
  const coherence = $derived(stats?.q_health_coherence_w ?? stats?.q_coherence ?? null);
  const separation = $derived(stats?.q_health_separation_w ?? stats?.q_separation ?? null);

  // Cluster mode data
  const clusterCoh = $derived(detail?.coherence ?? null);
  const clusterSep = $derived(detail?.separation ?? null);
  const outCoh = $derived(detail?.output_coherence ?? null);
  const avgScore = $derived(detail?.avg_score ?? null);
  const blendRaw = $derived(detail?.blend_w_raw ?? null);
  const blendOpt = $derived(detail?.blend_w_optimized ?? null);
  const blendTrans = $derived(detail?.blend_w_transform ?? null);

  // Domain mode: aggregate from children
  const domainChildren = $derived(detail?.children ?? []);
  const domainChildCount = $derived(domainChildren.length);
  const domainAvgCoh = $derived.by(() => {
    const vals = domainChildren.filter(c => c.coherence != null).map(c => c.coherence!);
    return vals.length > 0 ? vals.reduce((a, b) => a + b, 0) / vals.length : null;
  });
  const domainAvgSep = $derived.by(() => {
    const vals = domainChildren.filter(c => c.separation != null).map(c => c.separation!);
    return vals.length > 0 ? vals.reduce((a, b) => a + b, 0) / vals.length : null;
  });
  const domainAvgScore = $derived.by(() => {
    const vals = domainChildren.filter(c => c.avg_score != null).map(c => c.avg_score!);
    return vals.length > 0 ? vals.reduce((a, b) => a + b, 0) / vals.length : null;
  });
  const domainTotalMembers = $derived(
    domainChildren.reduce((sum, c) => sum + (c.member_count || 0), 0)
  );
  const domainBelowFloor = $derived(
    domainChildren.filter(c => c.coherence != null && c.coherence < 0.5).length
  );

  // Domain-mode readiness — fetched on selection, cached in readinessStore.
  const domainReadiness = $derived(
    mode === 'domain' && selectedId ? readinessStore.byDomain(selectedId) : null,
  );

  $effect(() => {
    if (mode === 'domain' && selectedId) {
      if (!readinessStore.isFresh) {
        void readinessStore.loadAll();
      }
      if (!readinessStore.byDomain(selectedId)) {
        void readinessStore.loadOne(selectedId);
      }
    }
  });

  // Project-mode computed values
  const projectDomains = $derived(
    (detail?.children ?? []).filter(c => c.state === 'domain')
  );
  const projectDomainCount = $derived(projectDomains.length);
  const projectClusterCount = $derived(
    projectDomains.reduce((sum, d) => sum + (d.member_count ?? 0), 0)
  );
  const projectOptCount = $derived(detail?.optimizations?.length ?? 0);

  // Insight text
  const insight = $derived(generatePanelInsight({
    mode,
    stats,
    detail: detail ? {
      coherence: detail.coherence,
      separation: detail.separation,
      output_coherence: detail.output_coherence ?? null,
      blend_w_optimized: detail.blend_w_optimized ?? null,
      member_count: detail.member_count,
      split_failures: detail.split_failures ?? 0,
      label: detail.label,
      state: detail.state,
    } : null,
    domainChildCount,
    domainBelowFloor,
  }));

  function fmt(v: number | null): string {
    if (v == null) return '--';
    return v.toFixed(2);
  }

  function pct(v: number | null): string {
    if (v == null) return '--';
    return Math.round(v * 100) + '%';
  }
</script>

<div class="ip-panel">
  <!-- ROW 1: Identity -->
  <div class="ip-row ip-identity">
    {#if mode === 'system'}
      <div class="ip-identity-row">
        <span class="ip-q" use:tooltip={TAXONOMY_TOOLTIPS.q_system}>
          <span class="ip-q-label">Q</span>
          <span class="ip-q-value" style="color: {qColor}">{qSystem != null ? qSystem.toFixed(3) : '--'}</span>
        </span>
        {#if hasSparkline}
          <span class="ip-sparkline"><ScoreSparkline scores={sparkline} width={64} height={14} minRange={0.2} /></span>
        {/if}
        {#if health}
          <span class="ip-severity" style="background: {health.color}"></span>
        {/if}
      </div>
      {#if health}
        <div class="ip-headline" style="color: {health.color}" use:tooltip={health.detail}>{health.headline}</div>
      {/if}
    {:else if mode === 'cluster' && detail}
      <div class="ip-identity-row">
        <span class="ip-name" title={detail.label}>{detail.label}</span>
        <span class="ip-member-count">{detail.member_count}m</span>
      </div>
      <div class="ip-badges">
        <span class="ip-badge ip-badge-domain">{detail.domain.toUpperCase()}</span>
        <span class="ip-badge ip-badge-state">{detail.state.toUpperCase()}</span>
        {#if avgScore != null}
          <span class="ip-badge-score">{avgScore.toFixed(1)}</span>
        {/if}
      </div>
    {:else if mode === 'domain' && detail}
      <div class="ip-identity-row">
        <span class="ip-domain-name">{detail.label.toUpperCase()}</span>
        <span class="ip-member-count">{domainChildCount} clusters</span>
      </div>
    {:else if mode === 'project' && detail}
      <div class="ip-identity-row">
        <span class="ip-domain-name">{detail.label.includes('/') ? detail.label.split('/').pop() : detail.label}</span>
        <span class="ip-member-count">{projectDomainCount}d · {projectClusterCount}c</span>
      </div>
    {/if}
  </div>

  <!-- ROW 2: 2x2 Metric Grid -->
  <div class="ip-row ip-grid">
    {#if mode === 'system'}
      <div class="ip-cell" use:tooltip={TAXONOMY_TOOLTIPS.coherence}>
        <span class="ip-cell-label">COH</span>
        <span class="ip-cell-value">{fmt(coherence)}</span>
      </div>
      <div class="ip-cell" use:tooltip={TAXONOMY_TOOLTIPS.separation}>
        <span class="ip-cell-label">SEP</span>
        <span class="ip-cell-value">{fmt(separation)}</span>
      </div>
      <div class="ip-cell" use:tooltip={TOPOLOGY_PANEL_TOOLTIPS.silhouette}>
        <span class="ip-cell-label ip-cell-label-accent">SIL</span>
        <span class="ip-cell-value">{fmt(silhouette)}</span>
      </div>
      <div class="ip-cell" use:tooltip={TOPOLOGY_PANEL_TOOLTIPS.coverage}>
        <span class="ip-cell-label">COV</span>
        <span class="ip-cell-value">{fmt(coverage)}</span>
      </div>
    {:else if mode === 'cluster'}
      <div class="ip-cell" use:tooltip={TAXONOMY_TOOLTIPS.coherence}>
        <span class="ip-cell-label">COH</span>
        <span class="ip-cell-value" class:ip-warn={clusterCoh != null && clusterCoh < 0.5}>{fmt(clusterCoh)}</span>
      </div>
      <div class="ip-cell" use:tooltip={TAXONOMY_TOOLTIPS.separation}>
        <span class="ip-cell-label">SEP</span>
        <span class="ip-cell-value">{fmt(clusterSep)}</span>
      </div>
      <div class="ip-cell" use:tooltip={TOPOLOGY_PANEL_TOOLTIPS.output_coherence}>
        <span class="ip-cell-label ip-cell-label-accent">OUT</span>
        <span class="ip-cell-value" class:ip-warn={outCoh != null && outCoh < 0.25} class:ip-caution={outCoh != null && outCoh >= 0.25 && outCoh < 0.5}>{fmt(outCoh)}</span>
      </div>
      <div class="ip-cell" use:tooltip={TAXONOMY_TOOLTIPS.q_system}>
        <span class="ip-cell-label">SCORE</span>
        <span class="ip-cell-value ip-cell-value-green">{avgScore != null ? avgScore.toFixed(1) : '--'}</span>
      </div>
    {:else if mode === 'domain'}
      <div class="ip-cell" use:tooltip={TAXONOMY_TOOLTIPS.coherence}>
        <span class="ip-cell-label">AVG COH</span>
        <span class="ip-cell-value">{fmt(domainAvgCoh)}</span>
      </div>
      <div class="ip-cell" use:tooltip={TAXONOMY_TOOLTIPS.separation}>
        <span class="ip-cell-label">AVG SEP</span>
        <span class="ip-cell-value">{fmt(domainAvgSep)}</span>
      </div>
      <div class="ip-cell" use:tooltip={TOPOLOGY_PANEL_TOOLTIPS.avg_score_domain}>
        <span class="ip-cell-label">AVG SCORE</span>
        <span class="ip-cell-value ip-cell-value-green">{domainAvgScore != null ? domainAvgScore.toFixed(1) : '--'}</span>
      </div>
      <div class="ip-cell" use:tooltip={TOPOLOGY_PANEL_TOOLTIPS.members_domain}>
        <span class="ip-cell-label">MEMBERS</span>
        <span class="ip-cell-value">{domainTotalMembers}</span>
      </div>
    {:else if mode === 'project' && detail}
      <div class="ip-cell" use:tooltip={'Domain nodes under this project'}>
        <span class="ip-cell-label">DOMAINS</span>
        <span class="ip-cell-value">{projectDomainCount}</span>
      </div>
      <div class="ip-cell" use:tooltip={'Active clusters across all domains'}>
        <span class="ip-cell-label">CLUSTERS</span>
        <span class="ip-cell-value">{projectClusterCount}</span>
      </div>
      <div class="ip-cell" use:tooltip={'Recent optimizations in this project'}>
        <span class="ip-cell-label">OPTS</span>
        <span class="ip-cell-value">{projectOptCount}</span>
      </div>
      <div class="ip-cell" use:tooltip={'Average score across project optimizations'}>
        <span class="ip-cell-label">SCORE</span>
        <span class="ip-cell-value ip-cell-value-green">{avgScore != null ? avgScore.toFixed(1) : '--'}</span>
      </div>
    {/if}
  </div>

  <!-- ROW 3: Visual Bar -->
  <div class="ip-row ip-bar">
    {#if mode === 'system'}
      <div class="ip-bar-empty"></div>
    {:else if mode === 'cluster' && blendRaw != null}
      <div class="ip-bar-label">BLEND</div>
      <div class="ip-blend-bar" use:tooltip={`Raw ${pct(blendRaw)} / Optimized ${pct(blendOpt)} / Transform ${pct(blendTrans)}`}>
        <div class="ip-blend-seg ip-blend-raw" style="flex: {(blendRaw ?? 0.65) * 100}">
          {#if (blendRaw ?? 0) > 0.3}<span>RAW {pct(blendRaw)}</span>{/if}
        </div>
        <div class="ip-blend-seg ip-blend-opt" style="flex: {(blendOpt ?? 0.20) * 100}">
          {#if (blendOpt ?? 0) > 0.1}<span>O {pct(blendOpt)}</span>{/if}
        </div>
        <div class="ip-blend-seg ip-blend-trans" style="flex: {(blendTrans ?? 0.15) * 100}">
          {#if (blendTrans ?? 0) > 0.1}<span>T {pct(blendTrans)}</span>{/if}
        </div>
      </div>
    {:else if mode === 'domain' && domainChildren.length > 0}
      <div class="ip-bar-label">TASKS</div>
      {@const taskCounts = (() => {
        const counts: Record<string, number> = {};
        for (const c of domainChildren) {
          const t = c.task_type || 'general';
          counts[t] = (counts[t] || 0) + (c.member_count || 0);
        }
        return Object.entries(counts).sort((a, b) => b[1] - a[1]);
      })()}
      <div class="ip-task-bar">
        {#each taskCounts.slice(0, 4) as [type, count]}
          <div class="ip-task-seg" style="flex: {count}" use:tooltip={`${type}: ${count} members`}>
            {#if count > 2}<span>{type.slice(0, 3)}</span>{/if}
          </div>
        {/each}
      </div>
    {:else if mode === 'project' && projectDomains.length > 0}
      <div class="ip-bar-label">DOMAINS</div>
      <div class="ip-blend-bar">
        {#each projectDomains as domain}
          <div class="ip-blend-seg" style="flex: {Math.max(1, domain.member_count)}; background: {taxonomyColor(domain.label)};">
            {#if domain.member_count > 3}<span>{domain.label}</span>{/if}
          </div>
        {/each}
      </div>
    {:else}
      <div class="ip-bar-empty"></div>
    {/if}
  </div>

  <!-- ROW 3.5: Readiness (domain mode only) -->
  {#if mode === 'domain' && domainReadiness}
    <div class="ip-row ip-readiness-window" role="radiogroup" aria-label="Readiness time window">
      {#each READINESS_WINDOWS as w}
        <button
          type="button"
          class="ip-rw-btn"
          class:ip-rw-active={readinessWindow === w}
          role="radio"
          aria-checked={readinessWindow === w}
          onclick={() => (readinessWindow = w)}
        >{w.toUpperCase()}</button>
      {/each}
    </div>
    <div class="ip-row ip-readiness">
      <DomainStabilityMeter report={domainReadiness.stability} />
      <DomainReadinessSparkline
        domainId={domainReadiness.domain_id}
        domainLabel={domainReadiness.domain_label}
        metric="consistency"
        baseline={domainReadiness.stability.dissolution_floor}
        window={readinessWindow}
      />
      <div class="ip-readiness-sep"></div>
      <SubDomainEmergenceList report={domainReadiness.emergence} />
      <DomainReadinessSparkline
        domainId={domainReadiness.domain_id}
        domainLabel={domainReadiness.domain_label}
        metric="gap"
        baseline={0}
        window={readinessWindow}
      />
    </div>
  {/if}

  <!-- ROW 4: Insight (hidden when parent renders it separately) -->
  {#if !hideInsight}
    <div class="ip-row ip-insight">
      <p class="ip-insight-text">{insight}</p>
    </div>
  {/if}
</div>

<style>
  .ip-panel {
    display: flex;
    flex-direction: column;
  }

  .ip-row {
    padding: 4px 6px;
  }

  .ip-row + .ip-row {
    border-top: 1px solid var(--color-border-subtle);
  }

  /* Last row has no bottom/top border — the parent hud-block separator handles the seam */
  .ip-row:last-child {
    border-top: none;
  }

  /* -- Row 1: Identity -- */

  .ip-identity {
    padding: 6px;
  }

  .ip-identity-row {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    width: 100%;
  }

  .ip-q {
    display: flex;
    align-items: center;
    gap: 3px;
    font-family: var(--font-mono);
    font-size: 11px;
  }

  .ip-q-label {
    color: var(--color-text-dim);
    font-weight: 500;
  }

  .ip-q-value {
    font-weight: 700;
  }

  .ip-sparkline {
    flex: 1;
    min-width: 0;
    display: flex;
    justify-content: flex-end;
  }

  .ip-severity {
    width: 5px;
    height: 5px;
    flex-shrink: 0;
    margin-left: 4px;
  }

  .ip-headline {
    font-family: var(--font-sans);
    font-size: 10px;
    font-weight: 500;
    margin-top: 3px;
    width: 100%;
  }

  .ip-name {
    font-family: var(--font-mono);
    font-size: 11px;
    font-weight: 700;
    color: var(--color-neon-cyan);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    max-width: 130px;
  }

  .ip-member-count {
    font-family: var(--font-mono);
    font-size: 9px;
    color: var(--color-text-dim);
    flex-shrink: 0;
  }

  .ip-badges {
    display: flex;
    gap: 4px;
    align-items: center;
    margin-top: 3px;
    width: 100%;
  }

  .ip-badge {
    font-family: var(--font-mono);
    font-size: 8px;
    padding: 0 4px;
    border: 1px solid;
    letter-spacing: 0.03em;
  }

  .ip-badge-domain {
    color: var(--color-neon-purple);
    border-color: color-mix(in srgb, var(--color-neon-purple) 40%, transparent);
  }

  .ip-badge-state {
    color: var(--color-neon-green);
    border-color: color-mix(in srgb, var(--color-neon-green) 40%, transparent);
  }

  .ip-badge-score {
    font-family: var(--font-mono);
    font-size: 8px;
    color: var(--color-text-dim);
    margin-left: auto;
  }

  .ip-domain-name {
    font-family: var(--font-display);
    font-size: 13px;
    font-weight: 700;
    color: var(--color-neon-purple);
    letter-spacing: 0.08em;
  }

  /* -- Row 2: 2x2 Grid -- */

  .ip-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 0;
    padding: 0;
    background: transparent;
  }

  .ip-cell {
    background: transparent;
    padding: 3px 6px;
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    cursor: default;
    border-bottom: 1px solid color-mix(in srgb, var(--color-border-subtle) 40%, transparent);
  }

  .ip-cell:nth-child(odd) {
    border-right: 1px solid color-mix(in srgb, var(--color-border-subtle) 40%, transparent);
  }

  .ip-cell:nth-child(n+3) {
    border-bottom: none;
  }

  .ip-cell-label {
    font-family: var(--font-mono);
    font-size: 8px;
    color: var(--color-text-dim);
    text-transform: uppercase;
    letter-spacing: 0.05em;
  }

  .ip-cell-label-accent {
    color: var(--color-neon-cyan);
  }

  .ip-cell-value {
    font-family: var(--font-mono);
    font-size: 12px;
    font-weight: 700;
    color: var(--color-text-secondary);
  }

  .ip-cell-value-green {
    color: var(--color-neon-green);
  }

  .ip-warn {
    color: var(--color-neon-orange);
  }

  .ip-caution {
    color: var(--color-neon-yellow);
  }

  /* -- Row 3: Visual bar -- */

  .ip-bar {
    padding: 3px 6px 4px;
  }

  .ip-bar-label {
    font-family: var(--font-mono);
    font-size: 8px;
    color: var(--color-text-dim);
    text-transform: uppercase;
    letter-spacing: 0.05em;
    margin-bottom: 2px;
  }

  .ip-bar-empty {
    height: 0;
  }

  .ip-blend-bar,
  .ip-task-bar {
    display: flex;
    gap: 1px;
    height: 14px;
    width: 100%;
  }

  .ip-blend-seg,
  .ip-task-seg {
    display: flex;
    align-items: center;
    justify-content: center;
    font-family: var(--font-mono);
    font-size: 7px;
    overflow: hidden;
    white-space: nowrap;
  }

  .ip-blend-raw {
    background: color-mix(in srgb, var(--color-neon-cyan) 10%, transparent);
    border: 1px solid color-mix(in srgb, var(--color-neon-cyan) 25%, transparent);
    color: var(--color-neon-cyan);
  }

  .ip-blend-opt {
    background: color-mix(in srgb, var(--color-neon-pink) 10%, transparent);
    border: 1px solid color-mix(in srgb, var(--color-neon-pink) 25%, transparent);
    color: var(--color-neon-pink);
  }

  .ip-blend-trans {
    background: color-mix(in srgb, var(--color-neon-indigo) 10%, transparent);
    border: 1px solid color-mix(in srgb, var(--color-neon-indigo) 25%, transparent);
    color: var(--color-neon-indigo);
  }

  .ip-task-seg {
    background: color-mix(in srgb, var(--color-neon-cyan) 8%, transparent);
    border: 1px solid color-mix(in srgb, var(--color-neon-cyan) 20%, transparent);
    color: var(--color-text-dim);
  }

  .ip-task-seg:nth-child(2) {
    border-color: color-mix(in srgb, var(--color-neon-pink) 20%, transparent);
    background: color-mix(in srgb, var(--color-neon-pink) 8%, transparent);
  }

  .ip-task-seg:nth-child(3) {
    border-color: color-mix(in srgb, var(--color-neon-green) 20%, transparent);
    background: color-mix(in srgb, var(--color-neon-green) 8%, transparent);
  }

  /* -- Row 3.5: Readiness -- */

  .ip-readiness-window {
    padding: 4px 6px 0;
    display: flex;
    gap: 4px;
    justify-content: flex-end;
  }

  .ip-rw-btn {
    appearance: none;
    background: transparent;
    border: 1px solid var(--color-border-subtle);
    color: var(--color-text-dim);
    font-family: var(--font-mono);
    font-size: 9px;
    letter-spacing: 0.06em;
    padding: 2px 6px;
    cursor: pointer;
    border-radius: 0;
    line-height: 1;
  }

  .ip-rw-btn:hover {
    color: var(--color-text);
    border-color: var(--color-border);
  }

  .ip-rw-active {
    color: var(--color-neon-cyan);
    border-color: var(--color-neon-cyan);
  }

  .ip-readiness {
    padding: 6px;
    display: flex;
    flex-direction: column;
    gap: 6px;
  }

  .ip-readiness-sep {
    height: 1px;
    width: 100%;
    background: var(--color-border-subtle);
  }

  /* -- Row 4: Insight -- */

  .ip-insight {
    padding: 4px 6px 5px;
  }

  .ip-insight-text {
    font-family: var(--font-sans);
    font-size: 10px;
    color: var(--color-text-dim);
    line-height: 1.5;
    text-align: justify;
    width: 100%;
    margin: 0;
  }
</style>
