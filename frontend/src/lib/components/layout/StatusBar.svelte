<script lang="ts">
  import UpdateBadge from '$lib/components/shared/UpdateBadge.svelte';
  import { updateStore } from '$lib/stores/update.svelte';
  import { clustersStore } from '$lib/stores/clusters.svelte';
  import { editorStore } from '$lib/stores/editor.svelte';
  import TierBadge from '$lib/components/shared/TierBadge.svelte';
  import { forgeStore } from '$lib/stores/forge.svelte';
  import { routing } from '$lib/stores/routing.svelte';
  import { githubStore } from '$lib/stores/github.svelte';
  import { taxonomyColor, qHealthColor } from '$lib/utils/colors';
  import { getPhaseLabel } from '$lib/utils/dimensions';
  import { formatScore, trendInfo } from '$lib/utils/formatting';
  import { assessTaxonomyHealth } from '$lib/utils/taxonomy-health';
  import { tooltip } from '$lib/actions/tooltip';
  import { STATUS_TOOLTIPS } from '$lib/utils/ui-tooltips';
  import Logo from '$lib/components/shared/Logo.svelte';
  import ScoreSparkline from '$lib/components/shared/ScoreSparkline.svelte';
  import { sseHealthStore } from '$lib/stores/sse-health.svelte';
  import type { ClusterStats } from '$lib/api/clusters';

  function qHealthTooltip(stats: ClusterStats | null): string {
    if (!stats) return 'No data';
    const q = stats.q_health ?? stats.q_system;
    if (q == null) return 'No data';

    const coh = stats.q_health_coherence_w ?? stats.q_coherence ?? 0;
    const sep = stats.q_health_separation_w ?? stats.q_separation ?? 0;
    const cov = stats.q_coverage ?? 1;
    const dbcv = stats.q_dbcv ?? 0;
    const w = stats.q_health_weights ?? { w_c: 0.40, w_s: 0.35, w_v: 0.25, w_d: 0.00 };
    const members = stats.q_health_total_members ?? '?';
    const clusters = stats.q_health_cluster_count ?? stats.nodes?.active ?? '?';

    return [
      'Q = Coh\u00D7w_c + Sep\u00D7w_s + Cov\u00D7w_v + DBCV\u00D7w_d',
      '',
      `Coherence (weighted):  ${coh.toFixed(3)}  \u00D7${w.w_c.toFixed(2)}`,
      `Separation (weighted): ${sep.toFixed(3)}  \u00D7${w.w_s.toFixed(2)}`,
      `Coverage:              ${cov.toFixed(3)}  \u00D7${w.w_v.toFixed(2)}`,
      `DBCV:                  ${dbcv.toFixed(3)}  \u00D7${w.w_d.toFixed(2)}`,
      `${'─'.repeat(35)}`,
      `Q_health:              ${q.toFixed(3)}`,
      '',
      `${clusters} clusters \u00B7 ${members} members`,
      'Member-weighted: larger clusters count more',
    ].join('\n');
  }

  // Tab-aware result: use per-tab cached data when available, fall back to global forge state
  const activeResult = $derived(editorStore.activeResult ?? forgeStore.result);

  // When viewing a completed optimization, show its persisted tier.
  // When idle or running, show the live routing tier.
  const displayTier = $derived(
    (activeResult?.routing_tier as typeof routing.tier) ?? routing.tier,
  );


  // Cluster count derived from taxonomy tree — excludes orphaned clusters (0 members + 0 usage)
  const clusterCount = $derived(clustersStore.liveClusterCount > 0 ? clustersStore.liveClusterCount : null);

  const PIPELINE_PHASES = ['analyzing', 'optimizing', 'scoring'];
  const phaseDisplay = $derived(getPhaseLabel(forgeStore.status)?.toLowerCase() ?? null);
  const phaseStep = $derived(PIPELINE_PHASES.indexOf(forgeStore.status) + 1);
  const phaseProgress = $derived.by(() => {
    if (!phaseDisplay) return null;
    if (phaseStep > 0) return `${phaseDisplay} [${phaseStep}/3]`;
    return `${phaseDisplay}...`;  // passthrough or other non-pipeline status
  });

  // Elapsed timer for active pipeline phases
  let elapsed = $state<number | null>(null);
  $effect(() => {
    if (forgeStore.synthesisStartedAt && phaseProgress) {
      elapsed = Math.floor((Date.now() - forgeStore.synthesisStartedAt) / 1000);
      const interval = setInterval(() => {
        elapsed = Math.floor((Date.now() - forgeStore.synthesisStartedAt!) / 1000);
      }, 1000);
      return () => { clearInterval(interval); elapsed = null; };
    } else {
      elapsed = null;
    }
  });

  const lastScore = $derived(
    activeResult?.overall_score
      ? formatScore(activeResult.overall_score)
      : null
  );

  const lastStrategy = $derived(activeResult?.strategy_used ?? null);

  // Breadcrumb: [domain] > intent_label (VS Code file-path pattern)
  const breadcrumbDomain = $derived(activeResult?.domain ?? null);
  const breadcrumbLabel = $derived(activeResult?.intent_label ?? null);

  // SSE connection health indicator
  const sseColor = $derived(
    sseHealthStore.connectionState === 'healthy'
      ? 'var(--color-neon-cyan)'
      : sseHealthStore.connectionState === 'degraded'
        ? 'var(--color-neon-yellow)'
        : 'var(--color-neon-red)'
  );
  const sseLabel = $derived(
    sseHealthStore.connectionState === 'disconnected' ? 'SSE \u00D7' : 'SSE'
  );
</script>

<div
  class="status-bar"
  role="status"
  aria-label="Status bar"
  style="background: var(--color-bg-secondary); border-top: 1px solid var(--color-border-subtle);"
>
  <!-- Left side: logo + tier badge + provider -->
  <div class="status-left">
    <div style="opacity: 0.8; margin-right: 2px;">
      <Logo size={14} variant="mark" />
    </div>
    <TierBadge tier={displayTier} provider={forgeStore.provider} degradedFrom={routing.isDegraded ? routing.requestedTier : null} />
    {#if githubStore.user}
      <img
        class="status-avatar"
        src={githubStore.user.avatar_url}
        alt=""
        width="16"
        height="16"
        use:tooltip={githubStore.user.login}
      />
    {/if}
    {#if githubStore.connectionState === 'ready'}
      <span class="status-github" style="color: var(--color-text-dim)">{githubStore.linkedRepo?.full_name.split('/')[1]}</span>
    {:else if githubStore.connectionState === 'linked'}
      <span class="status-github" style="color: var(--color-neon-cyan)">indexing...</span>
    {:else if githubStore.connectionState === 'expired'}
      <span class="status-github" style="color: var(--color-neon-red)">expired</span>
    {:else if githubStore.connectionState === 'authenticated'}
      <span class="status-github" style="color: var(--color-neon-yellow)">no repo</span>
    {/if}
    {#if forgeStore.mcpDisconnected && !routing.isDegraded && !routing.isAutoFallback}
      <span class="status-disconnected" use:tooltip={STATUS_TOOLTIPS.mcp_disconnected}>disconnected</span>
    {/if}
    {#if clustersStore.seedBatchActive}
      <span class="status-seed" use:tooltip={'Seed batch in progress'}>SEED {clustersStore.seedBatchProgress.completed}/{clustersStore.seedBatchProgress.total}</span>
    {/if}
    {#if phaseProgress}
      <span class="status-phase">{phaseProgress}{#if elapsed != null} {elapsed}s{/if}</span>
    {:else if lastScore}
      {#if breadcrumbLabel}
        <span class="status-breadcrumb">
          {#if breadcrumbDomain}
            <span class="status-breadcrumb-domain" style="color: {taxonomyColor(breadcrumbDomain)};">{breadcrumbDomain}</span>
            <span class="status-breadcrumb-sep">&rsaquo;</span>
          {/if}
          <span class="status-breadcrumb-label">{breadcrumbLabel}</span>
        </span>
      {/if}
      <span class="status-metric">{lastScore}</span>
      {#if lastStrategy}
        <span class="status-strategy">{lastStrategy}</span>
      {/if}
    {/if}
  </div>

  <!-- Right side: domain count + cluster count + keyboard shortcut hint -->
  <div class="status-right">
    {#if forgeStore.domainCount != null}
      <span
        class="statusbar-item"
        use:tooltip={`${forgeStore.domainCount} active domain nodes (ceiling: ${forgeStore.domainCeiling ?? 30})`}
        style="color: {forgeStore.domainCount >= (forgeStore.domainCeiling ?? 30) * 0.8 ? 'var(--color-neon-yellow)' : 'var(--color-text-dim)'};"
      >{forgeStore.domainCount} domains</span>
    {/if}
    {#if clusterCount !== null && clusterCount > 0}
      <span class="status-patterns" use:tooltip={`${clusterCount} active clusters`}>{clusterCount} clusters</span>
    {/if}
    {#if clustersStore.taxonomyStats?.q_health != null || clustersStore.taxonomyStats?.q_system != null}
      {@const stats = clustersStore.taxonomyStats}
      {@const qVal = (stats.q_health ?? stats.q_system)!}
      {#if stats.q_sparkline && stats.q_sparkline.length >= 2}
        <ScoreSparkline scores={stats.q_sparkline} width={60} height={14} minRange={0.2} />
      {/if}
      {@const health = assessTaxonomyHealth(stats)}
      <span class="statusbar-item" use:tooltip={qHealthTooltip(stats)}>
        Q: <span style="color: {qHealthColor(qVal)}">{qVal.toFixed(2)}</span>
      </span>
      {#if health}
        <span
          class="statusbar-trend"
          use:tooltip={health.detail}
          style="color: {health.color}"
        >{health.headline}</span>
      {:else if stats.q_point_count >= 3}
        {@const ti = trendInfo(stats.q_trend)}
        <span class="statusbar-trend" style="color: {ti.color}">{ti.char}</span>
      {/if}
    {/if}
    {#if updateStore.updateAvailable || updateStore.updating}
      <UpdateBadge />
    {/if}
    <span
      class="status-sse"
      use:tooltip={sseHealthStore.tooltipText}
      style="color: {sseColor}"
    >
      <span class="status-sse-dot" style="background: {sseColor}"></span>
      {sseLabel}
    </span>
    <span class="status-kbd" aria-label="Open command palette with Ctrl+K">Ctrl+K</span>
  </div>
</div>

<style>
  .status-bar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    height: 20px;
    padding: 0 4px;
    overflow: hidden;
  }

  .status-left,
  .status-right {
    display: flex;
    align-items: center;
    gap: 4px;
  }



  .status-kbd {
    font-size: 10px;
    font-family: var(--font-mono);
    color: var(--color-text-dim);
    border: 1px solid var(--color-border-subtle);
    padding: 1px 6px;
    white-space: nowrap;
  }

  .status-phase {
    font-family: var(--font-mono);
    font-size: 10px;
    color: var(--tier-accent, var(--color-neon-cyan));
    white-space: nowrap;
  }

  .status-metric {
    font-family: var(--font-mono);
    font-size: 10px;
    color: var(--color-text-primary);
    white-space: nowrap;
  }

  .status-strategy {
    font-family: var(--font-mono);
    font-size: 10px;
    color: var(--color-text-dim);
    white-space: nowrap;
  }

  .status-breadcrumb {
    display: inline-flex;
    align-items: center;
    gap: 3px;
    overflow: hidden;
    white-space: nowrap;
  }

  .status-breadcrumb-domain {
    font-family: var(--font-mono);
    font-size: 10px;
    font-weight: 400;
    flex-shrink: 0;
  }

  .status-breadcrumb-sep {
    font-size: 10px;
    color: var(--color-text-dim);
    flex-shrink: 0;
  }

  .status-breadcrumb-label {
    font-family: var(--font-mono);
    font-size: 10px;
    color: var(--color-text-primary);
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .status-patterns {
    font-family: var(--font-mono);
    font-size: 10px;
    color: var(--color-text-dim);
    white-space: nowrap;
  }


  .status-disconnected {
    font-family: var(--font-mono);
    font-size: 10px;
    color: var(--color-neon-yellow);
    white-space: nowrap;
  }

  .status-avatar {
    border: 1px solid var(--color-border-accent);
    flex-shrink: 0;
  }
  .status-github {
    font-family: var(--font-mono);
    font-size: 10px;
    color: var(--color-text-dim);
    border: 1px solid var(--color-border-subtle);
    padding: 0 4px;
    white-space: nowrap;
  }

  .status-seed {
    font-family: var(--font-mono);
    font-size: 10px;
    color: var(--color-neon-cyan);
    white-space: nowrap;
  }

  .statusbar-item {
    font-family: var(--font-mono);
    font-size: 10px;
    color: var(--color-text-dim);
    white-space: nowrap;
  }

  .statusbar-trend {
    font-family: var(--font-mono);
    font-size: 10px;
    font-weight: bold;
    margin-left: 1px;
    white-space: nowrap;
  }

  .status-sse {
    display: inline-flex;
    align-items: center;
    gap: 3px;
    font-family: var(--font-mono);
    font-size: 10px;
    white-space: nowrap;
    cursor: default;
  }

  .status-sse-dot {
    width: 5px;
    height: 5px;
    flex-shrink: 0;
  }
</style>
