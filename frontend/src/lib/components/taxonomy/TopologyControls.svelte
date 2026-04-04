<script lang="ts">
  import { clustersStore } from '$lib/stores/clusters.svelte';
  import { TAXONOMY_TOOLTIPS } from '$lib/utils/metric-tooltips';
  import { tooltip } from '$lib/actions/tooltip';
  import { TOPOLOGY_TOOLTIPS } from '$lib/utils/ui-tooltips';
  import TopologyInfoPanel from './TopologyInfoPanel.svelte';
  import type { LODTier } from './TopologyRenderer';

  interface Props {
    lodTier: LODTier;
    showActivity: boolean;
    onSearch: (query: string) => void;
    onRecluster: () => Promise<void>;
    onToggleActivity: () => void;
    onSeed: () => void;
  }

  let { lodTier, showActivity, onSearch, onRecluster, onToggleActivity, onSeed }: Props = $props();

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

  <!-- Edge layers — inline toggles, no section title (self-explanatory) -->
  <div class="tc-section tc-layers">
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

  <!-- Command strip — unified action bar -->
  <div class="tc-section tc-commands">
    <div class="tc-cmd-row">
      <button
        class="tc-cmd"
        onclick={onSeed}
        use:tooltip={'Seed taxonomy with generated prompts'}
      >
        Seed
      </button>
      <button
        class="tc-cmd"
        onclick={handleRecluster}
        disabled={reclustering}
        use:tooltip={TOPOLOGY_TOOLTIPS.recluster}
      >
        {reclustering ? 'Running...' : 'Recluster'}
      </button>
      <span class="tc-lod" use:tooltip={'Level of detail'}>{lodTier.toUpperCase()}</span>
    </div>
    <button
      class="tc-toggle tc-activity-toggle"
      class:tc-toggle-active={showActivity}
      style="--toggle-color: var(--color-neon-purple)"
      onclick={onToggleActivity}
      use:tooltip={'Toggle taxonomy decision feed'}
    >
      <span class="tc-toggle-dot"></span>
      Activity
    </button>
  </div>

  <!-- Search (Ctrl+F) -->
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

  <!-- Status strip — counts + visual encoding legend -->
  <div class="tc-section tc-status">
    <div class="tc-counts">
      <span use:tooltip={TAXONOMY_TOOLTIPS.active}>{filteredCounts.active} active</span>
      {#if filteredCounts.candidate > 0}
        <span class="tc-dot-sep"></span>
        <span class="tc-count-candidate" use:tooltip={TAXONOMY_TOOLTIPS.candidate}>{filteredCounts.candidate} forming</span>
      {/if}
      {#if filteredCounts.template > 0}
        <span class="tc-dot-sep"></span>
        <span use:tooltip={TAXONOMY_TOOLTIPS.template}>{filteredCounts.template} tmpl</span>
      {/if}
    </div>
    <div class="tc-legend">
      <span>wireframe <span class="tc-legend-sep">=</span> coherence</span>
      <span>saturation <span class="tc-legend-sep">=</span> score</span>
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
    padding: 3px 6px 4px;
  }

  .tc-layer-row {
    display: flex;
    gap: 3px;
  }

  .tc-toggle {
    display: flex;
    align-items: center;
    gap: 3px;
    flex: 1;
    padding: 2px 5px;
    background: transparent;
    border: 1px solid var(--color-border-subtle);
    color: var(--color-text-dim);
    font-family: var(--font-mono);
    font-size: 9px;
    cursor: pointer;
    transition: border-color 150ms cubic-bezier(0.16, 1, 0.3, 1),
                color 150ms cubic-bezier(0.16, 1, 0.3, 1),
                background 150ms cubic-bezier(0.16, 1, 0.3, 1);
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
    flex-shrink: 0;
    background: var(--toggle-color, var(--color-neon-cyan));
    opacity: 0.3;
    transition: opacity 150ms cubic-bezier(0.16, 1, 0.3, 1);
  }

  .tc-toggle.tc-toggle-active .tc-toggle-dot {
    opacity: 1;
  }

  /* ── Command strip ── */

  .tc-commands {
    padding: 3px 6px 4px;
    display: flex;
    flex-direction: column;
    gap: 3px;
  }

  .tc-cmd-row {
    display: flex;
    align-items: center;
    gap: 3px;
  }

  .tc-cmd {
    flex: 1;
    padding: 2px 4px;
    background: transparent;
    border: 1px solid var(--color-border-subtle);
    color: var(--color-text-dim);
    font-family: var(--font-mono);
    font-size: 9px;
    cursor: pointer;
    text-align: center;
    transition: border-color 150ms cubic-bezier(0.16, 1, 0.3, 1),
                color 150ms cubic-bezier(0.16, 1, 0.3, 1),
                background 150ms cubic-bezier(0.16, 1, 0.3, 1);
  }

  .tc-cmd:hover:not(:disabled) {
    border-color: color-mix(in srgb, var(--color-neon-cyan) 40%, transparent);
    color: var(--color-neon-cyan);
    background: color-mix(in srgb, var(--color-neon-cyan) 5%, transparent);
  }

  .tc-cmd:disabled {
    opacity: 0.4;
    cursor: not-allowed;
  }

  .tc-lod {
    font-family: var(--font-mono);
    font-size: 8px;
    font-weight: 700;
    color: var(--color-text-dim);
    padding: 2px 4px;
    letter-spacing: 0.08em;
    border: 1px solid color-mix(in srgb, var(--color-border-subtle) 50%, transparent);
    background: color-mix(in srgb, var(--color-bg-hover) 40%, transparent);
    flex-shrink: 0;
  }

  .tc-activity-toggle {
    width: 100%;
  }

  /* ── Search ── */

  .tc-search {
    padding: 3px 6px;
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

  /* ── Status strip ── */

  .tc-status {
    padding: 3px 6px 4px;
  }

  .tc-counts {
    display: flex;
    align-items: center;
    gap: 4px;
    font-family: var(--font-mono);
    font-size: 9px;
    color: var(--color-text-dim);
  }

  .tc-count-candidate {
    color: #7a7a9e;
  }

  .tc-dot-sep {
    width: 2px;
    height: 2px;
    flex-shrink: 0;
    background: var(--color-text-dim);
    opacity: 0.3;
  }

  .tc-legend {
    display: flex;
    gap: 8px;
    margin-top: 2px;
    font-family: var(--font-mono);
    font-size: 7px;
    color: color-mix(in srgb, var(--color-text-dim) 50%, transparent);
    letter-spacing: 0.02em;
  }

  .tc-legend-sep {
    opacity: 0.3;
    margin: 0 1px;
  }
</style>
