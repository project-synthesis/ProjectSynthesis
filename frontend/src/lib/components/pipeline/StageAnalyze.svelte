<script lang="ts">
  import { forge } from '$lib/stores/forge.svelte';

  let result = $derived(forge.stageResults['analyze']);
  let data = $derived((result?.data || {}) as Record<string, unknown>);
  let weaknesses = $derived(((data.weaknesses || []) as string[]));
  let strengths = $derived(((data.strengths || []) as string[]));
</script>

<div class="space-y-2 text-xs">
  {#if forge.stageStatuses['analyze'] === 'running'}
    <div class="flex items-center gap-2 text-neon-cyan">
      <div class="w-3 h-3 border border-neon-cyan/30 border-t-neon-cyan rounded-full animate-spin"></div>
      <span>Analyzing prompt quality...</span>
    </div>
  {:else if result}
    <!-- Task type and complexity -->
    <div class="flex flex-wrap items-center gap-2">
      {#if data.task_type}
        <span class="px-2 py-0.5 rounded bg-neon-cyan/10 text-neon-cyan border border-neon-cyan/20 text-[10px] font-semibold uppercase">
          {data.task_type}
        </span>
      {/if}
      {#if data.complexity}
        <span class="px-2 py-0.5 rounded text-[10px] font-semibold uppercase {
          data.complexity === 'complex' ? 'bg-neon-red/10 text-neon-red border border-neon-red/20' :
          data.complexity === 'moderate' ? 'bg-neon-amber/10 text-neon-amber border border-neon-amber/20' :
          'bg-neon-green/10 text-neon-green border border-neon-green/20'
        }">
          {data.complexity}
        </span>
      {/if}
    </div>

    <!-- Strengths -->
    {#if strengths.length > 0}
      <div class="mt-2">
        <span class="text-neon-green text-[10px] uppercase tracking-wider font-semibold">Strengths</span>
        <ul class="mt-1 space-y-0.5">
          {#each strengths as s}
            <li class="text-text-secondary flex gap-1.5">
              <span class="text-neon-green shrink-0">+</span>
              <span>{s}</span>
            </li>
          {/each}
        </ul>
      </div>
    {/if}

    <!-- Weaknesses -->
    {#if weaknesses.length > 0}
      <div class="mt-2">
        <span class="text-neon-red text-[10px] uppercase tracking-wider font-semibold">Weaknesses</span>
        <ul class="mt-1 space-y-0.5">
          {#each weaknesses as w}
            <li class="text-text-secondary flex gap-1.5">
              <span class="text-neon-red shrink-0">-</span>
              <span>{w}</span>
            </li>
          {/each}
        </ul>
      </div>
    {/if}
  {:else}
    <p class="text-text-dim">Waiting for Explore stage...</p>
  {/if}
</div>
