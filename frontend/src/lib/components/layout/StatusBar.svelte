<script lang="ts">
  import { getHealth } from '$lib/api/client';
  import { getPatternStats } from '$lib/api/patterns';
  import { patternsStore } from '$lib/stores/patterns.svelte';
  import ProviderBadge from '$lib/components/shared/ProviderBadge.svelte';
  import { forgeStore } from '$lib/stores/forge.svelte';

  let provider = $state<string | null>(null);
  let version = $state<string | null>(null);
  let patternCount = $state<number | null>(null);

  let loaded = false;
  $effect(() => {
    if (loaded) return;
    loaded = true;
    getHealth()
      .then((h) => { provider = h.provider; version = h.version; })
      .catch(() => {});
    getPatternStats()
      .then((s) => { patternCount = s.total_families; })
      .catch(() => {});
  });

  // Refresh pattern count when graph is invalidated (new patterns extracted)
  let _lastGraphLoaded = $state(true);
  $effect(() => {
    const gl = patternsStore.graphLoaded;
    if (_lastGraphLoaded && !gl) {
      getPatternStats()
        .then((s) => { patternCount = s.total_families; })
        .catch(() => {});
    }
    _lastGraphLoaded = gl;
  });

  const phaseDisplay = $derived.by(() => {
    switch (forgeStore.status) {
      case 'analyzing': return 'analyzing';
      case 'optimizing': return 'optimizing';
      case 'scoring': return 'scoring';
      case 'passthrough': return 'passthrough';
      default: return null;
    }
  });

  const lastScore = $derived(
    forgeStore.result?.overall_score
      ? forgeStore.result.overall_score.toFixed(1)
      : null
  );

  const lastStrategy = $derived(forgeStore.result?.strategy_used ?? null);
</script>

<div
  class="status-bar"
  role="status"
  aria-label="Status bar"
  style="background: var(--color-bg-secondary); border-top: 1px solid var(--color-border-subtle);"
>
  <!-- Left side: logo + provider badge + version -->
  <div class="status-left">
    <svg class="status-logo" width="12" height="12" viewBox="0 0 32 32" aria-hidden="true">
      <defs>
        <linearGradient id="sbl" x1="0" y1="0" x2="1" y2="1"><stop offset="0%" stop-color="#00e5ff"/><stop offset="100%" stop-color="#a855f7"/></linearGradient>
        <clipPath id="sblt"><rect x="0" y="0" width="32" height="15"/></clipPath>
        <clipPath id="sblb"><rect x="0" y="17" width="32" height="15"/></clipPath>
      </defs>
      <g clip-path="url(#sblt)" transform="translate(-1.5,0)"><g transform="translate(16,16) skewX(-10) translate(-16,-16)"><polyline fill="none" stroke="url(#sbl)" stroke-width="4" stroke-linecap="square" stroke-linejoin="bevel" points="23,6 9,6 9,10 12,14 20,18 23,22 23,26 9,26"/></g></g>
      <g clip-path="url(#sblb)" transform="translate(1.5,0)"><g transform="translate(16,16) skewX(-10) translate(-16,-16)"><polyline fill="none" stroke="url(#sbl)" stroke-width="4" stroke-linecap="square" stroke-linejoin="bevel" points="23,6 9,6 9,10 12,14 20,18 23,22 23,26 9,26"/></g></g>
      <rect x="0" y="15" width="32" height="2" fill="var(--color-bg-secondary)"/>
    </svg>
    <ProviderBadge {provider} />
    <span class="status-item">{version ? `v${version}` : ''}</span>
    {#if phaseDisplay}
      <span class="status-phase" class:status-phase-passthrough={phaseDisplay === 'passthrough'}>{phaseDisplay}...</span>
    {:else if lastScore}
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

  .status-patterns {
    font-family: var(--font-mono);
    font-size: 10px;
    color: var(--color-text-dim);
    white-space: nowrap;
  }
</style>
