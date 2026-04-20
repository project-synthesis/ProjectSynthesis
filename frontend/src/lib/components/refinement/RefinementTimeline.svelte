<script lang="ts">
  import { refinementStore } from '$lib/stores/refinement.svelte';
  import RefinementTurnCard from './RefinementTurnCard.svelte';
  import RefinementInput from './RefinementInput.svelte';
  import SuggestionChips from './SuggestionChips.svelte';
  import BranchSwitcher from './BranchSwitcher.svelte';
  import { tick } from 'svelte';

  let scrollContainer: HTMLDivElement | undefined = $state();
  let turnsCollapsed = $state(false);

  // Last 3 turns expanded by default
  let expandedSet = $state<Set<string>>(new Set());

  // Initialize expanded set when turns change
  $effect(() => {
    const turns = refinementStore.turns;
    const newSet = new Set<string>();
    const start = Math.max(0, turns.length - 3);
    for (let i = start; i < turns.length; i++) {
      newSet.add(turns[i].id);
    }
    expandedSet = newSet;
  });

  // Auto-scroll on new turn (skip when turns are collapsed — scrollContainer is unmounted)
  let prevTurnCount = $state(0);
  $effect(() => {
    const count = refinementStore.turns.length;
    if (turnsCollapsed || !scrollContainer) {
      prevTurnCount = count;
      return;
    }
    if (count > prevTurnCount) {
      tick().then(() => {
        scrollContainer?.scrollTo({ top: scrollContainer.scrollHeight, behavior: 'smooth' });
      });
    }
    prevTurnCount = count;
  });

  function toggleExpanded(id: string) {
    const next = new Set(expandedSet);
    if (next.has(id)) {
      next.delete(id);
    } else {
      next.add(id);
    }
    expandedSet = next;
  }

  function handleRefine(text: string) {
    refinementStore.refine(text);
  }

  function handleSuggestion(text: string) {
    refinementStore.refine(text);
  }

  function handleBranchSwitch(id: string) {
    refinementStore.activeBranchId = id;
    if (refinementStore.optimizationId) {
      refinementStore.init(refinementStore.optimizationId);
    }
  }
</script>

<div class="refinement-timeline">
  <!-- Header — only visible when turns exist -->
  {#if refinementStore.turns.length > 0}
    <div class="timeline-header" class:no-bottom-border={turnsCollapsed}>
      <button class="heading-toggle" onclick={() => turnsCollapsed = !turnsCollapsed} aria-expanded={!turnsCollapsed}>
        <span class="toggle-indicator">{turnsCollapsed ? '▸' : '▾'}</span>
        <span class="section-heading">REFINEMENT</span>
      </button>
      {#if !turnsCollapsed && refinementStore.branches.length > 1}
        <BranchSwitcher
          branches={refinementStore.branches}
          activeBranchId={refinementStore.activeBranchId ?? ''}
          onSwitch={handleBranchSwitch}
        />
      {/if}
      {#if refinementStore.status === 'refining'}
        <span class="status-indicator">refining...</span>
      {/if}
    </div>

    {#if !turnsCollapsed}
      <div class="timeline-scroll" bind:this={scrollContainer}>
        <div class="turns-list">
          {#each refinementStore.turns as turn (turn.id)}
            <RefinementTurnCard
              {turn}
              isExpanded={expandedSet.has(turn.id)}
              isSelected={refinementStore.selectedVersion?.id === turn.id}
              onToggle={() => toggleExpanded(turn.id)}
              onSelect={() => refinementStore.selectVersion(turn)}
            />
          {/each}
        </div>
      </div>
    {/if}
  {/if}

  <!-- Input area -->
  <div class="timeline-footer">
    {#if refinementStore.status === 'refining' && refinementStore.turns.length === 0}
      <span class="status-indicator">refining...</span>
    {/if}
    {#if refinementStore.suggestions.length > 0}
      <SuggestionChips
        suggestions={refinementStore.suggestions}
        onSelect={handleSuggestion}
      />
    {/if}
    <RefinementInput
      onSubmit={handleRefine}
      disabled={refinementStore.status === 'refining'}
    />
    {#if refinementStore.error}
      <span class="error-text">{refinementStore.error}</span>
    {/if}
  </div>
</div>

<style>
  .refinement-timeline {
    display: flex;
    flex-direction: column;
    height: 100%;
    overflow: hidden;
    border-top: 1px solid var(--color-border-subtle);
  }

  .timeline-header {
    display: flex;
    align-items: center;
    gap: 6px;
    height: 24px;
    padding: 0 6px;
    background: var(--color-bg-secondary);
    border-bottom: 1px solid var(--color-border-subtle);
    flex-shrink: 0;
  }

  .timeline-header.no-bottom-border {
    border-bottom: none;
  }

  .heading-toggle {
    display: flex;
    align-items: center;
    gap: 4px;
    padding: 0;
    background: transparent;
    border: none;
    color: inherit;
    font: inherit;
    cursor: pointer;
  }

  .heading-toggle:hover .section-heading {
    color: var(--color-text-primary);
  }

  .heading-toggle:focus-visible {
    outline: 1px solid color-mix(in srgb, var(--tier-accent, var(--color-neon-cyan)) 30%, transparent);
    outline-offset: 2px;
  }

  .section-heading {
    font-size: 10px;
    font-family: var(--font-display);
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    color: var(--tier-accent, var(--color-text-dim));
    transition: color var(--duration-hover) var(--ease-spring);
  }

  .status-indicator {
    font-size: 10px;
    font-family: var(--font-mono);
    color: var(--tier-accent, var(--color-neon-cyan));
    margin-left: auto;
  }

  .timeline-scroll {
    flex: 1;
    overflow-y: auto;
    min-height: 0;
  }

  .turns-list {
    display: flex;
    flex-direction: column;
    gap: 2px;
    padding: 4px;
  }

  .timeline-footer {
    flex-shrink: 0;
    padding: 4px 6px;
    border-top: 1px solid var(--color-border-subtle);
    background: var(--color-bg-secondary);
    display: flex;
    flex-direction: column;
    gap: 4px;
  }

  .error-text {
    font-size: 10px;
    font-family: var(--font-sans);
    color: var(--color-neon-red);
  }
</style>
