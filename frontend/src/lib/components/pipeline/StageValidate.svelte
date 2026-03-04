<script lang="ts">
  import { forge } from '$lib/stores/forge.svelte';
  import ScoreCircle from '$lib/components/shared/ScoreCircle.svelte';
  import ScoreBar from '$lib/components/shared/ScoreBar.svelte';

  let result = $derived(forge.stageResults['validate']);
  let data = $derived((result?.data || {}) as Record<string, unknown>);
  let scores = $derived((data.scores || {}) as Record<string, number>);
</script>

<div class="space-y-2 text-xs">
  {#if forge.stageStatuses['validate'] === 'running'}
    <div class="flex items-center gap-2 text-neon-cyan">
      <div class="w-3 h-3 border border-neon-cyan/30 border-t-neon-cyan rounded-full animate-spin"></div>
      <span>Validating optimized prompt...</span>
    </div>
  {:else if result}
    <!-- Overall score -->
    {#if forge.overallScore != null}
      <div class="flex items-center gap-3 p-2 bg-bg-primary rounded border border-border-subtle">
        <ScoreCircle score={forge.overallScore} size={40} />
        <div>
          <span class="text-sm font-semibold text-text-primary">Overall Score</span>
          <span class="text-[10px] text-text-dim block">{forge.overallScore}/10</span>
        </div>
      </div>
    {/if}

    <!-- Individual scores -->
    {#each Object.entries(scores) as [key, val]}
      <div class="space-y-1">
        <div class="flex justify-between">
          <span class="text-text-dim capitalize">{key.replace(/_/g, ' ')}</span>
          <span class="text-text-secondary">{val}/10</span>
        </div>
        <ScoreBar score={val} max={10} />
      </div>
    {/each}

    <!-- Verdict -->
    {#if data.verdict}
      <div class="mt-2 p-2 bg-bg-primary rounded border border-border-subtle">
        <div class="flex items-center gap-2 mb-1">
          <span class="text-[10px] text-text-dim uppercase tracking-wider font-semibold">Verdict</span>
          {#if data.is_improvement === true}
            <span class="text-[10px] text-neon-green">✓ Improved</span>
          {:else if data.is_improvement === false}
            <span class="text-[10px] text-neon-red">✗ Not Improved</span>
          {/if}
        </div>
        <p class="text-text-secondary">{data.verdict}</p>
      </div>
    {/if}
  {:else}
    <p class="text-text-dim">Waiting for Optimize stage...</p>
  {/if}
</div>
