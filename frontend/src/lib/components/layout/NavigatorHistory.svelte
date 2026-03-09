<script lang="ts">
  import { history } from '$lib/stores/history.svelte';
  import { editor } from '$lib/stores/editor.svelte';
  import { forge } from '$lib/stores/forge.svelte';
  import { toast } from '$lib/stores/toast.svelte';
  import { fetchHistory, fetchHistoryStats, fetchOptimization, deleteOptimization, fetchHistoryTrash, restoreOptimization, patchOptimization, type HistoryStats, type HistoryResponse } from '$lib/api/client';
  import { getStrategyHex } from '$lib/utils/strategy';
  import ScoreCircle from '$lib/components/shared/ScoreCircle.svelte';
  import { onMount, tick } from 'svelte';

  let loading = $state(false);
  let stats = $state<HistoryStats | null>(null);
  let searchTimer: ReturnType<typeof setTimeout> | null = null;
  let selectedIds = $state<Set<string>>(new Set());
  let showFilters = $state(false);
  let contextMenuId = $state<string | null>(null);

  // Inline title editing state
  let editingId = $state<string | null>(null);
  let editingValue = $state('');
  let titleInputEl = $state<HTMLInputElement | undefined>();

  $effect(() => {
    if (titleInputEl) titleInputEl.focus();
  });

  function startTitleEdit(e: MouseEvent, entry: typeof history.entries[0]) {
    e.stopPropagation();
    editingId = entry.id;
    editingValue = entry.title || entry.raw_prompt.slice(0, 60);
  }

  async function commitTitleEdit(entry: typeof history.entries[0]) {
    if (!editingId || editingId !== entry.id) return;
    const original = entry.title || '';
    const newTitle = editingValue.trim();
    if (!newTitle) { editingId = null; return; }
    if (newTitle === original) { editingId = null; return; }
    // Optimistic update
    history.updateEntryTitle(entry.id, newTitle);
    editingId = null;
    try {
      await patchOptimization(entry.id, { title: newTitle });
    } catch {
      // Revert on error
      history.updateEntryTitle(entry.id, original);
      toast.error('Failed to save title');
    }
  }

  function cancelTitleEdit() {
    editingId = null;
    editingValue = '';
  }

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
    // Reset offset when search changes — avoid empty results at a stale page
    history.updateFilters({ search: value, offset: 0 });
    if (searchTimer) clearTimeout(searchTimer);
    searchTimer = setTimeout(() => { loadHistory(); }, 200);
  }

  async function loadHistory() {
    loading = true;
    const startTime = Date.now();
    try {
      const res = await fetchHistory({
        offset: history.filters.offset,
        limit: history.filters.limit,
        search: history.filters.search || undefined,
        framework: history.filters.strategy || undefined,
        sort: history.filters.sortBy,
        order: history.filters.sortDir,
        has_repo: history.filters.has_repo,
        min_score: history.filters.min_score,
        max_score: history.filters.max_score,
        task_type: history.filters.task_type || undefined,
        status: history.filters.status || undefined
      });
      history.setEntries(res.items, res.total);
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
    // Optimistic removal
    history.removeEntry(id);
    try {
      await deleteOptimization(id);
      await loadStats();
      toast.info('Deleted — Undo', 5000, {
        label: 'Undo',
        onClick: () => history.restoreItem(id)
      });
    } catch {
      // Revert optimistic removal and surface error
      await history.loadHistory();
      toast.error('Failed to delete optimization');
    }
  }

  async function handleRestore(e: MouseEvent, id: string) {
    e.stopPropagation();
    await history.restoreItem(id);
    await loadStats();
  }

  async function openHistoryEntry(entry: typeof history.entries[0]) {
    history.select(entry.id);

    // Load full record and populate forge store for artifact view
    try {
      const record = await fetchOptimization(entry.id);
      forge.cacheRecord(record.id, record);
      forge.loadFromRecord(record);
    } catch {
      // If fetch fails, still open the tab
    }

    editor.openTab({
      id: `history-${entry.id}`,
      label: entry.raw_prompt.slice(0, 30) + (entry.raw_prompt.length > 30 ? '...' : ''),
      type: 'prompt',
      promptText: entry.raw_prompt,
      dirty: false,
      optimizationId: entry.id
    });
    // Switch to pipeline view to show the forge artifact with sub-tabs
    editor.setSubTab('pipeline');
  }

  async function handleHistoryRetry(e: MouseEvent, id: string) {
    e.stopPropagation();
    contextMenuId = null;
    // Open the entry tab so results are visible in the editor panel
    const entry = history.entries.find(en => en.id === id);
    if (entry) {
      editor.openTab({
        id: `history-${id}`,
        label: entry.raw_prompt.slice(0, 30) + (entry.raw_prompt.length > 30 ? '...' : ''),
        type: 'prompt',
        promptText: entry.raw_prompt,
        dirty: false,
        optimizationId: id
      });
      editor.setSubTab('pipeline');
    }
    await forge.retryForge(id);
  }

  onMount(() => {
    loadHistory();
    loadStats();
  });
</script>

<div
  class="flex flex-col h-full"
  role="presentation"
  onclick={() => { if (contextMenuId) contextMenuId = null; }}
  onkeydown={(e) => { if (e.key === 'Escape' && contextMenuId) contextMenuId = null; }}
>
  <!-- Top-level tab bar: History | Trash -->
  <div class="flex items-center h-8 border-b border-border-subtle bg-bg-secondary/50 px-2 gap-1 shrink-0">
    <button
      class="px-3 py-1 text-xs transition-colors
        {!history.showTrash
          ? 'text-neon-cyan border-b border-neon-cyan bg-bg-primary'
          : 'text-text-dim hover:text-text-secondary'}"
      onclick={() => { history.showTrash = false; }}
    >
      History
    </button>
    <button
      class="px-3 py-1 text-xs transition-colors
        {history.showTrash
          ? 'text-neon-cyan border-b border-neon-cyan bg-bg-primary'
          : 'text-text-dim hover:text-text-secondary'}"
      onclick={() => { history.showTrash = true; history.loadTrash(); }}
    >
      Trash{#if history.trashTotal > 0} <span class="text-[9px]">({history.trashTotal})</span>{/if}
    </button>
  </div>

  {#if history.showTrash}
    <!-- Trash view (no filter/sort controls) -->
    <div class="flex-1 overflow-y-auto p-1">
      <div class="px-2 py-1 mb-1 text-[9px] text-neon-red/60 border-b border-neon-red/10 font-mono uppercase tracking-wider">
        Trash — items deleted within 7 days
      </div>
      {#if history.trashLoading}
        <div class="text-[10px] text-text-dim px-2 py-4 text-center">Loading...</div>
      {:else if history.trashItems.length === 0}
        <div class="text-[10px] text-text-dim/50 px-2 py-4 text-center">Trash is empty</div>
      {:else}
        {#each history.trashItems as entry (entry.id)}
          <div class="flex items-center justify-between px-2 py-1 mb-0.5 border border-transparent hover:border-border-subtle hover:bg-bg-hover transition-colors">
            <div class="flex-1 min-w-0">
              <p class="text-[11px] text-text-dim truncate">{entry.raw_prompt}</p>
              <p class="text-[9px] text-text-dim/50">{new Date(entry.created_at).toLocaleDateString()}</p>
            </div>
            <button
              class="text-[9px] font-mono text-neon-cyan/60 hover:text-neon-cyan shrink-0 ml-2 border border-neon-cyan/20 hover:border-neon-cyan/50 px-1.5 py-0.5 transition-colors"
              aria-label="Restore optimization"
              onclick={(e: MouseEvent) => handleRestore(e, entry.id)}
            >
              RESTORE
            </button>
          </div>
        {/each}
      {/if}
    </div>
  {:else}
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
              {@const hex = getStrategyHex(fw)}
              <button
                class="text-[9px] px-1 py-0.5 border transition-colors cursor-pointer bg-bg-card"
                style="color: {hex}; border-color: {history.filters.strategy === fw ? hex + '80' : 'rgba(74,74,106,0.15)'}; {history.filters.strategy === fw ? `background: ${hex}20;` : ''}"
                onclick={() => { history.updateFilters({ strategy: history.filters.strategy === fw ? null : fw, offset: 0 }); loadHistory(); }}
              >
                {fw} <span class="text-text-dim">({count})</span>
              </button>
            {/each}
            {#if history.filters.strategy}
              <button
                class="text-[9px] px-1 py-0.5 bg-neon-red/10 border border-neon-red/20 text-neon-red hover:bg-neon-red/20 transition-colors"
                onclick={() => { history.updateFilters({ strategy: null, offset: 0 }); loadHistory(); }}
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
        name="history-search"
        placeholder="Search history..."
        class="w-full bg-bg-input border border-border-subtle px-2 py-1 text-xs text-text-primary placeholder:text-text-dim focus:outline-none focus:border-neon-cyan/30"
        oninput={(e) => debouncedSearch((e.target as HTMLInputElement).value)}
      />
      {#if showFilters}
        <div class="space-y-1.5 pt-1 border-t border-border-subtle">
          <!-- Min/Max Score -->
          <div class="flex items-center gap-1">
            <span class="text-[10px] text-text-dim w-14 shrink-0">Score:</span>
            <input
              type="number" min="1" max="10" placeholder="min"
              class="w-12 bg-bg-input border border-border-subtle px-1 py-0.5 text-[10px] text-text-primary focus:outline-none focus:border-neon-cyan/30"
              value={history.filters.min_score ?? ''}
              onchange={(e) => { const v = parseInt((e.target as HTMLInputElement).value); history.updateFilters({ min_score: isNaN(v) ? undefined : v, offset: 0 }); loadHistory(); }}
            />
            <span class="text-[9px] text-text-dim">–</span>
            <input
              type="number" min="1" max="10" placeholder="max"
              class="w-12 bg-bg-input border border-border-subtle px-1 py-0.5 text-[10px] text-text-primary focus:outline-none focus:border-neon-cyan/30"
              value={history.filters.max_score ?? ''}
              onchange={(e) => { const v = parseInt((e.target as HTMLInputElement).value); history.updateFilters({ max_score: isNaN(v) ? undefined : v, offset: 0 }); loadHistory(); }}
            />
          </div>
          <!-- Has Repo: three-way toggle All / With repo / Without repo -->
          <div class="flex items-center gap-1">
            <span class="text-[10px] text-text-dim w-14 shrink-0">Repo:</span>
            {#each (['All', 'With', 'Without'] as const) as label}
              {@const val = label === 'All' ? undefined : label === 'With' ? true : false}
              <button
                class="text-[9px] px-1.5 py-0.5 border {history.filters.has_repo === val ? 'border-neon-cyan/50 text-neon-cyan' : 'border-border-subtle text-text-dim hover:border-neon-cyan/30'} transition-colors"
                onclick={() => { history.updateFilters({ has_repo: val, offset: 0 }); loadHistory(); }}
              >{label}</button>
            {/each}
          </div>
          <!-- Task Type dropdown -->
          <div class="flex items-center gap-1">
            <span class="text-[10px] text-text-dim w-14 shrink-0">Type:</span>
            <select
              class="flex-1 bg-bg-input border border-border-subtle px-1 py-0.5 text-[10px] text-text-primary focus:outline-none focus:border-neon-cyan/30 appearance-none"
              value={history.filters.task_type ?? ''}
              onchange={(e) => { const v = (e.target as HTMLSelectElement).value; history.updateFilters({ task_type: v || undefined, offset: 0 }); loadHistory(); }}
            >
              <option value="">All</option>
              <option value="instruction">instruction</option>
              <option value="conversation">conversation</option>
              <option value="system">system</option>
              <option value="transformation">transformation</option>
              <option value="other">other</option>
            </select>
          </div>
          <!-- Status dropdown -->
          <div class="flex items-center gap-1">
            <span class="text-[10px] text-text-dim w-14 shrink-0">Status:</span>
            <select
              class="flex-1 bg-bg-input border border-border-subtle px-1 py-0.5 text-[10px] text-text-primary focus:outline-none focus:border-neon-cyan/30 appearance-none"
              value={history.filters.status ?? ''}
              onchange={(e) => { const v = (e.target as HTMLSelectElement).value; history.updateFilters({ status: v || undefined, offset: 0 }); loadHistory(); }}
            >
              <option value="">All</option>
              <option value="completed">completed</option>
              <option value="failed">failed</option>
              <option value="running">running</option>
            </select>
          </div>
          <!-- Clear filters -->
          {#if history.filters.has_repo !== undefined || history.filters.min_score !== undefined || history.filters.max_score !== undefined || history.filters.task_type || history.filters.status}
            <button
              class="text-[9px] text-neon-red/70 hover:text-neon-red transition-colors"
              onclick={() => { history.updateFilters({ has_repo: undefined, min_score: undefined, max_score: undefined, task_type: undefined, status: undefined, offset: 0 }); loadHistory(); }}
            >&#x2715; Clear filters</button>
          {/if}
        </div>
      {/if}
      <div class="flex items-center gap-1">
        <span class="text-[10px] text-text-dim mr-1">Sort:</span>
        <button
          class="text-[10px] px-1.5 py-0.5
            {history.filters.sortBy === 'created_at' ? 'btn-outline-cyan' : 'btn-outline-subtle'}"
          onclick={() => { history.updateFilters(history.filters.sortBy === 'created_at' ? { sortDir: history.filters.sortDir === 'desc' ? 'asc' : 'desc', offset: 0 } : { sortBy: 'created_at', sortDir: 'desc', offset: 0 }); loadHistory(); }}
        >
          Date {history.filters.sortBy === 'created_at' ? (history.filters.sortDir === 'desc' ? '↓' : '↑') : ''}
        </button>
        <button
          class="text-[10px] px-1.5 py-0.5
            {history.filters.sortBy === 'overall_score' ? 'btn-outline-cyan' : 'btn-outline-subtle'}"
          onclick={() => { history.updateFilters(history.filters.sortBy === 'overall_score' ? { sortDir: history.filters.sortDir === 'desc' ? 'asc' : 'desc', offset: 0 } : { sortBy: 'overall_score', sortDir: 'desc', offset: 0 }); loadHistory(); }}
        >
          Score {history.filters.sortBy === 'overall_score' ? (history.filters.sortDir === 'desc' ? '↓' : '↑') : ''}
        </button>
        <button
          class="text-[10px] px-1.5 py-0.5 border {showFilters ? 'border-neon-cyan/50 text-neon-cyan' : 'border-border-subtle text-text-dim hover:border-neon-cyan/30 hover:text-neon-cyan/70'} transition-colors"
          aria-expanded={showFilters}
          onclick={() => { showFilters = !showFilters; }}
        >
          FILTER
        </button>
      </div>
      <div class="text-[10px] text-text-dim">
        {#if history.filters.search || history.filters.strategy || history.filters.has_repo !== undefined || history.filters.min_score !== undefined || history.filters.max_score !== undefined || history.filters.task_type || history.filters.status}
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
    <div class="flex-1 overflow-y-auto p-1" style="overscroll-behavior: contain;" role="listbox" aria-multiselectable="true" aria-label="Optimization history">
      {#if loading}
        <!-- Skeleton loading rows with shimmer animation -->
        <div class="space-y-1 p-1" data-testid="history-skeleton">
          {#each Array(5) as _, i}
            <div class="h-[32px] flex items-center gap-2 px-2 rounded" style="animation: shimmer 2s linear infinite; animation-delay: {i * 50}ms;">
              <div class="w-5 h-5 rounded-full bg-bg-hover animate-shimmer" style="background: linear-gradient(90deg, var(--color-bg-hover) 25%, var(--color-bg-card) 50%, var(--color-bg-hover) 75%); background-size: 200% 100%;"></div>
              <div class="flex-1 space-y-1">
                <div class="h-2.5 rounded bg-bg-hover animate-shimmer" style="width: {70 + i * 5}%; background: linear-gradient(90deg, var(--color-bg-hover) 25%, var(--color-bg-card) 50%, var(--color-bg-hover) 75%); background-size: 200% 100%;"></div>
                <div class="h-2 rounded bg-bg-hover animate-shimmer" style="width: {40 + i * 8}%; background: linear-gradient(90deg, var(--color-bg-hover) 25%, var(--color-bg-card) 50%, var(--color-bg-hover) 75%); background-size: 200% 100%;"></div>
              </div>
            </div>
          {/each}
        </div>
      {:else if history.entries.length === 0}
        <div class="flex flex-col items-center justify-center text-center px-2 py-8">
          <svg class="w-8 h-8 mb-2 opacity-30" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="1">
            <path stroke-linecap="round" stroke-linejoin="round" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z"></path>
          </svg>
          <p class="text-xs text-text-secondary">No runs yet</p>
          <p class="text-[10px] text-text-dim/50 mt-0.5">Synthesize a prompt to get started.</p>
        </div>
      {:else}
        {#each history.entries as entry, i (entry.id)}
          <div
            class="w-full text-left px-2 rounded text-xs transition-colors duration-200 mb-0.5 cursor-pointer group/entry h-[32px] flex items-center
              {selectedIds.has(entry.id)
                ? 'bg-neon-cyan/5 border border-neon-cyan/20'
                : history.selectedId === entry.id
                  ? 'bg-bg-hover border border-border-accent'
                  : 'hover:bg-bg-hover border border-transparent'}"
            style="animation: list-item-in 0.2s cubic-bezier(0.16,1,0.3,1) {i*25}ms both;"
            role="option"
            aria-selected={selectedIds.has(entry.id) || history.selectedId === entry.id}
            tabindex={i === 0 ? 0 : -1}
            data-history-row
            onclick={() => openHistoryEntry(entry)}
            onkeydown={(e: KeyboardEvent) => {
              if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openHistoryEntry(entry); }
              else if (e.key === 'ArrowDown') { e.preventDefault(); const rows = document.querySelectorAll<HTMLElement>('[data-history-row]'); rows[i + 1]?.focus(); }
              else if (e.key === 'ArrowUp') { e.preventDefault(); const rows = document.querySelectorAll<HTMLElement>('[data-history-row]'); rows[i - 1]?.focus(); }
            }}
          >
            <div class="flex items-start gap-2 flex-1 min-w-0">
              <!-- Checkbox: visible on hover or when selected -->
              <label
                class="flex items-center justify-center w-4 h-4 shrink-0 mt-0.5 cursor-pointer
                  {selectedIds.has(entry.id) ? 'opacity-100' : 'opacity-0 group-hover/entry:opacity-100'} transition-opacity"
              >
                <input
                  type="checkbox"
                  name="history-entry-select"
                  checked={selectedIds.has(entry.id)}
                  onchange={(e: Event) => toggleSelect(e as unknown as MouseEvent, entry.id)}
                  onclick={(e: MouseEvent) => e.stopPropagation()}
                  class="w-3 h-3 rounded border-border-subtle accent-neon-cyan cursor-pointer"
                />
              </label>
              {#if entry.overall_score != null}
                <ScoreCircle score={entry.overall_score} size={20} />
              {/if}
              <div class="flex-1 min-w-0">
                {#if editingId === entry.id}
                  <input
                    type="text"
                    bind:this={titleInputEl}
                    bind:value={editingValue}
                    class="w-full bg-transparent border border-neon-cyan/50 px-1 py-0 text-xs text-text-primary font-sans focus:outline-none"
                    onclick={(e: MouseEvent) => e.stopPropagation()}
                    onblur={() => commitTitleEdit(entry)}
                    onkeydown={(e: KeyboardEvent) => {
                      e.stopPropagation();
                      if (e.key === 'Enter') { e.preventDefault(); commitTitleEdit(entry); }
                      if (e.key === 'Escape') { e.preventDefault(); cancelTitleEdit(); }
                    }}
                  />
                {:else}
                  <p
                    class="text-text-primary truncate cursor-default"
                    ondblclick={(e: MouseEvent) => startTitleEdit(e, entry)}
                    title="Double-click to rename"
                  >{entry.title || entry.raw_prompt}</p>
                {/if}
                <div class="flex items-center gap-2 mt-0.5 min-w-0">
                  {#if entry.primary_framework}
                    <span class="text-[10px] truncate" style="color: {getStrategyHex(entry.primary_framework)}">{entry.primary_framework}</span>
                  {/if}
                  <span class="text-[10px] text-text-dim shrink-0">{new Date(entry.created_at).toLocaleDateString()}</span>
                </div>
              </div>
              <!-- Context menu "..." button -->
              <div class="relative shrink-0">
                <button
                  class="w-5 h-5 flex items-center justify-center opacity-0 group-hover/entry:opacity-100 text-text-dim hover:text-text-primary transition-all font-mono text-[11px] leading-none"
                  onclick={(e: MouseEvent) => { e.stopPropagation(); contextMenuId = contextMenuId === entry.id ? null : entry.id; }}
                  aria-label="More options"
                  title="More options"
                >
                  ···
                </button>
                {#if contextMenuId === entry.id}
                  <div
                    role="menu"
                    tabindex="-1"
                    class="absolute right-0 top-full mt-0.5 w-28 bg-bg-card border border-border-subtle z-[300] font-mono"
                    onclick={(e) => e.stopPropagation()}
                    onkeydown={(e) => e.stopPropagation()}
                  >
                    <button
                      class="w-full text-left px-3 py-1.5 text-[11px] text-neon-cyan/80 hover:bg-bg-hover hover:text-neon-cyan transition-colors"
                      onclick={(e: MouseEvent) => handleHistoryRetry(e, entry.id)}
                    >
                      ↺ Retry
                    </button>
                    <button
                      class="w-full text-left px-3 py-1.5 text-[11px] text-text-dim hover:bg-bg-hover hover:text-neon-red transition-colors"
                      onclick={(e: MouseEvent) => { contextMenuId = null; handleDelete(e, entry.id); }}
                    >
                      Delete
                    </button>
                  </div>
                {/if}
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
  {/if}

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
