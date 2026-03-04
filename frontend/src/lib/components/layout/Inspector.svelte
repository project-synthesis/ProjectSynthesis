<script lang="ts">
  import { workbench } from '$lib/stores/workbench.svelte';
  import { editor } from '$lib/stores/editor.svelte';
  import { forge } from '$lib/stores/forge.svelte';
  import ScoreCircle from '$lib/components/shared/ScoreCircle.svelte';
  import ScoreBar from '$lib/components/shared/ScoreBar.svelte';
</script>

<aside
  class="bg-bg-secondary border-l border-border-subtle flex flex-col overflow-hidden transition-all duration-200"
  class:w-0={workbench.inspectorCollapsed}
  class:opacity-0={workbench.inspectorCollapsed}
  style="width: {workbench.inspectorCssWidth}"
  aria-label="Inspector"
>
  {#if !workbench.inspectorCollapsed}
    <div class="h-9 flex items-center px-3 border-b border-border-subtle shrink-0">
      <span class="text-xs font-semibold uppercase tracking-wider text-text-secondary">Inspector</span>
    </div>

    <div class="flex-1 overflow-y-auto p-3 space-y-4">
      {#if forge.isForging || forge.overallScore != null}
        <!-- Pipeline status -->
        <div class="space-y-2">
          <h3 class="text-xs font-semibold text-text-secondary uppercase tracking-wider">Pipeline</h3>

          {#if forge.overallScore != null}
            <div class="flex items-center gap-3 p-2 bg-bg-card rounded-lg border border-border-subtle">
              <ScoreCircle score={forge.overallScore} size={40} />
              <div>
                <div class="text-sm font-medium text-text-primary">Overall Score</div>
                <div class="text-xs text-text-dim">{forge.completedStages}/{forge.stages.filter(s => forge.stageStatuses[s] !== 'idle' || s !== 'explore').length} stages completed</div>
              </div>
            </div>
          {/if}

          {#each forge.stages.filter(s => !(s === 'explore' && forge.stageStatuses[s] === 'idle')) as stage}
            {@const status = forge.stageStatuses[stage]}
            <div class="flex items-center gap-2 text-xs">
              <span class="w-2 h-2 rounded-full {
                status === 'done' ? 'bg-neon-green' :
                status === 'running' ? 'bg-neon-cyan animate-status-pulse' :
                status === 'error' ? 'bg-neon-red' :
                'bg-text-dim/30'
              }"></span>
              <span class="capitalize {status === 'running' ? 'text-neon-cyan' : 'text-text-secondary'}">{stage}</span>
            </div>
          {/each}
        </div>

        <!-- Score breakdown -->
        {#if forge.stageResults['validate']}
          {@const validation = forge.stageResults['validate']?.data as Record<string, unknown> || {}}
          {@const scores = (validation.scores || {}) as Record<string, number>}
          <div class="space-y-2">
            <h3 class="text-xs font-semibold text-text-secondary uppercase tracking-wider">Scores</h3>
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
        {/if}
      {:else if editor.activeTab}
        <!-- Prompt info -->
        <div class="space-y-2">
          <h3 class="text-xs font-semibold text-text-secondary uppercase tracking-wider">Document Info</h3>
          <div class="text-xs text-text-dim space-y-1">
            <div class="flex justify-between">
              <span>Type</span>
              <span class="text-text-secondary capitalize">{editor.activeTab.type}</span>
            </div>
            <div class="flex justify-between">
              <span>Characters</span>
              <span class="text-text-secondary">{(editor.activeTab.promptText || '').length}</span>
            </div>
            <div class="flex justify-between">
              <span>Words</span>
              <span class="text-text-secondary">{(editor.activeTab.promptText || '').split(/\s+/).filter(Boolean).length}</span>
            </div>
            <div class="flex justify-between">
              <span>Status</span>
              <span class="text-text-secondary">{editor.activeTab.dirty ? 'Modified' : 'Clean'}</span>
            </div>
          </div>
        </div>
      {:else}
        <div class="text-xs text-text-dim text-center py-8">
          Open a document to see details here.
        </div>
      {/if}
    </div>
  {/if}
</aside>
