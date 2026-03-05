<script lang="ts">
  import { forge } from '$lib/stores/forge.svelte';
  import ScoreCircle from '$lib/components/shared/ScoreCircle.svelte';
</script>

<div class="bg-bg-card border border-border-subtle rounded-lg p-3">
  <div class="flex items-center justify-between">
    <div class="flex items-center gap-3">
      {#if forge.overallScore != null}
        <ScoreCircle score={forge.overallScore} size={36} />
      {:else if forge.isForging}
        <div class="w-9 h-9 rounded-full animate-spin" style="border: 2px solid rgba(0, 229, 255, 0.3); border-top-color: #00e5ff;"></div>
      {/if}

      <div>
        <h3 class="text-sm font-semibold text-text-primary">
          {#if forge.isForging}
            Forging in progress...
          {:else if forge.overallScore != null}
            Forge Complete
          {:else}
            Forge Error
          {/if}
        </h3>
        <p class="text-[10px] text-text-secondary mt-0.5 font-mono">
          {forge.completedStages} of {forge.visibleStages.length} stages completed
          {#if forge.totalDuration != null}
            <span class="ml-1">· {(forge.totalDuration / 1000).toFixed(1)}s</span>
          {/if}
          {#if forge.overallScore != null}
            <span class="ml-1">· {forge.overallScore}/10</span>
          {/if}
          {#if forge.totalTokens != null}
            <span class="ml-1">· {forge.totalTokens.toLocaleString()} tok</span>
          {/if}
          {#if forge.error}
            <span class="text-neon-red ml-1">{forge.error}</span>
          {/if}
        </p>
      </div>
    </div>

    <!-- Progress dots -->
    <div class="flex items-center gap-1">
      {#each forge.stages.filter(s => !(s === 'explore' && forge.stageStatuses[s] === 'idle')) as stage}
        {@const st = forge.stageStatuses[stage]}
        <div class="w-2 h-2 rounded-full transition-colors {
          st === 'done' ? 'bg-neon-green' :
          st === 'running' ? 'bg-neon-cyan animate-status-pulse' :
          st === 'error' ? 'bg-neon-red' :
          'bg-text-dim/20'
        }" title={stage}></div>
      {/each}
    </div>
  </div>
</div>
