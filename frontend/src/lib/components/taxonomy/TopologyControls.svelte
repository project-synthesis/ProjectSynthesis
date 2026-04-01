<script lang="ts">
  import { clustersStore } from '$lib/stores/clusters.svelte';
  import { qHealthColor } from '$lib/utils/colors';
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

  const stats = $derived(clustersStore.taxonomyStats);
  const qSystem = $derived(stats?.q_system ?? null);
  const qColor = $derived(qHealthColor(qSystem));

  // Compute filtered counts from the visible tree (respects state filter)
  const filteredCounts = $derived.by(() => {
    const tree = clustersStore.filteredTaxonomyTree;
    let active = 0, candidate = 0, template = 0, archived = 0;
    for (const n of tree) {
      if (n.state === 'active') active++;
      else if (n.state === 'candidate') candidate++;
      else if (n.state === 'template') template++;
      else if (n.state === 'archived') archived++;
    }
    return { active, candidate, template, archived };
  });

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

  // Ctrl+F opens search
  function handleGlobalKey(e: KeyboardEvent): void {
    if ((e.ctrlKey || e.metaKey) && e.key === 'f') {
      e.preventDefault();
      searchOpen = true;
    }
  }
</script>

<svelte:window onkeydown={handleGlobalKey} />

<div class="topology-controls">
  <!-- Q_system badge -->
  {#if qSystem != null}
    <div class="q-badge" style="border-color: {qColor}">
      <span class="q-label">Q</span>
      <span class="q-value" style="color: {qColor}">{qSystem.toFixed(3)}</span>
    </div>
  {/if}

  <!-- LOD indicator -->
  <div class="lod-indicator">
    <span class="lod-label">{lodTier.toUpperCase()}</span>
  </div>

  <!-- Search -->
  {#if searchOpen}
    <div class="search-bar">
      <input
        type="text"
        bind:value={searchQuery}
        onkeydown={handleKeyDown}
        placeholder="Search taxonomy..."
        class="search-input"
      />
    </div>
  {/if}

  <!-- Similarity toggle -->
  <button
    class="similarity-btn"
    class:similarity-active={clustersStore.showSimilarityEdges}
    onclick={() => { clustersStore.showSimilarityEdges = !clustersStore.showSimilarityEdges; }}
    title="Toggle similarity edges"
  >
    Similarity
  </button>

  <!-- Injection toggle -->
  <button
    class="injection-btn"
    class:injection-active={clustersStore.showInjectionEdges}
    onclick={() => { clustersStore.showInjectionEdges = !clustersStore.showInjectionEdges; }}
    title="Toggle injection provenance edges"
  >
    Injection
  </button>

  <!-- Recluster button -->
  <button
    class="recluster-btn"
    onclick={handleRecluster}
    disabled={reclustering}
    title="Trigger taxonomy recluster (cold path)"
  >
    {reclustering ? 'Reclustering...' : 'Recluster'}
  </button>

  <!-- Node counts (reflects state filter) -->
  <div class="stats-row">
    <span>{filteredCounts.active} active</span>
    <span class="stats-sep">|</span>
    <span>{filteredCounts.candidate} candidates</span>
    <span class="stats-sep">|</span>
    <span>{filteredCounts.template} templates</span>
  </div>

  <!-- Visual encoding legend -->
  <div class="legend">
    <span>Bright wireframe = high coherence</span>
    <span>Vivid color = high score</span>
  </div>
</div>

<style>
  .topology-controls {
    position: absolute;
    top: 8px;
    right: 8px;
    display: flex;
    flex-direction: column;
    gap: 6px;
    align-items: flex-end;
    pointer-events: none;
  }

  .topology-controls > * {
    pointer-events: auto;
  }

  .q-badge {
    display: flex;
    align-items: center;
    gap: 4px;
    padding: 2px 8px;
    background: var(--color-surface);
    border: 1px solid;
    font-family: var(--font-mono);
    font-size: 11px;
  }

  .q-label {
    color: var(--color-text-dim);
  }

  .lod-indicator {
    padding: 2px 6px;
    background: var(--color-surface);
    border: 1px solid var(--color-contour);
    font-family: var(--font-mono);
    font-size: 10px;
    color: var(--color-text-dim);
  }

  .search-bar {
    width: 200px;
  }

  .search-input {
    width: 100%;
    padding: 4px 8px;
    background: var(--color-surface);
    border: 1px solid var(--color-contour);
    color: var(--color-text);
    font-family: var(--font-mono);
    font-size: 11px;
    outline: none;
  }

  .search-input:focus {
    border-color: var(--tier-accent, var(--color-neon-cyan));
  }

  .similarity-btn {
    padding: 2px 8px;
    background: transparent;
    border: 1px solid var(--color-border-subtle);
    color: var(--color-text-dim);
    font-family: var(--font-mono);
    font-size: 10px;
    cursor: pointer;
  }

  .similarity-btn:hover {
    border-color: var(--color-neon-cyan);
    color: var(--color-neon-cyan);
  }

  .similarity-btn.similarity-active {
    border-color: var(--color-neon-cyan);
    color: var(--color-neon-cyan);
  }

  .injection-btn {
    padding: 2px 8px;
    background: transparent;
    border: 1px solid var(--color-border-subtle);
    color: var(--color-text-dim);
    font-family: var(--font-mono);
    font-size: 10px;
    cursor: pointer;
  }

  .injection-btn:hover {
    border-color: #ff9500;
    color: #ff9500;
  }

  .injection-btn.injection-active {
    border-color: #ff9500;
    color: #ff9500;
  }

  .recluster-btn {
    padding: 2px 8px;
    background: transparent;
    border: 1px solid var(--color-border-subtle);
    color: var(--color-text-dim);
    font-family: var(--font-mono);
    font-size: 10px;
    cursor: pointer;
  }

  .recluster-btn:hover:not(:disabled) {
    border-color: var(--tier-accent, var(--color-neon-cyan));
    color: var(--tier-accent, var(--color-neon-cyan));
  }

  .recluster-btn:disabled {
    opacity: 0.4;
    cursor: not-allowed;
  }

  .stats-row {
    font-family: var(--font-mono);
    font-size: 10px;
    color: var(--color-text-dim);
  }

  .stats-sep {
    margin: 0 4px;
    opacity: 0.4;
  }

  .legend {
    display: flex;
    flex-direction: column;
    gap: 2px;
    font-family: var(--font-mono);
    font-size: 10px;
    color: var(--color-text-dim);
  }
</style>
