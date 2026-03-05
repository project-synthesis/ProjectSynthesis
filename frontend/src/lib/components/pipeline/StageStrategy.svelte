<script lang="ts">
  import { forge } from '$lib/stores/forge.svelte';
  import StrategyBadge from '$lib/components/shared/StrategyBadge.svelte';

  let result = $derived(forge.stageResults['strategy']);
  let data = $derived((result?.data || {}) as Record<string, unknown>);

  // Strategy → chromatic color for confidence bar
  const strategyColors: Record<string, string> = {
    'auto':                '#00e5ff',
    'chain-of-thought':    '#00e5ff',
    'co-star':             '#a855f7',
    'CO-STAR':             '#a855f7',
    'risen':               '#22ff88',
    'RISEN':               '#22ff88',
    'role-task-format':    '#ff3366',
    'few-shot-scaffolding':'#fbbf24',
    'step-by-step':        '#ff8c00',
    'structured-output':   '#4d8eff',
    'constraint-injection':'#ff6eb4',
    'context-enrichment':  '#00d4aa',
    'persona-assignment':  '#7b61ff',
  };

  function getStrategyColor(s: string): string {
    return strategyColors[s] || strategyColors[s?.toLowerCase()] || '#00e5ff';
  }

  let confidence = $derived(
    typeof data.confidence === 'number' ? data.confidence :
    typeof data.confidence_score === 'number' ? data.confidence_score : 0.85
  );
</script>

<div class="space-y-2 text-xs">
  {#if forge.stageStatuses['strategy'] === 'running'}
    <div class="flex items-center gap-2 text-neon-cyan">
      <span class="w-3 h-3 rounded-full animate-spin" style="border: 2px solid transparent; border-top-color: {getStrategyColor(data.primary_framework as string || 'auto')};"></span>
      <span>Selecting optimization strategy...</span>
    </div>
  {:else if result}
    {#if data.primary_framework}
      <div class="flex items-center gap-2">
        <StrategyBadge strategy={data.primary_framework as string} />
        <!-- Confidence bar (500ms ease fill per spec) -->
        <div class="flex-1 h-1.5 bg-bg-input rounded-full overflow-hidden">
          <div
            class="h-full rounded-full transition-all ease-out"
            style="width: {confidence * 100}%; background-color: {getStrategyColor(data.primary_framework as string)}; transition-duration: 500ms;"
          ></div>
        </div>
      </div>
    {/if}

    {#if data.rationale}
      <p class="text-xs text-text-secondary italic mt-1">{data.rationale}</p>
    {/if}

    {#if data.secondary_frameworks && Array.isArray(data.secondary_frameworks) && (data.secondary_frameworks as string[]).length > 0}
      <div class="mt-2">
        <span class="font-display text-[11px] font-bold uppercase text-text-dim" style="letter-spacing: 0.08em;">Techniques</span>
        <div class="flex flex-wrap gap-1 mt-1">
          {#each data.secondary_frameworks as tech}
            <span class="px-1.5 py-0.5 rounded-md bg-neon-indigo/10 text-neon-indigo border border-neon-indigo/20 text-[10px] font-mono">
              {tech}
            </span>
          {/each}
        </div>
      </div>
    {/if}

    {#if data.approach_notes}
      <p class="text-text-dim mt-1 italic text-xs">{data.approach_notes}</p>
    {/if}
  {:else}
    <p class="text-text-dim">Waiting for Analyze stage...</p>
  {/if}
</div>
