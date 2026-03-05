<script lang="ts">
  import type { Snippet } from 'svelte';
  import type { StageStatus } from '$lib/stores/forge.svelte';
  import ModelBadge from '$lib/components/shared/ModelBadge.svelte';

  let { name, icon, status, index, isActive, children, duration, model, tokenCount, stageColor }: {
    name: string;
    icon: string;
    status: StageStatus;
    index: number;
    isActive: boolean;
    children: Snippet;
    duration?: number;
    model?: string;
    tokenCount?: number;
    stageColor?: string;
  } = $props();

  let expanded = $state(false);

  // Left border opacity: 30% at rest, 100% when active/running
  let leftBorderOpacity = $derived(
    (isActive || status === 'running') ? 1.0 : 0.3
  );

  let stageLabel = $derived(`0${index} // ${name.toUpperCase()}`);

  $effect(() => {
    if (isActive || status === 'running') {
      expanded = true;
    }
  });
</script>

<div
  class="bg-bg-card border border-border-subtle rounded-lg overflow-hidden transition-all duration-300"
  style="border-left: 2px solid {stageColor ? `color-mix(in srgb, ${stageColor} ${leftBorderOpacity * 100}%, transparent)` : 'transparent'};"
  data-testid="stage-card-{name.toLowerCase()}"
>
  <!-- Header (32px per spec) -->
  <button
    class="w-full flex items-center gap-2.5 px-3 h-[32px] text-left hover:bg-bg-hover/50 transition-colors duration-200"
    onclick={() => { expanded = !expanded; }}
  >
    <!-- Status indicator (12px circle) -->
    {#if status === 'running'}
      <span
        class="w-3 h-3 rounded-full shrink-0 border-t-2 animate-spin"
        style="border-color: transparent; border-top-color: {stageColor || '#00e5ff'};"
      ></span>
    {:else if status === 'done'}
      <span class="w-3 h-3 rounded-full shrink-0 flex items-center justify-center" style="background: color-mix(in srgb, {stageColor || '#22ff88'} 20%, transparent);">
        <svg class="w-2 h-2" fill="none" stroke="{stageColor || '#22ff88'}" viewBox="0 0 24 24" stroke-width="3">
          <path stroke-linecap="round" stroke-linejoin="round" d="M5 13l4 4L19 7"></path>
        </svg>
      </span>
    {:else if status === 'error'}
      <span class="w-3 h-3 rounded-full shrink-0 flex items-center justify-center bg-neon-red/20">
        <svg class="w-2 h-2 text-neon-red" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="3">
          <path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12"></path>
        </svg>
      </span>
    {:else}
      <span class="w-3 h-3 rounded-full shrink-0 border border-text-dim/40"></span>
    {/if}

    <!-- Stage label: Syne 11px 700 uppercase -->
    <span
      class="font-display text-[11px] font-bold uppercase flex-1"
      style="letter-spacing: 0.08em; color: {status === 'running' ? (stageColor || '#00e5ff') : status === 'done' ? (stageColor || '#22ff88') : status === 'error' ? '#ff3366' : '#7a7a9e'};"
    >
      {stageLabel}
    </span>

    {#if duration}
      <span class="text-[10px] text-text-dim font-mono">{(duration / 1000).toFixed(1)}s</span>
    {/if}

    {#if tokenCount}
      <span class="text-[10px] text-text-dim font-mono">{tokenCount.toLocaleString()}tok</span>
    {/if}

    {#if model}
      <ModelBadge {model} />
    {/if}

    <svg
      class="w-3 h-3 text-text-dim transition-transform duration-200"
      class:rotate-180={expanded}
      fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2"
    >
      <path stroke-linecap="round" stroke-linejoin="round" d="M19 9l-7 7-7-7"></path>
    </svg>
  </button>

  <!-- Content (grid-template-rows collapse/expand per spec) -->
  {#if status !== 'idle'}
    <div
      class="grid transition-all duration-300 ease-out"
      style="grid-template-rows: {expanded ? '1fr' : '0fr'};"
    >
      <div class="overflow-hidden">
        <div class="px-3 pb-3 border-t border-border-subtle">
          <div class="pt-2">
            {@render children()}
          </div>
        </div>
      </div>
    </div>
  {/if}
</div>
