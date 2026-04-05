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

  const filteredCounts = $derived(clustersStore.clusterCounts);

  // --- Auto-hide controls ---
  let controlsVisible = $state(false);
  let controlsTimer: ReturnType<typeof setTimeout> | null = null;

  function showControls() {
    controlsVisible = true;
    if (controlsTimer) clearTimeout(controlsTimer);
  }

  function startControlsHide() {
    if (controlsTimer) clearTimeout(controlsTimer);
    controlsTimer = setTimeout(() => { controlsVisible = false; }, 2000);
  }

  // --- Auto-hide metrics ---
  let metricsVisible = $state(false);
  let metricsTimer: ReturnType<typeof setTimeout> | null = null;

  function showMetrics() {
    metricsVisible = true;
    if (metricsTimer) clearTimeout(metricsTimer);
  }

  function startMetricsHide() {
    if (metricsTimer) clearTimeout(metricsTimer);
    metricsTimer = setTimeout(() => { metricsVisible = false; }, 2000);
  }

  // --- Edge hover detection (right 50px zone) ---
  function handleMouseMove(e: MouseEvent) {
    const target = e.currentTarget as HTMLElement;
    if (!target) return;
    const rect = target.getBoundingClientRect();
    const distFromRight = rect.right - e.clientX;
    if (distFromRight < 50 && !controlsVisible) {
      showControls();
    }
  }

  // --- Keyboard shortcuts ---
  function handleSearch(): void {
    if (searchQuery.trim()) onSearch(searchQuery.trim());
  }

  function handleKeyDown(e: KeyboardEvent): void {
    if (e.key === 'Enter') handleSearch();
    if (e.key === 'Escape') { searchOpen = false; searchQuery = ''; }
  }

  async function handleRecluster(): Promise<void> {
    reclustering = true;
    try { await onRecluster(); } finally { reclustering = false; }
  }

  function handleGlobalKey(e: KeyboardEvent): void {
    if ((e.ctrlKey || e.metaKey) && e.key === 'f') {
      e.preventDefault();
      searchOpen = true;
    }
    if (e.key === 'q' && !searchOpen && !(e.target instanceof HTMLInputElement)) {
      metricsVisible = !metricsVisible;
      if (metricsVisible) showMetrics();
    }
  }
</script>

<svelte:window onkeydown={handleGlobalKey} />

<!-- Diegetic UI — minimal overlay, auto-hide controls -->
<!-- svelte-ignore a11y_no_static_element_interactions -->
<div class="hud" onmousemove={handleMouseMove}>
  <!-- AMBIENT: Minimal telemetry — always visible, bottom-right -->
  <div class="hud-ambient">
    <span>{filteredCounts.active} clusters</span>
    <span class="hud-dot">·</span>
    <span class="hud-lod">{lodTier.toUpperCase()}</span>
  </div>

  <!-- CONTROLS: Auto-hide — appears on right-edge hover -->
  <!-- svelte-ignore a11y_no_static_element_interactions -->
  <div
    class="hud-controls"
    class:hud-controls--visible={controlsVisible}
    onmouseenter={showControls}
    onmouseleave={startControlsHide}
  >
    <div class="hud-row">
      <button class="hud-btn" class:hud-btn--on={clustersStore.showSimilarityEdges} style="--hud-accent: var(--color-neon-cyan)" onclick={() => { clustersStore.showSimilarityEdges = !clustersStore.showSimilarityEdges; }} use:tooltip={TOPOLOGY_TOOLTIPS.toggle_similarity}>Sim</button>
      <button class="hud-btn" class:hud-btn--on={clustersStore.showInjectionEdges} style="--hud-accent: var(--color-neon-orange)" onclick={() => { clustersStore.showInjectionEdges = !clustersStore.showInjectionEdges; }} use:tooltip={TOPOLOGY_TOOLTIPS.toggle_injection}>Inj</button>
    </div>
    <div class="hud-row">
      <button class="hud-btn" onclick={onSeed} use:tooltip={'Seed taxonomy with generated prompts'}>Seed</button>
      <button class="hud-btn" onclick={handleRecluster} disabled={reclustering} use:tooltip={TOPOLOGY_TOOLTIPS.recluster}>{reclustering ? '...' : 'Recluster'}</button>
    </div>
    <div class="hud-row">
      <button class="hud-btn" class:hud-btn--on={showActivity} style="--hud-accent: var(--color-neon-purple)" onclick={onToggleActivity} use:tooltip={'Toggle taxonomy decision feed'}>Activity</button>
    </div>
  </div>

  <!-- METRICS: On-demand — press Q or hover bottom-left -->
  <!-- svelte-ignore a11y_no_static_element_interactions -->
  <div
    class="hud-metrics"
    class:hud-metrics--visible={metricsVisible}
    onmouseenter={showMetrics}
    onmouseleave={startMetricsHide}
  >
    <TopologyInfoPanel />
  </div>

  <!-- SEARCH: Center-top (Ctrl+F) -->
  {#if searchOpen}
    <div class="hud-search">
      <input
        type="text"
        bind:value={searchQuery}
        onkeydown={handleKeyDown}
        placeholder="Search nodes..."
        class="hud-search-input"
      />
    </div>
  {/if}
</div>

<style>
  /* ══ Diegetic UI — Dead Space inspired ══
   *
   * Almost nothing visible by default. The graph IS the interface.
   * Controls reveal on edge-hover. Metrics on Q key.
   * Only ambient telemetry persists (cluster count + LOD).
   */

  .hud {
    position: absolute;
    inset: 0;
    pointer-events: none;
    z-index: 10;
  }

  /* ── AMBIENT: Always visible, minimal ── */

  .hud-ambient {
    position: absolute;
    bottom: 8px;
    right: 12px;
    display: flex;
    align-items: center;
    gap: 4px;
    pointer-events: none;
    font-family: var(--font-mono);
    font-size: 9px;
    color: color-mix(in srgb, var(--color-text-dim) 40%, transparent);
    transition: opacity 500ms ease;
  }

  .hud-dot {
    opacity: 0.3;
  }

  .hud-lod {
    font-weight: 700;
    font-size: 8px;
    letter-spacing: 0.08em;
  }

  /* ── CONTROLS: Auto-hide right edge ── */

  .hud-controls {
    position: absolute;
    bottom: 8px;
    right: 8px;
    width: 160px;
    display: flex;
    flex-direction: column;
    gap: 2px;
    padding: 6px;
    pointer-events: none;
    opacity: 0;
    transform: translateX(8px);
    transition: opacity 300ms cubic-bezier(0.16, 1, 0.3, 1),
                transform 300ms cubic-bezier(0.16, 1, 0.3, 1);
    background: color-mix(in srgb, var(--color-bg-primary) 80%, transparent);
    backdrop-filter: blur(6px);
    -webkit-backdrop-filter: blur(6px);
  }

  .hud-controls--visible {
    opacity: 1;
    pointer-events: auto;
    transform: translateX(0);
  }

  .hud-row {
    display: flex;
    gap: 2px;
  }

  .hud-btn {
    display: flex;
    flex: 1;
    align-items: center;
    justify-content: center;
    height: 24px;
    padding: 0 8px;
    background: transparent;
    border: 1px solid color-mix(in srgb, var(--color-border-subtle) 30%, transparent);
    color: var(--color-text-dim);
    font-family: var(--font-mono);
    font-size: 9px;
    cursor: pointer;
    transition: border-color 150ms cubic-bezier(0.16, 1, 0.3, 1),
                color 150ms cubic-bezier(0.16, 1, 0.3, 1),
                background 150ms cubic-bezier(0.16, 1, 0.3, 1);
  }

  .hud-btn:hover:not(:disabled) {
    border-color: color-mix(in srgb, var(--hud-accent, var(--color-neon-cyan)) 40%, transparent);
    color: var(--color-text-secondary);
  }

  .hud-btn--on {
    border-color: color-mix(in srgb, var(--hud-accent, var(--color-neon-cyan)) 50%, transparent);
    color: var(--color-text-primary);
    background: color-mix(in srgb, var(--hud-accent, var(--color-neon-cyan)) 6%, transparent);
  }

  .hud-btn:disabled {
    opacity: 0.35;
    cursor: not-allowed;
  }

  /* ── METRICS: On-demand, bottom-left ── */

  .hud-metrics {
    position: absolute;
    bottom: 8px;
    left: 8px;
    width: 190px;
    pointer-events: none;
    opacity: 0;
    transform: translateY(8px);
    transition: opacity 300ms cubic-bezier(0.16, 1, 0.3, 1),
                transform 300ms cubic-bezier(0.16, 1, 0.3, 1);
    background: color-mix(in srgb, var(--color-bg-primary) 80%, transparent);
    backdrop-filter: blur(6px);
    -webkit-backdrop-filter: blur(6px);
  }

  .hud-metrics--visible {
    opacity: 1;
    pointer-events: auto;
    transform: translateY(0);
  }

  /* ── SEARCH: Center-top ── */

  .hud-search {
    position: absolute;
    top: 8px;
    left: 50%;
    transform: translateX(-50%);
    width: 240px;
    pointer-events: auto;
  }

  .hud-search-input {
    width: 100%;
    padding: 4px 8px;
    background: color-mix(in srgb, var(--color-bg-primary) 88%, transparent);
    backdrop-filter: blur(6px);
    -webkit-backdrop-filter: blur(6px);
    border: 1px solid color-mix(in srgb, var(--color-neon-cyan) 25%, transparent);
    color: var(--color-text-primary);
    font-family: var(--font-mono);
    font-size: 11px;
    outline: none;
    text-align: center;
  }

  .hud-search-input:focus {
    border-color: color-mix(in srgb, var(--color-neon-cyan) 50%, transparent);
  }

  .hud-search-input::placeholder {
    color: var(--color-text-dim);
    opacity: 0.4;
  }
</style>
