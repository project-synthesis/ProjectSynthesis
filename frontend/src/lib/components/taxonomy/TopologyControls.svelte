<script lang="ts">
  import { onDestroy, onMount } from 'svelte';
  import { clustersStore } from '$lib/stores/clusters.svelte';
  import { tooltip } from '$lib/actions/tooltip';
  import { TOPOLOGY_TOOLTIPS } from '$lib/utils/ui-tooltips';
  import TopologyInfoPanel from './TopologyInfoPanel.svelte';
  import type { LODTier } from './TopologyRenderer';
  import { routing } from '$lib/stores/routing.svelte';

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
  /** Filter-aware label for the ambient badge. Show the count matching the
   *  current state filter so the badge stays coherent with the topology's
   *  highlight-and-dim visual pattern. */
  const ambientLabel = $derived.by(() => {
    const f = clustersStore.stateFilter;
    if (f === null) return `${clustersStore.liveClusterCount} clusters`;
    if (f === 'active') return `${filteredCounts.active} active`;
    if (f === 'candidate') return `${filteredCounts.candidate} candidates`;
    // mature/archived (and any future state): count from filteredTaxonomyTree directly
    const count = clustersStore.filteredTaxonomyTree.filter(n => n.state === f).length;
    return `${count} ${f}`;
  });

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

  // --- Hint card (shows once on first visit) ---
  const HINT_KEY = 'synthesis:pattern_graph_hints_dismissed';
  let hintVisible = $state(false);

  onMount(() => {
    if (localStorage.getItem(HINT_KEY) !== '1') {
      hintVisible = true;
    }
  });

  function dismissHint() {
    hintVisible = false;
    localStorage.setItem(HINT_KEY, '1');
  }

  function showHint() {
    hintVisible = true;
  }

  // Edge hover detection moved to a dedicated edge-zone div with pointer-events: auto.
  // The parent .hud has pointer-events: none (necessary to not block graph interaction)
  // so onmousemove on .hud never fires. The edge-zone div is the fix.

  // --- Cleanup timers on destroy ---
  onDestroy(() => {
    if (controlsTimer) clearTimeout(controlsTimer);
    if (metricsTimer) clearTimeout(metricsTimer);
  });

  // --- Dismiss all overlays ---
  function dismissAll() {
    if (hintVisible) { dismissHint(); }
    if (searchOpen) { searchOpen = false; searchQuery = ''; }
    if (controlsVisible) { controlsVisible = false; }
    if (metricsVisible) { metricsVisible = false; }
  }

  // --- Graph background click ---
  function handleGraphClick(e: MouseEvent) {
    // Only dismiss if clicking the HUD background itself (not a child element)
    if (e.target === e.currentTarget) {
      dismissAll();
    }
  }

  // --- Search ---
  function handleSearch(): void {
    if (searchQuery.trim()) {
      onSearch(searchQuery.trim());
    } else {
      // Empty search = close
      searchOpen = false;
    }
  }

  function handleSearchKeyDown(e: KeyboardEvent): void {
    if (e.key === 'Enter') handleSearch();
    if (e.key === 'Escape') { searchOpen = false; searchQuery = ''; }
  }

  async function handleRecluster(): Promise<void> {
    reclustering = true;
    try { await onRecluster(); } finally { reclustering = false; }
  }

  // --- Global keyboard ---
  function handleGlobalKey(e: KeyboardEvent): void {
    // Ctrl+F: toggle search
    if ((e.ctrlKey || e.metaKey) && e.key === 'f') {
      e.preventDefault();
      searchOpen = !searchOpen;
      if (!searchOpen) searchQuery = '';
      return;
    }

    // Escape: dismiss any visible overlay (priority order)
    if (e.key === 'Escape') {
      if (hintVisible) { dismissHint(); return; }
      if (searchOpen) { searchOpen = false; searchQuery = ''; return; }
      if (metricsVisible) { metricsVisible = false; return; }
      if (controlsVisible) { controlsVisible = false; return; }
    }

    // Q: toggle metrics (only when not in text input)
    if (e.key === 'q' && !searchOpen && !(e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement)) {
      metricsVisible = !metricsVisible;
      if (metricsVisible) showMetrics();
      else if (metricsTimer) clearTimeout(metricsTimer);
    }
  }
</script>

<svelte:window onkeydown={handleGlobalKey} />

<!-- Diegetic UI — minimal overlay, auto-hide controls -->
<!-- svelte-ignore a11y_no_static_element_interactions -->
<!-- svelte-ignore a11y_click_events_have_key_events -->
<div class="hud" onclick={handleGraphClick}>
  <!-- Invisible right-edge hover zone — triggers controls panel -->
  <!-- svelte-ignore a11y_no_static_element_interactions -->
  <div class="hud-edge-zone" onmouseenter={showControls} onmouseleave={startControlsHide}></div>

  <!-- AMBIENT: LOD — top center -->
  <div class="hud-lod-badge" class:hud-ambient--hidden={controlsVisible}>
    <span class="hud-lod">{lodTier.toUpperCase()}</span>
  </div>

  <!-- AMBIENT: Cluster count — bottom center -->
  <div class="hud-cluster-badge" class:hud-ambient--hidden={controlsVisible}>
    <span>{ambientLabel}</span>
  </div>

  <!-- HINT CARD: Compact shortcut cheat-sheet -->
  {#if hintVisible}
    <!-- svelte-ignore a11y_no_static_element_interactions -->
    <!-- svelte-ignore a11y_click_events_have_key_events -->
    <div class="hud-hint-overlay" onclick={dismissHint}>
      <div class="hud-hint" style="--hint-accent: {routing.tierColor};" onclick={(e) => e.stopPropagation()}>
        <div class="hud-hint-header">
          <span class="hud-hint-title">PATTERN GRAPH</span>
          <button class="hud-hint-close" onclick={dismissHint}>×</button>
        </div>
        <div class="hud-hint-shortcuts">
          <div class="hud-hint-row"><span class="hud-hint-key">Drag</span><span class="hud-hint-desc">Orbit</span></div>
          <div class="hud-hint-row"><span class="hud-hint-key">Scroll</span><span class="hud-hint-desc">Zoom</span></div>
          <div class="hud-hint-row"><span class="hud-hint-key">Click node</span><span class="hud-hint-desc">Inspect cluster</span></div>
          <div class="hud-hint-row"><span class="hud-hint-key">Right edge</span><span class="hud-hint-desc">Reveal controls</span></div>
          <div class="hud-hint-row"><span class="hud-hint-key">Q</span><span class="hud-hint-desc">Toggle metrics</span></div>
          <div class="hud-hint-row"><span class="hud-hint-key">Ctrl+F</span><span class="hud-hint-desc">Search nodes</span></div>
          <div class="hud-hint-row"><span class="hud-hint-key">Esc</span><span class="hud-hint-desc">Dismiss overlays</span></div>
        </div>
        <div class="hud-hint-visual">
          <span>Node size = members</span>
          <span>Color = domain</span>
          <span>Wireframe = coherence</span>
        </div>
      </div>
    </div>
  {/if}

  <!-- CONTROLS: Auto-hide — appears on right-edge hover -->
  <!-- svelte-ignore a11y_no_static_element_interactions -->
  <div
    class="hud-controls"
    class:hud-controls--visible={controlsVisible}
    onmouseenter={showControls}
    onmouseleave={startControlsHide}
  >
    <button class="hud-btn" class:hud-btn--on={clustersStore.showSimilarityEdges} style="--hud-accent: var(--color-neon-cyan)" onclick={() => { clustersStore.showSimilarityEdges = !clustersStore.showSimilarityEdges; }} use:tooltip={TOPOLOGY_TOOLTIPS.toggle_similarity}>Similarity</button>
    <button class="hud-btn" class:hud-btn--on={clustersStore.showInjectionEdges} style="--hud-accent: var(--color-neon-orange)" onclick={() => { clustersStore.showInjectionEdges = !clustersStore.showInjectionEdges; }} use:tooltip={TOPOLOGY_TOOLTIPS.toggle_injection}>Injection</button>
    <button class="hud-btn" onclick={onSeed} use:tooltip={'Seed taxonomy with generated prompts'}>Seed</button>
    <button class="hud-btn" onclick={handleRecluster} disabled={reclustering} use:tooltip={TOPOLOGY_TOOLTIPS.recluster}>{reclustering ? '...' : 'Recluster'}</button>
    <button class="hud-btn" class:hud-btn--on={showActivity} style="--hud-accent: var(--color-neon-purple)" onclick={onToggleActivity} use:tooltip={'Toggle taxonomy decision feed'}>Activity</button>
    <button class="hud-btn" onclick={showHint} use:tooltip={'Shortcuts'}>Help</button>
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
        onkeydown={handleSearchKeyDown}
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

  /* ── Edge detection zone — reveals controls on hover ── */

  .hud-edge-zone {
    position: absolute;
    top: 0;
    right: 0;
    width: 50px;
    height: 100%;
    pointer-events: auto;
  }

  /* ── AMBIENT: LOD badge — top center ── */

  .hud-lod-badge {
    position: absolute;
    top: 8px;
    left: 50%;
    transform: translateX(-50%);
    pointer-events: none;
    font-family: var(--font-mono);
    font-size: 9px;
    color: color-mix(in srgb, var(--color-text-dim) 40%, transparent);
    transition: opacity 300ms cubic-bezier(0.16, 1, 0.3, 1);
  }

  .hud-lod {
    font-weight: 700;
    font-size: 8px;
    letter-spacing: 0.08em;
  }

  /* ── AMBIENT: Cluster count — bottom center ── */

  .hud-cluster-badge {
    position: absolute;
    bottom: 8px;
    left: 50%;
    transform: translateX(-50%);
    pointer-events: none;
    font-family: var(--font-mono);
    font-size: 9px;
    color: color-mix(in srgb, var(--color-text-dim) 40%, transparent);
    transition: opacity 300ms cubic-bezier(0.16, 1, 0.3, 1);
    white-space: nowrap;
  }

  .hud-ambient--hidden {
    opacity: 0;
  }

  /* ── CONTROLS: Auto-hide right edge ── */

  .hud-controls {
    position: absolute;
    top: 50%;
    right: 8px;
    transform: translateY(-50%) translateX(8px);
    width: 100px;
    display: flex;
    flex-direction: column;
    gap: 2px;
    padding: 6px;
    pointer-events: none;
    opacity: 0;
    transition: opacity 300ms cubic-bezier(0.16, 1, 0.3, 1),
                transform 300ms cubic-bezier(0.16, 1, 0.3, 1);
    background: color-mix(in srgb, var(--color-bg-primary) 80%, transparent);
  }

  .hud-controls--visible {
    opacity: 1;
    pointer-events: auto;
    transform: translateY(-50%) translateX(0);
  }

  .hud-btn {
    display: flex;
    align-items: center;
    justify-content: center;
    height: 24px;
    padding: 0 8px;
    background: transparent;
    border: 1px solid transparent;
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
  }

  .hud-metrics--visible {
    opacity: 1;
    pointer-events: auto;
    transform: translateY(0);
  }

  /* ── HINT CARD ── */

  .hud-hint-overlay {
    position: absolute;
    inset: 0;
    display: flex;
    align-items: center;
    justify-content: center;
    pointer-events: auto;
    background: color-mix(in srgb, var(--color-bg-primary) 40%, transparent);
    animation: hint-fade-in 200ms cubic-bezier(0.16, 1, 0.3, 1);
    z-index: 20;
  }

  @keyframes hint-fade-in {
    from { opacity: 0; }
    to { opacity: 1; }
  }

  .hud-hint {
    width: 220px;
    background: color-mix(in srgb, var(--color-bg-secondary) 95%, transparent);
    border: 1px solid color-mix(in srgb, var(--hint-accent, var(--color-neon-cyan)) 25%, transparent);
    font-family: var(--font-mono);
    animation: hint-slide-in 300ms cubic-bezier(0.16, 1, 0.3, 1);
  }

  @keyframes hint-slide-in {
    from { opacity: 0; transform: scale(0.95) translateY(8px); }
    to { opacity: 1; transform: scale(1) translateY(0); }
  }

  .hud-hint-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 8px 10px 6px;
    border-bottom: 1px solid color-mix(in srgb, var(--hint-accent, var(--color-neon-cyan)) 15%, transparent);
  }

  .hud-hint-title {
    font-family: var(--font-display);
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.1em;
    color: var(--hint-accent, var(--color-neon-cyan));
  }

  .hud-hint-close {
    background: transparent;
    border: none;
    color: var(--color-text-dim);
    font-size: 14px;
    cursor: pointer;
    padding: 0 2px;
    line-height: 1;
  }

  .hud-hint-close:hover { color: var(--color-text-primary); }

  .hud-hint-shortcuts {
    padding: 8px 10px;
    display: flex;
    flex-direction: column;
    gap: 4px;
  }

  .hud-hint-row {
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 10px;
  }

  .hud-hint-key {
    min-width: 65px;
    color: var(--hint-accent, var(--color-neon-cyan));
    font-weight: 600;
    font-size: 9px;
  }

  .hud-hint-desc {
    color: var(--color-text-secondary);
    font-size: 9px;
  }

  .hud-hint-visual {
    padding: 6px 10px 8px;
    border-top: 1px solid color-mix(in srgb, var(--color-border-subtle) 30%, transparent);
    display: flex;
    flex-direction: column;
    gap: 2px;
    font-size: 8px;
    color: var(--color-text-dim);
  }

  /* ── SEARCH ── */

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
