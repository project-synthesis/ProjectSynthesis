<script lang="ts">
  import { onMount } from 'svelte';
  import type { TaxonomyActivityEvent } from '$lib/api/clusters';
  import { clustersStore } from '$lib/stores/clusters.svelte';

  // -- State --

  let totalInBuffer = $state(0);
  let filterPath = $state<string>('');
  let filterOp = $state<string | null>(null);
  let errorsOnly = $state(false);
  let pinToBottom = $state(true);
  let expandedTs = $state<string | null>(null);
  let scrollEl: HTMLDivElement;

  // -- Derived --

  const filtered = $derived(
    clustersStore.activityEvents.filter(e => {
      if (filterPath && e.path !== filterPath) return false;
      if (filterOp && e.op !== filterOp) return false;
      if (errorsOnly && !(e.op === 'error' || e.decision === 'rejected' || e.decision === 'failed')) return false;
      return true;
    }),
  );

  // -- Color coding --

  function decisionColor(e: TaxonomyActivityEvent): string {
    if (e.op === 'error') return 'var(--color-neon-red)';
    if (e.decision === 'accepted' || e.decision === 'merged' || e.decision === 'merge_into') return 'var(--color-neon-green)';
    if (e.decision === 'create_new' || e.decision === 'child_created') return 'var(--color-neon-cyan)';
    if (e.decision === 'rejected' || e.decision === 'blocked') return 'var(--color-neon-yellow)';
    return 'var(--color-text-dim)';
  }

  function pathColor(path: string): string {
    if (path === 'hot') return 'var(--color-neon-red)';
    if (path === 'warm') return 'var(--color-neon-yellow)';
    if (path === 'cold') return 'var(--color-neon-cyan)';
    return 'var(--color-text-dim)';
  }

  // -- Key metric from context --

  function keyMetric(e: TaxonomyActivityEvent): string {
    const c = e.context;
    if (!c) return '';
    if (e.op === 'assign' && typeof c.winner_label === 'string') {
      const candidates = c.candidates as any[] | undefined;
      const score = Array.isArray(candidates) && candidates.length > 0 && typeof candidates[0].effective_score === 'number'
        ? ` s=${(candidates[0].effective_score as number).toFixed(3)}`
        : '';
      return c.winner_label + score;
    }
    if (e.op === 'phase') {
      const qb = typeof c.q_before === 'number' ? c.q_before.toFixed(3) : '?';
      const qa = typeof c.q_after === 'number' ? c.q_after.toFixed(3) : '?';
      return `Q ${qb}→${qa}`;
    }
    if (e.op === 'refit') {
      return `Q ${(c.q_before as number)?.toFixed(3) ?? '?'}→${(c.q_after as number)?.toFixed(3) ?? '?'}`;
    }
    if (e.op === 'split') {
      return typeof c.hdbscan_clusters === 'number' ? `${c.hdbscan_clusters} sub-clusters` : '';
    }
    if (e.op === 'merge') {
      const sim = typeof c.similarity === 'number' ? `sim=${c.similarity.toFixed(3)}` : '';
      const gate = typeof c.gate === 'string' ? ` [${c.gate}]` : '';
      return sim + gate;
    }
    return '';
  }

  function formatTs(ts: string): string {
    try {
      const d = new Date(ts);
      return d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    } catch {
      return ts.slice(11, 19);
    }
  }

  // -- Cluster click → select in topology --

  function handleClusterClick(id: string | null): void {
    if (!id) return;
    window.dispatchEvent(new CustomEvent('select-cluster', { detail: { id } }));
  }

  // -- Initial seed + SSE --

  async function loadInitial(): Promise<void> {
    try {
      await clustersStore.loadActivity();
      totalInBuffer = clustersStore.activityEvents.length;
    } catch {
      // non-fatal
    }
  }

  onMount(() => {
    loadInitial();

    // Listen for taxonomy_activity SSE events dispatched by parent
    function onActivity(e: Event): void {
      const ev = (e as CustomEvent).detail as TaxonomyActivityEvent;
      clustersStore.pushActivityEvent(ev);
      totalInBuffer++;
      if (pinToBottom && scrollEl) {
        requestAnimationFrame(() => {
          scrollEl.scrollTop = 0; // newest at top
        });
      }
    }

    window.addEventListener('taxonomy-activity', onActivity);
    return () => window.removeEventListener('taxonomy-activity', onActivity);
  });
</script>

<div class="ap-panel">
  <!-- Header -->
  <div class="ap-header">
    <span class="ap-title">ACTIVITY</span>
    <span class="ap-count">{totalInBuffer} events</span>
    <div class="ap-header-spacer"></div>
    <button
      class="ap-pin"
      class:ap-pin-active={pinToBottom}
      onclick={() => { pinToBottom = !pinToBottom; }}
      title="Pin to newest"
    >⇩</button>
  </div>

  <!-- Filters -->
  <div class="ap-filters">
    <!-- Path filter chips -->
    <div class="ap-chip-row">
      {#each ['', 'hot', 'warm', 'cold'] as p}
        <button
          class="ap-chip"
          class:ap-chip-active={filterPath === p}
          style="--chip-color: {p ? pathColor(p) : 'var(--color-text-secondary)'}"
          onclick={() => { filterPath = filterPath === p ? '' : p; }}
        >{p || 'all'}</button>
      {/each}
      <div class="ap-chip-sep"></div>
      <button
        class="ap-chip"
        class:ap-chip-active={errorsOnly}
        style="--chip-color: var(--color-neon-red)"
        onclick={() => { errorsOnly = !errorsOnly; }}
      >errors</button>
    </div>
    <!-- Operation type filter chips -->
    <div class="ap-filter-row">
      {#each ['assign','split','merge','retire','phase','refit','emerge','discover','error'] as opVal}
        <button
          class="ap-chip"
          class:ap-chip-active={filterOp === opVal}
          onclick={() => { filterOp = filterOp === opVal ? null : opVal; }}
        >{opVal}</button>
      {/each}
    </div>
  </div>

  <!-- Event list -->
  <div class="ap-list" bind:this={scrollEl}>
    {#each filtered as ev (ev.ts + ev.op + ev.decision)}
      <div class="ap-row">
        <!-- Summary line -->
        <button
          class="ap-row-summary"
          onclick={() => { expandedTs = expandedTs === ev.ts + ev.op ? null : ev.ts + ev.op; }}
        >
          <span class="ap-ts">{formatTs(ev.ts)}</span>
          <span class="ap-badge ap-badge-path" style="color: {pathColor(ev.path)}">{ev.path}</span>
          <span class="ap-badge ap-badge-op">{ev.op}</span>
          <span class="ap-badge ap-badge-decision" style="color: {decisionColor(ev)}">{ev.decision}</span>
          {#if ev.cluster_id}
            <!-- svelte-ignore a11y_click_events_have_key_events -->
            <!-- svelte-ignore a11y_no_static_element_interactions -->
            <span
              class="ap-cluster-link"
              onclick={(e) => { e.stopPropagation(); handleClusterClick(ev.cluster_id); }}
            >{ev.cluster_id.slice(0, 8)}</span>
          {/if}
          <span class="ap-metric">{keyMetric(ev)}</span>
        </button>

        <!-- Expanded context -->
        {#if expandedTs === ev.ts + ev.op}
          <div class="ap-context">
            {#each Object.entries(ev.context) as [k, v]}
              <div class="ap-ctx-row">
                <span class="ap-ctx-key">{k}</span>
                <span class="ap-ctx-val">{typeof v === 'object' ? JSON.stringify(v, null, 2) : String(v)}</span>
              </div>
            {/each}
          </div>
        {/if}
      </div>
    {:else}
      <div class="ap-empty">No events{filterPath || filterOp || errorsOnly ? ' matching filters' : ''}</div>
    {/each}
  </div>
</div>

<style>
  .ap-panel {
    display: flex;
    flex-direction: column;
    height: 100%;
    background: var(--color-bg-secondary);
    border-top: 1px solid var(--color-border-subtle);
    font-family: var(--font-mono);
    overflow: hidden;
  }

  /* -- Header -- */

  .ap-header {
    display: flex;
    align-items: center;
    gap: 6px;
    padding: 4px 8px;
    border-bottom: 1px solid var(--color-border-subtle);
    flex-shrink: 0;
  }

  .ap-title {
    font-size: 9px;
    font-weight: 700;
    letter-spacing: 0.1em;
    color: var(--color-text-dim);
    font-family: var(--font-display);
  }

  .ap-count {
    font-size: 9px;
    color: var(--color-text-dim);
    opacity: 0.6;
  }

  .ap-header-spacer {
    flex: 1;
  }

  .ap-pin {
    padding: 1px 5px;
    background: transparent;
    border: 1px solid var(--color-border-subtle);
    color: var(--color-text-dim);
    font-size: 10px;
    cursor: pointer;
    line-height: 1;
    transition: border-color 0.15s, color 0.15s;
  }

  .ap-pin:hover {
    border-color: var(--color-border-accent);
    color: var(--color-text-secondary);
  }

  .ap-pin.ap-pin-active {
    border-color: color-mix(in srgb, var(--color-neon-cyan) 40%, transparent);
    color: var(--color-neon-cyan);
  }

  /* -- Filters -- */

  .ap-filters {
    padding: 3px 8px;
    border-bottom: 1px solid var(--color-border-subtle);
    flex-shrink: 0;
  }

  .ap-chip-row {
    display: flex;
    align-items: center;
    gap: 3px;
  }

  .ap-chip {
    padding: 1px 6px;
    background: transparent;
    border: 1px solid var(--color-border-subtle);
    color: var(--color-text-dim);
    font-family: var(--font-mono);
    font-size: 9px;
    cursor: pointer;
    transition: border-color 0.15s, color 0.15s, background 0.15s;
  }

  .ap-chip:hover {
    border-color: color-mix(in srgb, var(--chip-color, var(--color-neon-cyan)) 30%, transparent);
    color: var(--color-text-secondary);
  }

  .ap-chip.ap-chip-active {
    border-color: color-mix(in srgb, var(--chip-color, var(--color-neon-cyan)) 50%, transparent);
    color: var(--chip-color, var(--color-neon-cyan));
    background: color-mix(in srgb, var(--chip-color, var(--color-neon-cyan)) 8%, transparent);
  }

  .ap-chip-sep {
    width: 1px;
    height: 10px;
    background: var(--color-border-subtle);
    margin: 0 2px;
  }

  .ap-filter-row {
    display: flex;
    align-items: center;
    gap: 3px;
    flex-wrap: wrap;
    margin-top: 3px;
  }

  /* -- List -- */

  .ap-list {
    flex: 1;
    overflow-y: auto;
    overflow-x: hidden;
  }

  .ap-list::-webkit-scrollbar {
    width: 3px;
  }

  .ap-list::-webkit-scrollbar-thumb {
    background: var(--color-border-subtle);
  }

  .ap-row {
    border-bottom: 1px solid color-mix(in srgb, var(--color-border-subtle) 40%, transparent);
  }

  .ap-row-summary {
    display: flex;
    align-items: center;
    gap: 5px;
    width: 100%;
    padding: 3px 8px;
    background: transparent;
    border: none;
    cursor: pointer;
    text-align: left;
    font-family: var(--font-mono);
    font-size: 9px;
    color: var(--color-text-secondary);
    transition: background 0.1s;
  }

  .ap-row-summary:hover {
    background: var(--color-bg-hover);
  }

  .ap-ts {
    color: var(--color-text-dim);
    flex-shrink: 0;
    font-size: 8px;
    min-width: 62px;
  }

  .ap-badge {
    padding: 0 3px;
    border: 1px solid currentColor;
    font-size: 8px;
    opacity: 0.8;
    flex-shrink: 0;
  }

  .ap-badge-path {
    min-width: 24px;
    text-align: center;
  }

  .ap-badge-op {
    color: var(--color-text-dim);
    border-color: var(--color-border-subtle);
  }

  .ap-badge-decision {
    font-weight: 600;
  }

  .ap-cluster-link {
    font-family: var(--font-mono);
    font-size: 8px;
    color: var(--color-neon-cyan);
    opacity: 0.7;
    cursor: pointer;
    text-decoration: underline dotted;
    flex-shrink: 0;
  }

  .ap-cluster-link:hover {
    opacity: 1;
  }

  .ap-metric {
    color: var(--color-text-dim);
    font-size: 8px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    flex: 1;
    min-width: 0;
  }

  /* -- Expanded context -- */

  .ap-context {
    padding: 4px 8px 4px 16px;
    background: color-mix(in srgb, var(--color-bg-primary) 50%, transparent);
    border-top: 1px solid color-mix(in srgb, var(--color-border-subtle) 40%, transparent);
    display: flex;
    flex-direction: column;
    gap: 2px;
  }

  .ap-ctx-row {
    display: flex;
    gap: 8px;
    font-size: 8px;
  }

  .ap-ctx-key {
    color: var(--color-text-dim);
    flex-shrink: 0;
    min-width: 100px;
    font-weight: 600;
  }

  .ap-ctx-val {
    color: var(--color-text-secondary);
    word-break: break-all;
    white-space: pre-wrap;
    font-size: 7.5px;
  }

  /* -- Empty state -- */

  .ap-empty {
    padding: 12px 8px;
    font-size: 9px;
    color: var(--color-text-dim);
    opacity: 0.6;
    text-align: center;
  }
</style>
