<script lang="ts">
  import { history } from '$lib/stores/history.svelte';
  import { editor } from '$lib/stores/editor.svelte';
  import { forge } from '$lib/stores/forge.svelte';
  import { fetchHistory, retryOptimization } from '$lib/api/client';
  import ScoreCircle from '$lib/components/shared/ScoreCircle.svelte';
  import { formatRelativeTime } from '$lib/utils/format';

  let isLoading = $state(false);
  let expandedId = $state<string | null>(null);
  let reforgingId = $state<string | null>(null);

  async function handleReforge(entry: typeof history.entries[0]) {
    reforgingId = entry.id;
    try {
      // Populate the edit tab with the original prompt
      if (editor.activeTab) {
        editor.activeTab.promptText = entry.raw_prompt;
      }
      // Switch to edit sub-tab to show the prompt
      editor.setSubTab('edit');
      // Trigger the re-forge via API
      await retryOptimization(entry.id, entry.strategy);
      // Reload history after re-forge
      await loadHistory();
    } catch {
      // Re-forge failed silently
    } finally {
      reforgingId = null;
    }
  }

  // Load history entries on mount
  $effect(() => {
    loadHistory();
  });

  async function loadHistory() {
    isLoading = true;
    try {
      const resp = await fetchHistory({ page: 1, page_size: 100, sort_by: 'created_at', sort_dir: 'desc' });
      history.setEntries(resp.items, resp.total);
    } catch {
      // silently handle
    } finally {
      isLoading = false;
    }
  }

  // Filter entries to show only runs matching the current prompt
  let promptRuns = $derived.by(() => {
    const currentPrompt = editor.activeTab?.promptText?.trim();
    if (!currentPrompt) return [];
    return history.entries.filter(e => {
      const entryPrompt = e.raw_prompt?.trim();
      if (!entryPrompt) return false;
      return entryPrompt === currentPrompt || currentPrompt.startsWith(entryPrompt) || entryPrompt.startsWith(currentPrompt);
    });
  });

  function toggleExpand(id: string) {
    expandedId = expandedId === id ? null : id;
  }

  function getScoreDelta(index: number): string | null {
    if (index >= promptRuns.length - 1) return null;
    const current = promptRuns[index].overall_score;
    const previous = promptRuns[index + 1].overall_score;
    if (current == null || previous == null) return null;
    const delta = current - previous;
    if (delta === 0) return '±0';
    return delta > 0 ? `+${delta}` : `${delta}`;
  }

  function getDeltaColor(index: number): string {
    const delta = getScoreDelta(index);
    if (!delta) return '';
    if (delta.startsWith('+')) return 'text-neon-green';
    if (delta.startsWith('-')) return 'text-neon-red';
    return 'text-text-dim';
  }
</script>

<div class="p-4 space-y-3 animate-fade-in">
  <div class="flex items-center justify-between">
    <h3 class="text-xs font-semibold text-text-secondary uppercase tracking-wider">Prompt History</h3>
    <button
      class="text-[10px] text-text-dim hover:text-neon-cyan transition-colors"
      onclick={loadHistory}
      disabled={isLoading}
    >
      {isLoading ? 'Loading...' : 'Refresh'}
    </button>
  </div>

  {#if isLoading}
    <div class="text-center py-8">
      <p class="text-sm text-text-dim animate-status-pulse">Loading history...</p>
    </div>
  {:else if promptRuns.length === 0}
    <div class="flex flex-col items-center justify-center text-center py-12">
      <svg class="w-10 h-10 mb-3 opacity-30" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="1">
        <path stroke-linecap="round" stroke-linejoin="round" d="M13 10V3L4 14h7v7l9-11h-7z"></path>
      </svg>
      <p class="text-sm text-text-dim">This prompt has never been forged</p>
      <p class="text-[10px] text-text-dim/50 mt-1">Press <kbd class="px-1 py-0.5 bg-bg-card rounded border border-border-subtle text-text-secondary">Ctrl+Enter</kbd> to run the optimization pipeline</p>
    </div>
  {:else}
    <!-- Table layout -->
    <div class="overflow-x-auto">
      <table class="w-full text-xs">
        <thead>
          <tr class="border-b border-border-subtle text-text-dim">
            <th class="text-left py-2 px-2 font-medium">Run#</th>
            <th class="text-left py-2 px-2 font-medium">Strategy</th>
            <th class="text-left py-2 px-2 font-medium">Score</th>
            <th class="text-left py-2 px-2 font-medium">Duration</th>
            <th class="text-left py-2 px-2 font-medium">Date</th>
          </tr>
        </thead>
        <tbody>
          {#each promptRuns as entry, i (entry.id)}
            <tr
              class="border-b border-border-subtle/50 hover:bg-bg-hover transition-colors cursor-pointer animate-stagger-fade-in"
              onclick={() => toggleExpand(entry.id)}
              class:bg-bg-card={expandedId === entry.id}
            >
              <td class="py-2 px-2 text-text-secondary font-mono">#{promptRuns.length - i}</td>
              <td class="py-2 px-2">
                {#if entry.strategy}
                  <span class="px-1.5 py-0.5 rounded bg-neon-purple/10 text-neon-purple border border-neon-purple/20 text-[10px]">
                    {entry.strategy}
                  </span>
                {:else}
                  <span class="text-text-dim">auto</span>
                {/if}
              </td>
              <td class="py-2 px-2">
                {#if entry.overall_score != null}
                  <div class="flex items-center gap-1.5">
                    <ScoreCircle score={entry.overall_score} size={20} />
                    <span class="text-text-primary">{entry.overall_score}/10</span>
                    {#if getScoreDelta(i)}
                      <span class="text-[10px] {getDeltaColor(i)}">{getScoreDelta(i)}</span>
                    {/if}
                  </div>
                {:else}
                  <span class="text-text-dim">–</span>
                {/if}
              </td>
              <td class="py-2 px-2 text-text-dim font-mono">
                {#if entry.duration_ms}
                  {(entry.duration_ms / 1000).toFixed(1)}s
                {:else}
                  –
                {/if}
              </td>
              <td class="py-2 px-2 text-text-dim">
                {formatRelativeTime(entry.created_at)}
              </td>
            </tr>
            <!-- Expandable trace row -->
            {#if expandedId === entry.id}
              <tr class="bg-bg-card/50">
                <td colspan="5" class="px-4 py-3">
                  <div class="space-y-2">
                    <h4 class="text-[10px] font-semibold text-text-secondary uppercase tracking-wider">Stage Trace</h4>
                    <div class="space-y-1 font-mono text-[10px]">
                      <div class="flex items-center gap-2">
                        <span class="w-2 h-2 rounded-full bg-neon-green"></span>
                        <span class="text-text-secondary">Analyze</span>
                        <span class="text-text-dim">→ Task classification complete</span>
                      </div>
                      <div class="flex items-center gap-2">
                        <span class="w-2 h-2 rounded-full bg-neon-green"></span>
                        <span class="text-text-secondary">Strategy</span>
                        <span class="text-text-dim">→ {entry.strategy || 'auto'} selected</span>
                      </div>
                      <div class="flex items-center gap-2">
                        <span class="w-2 h-2 rounded-full bg-neon-green"></span>
                        <span class="text-text-secondary">Optimize</span>
                        <span class="text-text-dim">→ Prompt optimized</span>
                      </div>
                      <div class="flex items-center gap-2">
                        <span class="w-2 h-2 rounded-full bg-neon-green"></span>
                        <span class="text-text-secondary">Validate</span>
                        <span class="text-text-dim">→ Score: {entry.overall_score ?? '–'}/10</span>
                      </div>
                    </div>
                    {#if entry.duration_ms}
                      <p class="text-[10px] text-text-dim mt-1">Total duration: {(entry.duration_ms / 1000).toFixed(1)}s</p>
                    {/if}
                    <div class="mt-2 pt-2 border-t border-border-subtle/50">
                      <button
                        class="text-[10px] px-2 py-1 rounded bg-neon-cyan/10 border border-neon-cyan/20 text-neon-cyan hover:bg-neon-cyan/20 transition-colors disabled:opacity-50"
                        onclick={(e: MouseEvent) => { e.stopPropagation(); handleReforge(entry); }}
                        disabled={reforgingId === entry.id}
                      >
                        {reforgingId === entry.id ? 'Re-forging...' : 'Re-forge'}
                      </button>
                    </div>
                  </div>
                </td>
              </tr>
            {/if}
          {/each}
        </tbody>
      </table>
    </div>
  {/if}
</div>
