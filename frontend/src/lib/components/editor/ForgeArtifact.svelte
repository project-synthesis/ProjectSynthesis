<script lang="ts">
  import { forge } from '$lib/stores/forge.svelte';
  import { patchOptimization } from '$lib/api/client';
  import CopyButton from '$lib/components/shared/CopyButton.svelte';
  import ScoreCircle from '$lib/components/shared/ScoreCircle.svelte';
  import ScoreBar from '$lib/components/shared/ScoreBar.svelte';
  import DiffView from '$lib/components/shared/DiffView.svelte';

  type ArtifactSubTab = 'optimized' | 'diff' | 'scores' | 'trace';
  let activeSubTab = $state<ArtifactSubTab>('optimized');

  const subTabs: { id: ArtifactSubTab; label: string }[] = [
    { id: 'optimized', label: 'Optimized' },
    { id: 'diff', label: 'Diff' },
    { id: 'scores', label: 'Scores' },
    { id: 'trace', label: 'Trace' }
  ];

  let editingTitle = $state(false);
  let titleInput = $state('');
  let displayTitle = $state('Forge Artifact');

  $effect(() => {
    if (forge.optimizationId) displayTitle = 'Forge Artifact';
  });

  async function saveTitle() {
    // Guard against blur firing after Escape already cancelled the edit
    if (!editingTitle || !forge.optimizationId || !titleInput.trim()) {
      editingTitle = false;
      return;
    }
    try {
      await patchOptimization(forge.optimizationId, { title: titleInput.trim() });
      displayTitle = titleInput.trim();
    } catch { /* non-fatal */ }
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
  <div class="flex items-center justify-between px-4 py-2 border-b border-border-subtle shrink-0">
    {#if editingTitle && forge.optimizationId}
      <input
        class="text-sm font-semibold text-text-primary bg-transparent border-b
               border-neon-cyan/50 focus:outline-none max-w-[200px]"
        bind:value={titleInput}
        onblur={saveTitle}
        onkeydown={(e) => {
          if (e.key === 'Enter') saveTitle();
          if (e.key === 'Escape') { editingTitle = false; titleInput = displayTitle; }
        }}
        autofocus
      />
    {:else}
      <h2
        class="text-sm font-semibold text-text-primary
               {forge.optimizationId ? 'cursor-pointer hover:text-neon-cyan/80 transition-colors' : ''}"
        ondblclick={() => {
          if (forge.optimizationId) { titleInput = displayTitle; editingTitle = true; }
        }}
        title={forge.optimizationId ? 'Double-click to rename' : ''}
      >{displayTitle}</h2>
    {/if}
    {#if forge.overallScore != null}
      <ScoreCircle score={forge.overallScore} size={32} />
    {/if}
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
  <div class="flex-1 overflow-y-auto p-4">
    {#if activeSubTab === 'optimized'}
      {#if forge.streamingText}
        <div class="bg-bg-card border border-border-subtle rounded-lg p-4">
          <div class="flex items-center justify-between mb-2">
            <span class="text-xs text-text-secondary font-medium">Optimized Prompt</span>
            <CopyButton text={forge.streamingText} />
          </div>
          <pre class="text-sm text-text-primary font-mono whitespace-pre-wrap leading-relaxed">{forge.streamingText}</pre>
        </div>
      {:else}
        <div class="text-center py-12">
          <p class="text-sm text-text-dim">No artifact generated yet. Forge a prompt first.</p>
        </div>
      {/if}

    {:else if activeSubTab === 'diff'}
      {#if forge.rawPrompt && forge.streamingText}
        <DiffView original={forge.rawPrompt} modified={forge.streamingText} />
      {:else}
        <div class="text-center py-12">
          <p class="text-sm text-text-dim">Run a forge to see the diff comparison.</p>
        </div>
      {/if}

    {:else if activeSubTab === 'scores'}
      {#if Object.keys(scores).length > 0}
        <div class="space-y-3">
          {#each Object.entries(scores) as [key, val]}
            <div class="space-y-1">
              <div class="flex justify-between text-xs">
                <span class="text-text-secondary capitalize">{key.replace(/_/g, ' ')}</span>
                <span class="text-text-primary">{val}/10</span>
              </div>
              <ScoreBar score={typeof val === 'number' ? val : 0} max={10} />
            </div>
          {/each}
        </div>
      {:else}
        <div class="text-center py-12">
          <p class="text-sm text-text-dim">Scores will appear after validation completes.</p>
        </div>
      {/if}

    {:else if activeSubTab === 'trace'}
      {#if forge.pipelineEvents.length > 0}
        <div class="space-y-1 font-mono text-xs">
          {#each forge.pipelineEvents as ev, i}
            <div class="flex items-start gap-2 py-1 px-2 rounded hover:bg-bg-hover/30">
              <span class="text-text-dim/50 w-4 text-right shrink-0">{i + 1}</span>
              <span class="text-neon-cyan/70">{new Date(ev.timestamp).toLocaleTimeString()}</span>
              <span class="text-text-secondary">{ev.type}</span>
              {#if ev.stage}
                <span class="text-neon-purple capitalize">{ev.stage}</span>
              {/if}
            </div>
          {/each}
        </div>
      {:else}
        <div class="text-center py-12">
          <p class="text-sm text-text-dim">Pipeline trace will appear during forging.</p>
        </div>
      {/if}
    {/if}
  </div>
</div>
