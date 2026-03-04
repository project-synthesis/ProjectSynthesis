<script lang="ts">
  import { forge } from '$lib/stores/forge.svelte';
  import StrategyBadge from '$lib/components/shared/StrategyBadge.svelte';

  let result = $derived(forge.stageResults['strategy']);
  let data = $derived((result?.data || {}) as Record<string, unknown>);
</script>

<div class="space-y-2 text-xs">
  {#if forge.stageStatuses['strategy'] === 'running'}
    <div class="flex items-center gap-2 text-neon-cyan">
      <div class="w-3 h-3 border border-neon-cyan/30 border-t-neon-cyan rounded-full animate-spin"></div>
      <span>Selecting optimization strategy...</span>
    </div>
  {:else if result}
    {#if data.primary_framework}
      <div class="flex items-center gap-2">
        <span class="text-text-dim">Selected:</span>
        <StrategyBadge strategy={data.primary_framework as string} />
      </div>
    {/if}

    {#if data.rationale}
      <p class="text-text-secondary mt-1">{data.rationale}</p>
    {/if}

    {#if data.secondary_frameworks && Array.isArray(data.secondary_frameworks) && (data.secondary_frameworks as string[]).length > 0}
      <div class="mt-2">
        <span class="text-[10px] text-text-dim uppercase tracking-wider font-semibold">Techniques</span>
        <div class="flex flex-wrap gap-1 mt-1">
          {#each data.secondary_frameworks as tech}
            <span class="px-1.5 py-0.5 rounded bg-neon-indigo/10 text-neon-indigo border border-neon-indigo/20 text-[10px]">
              {tech}
            </span>
          {/each}
        </div>
      </div>
    {/if}

    {#if data.approach_notes}
      <p class="text-text-dim mt-1 italic">{data.approach_notes}</p>
    {/if}
  {:else}
    <p class="text-text-dim">Waiting for Analyze stage...</p>
  {/if}
</div>
