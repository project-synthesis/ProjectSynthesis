<script lang="ts">
  import { clustersStore } from '$lib/stores/clusters.svelte';
  import { TAXONOMY_TOOLTIPS } from '$lib/utils/metric-tooltips';
  import { tooltip } from '$lib/actions/tooltip';
  import { TOPOLOGY_TOOLTIPS } from '$lib/utils/ui-tooltips';
  import TopologyInfoPanel from './TopologyInfoPanel.svelte';
  import type { LODTier } from './TopologyRenderer';

  interface Props {
    lodTier: LODTier;
    onSearch: (query: string) => void;
    onRecluster: () => Promise<void>;
  }

  let { lodTier, onSearch, onRecluster }: Props = $props();

  let searchQuery = $state('');
  let searchOpen = $state(false);
  let reclustering = $state(false);

  // Canonical state breakdown from the store (respects orphan filter + state filter)
  const filteredCounts = $derived(clustersStore.clusterCounts);

  function handleSearch(): void {
    if (searchQuery.trim()) {
      onSearch(searchQuery.trim());
    }
  }

  function handleKeyDown(e: KeyboardEvent): void {
    if (e.key === 'Enter') handleSearch();
    if (e.key === 'Escape') {
      searchOpen = false;
      searchQuery = '';
    }
  }

  async function handleRecluster(): Promise<void> {
    reclustering = true;
    try {
      await onRecluster();
    } finally {
      reclustering = false;
    }
  }

  function handleGlobalKey(e: KeyboardEvent): void {
    if ((e.ctrlKey || e.metaKey) && e.key === 'f') {
      e.preventDefault();
      searchOpen = true;
    }
  }
</script>

<svelte:window onkeydown={handleGlobalKey} />

<div class="tc-panel">
  <!-- Adaptive info panel -->
  <div class="tc-section tc-info">
    <TopologyInfoPanel />
  </div>

  <!-- Edge layer toggles -->
  <div class="tc-section tc-layers">
    <span class="tc-section-title">LAYERS</span>
    <div class="tc-layer-row">
      <button
        class="tc-toggle"
        class:tc-toggle-active={clustersStore.showSimilarityEdges}
        style="--toggle-color: var(--color-neon-cyan)"
        onclick={() => { clustersStore.showSimilarityEdges = !clustersStore.showSimilarityEdges; }}
        use:tooltip={TOPOLOGY_TOOLTIPS.toggle_similarity}
      >
        <span class="tc-toggle-dot"></span>
        Similarity
      </button>
      <button
        class="tc-toggle"
        class:tc-toggle-active={clustersStore.showInjectionEdges}
        style="--toggle-color: var(--color-neon-orange)"
        onclick={() => { clustersStore.showInjectionEdges = !clustersStore.showInjectionEdges; }}
        use:tooltip={TOPOLOGY_TOOLTIPS.toggle_injection}
      >
        <span class="tc-toggle-dot"></span>
        Injection
      </button>
    </div>
  </div>

  <!-- Actions -->
  <div class="tc-section tc-actions">
    <div class="tc-action-row">
      <button
        class="tc-recluster"
        onclick={handleRecluster}
        disabled={reclustering}
        use:tooltip={TOPOLOGY_TOOLTIPS.recluster}
      >
        {reclustering ? 'Reclustering...' : 'Recluster'}
      </button>
      <span class="tc-lod">{lodTier.toUpperCase()}</span>
    </div>
  </div>

  <!-- Search (conditional) -->
  {#if searchOpen}
    <div class="tc-section tc-search">
      <input
        type="text"
        bind:value={searchQuery}
        onkeydown={handleKeyDown}
        placeholder="Search nodes..."
        class="tc-search-input"
      />
    </div>
  {/if}

  <!-- Footer: counts + legend -->
  <div class="tc-section tc-footer">
    <div class="tc-counts">
      <span use:tooltip={TAXONOMY_TOOLTIPS.active}>{filteredCounts.active} active</span>
      {#if filteredCounts.candidate > 0}
        <span class="tc-dot-sep"></span>
        <span use:tooltip={TAXONOMY_TOOLTIPS.candidate}>{filteredCounts.candidate} forming</span>
      {/if}
      {#if filteredCounts.template > 0}
        <span class="tc-dot-sep"></span>
        <span use:tooltip={TAXONOMY_TOOLTIPS.template}>{filteredCounts.template} templates</span>
      {/if}
    </div>
    <div class="tc-legend">
      <span>wireframe <span class="tc-legend-arrow">&rarr;</span> coherence</span>
      <span>saturation <span class="tc-legend-arrow">&rarr;</span> score</span>
    </div>
  </div>
</div>

<style>
  /* ── Panel container ── */

  .tc-panel {
    position: absolute;
    top: 6px;
    right: 6px;
    width: 184px;
    display: flex;
    flex-direction: column;
    background: color-mix(in srgb, var(--color-bg-secondary) 88%, transparent);
    border: 1px solid var(--color-border-subtle);
    pointer-events: auto;
    z-index: 10;
  }

  /* ── Sections ── */

  .tc-section {
    padding: 5px 6px;
  }

  .tc-section + .tc-section {
    border-top: 1px solid var(--color-border-subtle);
  }

  .tc-section-title {
    display: block;
    font-family: var(--font-display);
    font-size: 9px;
    font-weight: 700;
    letter-spacing: 0.1em;
    color: var(--color-text-dim);
    text-transform: uppercase;
    margin-bottom: 4px;
  }

  /* ── Info panel section ── */

  .tc-info {
    padding: 0;
  }

  /* ── Layer toggles ── */

  .tc-layers {
    padding: 4px 6px 5px;
  }

  .tc-layer-row {
    display: flex;
    gap: 4px;
  }

  .tc-toggle {
    display: flex;
    align-items: center;
    gap: 3px;
    padding: 1px 5px;
    background: transparent;
    border: 1px solid var(--color-border-subtle);
    color: var(--color-text-dim);
    font-family: var(--font-mono);
    font-size: 9px;
    cursor: pointer;
    transition: border-color 0.15s, color 0.15s, background-color 0.15s;
  }

  .tc-toggle:hover {
    border-color: color-mix(in srgb, var(--toggle-color, var(--color-neon-cyan)) 40%, transparent);
    color: var(--color-text-secondary);
  }

  .tc-toggle.tc-toggle-active {
    border-color: color-mix(in srgb, var(--toggle-color, var(--color-neon-cyan)) 50%, transparent);
    color: var(--color-text-primary);
    background: color-mix(in srgb, var(--toggle-color, var(--color-neon-cyan)) 6%, transparent);
  }

  .tc-toggle-dot {
    width: 4px;
    height: 4px;
    background: var(--toggle-color, var(--color-neon-cyan));
    opacity: 0.3;
    transition: opacity 0.15s;
  }

  .tc-toggle.tc-toggle-active .tc-toggle-dot {
    opacity: 1;
  }

  /* ── Actions ── */

  .tc-actions {
    padding: 4px 6px;
  }

  .tc-action-row {
    display: flex;
    align-items: center;
    gap: 4px;
  }

  .tc-recluster {
    flex: 1;
    padding: 2px 6px;
    background: transparent;
    border: 1px solid var(--color-border-subtle);
    color: var(--color-text-dim);
    font-family: var(--font-mono);
    font-size: 9px;
    cursor: pointer;
    transition: border-color 0.15s, color 0.15s, background-color 0.15s;
    text-align: center;
  }

  .tc-recluster:hover:not(:disabled) {
    border-color: color-mix(in srgb, var(--color-neon-purple) 50%, transparent);
    color: var(--color-neon-purple);
    background: color-mix(in srgb, var(--color-neon-purple) 6%, transparent);
  }

  .tc-recluster:disabled {
    opacity: 0.4;
    cursor: not-allowed;
  }

  .tc-lod {
    font-family: var(--font-mono);
    font-size: 9px;
    font-weight: 600;
    color: var(--color-text-dim);
    padding: 2px 4px;
    background: color-mix(in srgb, var(--color-bg-hover) 50%, transparent);
    border: 1px solid color-mix(in srgb, var(--color-border-subtle) 60%, transparent);
    letter-spacing: 0.06em;
  }

  /* ── Search ── */

  .tc-search {
    padding: 4px 6px;
  }

  .tc-search-input {
    width: 100%;
    padding: 2px 5px;
    background: var(--color-bg-input);
    border: 1px solid var(--color-border-subtle);
    color: var(--color-text-primary);
    font-family: var(--font-mono);
    font-size: 10px;
    outline: none;
  }

  .tc-search-input:focus {
    border-color: color-mix(in srgb, var(--color-neon-cyan) 40%, transparent);
  }

  .tc-search-input::placeholder {
    color: var(--color-text-dim);
    opacity: 0.5;
  }

  /* ── Footer ── */

  .tc-footer {
    padding: 4px 6px 5px;
  }

  .tc-counts {
    display: flex;
    align-items: center;
    gap: 4px;
    font-family: var(--font-mono);
    font-size: 9px;
    color: var(--color-text-dim);
  }

  .tc-dot-sep {
    width: 2px;
    height: 2px;
    background: var(--color-text-dim);
    opacity: 0.4;
  }

  .tc-legend {
    display: flex;
    flex-direction: column;
    gap: 1px;
    margin-top: 3px;
    font-family: var(--font-mono);
    font-size: 8px;
    color: color-mix(in srgb, var(--color-text-dim) 60%, transparent);
  }

  .tc-legend-arrow {
    opacity: 0.4;
    margin: 0 1px;
  }
</style>
