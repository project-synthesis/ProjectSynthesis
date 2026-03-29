<script lang="ts">
  import { clustersStore } from '$lib/stores/clusters.svelte';
  import { editorStore } from '$lib/stores/editor.svelte';
  import TierBadge from '$lib/components/shared/TierBadge.svelte';
  import { forgeStore } from '$lib/stores/forge.svelte';
  import { routing } from '$lib/stores/routing.svelte';
  import { taxonomyColor, qHealthColor } from '$lib/utils/colors';
  import { getPhaseLabel } from '$lib/utils/dimensions';
  import { formatScore, trendInfo } from '$lib/utils/formatting';
  import Logo from '$lib/components/shared/Logo.svelte';
  import ScoreSparkline from '$lib/components/refinement/ScoreSparkline.svelte';

  // Tab-aware result: use per-tab cached data when available, fall back to global forge state
  const activeResult = $derived(editorStore.activeResult ?? forgeStore.result);


  // Cluster count derived from taxonomy stats (loaded by clustersStore.loadTree)
  const clusterCount = $derived(clustersStore.taxonomyStats?.nodes?.active ?? null);

  const PIPELINE_PHASES = ['analyzing', 'optimizing', 'scoring'];
  const phaseDisplay = $derived(getPhaseLabel(forgeStore.status)?.toLowerCase() ?? null);
  const phaseStep = $derived(PIPELINE_PHASES.indexOf(forgeStore.status) + 1);
  const phaseProgress = $derived.by(() => {
    if (!phaseDisplay) return null;
    if (phaseStep > 0) return `${phaseDisplay} [${phaseStep}/3]`;
    return `${phaseDisplay}...`;  // passthrough or other non-pipeline status
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
    <TierBadge tier={routing.tier} provider={forgeStore.provider} degradedFrom={routing.isDegraded ? routing.requestedTier : null} />
    {#if forgeStore.mcpDisconnected && !routing.isDegraded && !routing.isAutoFallback}
      <span class="status-disconnected" title="MCP client disconnected">disconnected</span>
    {/if}
    {#if phaseProgress}
      <span class="status-phase" class:status-phase-passthrough={phaseDisplay === 'passthrough'} class:status-phase-sampling={routing.isSampling}>{phaseProgress}</span>
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
        title="{forgeStore.domainCount} active domain nodes (ceiling: {forgeStore.domainCeiling ?? 30})"
        style="color: {forgeStore.domainCount >= (forgeStore.domainCeiling ?? 30) * 0.8 ? 'var(--color-neon-yellow)' : 'var(--color-text-dim)'};"
      >{forgeStore.domainCount} domains</span>
    {/if}
    {#if clusterCount !== null && clusterCount > 0}
      <span class="status-patterns" title="{clusterCount} active clusters">{clusterCount} clusters</span>
    {/if}
    {#if clustersStore.taxonomyStats?.q_system != null}
      {@const stats = clustersStore.taxonomyStats}
      {@const qSystem = stats.q_system!}
      {#if stats.q_sparkline && stats.q_sparkline.length >= 2}
        <ScoreSparkline scores={stats.q_sparkline} width={60} height={14} />
      {/if}
      <span class="statusbar-item" title="Taxonomy health (Q_system)">
        Q: <span style="color: {qHealthColor(qSystem)}">{qSystem.toFixed(2)}</span>
      </span>
      {#if stats.q_point_count >= 3}
        {@const ti = trendInfo(stats.q_trend)}
        <span
          class="statusbar-trend"
          title="Q trend: {ti.label}"
          style="color: {ti.color}"
        >{ti.char}</span>
      {/if}
    {/if}
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

  .status-phase-sampling {
    color: var(--color-neon-green);
  }

  .status-phase-passthrough {
    color: var(--color-neon-yellow);
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
    max-width: 300px;
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
</style>
