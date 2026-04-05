<script lang="ts">
  import { onMount } from 'svelte';
  import type { TaxonomyActivityEvent } from '$lib/api/clusters';
  import { clustersStore } from '$lib/stores/clusters.svelte';

  // -- State --

  const totalInBuffer = $derived(clustersStore.activityEvents.length);
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
      if (errorsOnly && !(e.op === 'error' || e.decision === 'rejected' || e.decision === 'failed'
          || e.decision === 'seed_failed' || e.decision === 'candidate_rejected')) return false;
      return true;
    }),
  );

  // -- Color coding --

  function decisionColor(e: TaxonomyActivityEvent): string {
    if (e.op === 'error') return 'var(--color-neon-red)';
    const d = e.decision;
    // Red — batch-level seed failure only
    if (d === 'seed_failed') return 'var(--color-neon-red)';
    // Amber — individual prompt failures (expected, fail-forward), dissolution
    if (d === 'seed_prompt_failed' || d === 'dissolved') return 'var(--color-neon-yellow)';
    // Green — successful operations
    if (d === 'accepted' || d === 'merged' || d === 'merge_into' || d === 'complete'
        || d === 'split_complete' || d === 'archived' || d === 'domain_created'
        || d === 'created' || d === 'patterns_refreshed' || d === 'zombies_archived'
        || d === 'seed_completed')
      return 'var(--color-neon-green)';
    // Cyan — new entities created
    if (d === 'create_new' || d === 'child_created' || d === 'family_split')
      return 'var(--color-neon-cyan)';
    // Cyan — candidate created
    if (d === 'candidate_created') return 'var(--color-neon-cyan)';
    // Green — candidate promoted
    if (d === 'candidate_promoted') return 'var(--color-neon-green)';
    // Amber — candidate rejected, split fully reversed
    if (d === 'candidate_rejected' || d === 'split_fully_reversed')
      return 'var(--color-neon-yellow)';
    // Amber — rejections, blocks, skips
    if (d === 'rejected' || d === 'blocked' || d === 'skipped'
        || d === 'candidates_filtered')
      return 'var(--color-neon-yellow)';
    // Informational — algorithm results, noise, seed progress
    if (d === 'algorithm_result' || d === 'noise_reassigned' || d === 'mega_clusters_detected'
        || d === 'no_sub_structure' || d === 'scored'
        || d === 'seed_started' || d === 'seed_explore_complete' || d === 'seed_agents_complete'
        || d === 'seed_persist_complete' || d === 'seed_taxonomy_complete'
        || d === 'seed_prompt_scored')
      return 'var(--color-text-secondary)';
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
    if (e.op === 'assign') {
      if (e.decision === 'merge_into' && typeof c.winner_label === 'string') {
        const candidates = c.candidates as any[] | undefined;
        const score = Array.isArray(candidates) && candidates.length > 0 && typeof candidates[0].effective_score === 'number'
          ? `${(candidates[0].effective_score as number).toFixed(3)}`
          : '';
        const members = typeof c.member_count === 'number' ? `[${c.member_count}m]` : '';
        const label = typeof c.prompt_label === 'string' ? `"${c.prompt_label}" ` : '';
        return `${label}→ ${c.winner_label} ${members} ${score}`.trim();
      }
      if (e.decision === 'create_new' && typeof c.new_label === 'string') {
        return `new: ${c.new_label} [${c.prompt_domain ?? ''}]`;
      }
      if (typeof c.winner_label === 'string') {
        return c.winner_label;
      }
      return '';
    }
    if (e.op === 'phase') {
      if (typeof c.q_before === 'number' || typeof c.q_after === 'number') {
        const qb = typeof c.q_before === 'number' ? c.q_before.toFixed(3) : '?';
        const qa = typeof c.q_after === 'number' ? c.q_after.toFixed(3) : '?';
        return `Q ${qb}→${qa}`;
      }
      return '';
    }
    if (e.op === 'refit') {
      if (e.decision === 'cluster_detail') {
        const clusters = Array.isArray(c.clusters) ? c.clusters as any[] : [];
        const top = clusters.slice(0, 3).map((cl: any) => `${cl.label}(${cl.member_count})`).join(', ');
        return top || `${c.total_optimizations ?? '?'} optimizations`;
      }
      return `Q ${(c.q_before as number)?.toFixed(3) ?? '?'}→${(c.q_after as number)?.toFixed(3) ?? '?'}`;
    }
    if (e.op === 'split') {
      if (e.decision === 'spectral_evaluation') {
        const k = typeof c.best_k === 'number' ? `k=${c.best_k}` : '';
        const sil = typeof c.best_silhouette === 'number' ? `sil=${c.best_silhouette.toFixed(3)}` : '';
        const accepted = c.accepted ? 'accepted' : c.fallback_to_hdbscan ? '→ hdbscan' : 'rejected';
        return `${k} ${sil} ${accepted}`.trim();
      }
      if (typeof c.algorithm === 'string') {
        const algo = `[${c.algorithm}]`;
        const clusters = typeof c.clusters_found === 'number'
          ? `${c.clusters_found} sub-clusters`
          : typeof c.hdbscan_clusters === 'number'
            ? `${c.hdbscan_clusters} sub-clusters`
            : '';
        return `${clusters} ${algo}`.trim();
      }
      return typeof c.hdbscan_clusters === 'number' ? `${c.hdbscan_clusters} sub-clusters` : '';
    }
    if (e.op === 'candidate') {
      if (e.decision === 'candidate_created') {
        const label = typeof c.child_label === 'string' ? c.child_label : '';
        const algo = typeof c.split_algorithm === 'string' ? `[${c.split_algorithm}]` : '';
        return `${label} ${algo}`.trim();
      }
      if (e.decision === 'candidate_promoted') {
        const label = typeof c.cluster_label === 'string' ? c.cluster_label : '';
        const coh = typeof c.coherence === 'number' ? `coh=${c.coherence.toFixed(3)}` : '';
        return `${label} ${coh}`.trim();
      }
      if (e.decision === 'candidate_rejected') {
        const label = typeof c.cluster_label === 'string' ? c.cluster_label : '';
        const coh = typeof c.coherence === 'number' ? `coh=${c.coherence.toFixed(3)}` : '';
        const count = typeof c.member_count === 'number' ? `${c.member_count}m` : '';
        return `${label} ${coh} ${count}`.trim();
      }
      if (e.decision === 'split_fully_reversed') {
        const label = typeof c.parent_label === 'string' ? c.parent_label : '';
        const n = typeof c.candidates_rejected === 'number' ? `${c.candidates_rejected} rejected` : '';
        return `${label} ${n}`.trim();
      }
      return '';
    }
    if (e.op === 'merge') {
      const sim = typeof c.similarity === 'number' ? `sim=${c.similarity.toFixed(3)}` : '';
      const gate = typeof c.gate === 'string' ? ` [${c.gate}]` : '';
      return sim + gate;
    }
    if (e.op === 'score') {
      const overall = typeof c.overall === 'number' ? c.overall.toFixed(1) : '?';
      const label = typeof c.intent_label === 'string' ? c.intent_label : '';
      const divs = Array.isArray(c.divergence) && c.divergence.length > 0 ? ` [${c.divergence.join(',')}]` : '';
      return `${overall} ${label}${divs}`;
    }
    if (e.op === 'extract') {
      return typeof c.meta_patterns_added === 'number' ? `${c.meta_patterns_added} patterns` : '';
    }
    if (e.op === 'retire') {
      if (e.decision === 'dissolved') {
        const label = typeof c.cluster_label === 'string' ? c.cluster_label : '';
        const coh = typeof c.coherence === 'number' ? `coh=${c.coherence.toFixed(3)}` : '';
        const mc = typeof c.member_count === 'number' ? `${c.member_count}m` : '';
        return `${label} ${coh} ${mc} → reassigned`.trim();
      }
      return typeof c.sibling_label === 'string' ? `→ ${c.sibling_label}` : '';
    }
    if (e.op === 'discover') {
      return typeof c.domain_label === 'string' ? c.domain_label : '';
    }
    if (e.op === 'error') {
      return typeof c.error_message === 'string' ? c.error_message.slice(0, 60) : '';
    }
    if (e.op === 'emerge') {
      return typeof c.domain === 'string' ? c.domain : '';
    }
    if (e.op === 'reconcile') {
      return typeof c.count === 'number' ? `${c.count} zombies` : '';
    }
    if (e.op === 'seed') {
      if (e.decision === 'seed_agents_complete') {
        return `${c.prompts_after_dedup ?? c.prompts_generated ?? '?'} prompts`;
      }
      if (e.decision === 'seed_prompt_scored') {
        const score = typeof c.overall_score === 'number' ? c.overall_score.toFixed(1) : '?';
        return `${score} ${c.intent_label ?? c.task_type ?? ''}`;
      }
      if (e.decision === 'seed_prompt_failed') {
        return typeof c.error === 'string' ? c.error.slice(0, 40) : 'failed';
      }
      if (e.decision === 'seed_completed') {
        return `${c.prompts_optimized ?? '?'} done, ${c.clusters_created ?? 0} clusters`;
      }
      if (e.decision === 'seed_failed') {
        return typeof c.error_message === 'string' ? c.error_message.slice(0, 40) : 'failed';
      }
      if (e.decision === 'seed_persist_complete') {
        return `${c.rows_inserted ?? '?'} rows`;
      }
      if (e.decision === 'seed_taxonomy_complete') {
        const domains = Array.isArray(c.domains_touched) ? c.domains_touched.length : 0;
        return `${c.clusters_created ?? 0} clusters, ${domains} domains`;
      }
      return '';
    }
    return '';
  }

  function severityLevel(e: TaxonomyActivityEvent): 'error' | 'info' | 'normal' {
    if (e.op === 'error' || e.decision === 'rejected' || e.decision === 'failed'
        || e.decision === 'seed_failed' || e.decision === 'candidate_rejected') return 'error';
    const c = decisionColor(e);
    if (c === 'var(--color-text-secondary)' || c === 'var(--color-text-dim)') return 'info';
    return 'normal';
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
    clustersStore.selectCluster(id);
  }

  // -- Score event → load optimization in main editor --

  function loadOptimization(traceId: string | null): void {
    if (!traceId) return;
    window.dispatchEvent(new CustomEvent('load-optimization', { detail: { trace_id: traceId } }));
  }

  // -- Initial seed --

  async function loadInitial(): Promise<void> {
    try {
      await clustersStore.loadActivity();
    } catch {
      // non-fatal
    }
  }

  // Auto-scroll to top (newest) when new events arrive
  $effect(() => {
    // Track event count to trigger on new pushes
    const _count = clustersStore.activityEvents.length;
    if (pinToBottom && scrollEl) {
      requestAnimationFrame(() => {
        scrollEl.scrollTop = 0; // newest at top
      });
    }
  });

  onMount(() => {
    loadInitial();
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
      {#each ['assign','extract','score','seed','split','candidate','merge','retire','phase','refit','emerge','discover','reconcile','refresh','error'] as opVal}
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
    {#each filtered as ev, i (ev.ts + ev.op + ev.decision + (ev.cluster_id ?? '') + i)}
      {@const severity = severityLevel(ev)}
      <div class="ap-row" class:ap-row--error={severity === 'error'} class:ap-row--info={severity === 'info'} style="--row-path-color: {pathColor(ev.path)}">
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
          {#if ev.op === 'score' && ev.optimization_id}
            <!-- svelte-ignore a11y_click_events_have_key_events -->
            <!-- svelte-ignore a11y_no_static_element_interactions -->
            <span
              class="ap-cluster-link"
              onclick={(e) => { e.stopPropagation(); loadOptimization(ev.optimization_id); }}
              title="View optimization"
            >↗</span>
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
  /* ══ Mission Control Terminal ══ */

  .ap-panel {
    display: flex;
    flex-direction: column;
    height: 100%;
    background: color-mix(in srgb, var(--color-bg-primary) 80%, transparent);
    backdrop-filter: blur(4px);
    -webkit-backdrop-filter: blur(4px);
    border-top: 1px solid color-mix(in srgb, var(--color-neon-purple) 15%, transparent);
    font-family: var(--font-mono);
    overflow: hidden;
  }

  /* ── Header ── */

  .ap-header {
    display: flex;
    align-items: center;
    gap: 6px;
    padding: 5px 8px;
    border-bottom: 1px solid color-mix(in srgb, var(--color-border-subtle) 60%, transparent);
    flex-shrink: 0;
    position: relative;
  }

  .ap-header::after {
    content: '';
    position: absolute;
    bottom: 0;
    left: 0;
    right: 0;
    height: 1px;
    background: color-mix(in srgb, var(--color-neon-purple) 20%, transparent);
  }

  .ap-title {
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.1em;
    color: var(--color-text-secondary);
    font-family: var(--font-display);
  }

  .ap-count {
    font-size: 9px;
    font-family: var(--font-mono);
    color: var(--color-text-dim);
    opacity: 0.7;
    padding: 1px 4px;
    border: 1px solid color-mix(in srgb, var(--color-border-subtle) 50%, transparent);
  }

  .ap-header-spacer { flex: 1; }

  .ap-pin {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 20px;
    height: 18px;
    padding: 0;
    background: transparent;
    border: 1px solid color-mix(in srgb, var(--color-border-subtle) 30%, transparent);
    color: var(--color-text-dim);
    font-size: 10px;
    cursor: pointer;
    line-height: 1;
    transition: border-color 150ms cubic-bezier(0.16, 1, 0.3, 1),
                color 150ms cubic-bezier(0.16, 1, 0.3, 1),
                background 150ms cubic-bezier(0.16, 1, 0.3, 1);
  }

  .ap-pin:hover {
    border-color: color-mix(in srgb, var(--color-neon-cyan) 40%, transparent);
    color: var(--color-text-secondary);
  }

  .ap-pin.ap-pin-active {
    border-color: color-mix(in srgb, var(--color-neon-cyan) 50%, transparent);
    color: var(--color-neon-cyan);
    background: color-mix(in srgb, var(--color-neon-cyan) 6%, transparent);
  }

  /* ── Filters ── */

  .ap-filters {
    padding: 4px 8px;
    border-bottom: 1px solid color-mix(in srgb, var(--color-border-subtle) 60%, transparent);
    flex-shrink: 0;
    display: flex;
    flex-direction: column;
    gap: 3px;
  }

  /* Path filter chips — prominent with colored dots */
  .ap-chip-row {
    display: flex;
    align-items: center;
    gap: 2px;
  }

  .ap-chip-row > .ap-chip {
    display: flex;
    align-items: center;
    gap: 3px;
    padding: 1px 6px;
    background: transparent;
    border: 1px solid color-mix(in srgb, var(--color-border-subtle) 40%, transparent);
    color: var(--color-text-dim);
    font-family: var(--font-mono);
    font-size: 9px;
    font-weight: 600;
    cursor: pointer;
    transition: border-color 150ms cubic-bezier(0.16, 1, 0.3, 1),
                color 150ms cubic-bezier(0.16, 1, 0.3, 1),
                background 150ms cubic-bezier(0.16, 1, 0.3, 1);
  }

  .ap-chip-row > .ap-chip::before {
    content: '';
    width: 4px;
    height: 4px;
    flex-shrink: 0;
    background: var(--chip-color, var(--color-text-dim));
    opacity: 0.4;
    transition: opacity 150ms cubic-bezier(0.16, 1, 0.3, 1);
  }

  .ap-chip-row > .ap-chip:hover {
    border-color: color-mix(in srgb, var(--chip-color, var(--color-neon-cyan)) 40%, transparent);
    color: var(--color-text-secondary);
  }

  .ap-chip-row > .ap-chip:hover::before { opacity: 0.7; }

  .ap-chip-row > .ap-chip.ap-chip-active {
    border-color: color-mix(in srgb, var(--chip-color, var(--color-neon-cyan)) 50%, transparent);
    color: var(--chip-color, var(--color-neon-cyan));
    background: color-mix(in srgb, var(--chip-color, var(--color-neon-cyan)) 8%, transparent);
  }

  .ap-chip-row > .ap-chip.ap-chip-active::before { opacity: 1; }

  .ap-chip-sep {
    width: 1px;
    height: 12px;
    background: color-mix(in srgb, var(--color-text-dim) 20%, transparent);
    margin: 0 3px;
  }

  /* Op filter chips — smaller, dimmer, subordinate */
  .ap-filter-row {
    display: flex;
    align-items: center;
    gap: 2px;
    flex-wrap: wrap;
    margin-top: 1px;
  }

  .ap-filter-row > .ap-chip {
    padding: 0px 4px;
    background: transparent;
    border: 1px solid color-mix(in srgb, var(--color-border-subtle) 25%, transparent);
    color: var(--color-text-dim);
    font-family: var(--font-mono);
    font-size: 8px;
    cursor: pointer;
    opacity: 0.55;
    transition: border-color 150ms cubic-bezier(0.16, 1, 0.3, 1),
                color 150ms cubic-bezier(0.16, 1, 0.3, 1),
                opacity 150ms cubic-bezier(0.16, 1, 0.3, 1),
                background 150ms cubic-bezier(0.16, 1, 0.3, 1);
  }

  .ap-filter-row > .ap-chip:hover {
    opacity: 1;
    border-color: color-mix(in srgb, var(--color-neon-cyan) 30%, transparent);
    color: var(--color-text-secondary);
  }

  .ap-filter-row > .ap-chip.ap-chip-active {
    opacity: 1;
    border-color: color-mix(in srgb, var(--color-neon-cyan) 50%, transparent);
    color: var(--color-neon-cyan);
    background: color-mix(in srgb, var(--color-neon-cyan) 6%, transparent);
  }

  /* ── Event list ── */

  .ap-list {
    flex: 1;
    overflow-y: auto;
    overflow-x: hidden;
  }

  .ap-list::-webkit-scrollbar { width: 3px; }
  .ap-list::-webkit-scrollbar-track { background: transparent; }
  .ap-list::-webkit-scrollbar-thumb {
    background: color-mix(in srgb, var(--color-neon-purple) 20%, transparent);
  }

  /* ── Event rows — severity-driven visual weight ── */

  .ap-row {
    border-bottom: 1px solid color-mix(in srgb, var(--color-border-subtle) 25%, transparent);
    border-left: 2px solid var(--row-path-color, transparent);
  }

  .ap-row--info { opacity: 0.5; }
  .ap-row--info:hover { opacity: 0.85; }

  .ap-row--error {
    background: color-mix(in srgb, var(--color-neon-red) 3%, transparent);
  }

  .ap-row-summary {
    display: flex;
    align-items: center;
    gap: 5px;
    width: 100%;
    padding: 3px 8px 3px 6px;
    background: transparent;
    border: none;
    cursor: pointer;
    text-align: left;
    font-family: var(--font-mono);
    font-size: 9px;
    color: var(--color-text-secondary);
    transition: background 100ms cubic-bezier(0.16, 1, 0.3, 1);
  }

  .ap-row-summary:hover {
    background: color-mix(in srgb, var(--color-bg-hover) 60%, transparent);
  }

  .ap-ts {
    color: var(--color-text-dim);
    flex-shrink: 0;
    font-size: 8px;
    min-width: 56px;
    opacity: 0.5;
  }

  .ap-row-summary:hover .ap-ts { opacity: 0.8; }

  .ap-badge {
    padding: 0 3px;
    font-size: 8px;
    flex-shrink: 0;
    font-family: var(--font-mono);
  }

  .ap-badge-path {
    min-width: 24px;
    text-align: center;
    border: 1px solid currentColor;
    opacity: 0.6;
  }

  .ap-badge-op {
    color: var(--color-text-dim);
    border: 1px solid color-mix(in srgb, var(--color-border-subtle) 50%, transparent);
    opacity: 0.5;
  }

  .ap-badge-decision {
    font-weight: 700;
    font-size: 9px;
    border: none;
    padding: 0 1px;
    opacity: 1;
    max-width: 140px;
    min-width: 0;
    flex-shrink: 1;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .ap-cluster-link {
    font-family: var(--font-mono);
    font-size: 8px;
    color: var(--color-neon-cyan);
    opacity: 0;
    cursor: pointer;
    text-decoration: underline dotted;
    flex-shrink: 0;
    transition: opacity 150ms cubic-bezier(0.16, 1, 0.3, 1);
  }

  .ap-row-summary:hover .ap-cluster-link { opacity: 0.6; }
  .ap-cluster-link:hover { opacity: 1 !important; }

  .ap-metric {
    color: var(--color-text-dim);
    font-size: 8px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    flex: 1;
    min-width: 0;
    opacity: 0.7;
  }

  /* ── Expanded context — data card ── */

  .ap-context {
    padding: 6px 8px 6px 14px;
    background: color-mix(in srgb, var(--color-bg-card) 70%, transparent);
    border-top: 1px solid color-mix(in srgb, var(--color-border-subtle) 30%, transparent);
    border-left: 2px solid var(--row-path-color, var(--color-border-subtle));
    display: flex;
    flex-direction: column;
    gap: 1px;
    animation: ctx-enter 200ms cubic-bezier(0.16, 1, 0.3, 1);
  }

  @keyframes ctx-enter {
    from { opacity: 0; transform: translateY(-4px); }
    to { opacity: 1; transform: translateY(0); }
  }

  .ap-ctx-row {
    display: flex;
    gap: 8px;
    font-size: 8px;
    padding: 1px 0;
    border-bottom: 1px solid color-mix(in srgb, var(--color-border-subtle) 15%, transparent);
  }

  .ap-ctx-row:last-child { border-bottom: none; }

  .ap-ctx-key {
    color: var(--color-text-dim);
    flex-shrink: 0;
    min-width: 100px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    font-size: 7.5px;
  }

  .ap-ctx-val {
    color: var(--color-text-secondary);
    word-break: break-all;
    white-space: pre-wrap;
    font-size: 8px;
  }

  /* ── Empty state ── */

  .ap-empty {
    padding: 16px 8px;
    font-size: 9px;
    color: var(--color-text-dim);
    opacity: 0.5;
    text-align: center;
    font-family: var(--font-display);
    letter-spacing: 0.05em;
  }
</style>
