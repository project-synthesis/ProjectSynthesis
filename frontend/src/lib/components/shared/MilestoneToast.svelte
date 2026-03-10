<script lang="ts">
  import type { MilestoneData } from '$lib/stores/toast.svelte';

  interface Props {
    milestone: MilestoneData;
    onDismiss: () => void;
    dismissing: boolean;
  }
  const { milestone, onDismiss, dismissing }: Props = $props();
</script>

<div
  class="flex items-start gap-3 px-4 py-3 bg-bg-card border border-border-subtle max-w-md"
  style="border-left: 3px solid {milestone.color}; animation: {dismissing ? 'slide-out-right 0.3s ease-in forwards' : 'slide-in-right 0.3s cubic-bezier(0.16, 1, 0.3, 1) both, scale-in 0.3s cubic-bezier(0.16, 1, 0.3, 1) both'}"
  role="alert"
  aria-live="assertive"
>
  <div class="flex-1 min-w-0">
    <div class="font-display text-[8px] uppercase tracking-[0.15em] mb-0.5" style="color: {milestone.color}">
      MILESTONE UNLOCKED
    </div>
    <div class="font-display text-[11px] font-bold uppercase text-text-primary mb-0.5">
      {milestone.title}
    </div>
    <div class="font-mono text-[9px] text-text-dim leading-snug">
      {milestone.celebrationText}
    </div>
  </div>
  <button
    class="opacity-60 hover:opacity-100 transition-opacity shrink-0 mt-0.5"
    onclick={(e) => { e.stopPropagation(); onDismiss(); }}
    aria-label="Dismiss"
  >
    <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2">
      <path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12"></path>
    </svg>
  </button>
</div>
