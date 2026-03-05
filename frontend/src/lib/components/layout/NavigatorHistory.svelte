<script lang="ts">
  import { history } from '$lib/stores/history.svelte';
  import { editor } from '$lib/stores/editor.svelte';
  import { forge } from '$lib/stores/forge.svelte';
  import { fetchHistory, fetchHistoryStats, fetchOptimization, deleteOptimization, type HistoryStats } from '$lib/api/client';
  import ScoreCircle from '$lib/components/shared/ScoreCircle.svelte';
  import { onMount } from 'svelte';

  let loading = $state(false);
  let stats = $state<HistoryStats | null>(null);
  let searchTimer: ReturnType<typeof setTimeout> | null = null;
  let selectedIds = $state<Set<string>>(new Set());

  function toggleSelect(e: MouseEvent, id: string) {
    e.stopPropagation();
    const next = new Set(selectedIds);
    if (next.has(id)) { next.delete(id); } else { next.add(id); }
    selectedIds = next;
  }

  function clearSelection() { selectedIds = new Set(); }

  function handleCompare() {
    // Open a diff view comparing the two selected entries
    const ids = Array.from(selectedIds);
    if (ids.length === 2) {
      const a = history.entries.find(e => e.id === ids[0]);
      const b = history.entries.find(e => e.id === ids[1]);
      if (a && b) {
        editor.openTab({
          id: `compare-${ids[0]}-${ids[1]}`,
          label: 'Compare',
          type: 'prompt',
          promptText: `Comparing: ${a.raw_prompt.slice(0, 30)} vs ${b.raw_prompt.slice(0, 30)}`,
          dirty: false
        });
      }
      clearSelection();
    }
  }

  function debouncedSearch(value: string) {
    history.filters.search = value;
    if (searchTimer) clearTimeout(searchTimer);
    searchTimer = setTimeout(() => { loadHistory(); }, 200);
  }

  async function loadHistory() {
    loading = true;
    const startTime = Date.now();
    try {
      const res = await fetchHistory({
        page: history.filters.page,
        per_page: history.filters.pageSize,
        search: history.filters.search || undefined,
        framework: history.filters.strategy || undefined,
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
      // Minimum 200ms skeleton display per spec
      const elapsed = Date.now() - startTime;
      if (elapsed < 200) {
        await new Promise(r => setTimeout(r, 200 - elapsed));
      }
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

  async function openHistoryEntry(entry: typeof history.entries[0]) {
    history.select(entry.id);

    // Load full record and populate forge store for artifact view
    try {
      const record = await fetchOptimization(entry.id);
      forge.loadFromRecord(record);
    } catch {
      // If fetch fails, still open the tab
    }

    editor.openTab({
      id: `history-${entry.id}`,
      label: entry.raw_prompt.slice(0, 30) + (entry.raw_prompt.length > 30 ? '...' : ''),
      type: 'prompt',
      promptText: entry.raw_prompt,
      dirty: false
    });
    // Switch to pipeline view to show the forge artifact with sub-tabs
    editor.setSubTab('pipeline');
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
            <button
              class="text-[9px] px-1 py-0.5 rounded border transition-colors cursor-pointer
                {history.filters.strategy === fw
                  ? 'bg-neon-purple/20 border-neon-purple/40 text-neon-purple'
                  : 'bg-bg-card border-border-subtle text-neon-purple hover:border-neon-purple/30'}"
              onclick={() => { history.filters.strategy = history.filters.strategy === fw ? null : fw; loadHistory(); }}
            >
              {fw} <span class="text-text-dim">({count})</span>
            </button>
          {/each}
          {#if history.filters.strategy}
            <button
              class="text-[9px] px-1 py-0.5 rounded bg-neon-red/10 border border-neon-red/20 text-neon-red hover:bg-neon-red/20 transition-colors"
              onclick={() => { history.filters.strategy = null; loadHistory(); }}
            >
              ✕ Clear
            </button>
          {/if}
        </div>
      {/if}
    </div>
  {/if}

  <!-- Search + Sort -->
  <div class="p-2 border-b border-border-subtle space-y-1.5">
    <input
      type="text"
      placeholder="Search history..."
      class="w-full bg-bg-input border border-border-subtle rounded px-2 py-1 text-xs text-text-primary placeholder:text-text-dim focus:outline-none focus:border-neon-cyan/30"
      oninput={(e) => debouncedSearch((e.target as HTMLInputElement).value)}
    />
    <div class="flex items-center gap-1">
      <span class="text-[10px] text-text-dim mr-1">Sort:</span>
      <button
        class="text-[10px] px-1.5 py-0.5 rounded border transition-colors
          {history.filters.sortBy === 'created_at'
            ? 'text-neon-cyan border-neon-cyan/30 bg-neon-cyan/10'
            : 'text-text-dim border-border-subtle hover:border-neon-cyan/20 hover:text-text-secondary'}"
        onclick={() => { if (history.filters.sortBy === 'created_at') { history.filters.sortDir = history.filters.sortDir === 'desc' ? 'asc' : 'desc'; } else { history.filters.sortBy = 'created_at'; history.filters.sortDir = 'desc'; } loadHistory(); }}
      >
        Date {history.filters.sortBy === 'created_at' ? (history.filters.sortDir === 'desc' ? '↓' : '↑') : ''}
      </button>
      <button
        class="text-[10px] px-1.5 py-0.5 rounded border transition-colors
          {history.filters.sortBy === 'overall_score'
            ? 'text-neon-cyan border-neon-cyan/30 bg-neon-cyan/10'
            : 'text-text-dim border-border-subtle hover:border-neon-cyan/20 hover:text-text-secondary'}"
        onclick={() => { if (history.filters.sortBy === 'overall_score') { history.filters.sortDir = history.filters.sortDir === 'desc' ? 'asc' : 'desc'; } else { history.filters.sortBy = 'overall_score'; history.filters.sortDir = 'desc'; } loadHistory(); }}
      >
        Score {history.filters.sortBy === 'overall_score' ? (history.filters.sortDir === 'desc' ? '↓' : '↑') : ''}
      </button>
    </div>
    <div class="text-[10px] text-text-dim">
      {#if history.filters.search || history.filters.strategy}
        {history.totalCount} of {stats?.total_optimizations ?? history.totalCount} runs
      {:else}
        {history.totalCount} runs
      {/if}
    </div>
  </div>

  <!-- Compare toolbar -->
  {#if selectedIds.size >= 2}
    <div class="px-2 py-1.5 border-b border-neon-cyan/20 bg-neon-cyan/5 flex items-center justify-between">
      <span class="text-[10px] text-neon-cyan">{selectedIds.size} selected</span>
      <div class="flex items-center gap-1">
        <button
          class="text-[10px] px-2 py-0.5 rounded bg-neon-cyan/20 border border-neon-cyan/30 text-neon-cyan hover:bg-neon-cyan/30 transition-colors"
          onclick={handleCompare}
        >
          Compare
        </button>
        <button
          class="text-[10px] px-1.5 py-0.5 rounded text-text-dim hover:text-text-secondary transition-colors"
          onclick={clearSelection}
        >
          Cancel
        </button>
      </div>
    </div>
  {/if}

  <!-- List -->
  <div class="flex-1 overflow-y-auto p-1">
    {#if loading}
      <!-- Skeleton loading rows with shimmer animation -->
      <div class="space-y-1 p-1" data-testid="history-skeleton">
        {#each Array(5) as _, i}
          <div class="h-[32px] flex items-center gap-2 px-2 rounded" style="animation-delay: {i * 50}ms;">
            <div class="w-5 h-5 rounded-full bg-bg-hover animate-shimmer" style="background: linear-gradient(90deg, var(--color-bg-hover) 25%, var(--color-bg-card) 50%, var(--color-bg-hover) 75%); background-size: 200% 100%;"></div>
            <div class="flex-1 space-y-1">
              <div class="h-2.5 rounded bg-bg-hover animate-shimmer" style="width: {70 + i * 5}%; background: linear-gradient(90deg, var(--color-bg-hover) 25%, var(--color-bg-card) 50%, var(--color-bg-hover) 75%); background-size: 200% 100%;"></div>
              <div class="h-2 rounded bg-bg-hover animate-shimmer" style="width: {40 + i * 8}%; background: linear-gradient(90deg, var(--color-bg-hover) 25%, var(--color-bg-card) 50%, var(--color-bg-hover) 75%); background-size: 200% 100%;"></div>
            </div>
          </div>
        {/each}
      </div>
    {:else if history.entries.length === 0}
      <p class="text-xs text-text-dim px-2 py-8 text-center">No history entries yet. Forge a prompt to get started.</p>
    {:else}
      {#each history.entries as entry (entry.id)}
        <!-- svelte-ignore a11y_click_events_have_key_events -->
        <!-- svelte-ignore a11y_no_static_element_interactions -->
        <div
          class="w-full text-left px-2 rounded text-xs transition-colors duration-200 mb-0.5 cursor-pointer group/entry h-[32px] flex items-center
            {selectedIds.has(entry.id)
              ? 'bg-neon-cyan/5 border border-neon-cyan/20'
              : history.selectedId === entry.id
                ? 'bg-bg-hover border border-border-accent'
                : 'hover:bg-bg-hover border border-transparent'}"
          onclick={() => openHistoryEntry(entry)}
        >
          <div class="flex items-start gap-2">
            <!-- Checkbox: visible on hover or when selected -->
            <label
              class="flex items-center justify-center w-4 h-4 shrink-0 mt-0.5 cursor-pointer
                {selectedIds.has(entry.id) ? 'opacity-100' : 'opacity-0 group-hover/entry:opacity-100'} transition-opacity"
              onclick={(e: MouseEvent) => e.stopPropagation()}
            >
              <input
                type="checkbox"
                checked={selectedIds.has(entry.id)}
                onchange={(e: Event) => toggleSelect(e as unknown as MouseEvent, entry.id)}
                class="w-3 h-3 rounded border-border-subtle accent-neon-cyan cursor-pointer"
              />
            </label>
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
