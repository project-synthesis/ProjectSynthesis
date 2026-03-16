<script lang="ts">
  import { refinementStore } from '$lib/stores/refinement.svelte';
  import RefinementTurnCard from './RefinementTurnCard.svelte';
  import RefinementInput from './RefinementInput.svelte';
  import SuggestionChips from './SuggestionChips.svelte';
  import BranchSwitcher from './BranchSwitcher.svelte';
  import { tick } from 'svelte';

  let scrollContainer: HTMLDivElement | undefined = $state();

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

  // Auto-scroll on new turn
  let prevTurnCount = $state(0);
  $effect(() => {
    const count = refinementStore.turns.length;
    if (count > prevTurnCount && scrollContainer) {
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
  <!-- Header -->
  <div class="timeline-header">
    <span class="section-heading">REFINEMENT</span>
    {#if refinementStore.branches.length > 1}
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

  <!-- Scrollable turn list -->
  <div class="timeline-scroll" bind:this={scrollContainer}>
    {#if refinementStore.turns.length === 0}
      <div class="empty-state">
        <span class="empty-text">No refinement history</span>
      </div>
    {:else}
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
    {/if}
  </div>

  <!-- Input area -->
  <div class="timeline-footer">
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
    gap: 8px;
    height: 32px;
    padding: 0 8px;
    background: var(--color-bg-secondary);
    border-bottom: 1px solid var(--color-border-subtle);
    flex-shrink: 0;
  }

  .section-heading {
    font-size: 10px;
    font-family: var(--font-display);
    text-transform: uppercase;
    letter-spacing: 0.1em;
    color: var(--color-text-dim);
  }

  .status-indicator {
    font-size: 10px;
    font-family: var(--font-mono);
    color: var(--color-neon-cyan);
    margin-left: auto;
  }

  .timeline-scroll {
    flex: 1;
    overflow-y: auto;
    min-height: 0;
  }

  .empty-state {
    display: flex;
    align-items: center;
    justify-content: center;
    height: 48px;
  }

  .empty-text {
    font-size: 11px;
    font-family: var(--font-sans);
    color: var(--color-text-dim);
  }

  .turns-list {
    display: flex;
    flex-direction: column;
    gap: 2px;
    padding: 4px;
  }

  .timeline-footer {
    flex-shrink: 0;
    padding: 6px 8px;
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
