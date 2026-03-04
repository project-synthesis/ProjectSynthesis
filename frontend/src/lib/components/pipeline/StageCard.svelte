<script lang="ts">
  import type { Snippet } from 'svelte';
  import type { StageStatus } from '$lib/stores/forge.svelte';
  import ModelBadge from '$lib/components/shared/ModelBadge.svelte';

  let { name, icon, status, index, isActive, children, duration, model }: {
    name: string;
    icon: string;
    status: StageStatus;
    index: number;
    isActive: boolean;
    children: Snippet;
    duration?: number;
    model?: string;
  } = $props();

  let expanded = $state(false);

  const statusColors: Record<StageStatus, string> = {
    idle: 'border-border-subtle',
    running: 'border-neon-cyan/40',
    done: 'border-neon-green/30',
    error: 'border-neon-red/30',
    skipped: 'border-text-dim/20'
  };

  const statusDots: Record<StageStatus, string> = {
    idle: 'bg-text-dim/30',
    running: 'bg-neon-cyan animate-status-pulse',
    done: 'bg-neon-green',
    error: 'bg-neon-red',
    skipped: 'bg-text-dim/20'
  };

  $effect(() => {
    if (isActive || status === 'running') {
      expanded = true;
    }
  });
</script>

<div
  class="bg-bg-card border rounded-lg overflow-hidden transition-all duration-300 {statusColors[status]}"
  class:animate-forge-spark={status === 'running'}
  style="animation-delay: {index * 100}ms"
>
  <!-- Header -->
  <button
    class="w-full flex items-center gap-2.5 px-3 py-2 text-left hover:bg-bg-hover/50 transition-colors"
    onclick={() => { expanded = !expanded; }}
  >
    <span class="w-2 h-2 rounded-full shrink-0 {statusDots[status]}"></span>

    <svg class="w-4 h-4 shrink-0 {status === 'running' ? 'text-neon-cyan' : 'text-text-dim'}" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="1.5">
      <path stroke-linecap="round" stroke-linejoin="round" d={icon}></path>
    </svg>

    <span class="text-xs font-medium flex-1 {
      status === 'running' ? 'text-neon-cyan' :
      status === 'done' ? 'text-neon-green' :
      status === 'error' ? 'text-neon-red' :
      'text-text-secondary'
    }">
      {name}
    </span>

    {#if duration}
      <span class="text-[10px] text-text-dim">{(duration / 1000).toFixed(1)}s</span>
    {/if}

    {#if model}
      <ModelBadge {model} />
    {/if}

    <span class="text-[10px] text-text-dim capitalize">{status}</span>

    <svg
      class="w-3 h-3 text-text-dim transition-transform duration-200"
      class:rotate-180={expanded}
      fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2"
    >
      <path stroke-linecap="round" stroke-linejoin="round" d="M19 9l-7 7-7-7"></path>
    </svg>
  </button>

  <!-- Content -->
  {#if expanded && status !== 'idle'}
    <div class="px-3 pb-3 border-t border-border-subtle animate-section-expand">
      <div class="pt-2">
        {@render children()}
      </div>
    </div>
  {/if}
</div>
