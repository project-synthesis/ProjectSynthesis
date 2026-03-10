<script lang="ts">
  import { strategyReference, type StrategyInfo } from '$lib/utils/strategyReference';
  import { editor } from '$lib/stores/editor.svelte';

  let searchQuery = $state('');
  let expandedId = $state<string | null>(null);

  let filtered = $derived(
    searchQuery.length === 0
      ? strategyReference
      : strategyReference.filter(s =>
          s.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
          s.fullName.toLowerCase().includes(searchQuery.toLowerCase()) ||
          s.description.toLowerCase().includes(searchQuery.toLowerCase()) ||
          s.bestFor.some(b => b.toLowerCase().includes(searchQuery.toLowerCase()))
        )
  );

  function tryStrategy(strategy: StrategyInfo) {
    editor.openTab({
      id: `prompt-${Date.now()}`,
      label: 'New Prompt',
      type: 'prompt',
      promptText: '',
      dirty: false,
      strategy: strategy.id,
    });
  }

  function toggle(id: string) {
    expandedId = expandedId === id ? null : id;
  }
</script>

<div class="h-full overflow-y-auto p-6" style="overscroll-behavior: contain;">
  <div class="max-w-2xl mx-auto">
    <div class="flex items-center justify-between mb-4">
      <h1 class="font-display text-[11px] uppercase tracking-[0.1em] text-text-primary">Strategy Reference</h1>
      <span class="font-mono text-[9px] text-text-dim">{strategyReference.length} strategies</span>
    </div>

    <!-- Search bar -->
    <div class="mb-4">
      <input
        type="text"
        placeholder="Search strategies..."
        bind:value={searchQuery}
        class="w-full bg-bg-input border border-border-subtle px-3 py-1.5
               font-mono text-[10px] text-text-primary focus:outline-none
               focus:border-neon-cyan/30 placeholder:text-text-dim/40"
      />
    </div>

    <!-- Strategy cards -->
    <div class="space-y-2">
      {#each filtered as strategy (strategy.id)}
        <div class="border border-border-subtle hover:border-opacity-60 transition-colors" style="border-left: 2px solid {strategy.color};">
          <!-- Header — always visible -->
          <button
            class="w-full flex items-start gap-3 p-3 text-left"
            onclick={() => toggle(strategy.id)}
          >
            <div class="flex-1 min-w-0">
              <div class="flex items-center gap-2 mb-0.5">
                <span class="font-display text-[10px] font-bold uppercase text-text-primary">{strategy.name}</span>
                <span class="font-mono text-[8px] text-text-dim">{strategy.fullName}</span>
              </div>
              <p class="font-mono text-[9px] text-text-dim leading-snug">{strategy.description}</p>
              <div class="flex flex-wrap gap-1 mt-1.5">
                {#each strategy.bestFor as tag}
                  <span class="px-1.5 py-0.5 border border-border-subtle font-mono text-[7px] text-text-dim/60 uppercase">{tag}</span>
                {/each}
              </div>
            </div>
            <svg
              class="w-3 h-3 text-text-dim/40 shrink-0 mt-1 transition-transform {expandedId === strategy.id ? 'rotate-180' : ''}"
              fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2"
            >
              <path stroke-linecap="round" stroke-linejoin="round" d="M19 9l-7 7-7-7"></path>
            </svg>
          </button>

          <!-- Expanded content -->
          {#if expandedId === strategy.id}
            <div class="px-3 pb-3 border-t border-border-subtle pt-2 animate-fade-in">
              <div class="font-mono text-[8px] text-neon-cyan/50 uppercase tracking-[0.1em] mb-1">EXAMPLE</div>
              <pre class="font-mono text-[9px] text-text-dim/80 leading-relaxed whitespace-pre-wrap bg-bg-input p-2 border border-border-subtle mb-2">{strategy.example}</pre>
              <button
                onclick={() => tryStrategy(strategy)}
                class="px-3 py-1 border border-neon-cyan/30 font-mono text-[9px] text-neon-cyan uppercase tracking-[0.05em] hover:bg-neon-cyan/5 transition-colors"
              >TRY WITH {strategy.name.toUpperCase()}</button>
            </div>
          {/if}
        </div>
      {/each}

      {#if filtered.length === 0}
        <div class="text-center py-8">
          <p class="font-mono text-[10px] text-text-dim">No strategies match "{searchQuery}"</p>
        </div>
      {/if}
    </div>
  </div>
</div>
