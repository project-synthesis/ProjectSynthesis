<script lang="ts">
  import { clustersStore } from '$lib/stores/clusters.svelte';
  import { editorStore } from '$lib/stores/editor.svelte';
  import TierBadge from '$lib/components/shared/TierBadge.svelte';
  import { forgeStore } from '$lib/stores/forge.svelte';
  import { routing } from '$lib/stores/routing.svelte';
  import { taxonomyColor, qHealthColor } from '$lib/utils/colors';
  import { getPhaseLabel } from '$lib/utils/dimensions';
  import { formatScore } from '$lib/utils/formatting';
  import Logo from '$lib/components/shared/Logo.svelte';

  // Tab-aware result: use per-tab cached data when available, fall back to global forge state
  const activeResult = $derived(editorStore.activeResult ?? forgeStore.result);

  // Provider and version are fed reactively from forgeStore (via health polls + SSE events)
  const provider = $derived(forgeStore.provider);
  const version = $derived(forgeStore.version);

  // Short provider label for secondary display (e.g. "cli", "api")
  const providerShort = $derived.by(() => {
    if (!provider) return null;
    const p = provider.toLowerCase();
    if (p.includes('cli')) return 'cli';
    if (p.includes('api') || p.includes('anthropic')) return 'api';
    return null;
  });

  // Cluster count derived from taxonomy stats (loaded by clustersStore.loadTree)
  const clusterCount = $derived(clustersStore.taxonomyStats?.nodes?.active ?? null);

  const phaseDisplay = $derived(getPhaseLabel(forgeStore.status)?.toLowerCase() ?? null);

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
  <!-- Left side: logo + tier badge + provider + version -->
  <div class="status-left">
    <div style="opacity: 0.8; margin-right: 2px;">
      <Logo size={14} variant="mark" />
    </div>
    <TierBadge tier={routing.tier} degradedFrom={routing.isDegraded ? routing.requestedTier : null} />
    {#if providerShort && routing.isInternal}
      <span class="status-provider">{providerShort}</span>
    {/if}
    {#if forgeStore.mcpDisconnected && !routing.isDegraded && !routing.isAutoFallback}
      <span class="status-disconnected" title="MCP client disconnected">disconnected</span>
    {/if}
    <span class="status-item">{version ? `v${version}` : ''}</span>
    {#if phaseDisplay}
      <span class="status-phase" class:status-phase-passthrough={phaseDisplay === 'passthrough'} class:status-phase-sampling={routing.isSampling}>{phaseDisplay}...</span>
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

  <!-- Right side: pattern count + keyboard shortcut hint -->
  <div class="status-right">
    {#if clusterCount !== null && clusterCount > 0}
      <span class="status-patterns" title="{clusterCount} active clusters">{clusterCount} clusters</span>
    {/if}
    {#if clustersStore.taxonomyStats?.q_system != null}
      <span class="statusbar-item" title="Taxonomy health (Q_system)">
        Q: <span style="color: {qHealthColor(clustersStore.taxonomyStats.q_system)}">{clustersStore.taxonomyStats.q_system.toFixed(2)}</span>
      </span>
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

  .status-item {
    font-size: 10px;
    font-family: var(--font-mono);
    color: var(--color-text-dim);
    white-space: nowrap;
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
    color: var(--color-neon-cyan);
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

  .status-provider {
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
</style>
