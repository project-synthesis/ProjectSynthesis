<script lang="ts">
  import { history } from '$lib/stores/history.svelte';
  import { editor } from '$lib/stores/editor.svelte';
  import { fetchHistory, fetchHistoryStats, deleteOptimization, type HistoryStats } from '$lib/api/client';
  import ScoreCircle from '$lib/components/shared/ScoreCircle.svelte';
  import { onMount } from 'svelte';

  let loading = $state(false);
  let stats = $state<HistoryStats | null>(null);

  async function loadHistory() {
    loading = true;
    try {
      const res = await fetchHistory({
        page: history.filters.page,
        per_page: history.filters.pageSize,
        search: history.filters.search || undefined,
        task_type: history.filters.strategy || undefined,
        sort: history.filters.sortBy,
        order: history.filters.sortDir
      });
      history.setEntries(
        res.items.map((item: Record<string, unknown>) => ({
          id: item.id as string,
          raw_prompt: (item.raw_prompt || '') as string,
          optimized_prompt: item.optimized_prompt as string | undefined,
          overall_score: item.overall_score as number | undefined,
          strategy: (item.primary_framework || item.strategy) as string | undefined,
          model: (item.provider_used || item.model) as string | undefined,
          created_at: item.created_at as string,
          duration_ms: item.duration_ms as number | undefined,
          tags: item.tags as string[] | undefined
        })),
        res.total
      );
    } catch {
      // API not available yet
    } finally {
      loading = false;
    }
  }

  async function loadStats() {
    try {
      stats = await fetchHistoryStats();
    } catch {
      // Stats not available
    }
  }

  async function handleDelete(e: MouseEvent, id: string) {
    e.stopPropagation();
    try {
      await deleteOptimization(id);
      await loadHistory();
      await loadStats();
    } catch {
      // Delete failed
    }
  }

  function openHistoryEntry(entry: typeof history.entries[0]) {
    history.select(entry.id);
    editor.openTab({
      id: `history-${entry.id}`,
      label: entry.raw_prompt.slice(0, 30) + (entry.raw_prompt.length > 30 ? '...' : ''),
      type: 'prompt',
      promptText: entry.optimized_prompt || entry.raw_prompt,
      dirty: false
    });
  }

  onMount(() => {
    loadHistory();
    loadStats();
  });
</script>

<div class="flex flex-col h-full">
  <!-- Stats summary -->
  {#if stats}
    <div class="px-2 py-1.5 border-b border-border-subtle bg-bg-secondary/50">
      <div class="flex items-center justify-between text-[10px] text-text-dim">
        <span>{stats.total_optimizations} total</span>
        {#if stats.average_score != null}
          <span>avg <span class="text-neon-green">{stats.average_score.toFixed(1)}</span>/10</span>
        {/if}
      </div>
      {#if Object.keys(stats.framework_breakdown || {}).length > 0}
        <div class="flex flex-wrap gap-1 mt-1">
          {#each Object.entries(stats.framework_breakdown).slice(0, 4) as [fw, count]}
            <span class="text-[9px] px-1 py-0.5 rounded bg-bg-card border border-border-subtle text-neon-purple">
              {fw} <span class="text-text-dim">({count})</span>
            </span>
          {/each}
        </div>
      {/if}
    </div>
  {/if}

  <!-- Search -->
  <div class="p-2 border-b border-border-subtle">
    <input
      type="text"
      placeholder="Search history..."
      class="w-full bg-bg-input border border-border-subtle rounded px-2 py-1 text-xs text-text-primary placeholder:text-text-dim focus:outline-none focus:border-neon-cyan/30"
      oninput={(e) => { history.filters.search = (e.target as HTMLInputElement).value; }}
    />
  </div>

  <!-- List -->
  <div class="flex-1 overflow-y-auto p-1">
    {#if loading}
      <div class="flex items-center justify-center py-8">
        <div class="w-4 h-4 border-2 border-neon-cyan/30 border-t-neon-cyan rounded-full animate-spin"></div>
      </div>
    {:else if history.entries.length === 0}
      <p class="text-xs text-text-dim px-2 py-8 text-center">No history entries yet. Forge a prompt to get started.</p>
    {:else}
      {#each history.entries as entry (entry.id)}
        <!-- svelte-ignore a11y_click_events_have_key_events -->
        <!-- svelte-ignore a11y_no_static_element_interactions -->
        <div
          class="w-full text-left px-2 py-2 rounded text-xs transition-colors mb-0.5 cursor-pointer group/entry
            {history.selectedId === entry.id
              ? 'bg-bg-hover border border-border-accent'
              : 'hover:bg-bg-hover border border-transparent'}"
          onclick={() => openHistoryEntry(entry)}
        >
          <div class="flex items-start gap-2">
            {#if entry.overall_score != null}
              <ScoreCircle score={entry.overall_score} size={20} />
            {/if}
            <div class="flex-1 min-w-0">
              <p class="text-text-primary truncate">{entry.raw_prompt.slice(0, 50)}</p>
              <div class="flex items-center gap-2 mt-0.5">
                {#if entry.strategy}
                  <span class="text-[10px] text-neon-purple">{entry.strategy}</span>
                {/if}
                <span class="text-[10px] text-text-dim">{new Date(entry.created_at).toLocaleDateString()}</span>
              </div>
            </div>
            <button
              class="w-5 h-5 flex items-center justify-center rounded opacity-0 group-hover/entry:opacity-100 text-text-dim hover:text-neon-red hover:bg-neon-red/10 transition-all shrink-0"
              onclick={(e: MouseEvent) => handleDelete(e, entry.id)}
              aria-label="Delete optimization"
              title="Delete"
            >
              <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2">
                <path stroke-linecap="round" stroke-linejoin="round" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"></path>
              </svg>
            </button>
          </div>
        </div>
      {/each}
    {/if}
  </div>

  <!-- Footer -->
  <div class="p-2 border-t border-border-subtle">
    <button
      class="w-full text-xs text-text-dim hover:text-neon-cyan transition-colors py-1"
      onclick={loadHistory}
    >
      Refresh
    </button>
  </div>
</div>
