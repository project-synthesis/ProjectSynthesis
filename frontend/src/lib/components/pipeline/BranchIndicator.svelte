<script lang="ts">
  import { refinement } from '$lib/stores/refinement.svelte';
  import BranchCompare from './BranchCompare.svelte';

  let { optimizationId }: { optimizationId: string } = $props();

  let dropdownOpen = $state(false);
  let showCompare = $state(false);
  let compareIdA = $state<string | null>(null);
  let compareIdB = $state<string | null>(null);
  let forkLabel = $state('');
  let forkingFor = $state<string | null>(null);

  let branchA = $derived(
    compareIdA ? refinement.branches.find((b) => b.id === compareIdA) : undefined
  );
  let branchB = $derived(
    compareIdB ? refinement.branches.find((b) => b.id === compareIdB) : undefined
  );

  function openDropdown() {
    dropdownOpen = true;
  }

  function closeDropdown() {
    dropdownOpen = false;
    forkingFor = null;
    forkLabel = '';
  }

  function selectBranch(branchId: string) {
    refinement.activeBranchId = branchId;
    closeDropdown();
  }

  function openFork(branchId: string) {
    forkingFor = branchId;
    forkLabel = '';
  }

  async function confirmFork() {
    if (!forkingFor) return;
    await refinement.startFork(optimizationId, forkingFor, 'Fork branch', forkLabel || undefined);
    closeDropdown();
  }

  function openCompare() {
    const active = refinement.activeBranch;
    if (!active) return;
    // Default: compare active branch with the next available branch
    const others = refinement.branches.filter((b) => b.id !== active.id);
    if (others.length === 0) return;
    compareIdA = active.id;
    compareIdB = others[0].id;
    showCompare = true;
    closeDropdown();
  }

  function handleKeydown(e: KeyboardEvent) {
    if (e.key === 'Escape') closeDropdown();
  }
</script>

<!-- svelte-ignore a11y_no_static_element_interactions -->
<div class="relative inline-flex" onkeydown={handleKeydown}>
  <!-- Badge trigger -->
  <button
    class="inline-flex items-center gap-1.5 px-2 py-0.5 font-mono text-[10px]
           border border-neon-purple/30 bg-neon-purple/5 text-neon-purple
           hover:bg-neon-purple/10 hover:border-neon-purple/50
           transition-colors"
    onclick={() => { if (refinement.branchCount > 1) openDropdown(); }}
    aria-haspopup={refinement.branchCount > 1 ? 'listbox' : undefined}
    aria-expanded={refinement.branchCount > 1 ? dropdownOpen : undefined}
    aria-label="Branch indicator: {refinement.activeBranch?.label ?? 'none'}"
    title="Branch: {refinement.activeBranch?.label ?? 'none'}"
  >
    <span class="text-[9px]">◈</span>
    <span>{refinement.activeBranch?.label ?? '—'}</span>
    {#if (refinement.activeBranch?.turnCount ?? 0) > 0}
      <span class="text-neon-purple/60">×{refinement.activeBranch?.turnCount}</span>
    {/if}
    {#if refinement.branchCount > 1}
      <svg
        class="w-2.5 h-2.5 text-neon-purple/60 transition-transform duration-150"
        class:rotate-180={dropdownOpen}
        fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2.5"
        aria-hidden="true"
      >
        <path stroke-linecap="round" stroke-linejoin="round" d="M19 9l-7 7-7-7"></path>
      </svg>
    {/if}
  </button>

  <!-- Dropdown -->
  {#if dropdownOpen && refinement.branchCount > 1}
    <!-- Backdrop dismiss -->
    <div
      class="fixed inset-0 z-40"
      onclick={closeDropdown}
      role="presentation"
    ></div>

    <div
      class="absolute top-full left-0 mt-1 z-50 min-w-[220px]
             border border-neon-purple/20 bg-bg-card"
      role="listbox"
      aria-label="Branch list"
      style="animation: dropdown-enter 0.15s ease-out both;"
    >
      <!-- Branch list -->
      <div class="py-1">
        {#each refinement.branches as branch}
          {@const isActive = branch.id === refinement.activeBranchId}
          <div
            class="group flex items-center justify-between px-2 py-1.5 gap-2
                   hover:bg-bg-hover/60 transition-colors cursor-pointer
                   {isActive ? 'bg-neon-purple/8' : ''}"
            role="option"
            aria-selected={isActive}
            onclick={() => selectBranch(branch.id)}
            onkeydown={(e) => e.key === 'Enter' && selectBranch(branch.id)}
            tabindex="0"
          >
            <div class="flex items-center gap-1.5 min-w-0">
              {#if isActive}
                <span class="text-neon-purple text-[9px] shrink-0">◈</span>
              {:else}
                <span class="text-text-dim/40 text-[9px] shrink-0">◇</span>
              {/if}
              <span class="font-mono text-[10px] truncate
                           {isActive ? 'text-neon-purple' : 'text-text-secondary'}">
                {branch.label}
              </span>
              <span class="font-mono text-[9px] text-text-dim/60 shrink-0">
                ×{branch.turnCount}
              </span>
            </div>

            {#if branch.scores?.overall_score != null}
              <span class="font-mono text-[9px] text-neon-green/70 shrink-0">
                {branch.scores.overall_score.toFixed(1)}
              </span>
            {/if}
          </div>

          <!-- Fork inline form -->
          {#if forkingFor === branch.id}
            <div class="px-2 py-1.5 border-t border-neon-purple/10 bg-bg-primary/60"
                 onclick={(e) => e.stopPropagation()}
                 role="presentation"
            >
              <input
                type="text"
                bind:value={forkLabel}
                placeholder="Fork label (optional)"
                class="w-full px-2 py-1 text-[10px] font-mono bg-bg-input
                       border border-neon-purple/20 text-text-primary
                       placeholder-text-dim/40 outline-none
                       focus:border-neon-purple/50"
                onkeydown={(e) => { if (e.key === 'Enter') confirmFork(); if (e.key === 'Escape') forkingFor = null; }}
              />
              <div class="flex gap-1.5 mt-1">
                <button
                  class="btn-outline-subtle text-[9px] px-2 py-0.5 font-mono"
                  onclick={() => { forkingFor = null; }}
                >Cancel</button>
                <button
                  class="text-[9px] px-2 py-0.5 font-mono border border-neon-purple/40
                         text-neon-purple bg-neon-purple/5 hover:bg-neon-purple/10
                         transition-colors"
                  onclick={confirmFork}
                  disabled={refinement.refinementStreaming}
                >Fork</button>
              </div>
            </div>
          {:else}
            <div class="px-2 pb-1 opacity-0 group-hover:opacity-100 transition-opacity flex gap-1.5"
                 onclick={(e) => e.stopPropagation()}
                 role="presentation"
            >
              <button
                class="text-[9px] font-mono px-1.5 py-0.5 border border-neon-purple/25
                       text-neon-purple/70 hover:border-neon-purple/50 hover:text-neon-purple
                       transition-colors"
                onclick={() => openFork(branch.id)}
                title="Fork branch"
              >Fork</button>
            </div>
          {/if}
        {/each}
      </div>

      <!-- Footer actions -->
      <div class="border-t border-border-subtle px-2 py-1.5 flex gap-2">
        <button
          class="text-[10px] font-mono px-2 py-0.5 border border-neon-blue/25
                 text-neon-blue/70 hover:border-neon-blue/50 hover:text-neon-blue
                 transition-colors"
          onclick={openCompare}
          disabled={refinement.branches.length < 2}
          title="Compare branches side-by-side"
        >
          Compare
        </button>
      </div>
    </div>
  {/if}
</div>

<!-- Compare modal -->
{#if showCompare && branchA && branchB}
  <BranchCompare
    {optimizationId}
    branchA={branchA}
    branchB={branchB}
    onclose={() => { showCompare = false; }}
  />
{/if}
