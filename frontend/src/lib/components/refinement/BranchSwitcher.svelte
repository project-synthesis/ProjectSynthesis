<script lang="ts">
  import type { RefinementBranch } from '$lib/api/client';

  interface Props {
    branches: RefinementBranch[];
    activeBranchId: string;
    onSwitch: (id: string) => void;
  }

  let { branches, activeBranchId, onSwitch }: Props = $props();

  const activeIndex = $derived(branches.findIndex(b => b.id === activeBranchId));
  const canPrev = $derived(activeIndex > 0);
  const canNext = $derived(activeIndex < branches.length - 1);

  function prev() {
    if (canPrev) onSwitch(branches[activeIndex - 1].id);
  }

  function next() {
    if (canNext) onSwitch(branches[activeIndex + 1].id);
  }
</script>

{#if branches.length > 1}
  <div class="branch-switcher" aria-label="Branch navigation">
    <button
      class="nav-btn"
      onclick={prev}
      disabled={!canPrev}
      aria-label="Previous branch"
    >&larr;</button>
    <span class="branch-label">Branch {activeIndex + 1}/{branches.length}</span>
    <button
      class="nav-btn"
      onclick={next}
      disabled={!canNext}
      aria-label="Next branch"
    >&rarr;</button>
  </div>
{/if}

<style>
  .branch-switcher {
    display: flex;
    align-items: center;
    gap: 6px;
  }

  .branch-label {
    font-size: 10px;
    font-family: var(--font-mono);
    color: var(--color-text-secondary);
    white-space: nowrap;
  }

  .nav-btn {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 20px;
    height: 20px;
    background: transparent;
    border: 1px solid var(--color-border-subtle);
    color: var(--color-text-secondary);
    font-size: 11px;
    cursor: pointer;
    padding: 0;
    transition: border-color var(--duration-hover) var(--ease-spring),
                color var(--duration-hover) var(--ease-spring);
  }

  .nav-btn:hover:not(:disabled) {
    border-color: var(--tier-accent, var(--color-neon-cyan));
    color: var(--color-text-primary);
  }

  .nav-btn:disabled {
    opacity: 0.3;
    cursor: not-allowed;
  }
</style>
