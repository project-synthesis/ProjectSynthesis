<script lang="ts">
  import { workbench } from '$lib/stores/workbench.svelte';
  import { editor } from '$lib/stores/editor.svelte';
  import { forge } from '$lib/stores/forge.svelte';
  import ScoreCircle from '$lib/components/shared/ScoreCircle.svelte';
  import ScoreBar from '$lib/components/shared/ScoreBar.svelte';

  let strategyRecommendations = $derived.by(() => {
    const promptLen = (editor.activeTab?.promptText || '').length;
    if (promptLen > 200) {
      return [
        { name: 'CO-STAR', confidence: 0.85, desc: 'Best for detailed task prompts' },
        { name: 'chain-of-thought', confidence: 0.72, desc: 'Great for reasoning tasks' },
        { name: 'RISEN', confidence: 0.65, desc: 'Good for role-based prompts' }
      ];
    } else if (promptLen > 50) {
      return [
        { name: 'role-task-format', confidence: 0.78, desc: 'Simple and effective' },
        { name: 'step-by-step', confidence: 0.71, desc: 'Breaks down complex tasks' },
        { name: 'few-shot-scaffolding', confidence: 0.60, desc: 'Learn by example' }
      ];
    }
    return [
      { name: 'auto', confidence: 0.90, desc: 'Let PromptForge choose' },
      { name: 'context-enrichment', confidence: 0.65, desc: 'Add more context' },
      { name: 'persona-assignment', confidence: 0.55, desc: 'Assign expert role' }
    ];
  });
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
      <span class="font-display text-[12px] font-bold uppercase text-text-dim" style="letter-spacing: 0.1em;">Inspector</span>
    </div>

    <div class="flex-1 overflow-y-auto p-3 space-y-4">
      {#if forge.isForging || forge.overallScore != null}
        <!-- Pipeline status -->
        <div class="space-y-2">
          <h3 class="font-display text-[12px] font-bold text-text-dim uppercase">Pipeline</h3>

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
            <h3 class="font-display text-[12px] font-bold text-text-dim uppercase">Scores</h3>
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

        <!-- Original Prompt -->
        {#if forge.rawPrompt}
          <div class="space-y-2">
            <h3 class="font-display text-[12px] font-bold text-text-dim uppercase">Original Prompt</h3>
            <div class="text-xs text-text-secondary bg-bg-card rounded-lg border border-border-subtle p-2 max-h-32 overflow-y-auto whitespace-pre-wrap break-words">
              {forge.rawPrompt}
            </div>
          </div>
        {/if}
      {:else if editor.activeTab}
        <!-- Strategy Recommendations (when Edit sub-tab is active) -->
        {#if editor.activeSubTab === 'edit'}
          <div class="space-y-2">
            <h3 class="font-display text-[12px] font-bold text-text-dim uppercase">Strategy Recommendations</h3>
            <div class="space-y-2">
              {#each strategyRecommendations as rec}
                <div class="space-y-1">
                  <div class="flex justify-between text-xs">
                    <span class="text-text-secondary">{rec.name}</span>
                    <span class="text-text-dim">{Math.round(rec.confidence * 100)}%</span>
                  </div>
                  <div class="h-1.5 bg-bg-card rounded-full overflow-hidden">
                    <div
                      class="h-full rounded-full transition-all duration-500"
                      style="width: {rec.confidence * 100}%; background: linear-gradient(90deg, var(--color-neon-cyan), var(--color-neon-purple))"
                    ></div>
                  </div>
                  <p class="text-[10px] text-text-dim">{rec.desc}</p>
                </div>
              {/each}
            </div>
          </div>
        {/if}

        <!-- Document Info -->
        <div class="space-y-2">
          <h3 class="font-display text-[12px] font-bold text-text-dim uppercase">Document Info</h3>
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
