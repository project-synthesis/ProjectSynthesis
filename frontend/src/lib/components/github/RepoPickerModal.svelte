<script lang="ts">
  import { github } from '$lib/stores/github.svelte';
  import RepoBadge from './RepoBadge.svelte';

  let { open = false, onclose }: { open?: boolean; onclose?: () => void } = $props();

  let search = $state('');

  let filtered = $derived(
    github.repos.filter(r =>
      r.full_name.toLowerCase().includes(search.toLowerCase()) ||
      r.description.toLowerCase().includes(search.toLowerCase())
    )
  );

  function selectRepo(name: string) {
    github.selectRepo(name);
    onclose?.();
  }
</script>

{#if open}
  <div class="fixed inset-0 bg-black/50 z-50" onclick={() => onclose?.()} role="presentation"></div>

  <div class="fixed top-[20%] left-1/2 -translate-x-1/2 w-[400px] max-w-[90vw] bg-bg-card border border-border-subtle rounded-xl z-50 overflow-hidden animate-dialog-in">
    <div class="px-4 py-3 border-b border-border-subtle">
      <h2 class="text-sm font-semibold text-text-primary mb-2">Select Repository</h2>
      <input
        type="text"
        placeholder="Search repositories..."
        class="w-full bg-bg-input border border-border-subtle rounded px-2 py-1.5 text-xs text-text-primary placeholder:text-text-dim focus:outline-none focus:border-neon-cyan/30"
        bind:value={search}
      />
    </div>

    <div class="max-h-[300px] overflow-y-auto py-1">
      {#each filtered as repo (repo.full_name)}
        <button
          class="w-full text-left px-4 py-2 hover:bg-bg-hover transition-colors flex items-center justify-between"
          onclick={() => selectRepo(repo.full_name)}
        >
          <div>
            <RepoBadge name={repo.full_name} isPrivate={repo.private} />
            {#if repo.description}
              <p class="text-[10px] text-text-dim mt-0.5 ml-1">{repo.description}</p>
            {/if}
          </div>
          {#if github.selectedRepo === repo.full_name}
            <svg class="w-4 h-4 text-neon-cyan shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2">
              <path stroke-linecap="round" stroke-linejoin="round" d="M5 13l4 4L19 7"></path>
            </svg>
          {/if}
        </button>
      {/each}

      {#if filtered.length === 0}
        <p class="text-xs text-text-dim text-center py-6">No repositories match your search.</p>
      {/if}
    </div>
  </div>
{/if}
