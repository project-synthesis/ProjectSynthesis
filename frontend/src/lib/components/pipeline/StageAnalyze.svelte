<script lang="ts">
  import { forge } from '$lib/stores/forge.svelte';

  let result = $derived(forge.stageResults['analyze']);
  let data = $derived((result?.data || {}) as Record<string, unknown>);
  let weaknesses = $derived(((data.weaknesses || []) as string[]));
  let strengths = $derived(((data.strengths || []) as string[]));

  // Task type → chromatic color per spec
  const taskTypeColors: Record<string, string> = {
    'coding':         'bg-neon-cyan/10 text-neon-cyan border-neon-cyan/20',
    'analysis':       'bg-neon-blue/10 text-neon-blue border-neon-blue/20',
    'reasoning':      'bg-neon-indigo/10 text-neon-indigo border-neon-indigo/20',
    'math':           'bg-neon-purple/10 text-neon-purple border-neon-purple/20',
    'writing':        'bg-neon-green/10 text-neon-green border-neon-green/20',
    'creative':       'bg-neon-pink/10 text-neon-pink border-neon-pink/20',
    'extraction':     'bg-neon-teal/10 text-neon-teal border-neon-teal/20',
    'classification': 'bg-neon-orange/10 text-neon-orange border-neon-orange/20',
    'formatting':     'bg-neon-yellow/10 text-neon-yellow border-neon-yellow/20',
    'medical':        'bg-neon-red/10 text-neon-red border-neon-red/20',
    'legal':          'bg-neon-red/10 text-neon-red/70 border-neon-red/15',
    'education':      'bg-neon-teal/10 text-neon-teal/70 border-neon-teal/15',
    'general':        'bg-neon-cyan/10 text-neon-cyan/60 border-neon-cyan/15',
  };

  // Complexity → color per spec: simple=green, moderate=yellow, complex=red
  function getComplexityColor(c: string): string {
    const lower = c?.toLowerCase() || '';
    if (lower === 'complex' || lower === 'high') return 'bg-neon-red/10 text-neon-red border-neon-red/20';
    if (lower === 'moderate' || lower === 'medium') return 'bg-neon-yellow/10 text-neon-yellow border-neon-yellow/20';
    return 'bg-neon-green/10 text-neon-green border-neon-green/20';
  }

  function getTaskTypeColor(t: string): string {
    return taskTypeColors[t?.toLowerCase()] || 'bg-neon-cyan/10 text-neon-cyan/60 border-neon-cyan/15';
  }
</script>

<div class="space-y-2 text-xs">
  {#if forge.stageStatuses['analyze'] === 'running'}
    <div class="flex items-center gap-2 text-neon-cyan">
      <span class="w-3 h-3 rounded-full border-t-2 border-neon-cyan animate-spin" style="border-color: transparent; border-top-color: #4d8eff;"></span>
      <span>Analyzing prompt quality...</span>
    </div>
  {:else if result}
    <!-- Task type and complexity -->
    <div class="flex flex-wrap items-center gap-2">
      {#if data.task_type}
        <span class="px-2 py-0.5 rounded-full font-mono text-[10px] font-medium border {getTaskTypeColor(data.task_type as string)}" data-testid="task-type-chip">
          {data.task_type}
        </span>
      {/if}
      {#if data.complexity}
        <span class="px-2 py-0.5 rounded-md font-mono text-[10px] font-medium border {getComplexityColor(data.complexity as string)}" data-testid="complexity-badge">
          {data.complexity}
        </span>
      {/if}
    </div>

    <!-- Strengths -->
    {#if strengths.length > 0}
      <div class="mt-2">
        <span class="font-display text-[11px] font-bold uppercase text-text-dim" style="letter-spacing: 0.08em;">Strengths</span>
        <ul class="mt-1 space-y-0.5">
          {#each strengths as s}
            <li class="text-text-secondary flex gap-1.5" style="color: rgba(34, 255, 136, 0.6);">
              <span class="shrink-0">+</span>
              <span class="text-text-secondary">{s}</span>
            </li>
          {/each}
        </ul>
      </div>
    {/if}

    <!-- Weaknesses -->
    {#if weaknesses.length > 0}
      <div class="mt-2">
        <span class="font-display text-[11px] font-bold uppercase text-text-dim" style="letter-spacing: 0.08em;">Weaknesses</span>
        <ul class="mt-1 space-y-0.5">
          {#each weaknesses as w}
            <li class="text-text-secondary flex gap-1.5" style="color: rgba(255, 51, 102, 0.6);">
              <span class="shrink-0">−</span>
              <span class="text-text-secondary">{w}</span>
            </li>
          {/each}
        </ul>
      </div>
    {/if}
  {:else}
    <p class="text-text-dim">Waiting for Explore stage...</p>
  {/if}
</div>
