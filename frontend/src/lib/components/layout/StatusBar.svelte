<script lang="ts">
  import { getHealth } from '$lib/api/client';
  import { patternsStore } from '$lib/stores/patterns.svelte';
  import ProviderBadge from '$lib/components/shared/ProviderBadge.svelte';
  import { forgeStore } from '$lib/stores/forge.svelte';
  import { taxonomyColor, qHealthColor } from '$lib/utils/colors';
  import { getPhaseLabel } from '$lib/utils/dimensions';
  import { formatScore } from '$lib/utils/formatting';
  import Logo from '$lib/components/shared/Logo.svelte';


  let provider = $state<string | null>(null);
  let version = $state<string | null>(null);

  // Pattern count derived from taxonomy stats (loaded by patternsStore.loadTree)
  const patternCount = $derived(patternsStore.taxonomyStats?.nodes?.confirmed ?? null);

  let loaded = false;
  $effect(() => {
    if (loaded) return;
    loaded = true;
    getHealth()
      .then((h) => { provider = h.provider; version = h.version; })
      .catch(() => {});
  });

  const phaseDisplay = $derived(getPhaseLabel(forgeStore.status)?.toLowerCase() ?? null);

  const lastScore = $derived(
    forgeStore.result?.overall_score
      ? formatScore(forgeStore.result.overall_score)
      : null
  );

  const lastStrategy = $derived(forgeStore.result?.strategy_used ?? null);

  // Breadcrumb: [domain] > intent_label (VS Code file-path pattern)
  const breadcrumbDomain = $derived(forgeStore.result?.domain ?? null);
  const breadcrumbLabel = $derived(forgeStore.result?.intent_label ?? null);
</script>

<div
  class="status-bar"
  role="status"
  aria-label="Status bar"
  style="background: var(--color-bg-secondary); border-top: 1px solid var(--color-border-subtle);"
>
  <!-- Left side: logo + provider badge + version -->
  <div class="status-left">
    <div style="opacity: 0.8; margin-right: 2px;">
      <Logo size={14} variant="mark" />
    </div>
    <ProviderBadge {provider} />
    {#if forgeStore.mcpDisconnected}
      <span class="status-disconnected" title="MCP client disconnected">disconnected</span>
    {/if}
    <span class="status-item">{version ? `v${version}` : ''}</span>
    {#if phaseDisplay}
      <span class="status-phase" class:status-phase-passthrough={phaseDisplay === 'passthrough'}>{phaseDisplay}...</span>
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
    {#if patternCount !== null && patternCount > 0}
      <span class="status-patterns" title="{patternCount} pattern families">{patternCount} patterns</span>
    {/if}
    {#if patternsStore.taxonomyStats?.q_system != null}
      <span class="statusbar-item" title="Taxonomy health (Q_system)">
        Q: <span style="color: {qHealthColor(patternsStore.taxonomyStats.q_system)}">{patternsStore.taxonomyStats.q_system.toFixed(2)}</span>
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
</style>
