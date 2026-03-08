<script lang="ts">
  import { tick } from 'svelte';
  import { forge } from '$lib/stores/forge.svelte';
  import { editor } from '$lib/stores/editor.svelte';
  import { patchOptimization } from '$lib/api/client';
  import CopyButton from '$lib/components/shared/CopyButton.svelte';
  import ScoreCircle from '$lib/components/shared/ScoreCircle.svelte';
  import ScoreBar from '$lib/components/shared/ScoreBar.svelte';
  import DiffView from '$lib/components/shared/DiffView.svelte';
  import StrategyBadge from '$lib/components/shared/StrategyBadge.svelte';
  import TraceView from '$lib/components/pipeline/TraceView.svelte';
  import { toast } from '$lib/stores/toast.svelte';
  import { getScoreColor } from '$lib/utils/colors';

  type ArtifactSubTab = 'optimized' | 'diff' | 'scores' | 'trace';
  let activeSubTab = $state<ArtifactSubTab>('optimized');
  let titleInputEl = $state<HTMLInputElement | undefined>();

  const subTabs: { id: ArtifactSubTab; label: string }[] = [
    { id: 'optimized', label: 'Optimized' },
    { id: 'diff', label: 'Diff' },
    { id: 'scores', label: 'Scores' },
    { id: 'trace', label: 'Trace' }
  ];

  let editingTitle = $state(false);
  let titleInput = $state('');
  let displayTitle = $state('Forge Artifact');
  let completedAt = $state<Date | null>(null);

  $effect(() => {
    if (forge.optimizationId) displayTitle = 'Forge Artifact';
  });

  $effect(() => {
    if (titleInputEl) titleInputEl.focus();
  });

  $effect(() => {
    if (forge.overallScore != null && !forge.isForging) {
      completedAt = new Date();
    } else if (forge.isForging) {
      completedAt = null;
    }
  });

  async function handleReforge() {
    editor.setSubTab('edit');
    await tick();
    document.querySelector<HTMLButtonElement>('[data-testid="forge-button"]')?.click();
  }

  async function saveTitle() {
    // Guard against blur firing after Escape already cancelled the edit
    if (!editingTitle || !forge.optimizationId || !titleInput.trim()) {
      editingTitle = false;
      return;
    }
    try {
      await patchOptimization(forge.optimizationId, { title: titleInput.trim() });
      displayTitle = titleInput.trim();
      toast.success('Title saved');
    } catch {
      toast.error('Failed to save title');
    }
    editingTitle = false;
  }

  let validationData = $derived(
    forge.stageResults['validate']?.data as Record<string, unknown> || {}
  );
  let scores = $derived(
    (validationData.scores || {}) as Record<string, number>
  );
</script>

<div class="flex flex-col h-full animate-fade-in">
  <!-- Header -->
  <div class="flex items-center justify-between px-4 py-2 border-b border-border-subtle shrink-0 gap-2">
    <div class="flex items-center gap-2 min-w-0">
      {#if editingTitle && forge.optimizationId}
        <input
          name="artifact-title"
          class="text-sm font-semibold text-text-primary bg-transparent border-b
                 border-neon-cyan/50 focus:outline-none max-w-[200px]"
          bind:this={titleInputEl}
          bind:value={titleInput}
          onblur={saveTitle}
          onkeydown={(e) => {
            if (e.key === 'Enter') saveTitle();
            if (e.key === 'Escape') { editingTitle = false; titleInput = displayTitle; }
          }}
        />
      {:else}
        <div class="group flex items-center gap-1.5 min-w-0">
          <h2
            class="text-sm font-semibold text-text-primary shrink-0
                   {forge.optimizationId ? 'cursor-pointer hover:text-neon-cyan/80 transition-colors' : ''}"
            ondblclick={() => {
              if (forge.optimizationId) { titleInput = displayTitle; editingTitle = true; }
            }}
            title={forge.optimizationId ? 'Double-click to rename' : ''}
          >{displayTitle}</h2>
          {#if forge.optimizationId}
            <svg class="w-3 h-3 text-text-dim opacity-0 group-hover:opacity-40 transition-opacity shrink-0"
                 fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="1.5">
              <path stroke-linecap="round" stroke-linejoin="round" d="M16.862 4.487l1.687-1.688a1.875 1.875 0 112.652 2.652L10.582 16.07a4.5 4.5 0 01-1.897 1.13L6 18l.8-2.685a4.5 4.5 0 011.13-1.897l8.932-8.931zm0 0L19.5 7.125"></path>
            </svg>
          {/if}
        </div>
      {/if}
      {#if forge.stageResults?.strategy?.data?.primary_framework}
        <StrategyBadge strategy={forge.stageResults.strategy.data.primary_framework as string} />
      {/if}
      {#if completedAt}
        <span class="text-[10px] text-text-dim font-mono shrink-0">
          {completedAt.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
        </span>
      {/if}
    </div>
    <div class="flex items-center gap-2 shrink-0">
      {#if forge.streamingText && !forge.isForging}
        <button
          class="text-[10px] px-2 py-1 rounded bg-bg-card border border-border-subtle text-text-secondary hover:border-neon-cyan/30 hover:text-text-primary transition-colors"
          onclick={handleReforge}
          title="Re-synthesize this prompt"
        >
          Re-run
        </button>
      {/if}
      {#if forge.overallScore != null}
        <ScoreCircle score={forge.overallScore} size={28} />
      {/if}
    </div>
  </div>

  <!-- Sub-tab bar -->
  <div class="flex items-center h-8 border-b border-border-subtle bg-bg-secondary/50 px-2 gap-1 shrink-0">
    {#each subTabs as st}
      <button
        class="px-3 py-1 text-xs rounded-t transition-colors
          {activeSubTab === st.id
            ? 'text-neon-cyan border-b border-neon-cyan bg-bg-primary'
            : 'text-text-dim hover:text-text-secondary'}"
        onclick={() => { activeSubTab = st.id; }}
      >
        {st.label}
      </button>
    {/each}
  </div>

  <!-- Sub-tab content -->
  <div class="flex-1 overflow-y-auto p-4" style="overscroll-behavior: contain;">
    {#if activeSubTab === 'optimized'}
      {#if forge.streamingText}
        <div class="bg-bg-card border border-border-subtle rounded-lg p-4">
          <div class="flex items-center justify-between mb-2">
            <span class="text-xs text-text-secondary font-medium">Optimized Prompt</span>
            <CopyButton text={forge.streamingText} />
          </div>
          <p class="text-[13px] text-text-primary font-sans whitespace-pre-wrap leading-relaxed">{forge.streamingText}</p>
        </div>
      {:else}
        <div class="text-center py-12">
          <p class="text-sm text-text-dim">No artifact generated yet. Synthesize a prompt first.</p>
        </div>
      {/if}

    {:else if activeSubTab === 'diff'}
      {#if forge.rawPrompt && forge.streamingText}
        <DiffView original={forge.rawPrompt} modified={forge.streamingText} />
      {:else}
        <div class="text-center py-12">
          <p class="text-sm text-text-dim">Run a synthesis to see the diff comparison.</p>
        </div>
      {/if}

    {:else if activeSubTab === 'scores'}
      {#if Object.keys(scores).length > 0}
        <div class="space-y-3">
          {#each Object.entries(scores).filter(([k]) => k !== 'overall_score') as [key, val]}
            {@const scoreVal = typeof val === 'number' ? val : 0}
            <div class="space-y-1">
              <div class="flex justify-between text-xs">
                <span class="text-text-secondary capitalize">{key.replace(/_/g, ' ')}</span>
                <span class="font-mono text-text-primary">{scoreVal}/10</span>
              </div>
              <div class="relative h-1.5 bg-bg-primary overflow-hidden"
                   style="--bar-accent: {getScoreColor(scoreVal)}33;">
                <ScoreBar score={scoreVal} max={10} />
                <div class="bar-glass absolute inset-0 pointer-events-none"></div>
              </div>
            </div>
          {/each}
        </div>
      {:else}
        <div class="text-center py-12">
          <p class="text-sm text-text-dim">Scores will appear after validation completes.</p>
        </div>
      {/if}

    {:else if activeSubTab === 'trace'}
      <TraceView />
    {/if}
  </div>
</div>
