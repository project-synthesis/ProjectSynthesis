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
  import { preferencesStore } from '$lib/stores/preferences.svelte';
  import { tooltip } from '$lib/actions/tooltip';
  import {
    stabilityTierVar,
    emergenceTierVar,
    emergenceTierBadge,
  } from './readiness-tier';

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

  function onRowKey(event: KeyboardEvent, report: DomainReadinessReport) {
    // Space scrolls the page by default on a div[role="button"]; suppress it
    // so activation matches <button> semantics. Enter has no scroll side
    // effect, so leave it alone per WAI-ARIA authoring guidance.
    //
    // A11y: skip when the keydown originated inside a nested interactive
    // control (e.g. the mute toggle button). Otherwise Space/Enter on the
    // nested button would bubble here and activate the row's `select()` in
    // addition to (or instead of) the button's own handler — two activations
    // from one keypress.
    if (event.target !== event.currentTarget) return;
    if (event.key === ' ') {
      event.preventDefault();
      select(report);
    } else if (event.key === 'Enter') {
      select(report);
    }
  }

  /**
   * O(1) mute lookup. Recomputes reactively whenever the backing array is
   * replaced (optimistic toggles) or mutated in place (external updates via
   * `preferencesStore.prefs.domain_readiness_notifications.muted_domain_ids =
   * [...]`). Reading `.length` inside the derivation ensures the derived
   * remains subscribed even if the reference is reused across writes.
   */
  const mutedSet = $derived.by(() => {
    const ids = preferencesStore.prefs.domain_readiness_notifications.muted_domain_ids;
    void ids.length;
    return new Set(ids);
  });

  function onToggleMute(event: MouseEvent, report: DomainReadinessReport) {
    event.stopPropagation();
    void preferencesStore.toggleDomainMute(report.domain_id);
  }

  /** Accessible name for the row as a whole (the role="button" container). */
  function rowAriaLabel(r: DomainReadinessReport, muted: boolean): string {
    const cons = Math.round(r.stability.consistency * 100);
    const mute = muted ? ', notifications muted' : '';
    return `${r.domain_label} — stability ${r.stability.tier} ${cons}%, emergence ${r.emergence.tier}${mute}`;
  }

  function onRefresh() {
    void readinessStore.loadAll(true);
  }

  /**
   * Master mute state mirrors `domain_readiness_notifications.enabled`.
   * When `enabled=false`, SSE tier-crossing toasts are suppressed globally;
   * per-row mutes still carry their own opt-outs. Separate from per-row so
   * an operator can silence everything briefly (e.g. during a bulk split)
   * without losing their curated per-domain mute list.
   */
  const globalMuted = $derived(
    !preferencesStore.prefs.domain_readiness_notifications.enabled,
  );

  function onToggleGlobalMute() {
    void preferencesStore.update({
      domain_readiness_notifications: { enabled: globalMuted ? true : false },
    });
  }
</script>

<div class="drp" bind:this={rootEl}>
  {#if !hideHeader}
    <div class="drp-header">
      <span class="drp-title">DOMAIN READINESS</span>
      <div class="drp-header-actions">
        <button
          type="button"
          class="drp-master-mute"
          class:drp-master-mute--active={globalMuted}
          aria-pressed={globalMuted ? 'true' : 'false'}
          aria-label={globalMuted
            ? 'Unmute all readiness notifications'
            : 'Mute all readiness notifications'}
          use:tooltip={globalMuted
            ? 'Readiness toasts suppressed globally. Click to re-enable.'
            : 'Mute every readiness tier-crossing toast (per-row mutes preserved).'}
          onclick={onToggleGlobalMute}
        >
          <svg
            xmlns="http://www.w3.org/2000/svg"
            viewBox="0 0 16 16"
            width="11"
            height="11"
            fill="none"
            stroke="currentColor"
            stroke-width="1"
            stroke-linecap="round"
            stroke-linejoin="round"
            aria-hidden="true"
          >
            <path d="M4 6a4 4 0 0 1 8 0v3l1 2H3l1-2V6z" />
            <path d="M6.5 13a1.5 1.5 0 0 0 3 0" />
            {#if globalMuted}
              <line x1="2.5" y1="2.5" x2="13.5" y2="13.5" />
            {/if}
          </svg>
        </button>
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
      <span></span>
    </div>
    <div class="drp-list">
      {#each sorted as r (r.domain_id)}
        {@const consistencyPct = Math.round(r.stability.consistency * 100)}
        {@const thresholdPct = Math.round(r.emergence.threshold * 100)}
        {@const isEmpty = r.emergence.total_opts === 0}
        {@const muted = mutedSet.has(r.domain_id)}
        <div
          role="button"
          tabindex="0"
          class="drp-row"
          class:drp-row--empty={isEmpty}
          aria-label={rowAriaLabel(r, muted)}
          onclick={() => select(r)}
          onkeydown={(e) => onRowKey(e, r)}
          use:tooltip={`${r.domain_label} — stability ${r.stability.tier} (${consistencyPct}%), emergence ${r.emergence.tier} (threshold ${thresholdPct}%)`}
        >
          <span class="drp-cell drp-name">
            <span
              class="drp-name-rail"
              style="background: {stabilityTierVar(r.stability.tier)}"
              aria-hidden="true"
            ></span>
            <span class="drp-name-text">{displayLabel(r)}</span>
          </span>
          <span
            class="drp-cell drp-num"
            style="color: {stabilityTierVar(r.stability.tier)}"
            aria-label="Consistency {consistencyPct}%"
          >{consistencyPct}%</span>
          <span
            class="drp-cell drp-badge"
            style="color: {emergenceTierVar(r.emergence.tier)}; border-color: color-mix(in srgb, {emergenceTierVar(r.emergence.tier)} 40%, transparent);"
            aria-label="Emergence {r.emergence.tier}"
          >{emergenceTierBadge(r.emergence.tier)}</span>
          <span
            class="drp-cell drp-num drp-gap"
            style="color: {emergenceTierVar(r.emergence.tier)}"
          >{formatGap(r.emergence.gap_to_threshold)}</span>
          <span class="drp-cell drp-num drp-members">{r.member_count}</span>
          <button
            type="button"
            class="drp-mute"
            class:drp-mute--active={muted}
            aria-pressed={muted ? 'true' : 'false'}
            aria-label={muted
              ? `Unmute notifications for ${r.domain_label}`
              : `Mute notifications for ${r.domain_label}`}
            onclick={(e) => onToggleMute(e, r)}
          >
            <!--
              Inline 1px-stroke bell. `stroke="currentColor"` means the glyph
              inherits the button's text color — so the muted-active yellow
              and the hover cyan come free without duplicating a color table.
              When muted we overlay a diagonal slash (bell-off), matching the
              lucide bell/bell-off convention used elsewhere in the app.
            -->
            <svg
              xmlns="http://www.w3.org/2000/svg"
              viewBox="0 0 16 16"
              width="10"
              height="10"
              fill="none"
              stroke="currentColor"
              stroke-width="1"
              stroke-linecap="round"
              stroke-linejoin="round"
              aria-hidden="true"
            >
              <path d="M4 6a4 4 0 0 1 8 0v3l1 2H3l1-2V6z" />
              <path d="M6.5 13a1.5 1.5 0 0 0 3 0" />
              {#if muted}
                <line x1="2.5" y1="2.5" x2="13.5" y2="13.5" />
              {/if}
            </svg>
          </button>
        </div>
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

  .drp-header-actions {
    display: inline-flex;
    align-items: center;
    gap: 6px;
  }

  .drp-master-mute {
    all: unset;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 18px;
    height: 16px;
    color: var(--color-text-dim);
    cursor: pointer;
    border: 1px solid var(--color-border-subtle);
    box-sizing: border-box;
    transition: color 150ms cubic-bezier(0.16, 1, 0.3, 1),
      border-color 150ms cubic-bezier(0.16, 1, 0.3, 1);
  }

  .drp-master-mute:hover {
    color: var(--color-neon-cyan);
    border-color: color-mix(in srgb, var(--color-neon-cyan) 40%, transparent);
  }

  .drp-master-mute--active {
    color: var(--color-neon-yellow);
    border-color: color-mix(in srgb, var(--color-neon-yellow) 40%, transparent);
  }

  .drp-master-mute:focus-visible {
    outline: 1px solid color-mix(in srgb, var(--color-neon-cyan) 40%, transparent);
    outline-offset: -1px;
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
    grid-template-columns: 1fr 36px 28px 40px 22px 16px;
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
    grid-template-columns: 1fr 36px 28px 40px 22px 16px;
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

  .drp-mute {
    all: unset;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 16px;
    height: 16px;
    font-size: 9px;
    line-height: 1;
    color: var(--color-text-dim);
    cursor: pointer;
    border: 1px solid transparent;
    box-sizing: border-box;
    transition: color 150ms cubic-bezier(0.16, 1, 0.3, 1),
      border-color 150ms cubic-bezier(0.16, 1, 0.3, 1);
  }

  .drp-mute:hover {
    color: var(--color-neon-cyan);
    border-color: color-mix(in srgb, var(--color-neon-cyan) 40%, transparent);
  }

  .drp-mute--active {
    color: var(--color-neon-yellow);
  }

  .drp-mute:focus-visible {
    outline: 1px solid color-mix(in srgb, var(--color-neon-cyan) 40%, transparent);
    outline-offset: -1px;
  }

  @media (prefers-reduced-motion: reduce) {
    .drp-refresh,
    .drp-row,
    .drp-mute,
    .drp-master-mute {
      transition: none;
    }
  }
</style>
