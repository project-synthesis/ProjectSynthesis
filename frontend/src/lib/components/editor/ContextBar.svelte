<script lang="ts">
  import { github } from '$lib/stores/github.svelte';

  export type ContextChip = { id: string; label: string; type: string };

  let chips = $state<ContextChip[]>([]);
  let showMenu = $state(false);

  const contextOptions = [
    { type: 'file', label: 'File', icon: 'M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z' },
    { type: 'repo', label: 'Repository', icon: 'M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z' },
    { type: 'url', label: 'URL', icon: 'M13.828 10.172a4 4 0 00-5.656 0l-4 4a4 4 0 105.656 5.656l1.102-1.101m-.758-4.899a4 4 0 005.656 0l4-4a4 4 0 00-5.656-5.656l-1.1 1.1' },
    { type: 'template', label: 'Template', icon: 'M4 5a1 1 0 011-1h14a1 1 0 011 1v2a1 1 0 01-1 1H5a1 1 0 01-1-1V5zM4 13a1 1 0 011-1h6a1 1 0 011 1v6a1 1 0 01-1 1H5a1 1 0 01-1-1v-6zM16 13a1 1 0 011-1h2a1 1 0 011 1v6a1 1 0 01-1 1h-2a1 1 0 01-1-1v-6z' },
    { type: 'instruction', label: 'Instruction', icon: 'M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z' }
  ];

  export function addChip(type: string, label?: string) {
    const chipLabel = label || (type === 'repo' && github.selectedRepo
      ? github.selectedRepo
      : `@${type}`);
    chips = [...chips, { id: `ctx-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`, label: chipLabel, type }];
    showMenu = false;
  }

  function removeChip(id: string) {
    chips = chips.filter(c => c.id !== id);
  }

  export function getChips(): ContextChip[] {
    return chips;
  }

  export function getContextOptions() {
    return contextOptions;
  }
</script>

<div class="flex items-center gap-1.5 px-4 py-1.5 border-b border-border-subtle bg-bg-secondary/30 shrink-0 min-h-[32px]">
  <span class="text-[10px] text-text-dim uppercase tracking-wider mr-1">Context</span>

  {#if chips.length === 0}
    <span class="text-[10px] text-text-dim/50 italic">Add context with @</span>
  {/if}

  {#each chips as chip (chip.id)}
    <span class="inline-flex items-center gap-1 px-2 py-0.5 rounded-full font-mono text-[10px] bg-neon-teal/8 border border-neon-teal/40 text-neon-teal animate-scale-in" data-testid="context-chip">
      <span>@</span>{chip.label}
      <button
        class="ml-0.5 text-text-dim hover:text-neon-red transition-colors duration-150"
        onclick={() => removeChip(chip.id)}
        aria-label="Remove context"
      >
        ×
      </button>
    </span>
  {/each}

  <div class="relative">
    <button
      class="w-5 h-5 flex items-center justify-center rounded text-text-dim hover:text-neon-cyan hover:bg-bg-hover transition-colors"
      onclick={() => { showMenu = !showMenu; }}
      aria-label="Add context"
    >
      <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2">
        <path stroke-linecap="round" stroke-linejoin="round" d="M12 4v16m8-8H4"></path>
      </svg>
    </button>

    {#if showMenu}
      <div class="absolute top-full left-0 mt-1 w-36 bg-bg-card border border-border-subtle rounded-lg z-[300] py-1 animate-dropdown-enter">
        {#each contextOptions as opt}
          <button
            class="w-full flex items-center gap-2 px-3 py-1.5 text-xs text-text-secondary hover:bg-bg-hover hover:text-text-primary transition-colors"
            onclick={() => addChip(opt.type)}
          >
            <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="1.5">
              <path stroke-linecap="round" stroke-linejoin="round" d={opt.icon}></path>
            </svg>
            {opt.label}
          </button>
        {/each}
      </div>
    {/if}
  </div>
</div>
