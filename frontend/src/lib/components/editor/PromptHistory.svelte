<script lang="ts">
  import { tick } from 'svelte';
  import { history } from '$lib/stores/history.svelte';
  import { editor } from '$lib/stores/editor.svelte';
  import { github } from '$lib/stores/github.svelte';
  import { fetchHistory } from '$lib/api/client';
  import ScoreCircle from '$lib/components/shared/ScoreCircle.svelte';
  import StrategyBadge from '$lib/components/shared/StrategyBadge.svelte';
  import { formatRelativeTime } from '$lib/utils/format';

  let isLoading = $state(false);
  let expandedId = $state<string | null>(null);
  let reforgingId = $state<string | null>(null);

  async function handleReforge(entry: typeof history.entries[0]) {
    reforgingId = entry.id;
    try {
      const tab = editor.activeTab;
      if (tab) {
        // Load the raw prompt and clear the stale optimizationId so the forge
        // result from this re-run replaces the old association on this tab
        tab.optimizationId = undefined;
        editor.updateTabPrompt(tab.id, entry.raw_prompt);
      }
      // Restore the linked repo from the original run so the Explore stage
      // re-runs with the same codebase context. Only overrides if the entry
      // had a repo — leaves the current selection intact otherwise.
      if (entry.linked_repo_full_name) {
        github.selectRepo(entry.linked_repo_full_name, entry.linked_repo_branch ?? undefined);
      }
      // Switch to edit sub-tab so the forge button is visible, then await DOM update
      editor.setSubTab('edit');
      await tick();
      document.querySelector<HTMLButtonElement>('[data-testid="forge-button"]')?.click();
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
      const resp = await fetchHistory({ offset: 0, limit: 100, sort: 'created_at', order: 'desc' });
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
    <h3 class="section-heading">Prompt History</h3>
    <button
      class="text-[10px] text-text-dim hover:text-neon-cyan transition-colors"
      onclick={loadHistory}
      disabled={isLoading}
    >
      {isLoading ? 'Loading...' : 'Refresh'}
    </button>
  </div>

  {#if isLoading}
    <div class="space-y-2">
      {#each [1, 2, 3, 4] as _, i}
        <div
          class="h-8 rounded border border-border-subtle animate-shimmer"
          style="background: linear-gradient(90deg, var(--color-bg-hover) 25%, var(--color-bg-card) 50%, var(--color-bg-hover) 75%); background-size: 200% 100%; animation-delay: {i * 60}ms;"
        ></div>
      {/each}
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
                <StrategyBadge strategy={entry.primary_framework ?? 'auto'} />
              </td>
              <td class="py-2 px-2">
                {#if entry.overall_score != null}
                  <div class="flex items-center gap-1.5">
                    <ScoreCircle score={entry.overall_score} size={20} />
                    <span class="text-text-primary">{entry.overall_score}/10</span>
                    {#if getScoreDelta(i)}
                      <span class="text-[11px] font-mono font-bold {getDeltaColor(i)}">{getScoreDelta(i)}</span>
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
                    <div class="space-y-0.5 font-mono text-[10px] py-1">
                      {#if entry.primary_framework}
                        <div class="flex items-center gap-2 py-0.5">
                          <span class="w-1.5 h-1.5 rounded-full bg-neon-purple/60 shrink-0"></span>
                          <span class="text-text-dim w-16 shrink-0">Strategy</span>
                          <span class="text-neon-purple/70">{entry.primary_framework}</span>
                        </div>
                      {/if}
                      {#if entry.model_optimize}
                        <div class="flex items-center gap-2 py-0.5">
                          <span class="w-1.5 h-1.5 rounded-full bg-neon-blue/60 shrink-0"></span>
                          <span class="text-text-dim w-16 shrink-0">Model</span>
                          <span class="text-text-secondary">{entry.model_optimize}</span>
                        </div>
                      {/if}
                      {#if entry.overall_score != null}
                        <div class="flex items-center gap-2 py-0.5">
                          <span class="w-1.5 h-1.5 rounded-full bg-neon-green/60 shrink-0"></span>
                          <span class="text-text-dim w-16 shrink-0">Score</span>
                          <span class="text-neon-green">{entry.overall_score}/10</span>
                        </div>
                      {/if}
                      {#if entry.duration_ms}
                        <div class="flex items-center gap-2 py-0.5">
                          <span class="w-1.5 h-1.5 rounded-full bg-neon-cyan/40 shrink-0"></span>
                          <span class="text-text-dim w-16 shrink-0">Duration</span>
                          <span class="text-text-secondary">{(entry.duration_ms/1000).toFixed(1)}s</span>
                        </div>
                      {/if}
                    </div>
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
