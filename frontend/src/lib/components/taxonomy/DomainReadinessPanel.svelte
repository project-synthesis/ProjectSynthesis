<script lang="ts">
  /**
   * DomainReadinessPanel — global sidebar view listing every top-level
   * domain ordered by proximity to a lifecycle event (dissolution or
   * emergence). Click-through dispatches a `domain:select` CustomEvent with
   * the selected `domain_id` so parent views can focus the topology.
   *
   * Dense 20px-row layout per brand spec. Zero glow/shadow — chromatic tier
   * dots + 1px contour accents.
   */
  import { onMount } from 'svelte';
  import type { DomainReadinessReport } from '$lib/api/readiness';
  import { readinessStore } from '$lib/stores/readiness.svelte';
  import { tooltip } from '$lib/actions/tooltip';

  interface Props {
    /** When provided, notifies parent of domain selection. */
    onSelect?: (domainId: string) => void;
    /** Hide the internal header (when wrapper supplies its own). */
    hideHeader?: boolean;
  }

  let { onSelect, hideHeader = false }: Props = $props();

  let rootEl: HTMLDivElement | undefined = $state();

  onMount(() => {
    if (!readinessStore.isFresh) {
      void readinessStore.loadAll();
    }
  });

  /**
   * Sort by lifecycle proximity:
   *   critical stability first, then guarded,
   *   within each stability tier, smallest emergence gap wins,
   *   ready emergence (negative gap) sorts ahead.
   * Domains with zero optimizations are pushed to the bottom — no signal to rank.
   */
  const sorted = $derived.by(() => {
    const stabilityWeight = (t: DomainReadinessReport['stability']['tier']) =>
      t === 'critical' ? 0 : t === 'guarded' ? 1 : 2;
    const emergenceWeight = (t: DomainReadinessReport['emergence']['tier']) =>
      t === 'ready' ? 0 : t === 'warming' ? 1 : 2;
    return [...readinessStore.reports].sort((a, b) => {
      // Empty domains last — no activity to rank on.
      const aEmpty = a.emergence.total_opts === 0 ? 1 : 0;
      const bEmpty = b.emergence.total_opts === 0 ? 1 : 0;
      if (aEmpty !== bEmpty) return aEmpty - bEmpty;
      const sw = stabilityWeight(a.stability.tier) - stabilityWeight(b.stability.tier);
      if (sw !== 0) return sw;
      const ew = emergenceWeight(a.emergence.tier) - emergenceWeight(b.emergence.tier);
      if (ew !== 0) return ew;
      const ga = a.emergence.gap_to_threshold ?? Number.POSITIVE_INFINITY;
      const gb = b.emergence.gap_to_threshold ?? Number.POSITIVE_INFINITY;
      return ga - gb;
    });
  });

  /**
   * Disambiguate duplicate labels (e.g. two "general" domains across projects)
   * by appending a short id suffix to every occurrence after the first.
   */
  const labelCounts = $derived.by(() => {
    const counts = new Map<string, number>();
    for (const r of readinessStore.reports) {
      counts.set(r.domain_label, (counts.get(r.domain_label) ?? 0) + 1);
    }
    return counts;
  });

  function displayLabel(r: DomainReadinessReport): string {
    const count = labelCounts.get(r.domain_label) ?? 1;
    if (count <= 1) return r.domain_label;
    return `${r.domain_label} · ${r.domain_id.slice(0, 4)}`;
  }

  function stabilityColor(tier: DomainReadinessReport['stability']['tier']): string {
    switch (tier) {
      case 'healthy':
        return 'var(--color-neon-green)';
      case 'guarded':
        return 'var(--color-neon-yellow)';
      case 'critical':
        return 'var(--color-neon-red)';
    }
  }

  function emergenceColor(tier: DomainReadinessReport['emergence']['tier']): string {
    switch (tier) {
      case 'ready':
        return 'var(--color-neon-green)';
      case 'warming':
        return 'var(--color-neon-cyan)';
      case 'inert':
        return 'var(--color-text-dim)';
    }
  }

  function emergenceBadge(tier: DomainReadinessReport['emergence']['tier']): string {
    switch (tier) {
      case 'ready':
        return 'RDY';
      case 'warming':
        return 'WRM';
      case 'inert':
        return '—';
    }
  }

  /**
   * Render the emergence gap. Positive values (ready) show as `+N` in the
   * emergence tier colour so the eye immediately picks out promotion
   * candidates; inert/warming show the shortfall as `-N`.
   */
  function formatGap(gap: number | null): string {
    if (gap == null) return '—';
    const pts = gap * 100;
    // Negative gap in backend = consistency OVER threshold = ready.
    if (pts <= 0) return `+${(-pts).toFixed(1)}`;
    return `-${pts.toFixed(1)}`;
  }

  function select(report: DomainReadinessReport) {
    onSelect?.(report.domain_id);
    rootEl?.dispatchEvent(
      new CustomEvent('domain:select', {
        detail: { domainId: report.domain_id, label: report.domain_label },
        bubbles: true,
      }),
    );
  }

  function onRefresh() {
    void readinessStore.loadAll(true);
  }
</script>

<div class="drp" bind:this={rootEl}>
  {#if !hideHeader}
    <div class="drp-header">
      <span class="drp-title">DOMAIN READINESS</span>
      <button
        type="button"
        class="drp-refresh"
        onclick={onRefresh}
        disabled={readinessStore.loading}
        use:tooltip={'Force a fresh recomputation (bypasses 30s backend cache).'}
        aria-label="Refresh readiness"
      >
        {readinessStore.loading ? '···' : 'SYNC'}
      </button>
    </div>
  {/if}

  {#if readinessStore.lastError && !readinessStore.loaded}
    <div class="drp-empty">Unable to load readiness.</div>
  {:else if !readinessStore.loaded && readinessStore.loading}
    <div class="drp-empty">Loading…</div>
  {:else if sorted.length === 0}
    <div class="drp-empty">No top-level domains yet.</div>
  {:else}
    <div class="drp-columns" aria-hidden="true">
      <span>DOMAIN</span>
      <span class="drp-col-num">CONS</span>
      <span class="drp-col-num">EMRG</span>
      <span class="drp-col-num">GAP</span>
      <span class="drp-col-num">M</span>
    </div>
    <div class="drp-list">
      {#each sorted as r (r.domain_id)}
        {@const consistencyPct = Math.round(r.stability.consistency * 100)}
        {@const thresholdPct = Math.round(r.emergence.threshold * 100)}
        {@const isEmpty = r.emergence.total_opts === 0}
        <button
          type="button"
          class="drp-row"
          class:drp-row--empty={isEmpty}
          onclick={() => select(r)}
          use:tooltip={`${r.domain_label} — stability ${r.stability.tier} (${consistencyPct}%), emergence ${r.emergence.tier} (threshold ${thresholdPct}%)`}
        >
          <span class="drp-cell drp-name">
            <span
              class="drp-name-rail"
              style="background: {stabilityColor(r.stability.tier)}"
              aria-hidden="true"
            ></span>
            <span class="drp-name-text">{displayLabel(r)}</span>
          </span>
          <span
            class="drp-cell drp-num"
            style="color: {stabilityColor(r.stability.tier)}"
            aria-label="Consistency {consistencyPct}%"
          >{consistencyPct}%</span>
          <span
            class="drp-cell drp-badge"
            style="color: {emergenceColor(r.emergence.tier)}; border-color: color-mix(in srgb, {emergenceColor(r.emergence.tier)} 40%, transparent);"
            aria-label="Emergence {r.emergence.tier}"
          >{emergenceBadge(r.emergence.tier)}</span>
          <span
            class="drp-cell drp-num drp-gap"
            style="color: {emergenceColor(r.emergence.tier)}"
          >{formatGap(r.emergence.gap_to_threshold)}</span>
          <span class="drp-cell drp-num drp-members">{r.member_count}</span>
        </button>
      {/each}
    </div>
  {/if}
</div>

<style>
  .drp {
    display: flex;
    flex-direction: column;
    padding: 2px 0 4px;
    gap: 4px;
  }

  .drp-header {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    height: 20px;
    padding: 0 6px;
  }

  .drp-title {
    font-family: var(--font-display);
    font-size: 11px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    color: var(--color-text-primary);
  }

  .drp-refresh {
    font-family: var(--font-mono);
    font-size: 9px;
    letter-spacing: 0.05em;
    padding: 0 6px;
    height: 16px;
    background: transparent;
    border: 1px solid var(--color-border-subtle);
    color: var(--color-text-secondary);
    cursor: pointer;
    transition: color 150ms cubic-bezier(0.16, 1, 0.3, 1),
      border-color 150ms cubic-bezier(0.16, 1, 0.3, 1);
  }

  .drp-refresh:hover:not(:disabled) {
    color: var(--color-neon-cyan);
    border-color: color-mix(in srgb, var(--color-neon-cyan) 40%, transparent);
  }

  .drp-refresh:disabled {
    opacity: 0.4;
    cursor: not-allowed;
  }

  .drp-empty {
    font-family: var(--font-sans);
    font-size: 10px;
    color: var(--color-text-dim);
    padding: 6px;
  }

  .drp-columns {
    display: grid;
    grid-template-columns: 1fr 36px 28px 40px 22px;
    gap: 6px;
    align-items: baseline;
    font-family: var(--font-mono);
    font-size: 8px;
    color: var(--color-text-dim);
    letter-spacing: 0.05em;
    padding: 0 6px 2px;
    border-bottom: 1px solid var(--color-border-subtle);
  }

  .drp-col-num {
    text-align: right;
  }

  .drp-list {
    display: flex;
    flex-direction: column;
  }

  .drp-row {
    all: unset;
    display: grid;
    grid-template-columns: 1fr 36px 28px 40px 22px;
    gap: 6px;
    align-items: center;
    height: 20px;
    padding: 0 6px;
    cursor: pointer;
    border-bottom: 1px solid color-mix(in srgb, var(--color-border-subtle) 50%, transparent);
    transition: background-color 150ms cubic-bezier(0.16, 1, 0.3, 1);
    box-sizing: border-box;
  }

  .drp-row:hover {
    background: var(--color-bg-hover);
  }

  .drp-row:focus-visible {
    outline: 1px solid color-mix(in srgb, var(--color-neon-cyan) 30%, transparent);
    outline-offset: -1px;
  }

  .drp-row--empty {
    opacity: 0.45;
  }

  .drp-cell {
    display: flex;
    align-items: center;
    min-width: 0;
  }

  .drp-name {
    gap: 5px;
    overflow: hidden;
  }

  .drp-name-rail {
    width: 2px;
    height: 10px;
    flex-shrink: 0;
  }

  .drp-name-text {
    font-family: var(--font-sans);
    font-size: 11px;
    color: var(--color-text-primary);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .drp-num {
    font-family: var(--font-mono);
    font-size: 10px;
    color: var(--color-text-secondary);
    justify-content: flex-end;
  }

  .drp-badge {
    font-family: var(--font-mono);
    font-size: 9px;
    font-weight: 500;
    letter-spacing: 0.05em;
    justify-content: center;
    padding: 0 2px;
    height: 12px;
    border: 1px solid transparent;
    box-sizing: border-box;
  }

  .drp-members {
    color: var(--color-text-dim);
    font-size: 9px;
  }

  @media (prefers-reduced-motion: reduce) {
    .drp-refresh,
    .drp-row {
      transition: none;
    }
  }
</style>
