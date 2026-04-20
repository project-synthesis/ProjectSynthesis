<script lang="ts">
  /**
   * StateFilterTabs — tablist of cluster-state filters with a sliding
   * 1px indicator beneath the active tab.
   *
   * Extracted from ClusterNavigator for separation of concerns. The
   * indicator is GPU-accelerated (transform: translateX instead of
   * animating `left`) and brand-compliant (1px contour, zero glow).
   */
  import { tick } from 'svelte';
  import type { StateFilter } from '$lib/stores/clusters.svelte';
  import { stateColor } from '$lib/utils/colors';
  import { tooltip } from '$lib/actions/tooltip';
  import { handleTablistArrowKeys } from '$lib/utils/keyboard';

  interface Props {
    stateFilter: StateFilter;
    candidateCount: number;
    onChange: (filter: StateFilter) => void;
  }

  let { stateFilter, candidateCount, onChange }: Props = $props();

  const STATE_TABS: { filter: StateFilter; label: string; ariaLabel: string }[] = [
    { filter: null,        label: 'all', ariaLabel: 'All' },
    { filter: 'active',    label: 'act', ariaLabel: 'active' },
    { filter: 'candidate', label: 'can', ariaLabel: 'candidate' },
    { filter: 'mature',    label: 'mat', ariaLabel: 'mature' },
    { filter: 'archived',  label: 'arc', ariaLabel: 'archived' },
  ];

  let stateTabsEl = $state<HTMLDivElement | null>(null);
  let indicatorLeft = $state(0);
  let indicatorWidth = $state(0);

  function updateStateTabIndicator() {
    if (!stateTabsEl) return;
    const active = stateTabsEl.querySelector<HTMLButtonElement>('.state-tab--active');
    if (!active) {
      indicatorWidth = 0;
      return;
    }
    const containerBox = stateTabsEl.getBoundingClientRect();
    const activeBox = active.getBoundingClientRect();
    indicatorLeft = activeBox.left - containerBox.left;
    indicatorWidth = activeBox.width;
  }

  $effect(() => {
    void stateFilter;
    void stateTabsEl;
    void tick().then(updateStateTabIndicator);
  });

  function handleKeydown(event: KeyboardEvent) {
    const filters = STATE_TABS.map((t) => t.filter);
    handleTablistArrowKeys(
      event,
      { items: filters, current: stateFilter, orientation: 'horizontal' },
      (next) => onChange(next),
    );
  }
</script>

<div
  class="state-tabs"
  role="tablist"
  aria-label="Filter by cluster state"
  aria-orientation="horizontal"
  tabindex={-1}
  bind:this={stateTabsEl}
  onkeydown={handleKeydown}
>
  {#each STATE_TABS as tab (tab.ariaLabel)}
    <button
      class="state-tab"
      class:state-tab--active={stateFilter === tab.filter}
      onclick={() => onChange(tab.filter)}
      role="tab"
      aria-selected={stateFilter === tab.filter}
      aria-label={tab.ariaLabel}
      tabindex={stateFilter === tab.filter ? 0 : -1}
      use:tooltip={tab.ariaLabel}
      style="--tab-state-color: {tab.filter ? stateColor(tab.filter) : 'var(--color-text-dim)'};"
    >{tab.label}{#if tab.filter === 'candidate' && candidateCount > 0}<span class="cn-tab-badge">{candidateCount}</span>{/if}</button>
  {/each}
  <span
    class="state-tabs-indicator"
    aria-hidden="true"
    style="transform: translateX({indicatorLeft}px); width: {indicatorWidth}px; --tab-state-color: {stateFilter ? stateColor(stateFilter) : 'var(--color-neon-cyan)'};"
  ></span>
</div>

<style>
  .state-tabs {
    display: flex;
    align-items: stretch;
    height: 24px;
    border-bottom: 1px solid var(--color-border-subtle);
    flex-shrink: 0;
    position: relative;
  }

  .state-tab {
    flex: 1 1 0%;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    height: 100%;
    padding: 0;
    border: none;
    background: transparent;
    color: var(--color-text-dim);
    font-size: 10px;
    font-weight: 600;
    font-family: var(--font-mono);
    cursor: pointer;
    text-transform: uppercase;
    letter-spacing: 0.03em;
    transition: color var(--duration-micro) var(--ease-spring),
                background var(--duration-micro) var(--ease-spring);
  }

  .state-tab:hover {
    color: var(--color-text-primary);
    background: color-mix(in srgb, var(--color-bg-hover) 50%, transparent);
  }

  .state-tab--active {
    color: var(--tab-state-color, var(--color-neon-cyan));
  }

  .state-tabs-indicator {
    position: absolute;
    left: 0;
    bottom: -1px;
    height: 1px;
    background: var(--tab-state-color, var(--color-neon-cyan));
    pointer-events: none;
    will-change: transform, width;
    transition: transform var(--duration-micro) var(--ease-spring),
                width var(--duration-micro) var(--ease-spring),
                background var(--duration-micro) var(--ease-spring);
  }

  .state-tab:focus-visible {
    outline-offset: -1px;
  }

  .cn-tab-badge {
    font-family: var(--font-mono);
    font-size: 9px;
    font-weight: 700;
    color: var(--tab-state-color, var(--color-text-dim));
    margin-left: 2px;
  }
</style>
