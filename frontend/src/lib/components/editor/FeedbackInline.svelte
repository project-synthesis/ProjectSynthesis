<script lang="ts">
  import { feedback } from '$lib/stores/feedback.svelte';
  import { refinement } from '$lib/stores/refinement.svelte';
  import { forge } from '$lib/stores/forge.svelte';
  import { toast } from '$lib/stores/toast.svelte';
  import { createEventDispatcher } from 'svelte';

  let { optimizationId }: { optimizationId: string } = $props();

  const dispatch = createEventDispatcher<{
    expandTier2: void;
    openTier3: void;
  }>();

  // Scores from the validate stage result
  let validateScores = $derived(
    (forge.stageResults['validate']?.data?.scores as Record<string, number> | undefined) ?? {}
  );

  // Current rating from the feedback store
  let currentRating = $derived(feedback.currentFeedback.rating);

  // Adaptation pulse
  let pulse = $derived(feedback.adaptationPulse);

  // Impact delta flash state
  let impactDelta = $state<{ label: string; value: string } | null>(null);
  let impactTimeout: ReturnType<typeof setTimeout> | null = null;

  export function flashImpactDelta(label: string, value: string) {
    if (impactTimeout) clearTimeout(impactTimeout);
    impactDelta = { label, value };
    impactTimeout = setTimeout(() => {
      impactDelta = null;
    }, 3000);
  }

  async function handleThumbUp() {
    const next: -1 | 0 | 1 = currentRating === 1 ? 0 : 1;
    feedback.setRating(next);
    if (next === 1) {
      const confirmation = await feedback.submit(optimizationId);
      if (confirmation) {
        toast.success(confirmation.summary, 3000);
      } else if (feedback.error) {
        toast.error(`Feedback failed: ${feedback.error}`);
      }
    }
  }

  async function handleThumbDown() {
    const next: -1 | 0 | 1 = currentRating === -1 ? 0 : -1;
    feedback.setRating(next);
    if (next === -1) {
      dispatch('expandTier2');
    }
  }

  function handleDetails() {
    dispatch('openTier3');
  }

  function handleRefine() {
    refinement.openRefinement();
  }

  // Pulse dot color
  let pulseDotColor = $derived(
    pulse?.status === 'active'
      ? '#00e5ff'
      : pulse?.status === 'learning'
        ? '#fbbf24'
        : '#555'
  );
</script>

<!--
  FeedbackInline — compact strip beneath the optimized prompt.
  Thumbs + adaptation pulse + impact delta + Details + Refine.
  Zero-effects: 1px solid borders only; no box-shadow, text-shadow, drop-shadow.
-->
<div
  class="flex items-center h-7 px-2 gap-2 border-t border-border-subtle bg-bg-card"
  aria-label="Inline feedback controls"
>
  <!-- Thumbs Up -->
  <button
    class="inline-flex items-center justify-center w-6 h-6 transition-colors
           {currentRating === 1
             ? 'border border-neon-green bg-neon-green/8 text-neon-green'
             : 'border border-border-subtle text-text-dim hover:border-neon-green/40 hover:text-neon-green'}"
    style="border-radius: 4px;"
    onclick={handleThumbUp}
    disabled={feedback.currentFeedback.submitting}
    role="radio"
    aria-label="Thumbs up"
    aria-pressed={currentRating === 1}
    title="Positive feedback"
  >
    <svg width="12" height="12" viewBox="0 0 24 24" fill={currentRating === 1 ? 'currentColor' : 'none'} stroke="currentColor" stroke-width="2">
      <path stroke-linecap="round" stroke-linejoin="round"
        d="M14 9V5a3 3 0 0 0-3-3l-4 9v11h11.28a2 2 0 0 0 2-1.7l1.38-9a2 2 0 0 0-2-2.3H14z" />
      <path stroke-linecap="round" stroke-linejoin="round"
        d="M7 22H4a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2h3" />
    </svg>
  </button>

  <!-- Thumbs Down -->
  <button
    class="inline-flex items-center justify-center w-6 h-6 transition-colors
           {currentRating === -1
             ? 'border border-neon-red bg-neon-red/8 text-neon-red'
             : 'border border-border-subtle text-text-dim hover:border-neon-red/40 hover:text-neon-red'}"
    style="border-radius: 4px;"
    onclick={handleThumbDown}
    disabled={feedback.currentFeedback.submitting}
    role="radio"
    aria-label="Thumbs down"
    aria-pressed={currentRating === -1}
    title="Negative feedback"
  >
    <svg width="12" height="12" viewBox="0 0 24 24" fill={currentRating === -1 ? 'currentColor' : 'none'} stroke="currentColor" stroke-width="2">
      <path stroke-linecap="round" stroke-linejoin="round"
        d="M10 15v4a3 3 0 0 0 3 3l4-9V2H5.72a2 2 0 0 0-2 1.7l-1.38 9a2 2 0 0 0 2 2.3H10z" />
      <path stroke-linecap="round" stroke-linejoin="round"
        d="M17 2h2.67A2.31 2.31 0 0 1 22 4v7a2.31 2.31 0 0 1-2.33 2H17" />
    </svg>
  </button>

  <!-- Divider -->
  <div class="w-px h-4 bg-border-subtle" aria-hidden="true"></div>

  <!-- Adaptation pulse dot + label -->
  {#if pulse}
    <div class="inline-flex items-center gap-1.5" title={pulse.detail}>
      <span
        class="inline-block rounded-full"
        style="width: 5px; height: 5px; background: {pulseDotColor};"
        aria-hidden="true"
      ></span>
      <span class="text-[9px] font-mono text-text-dim">{pulse.label}</span>
    </div>
  {/if}

  <!-- Impact delta flash -->
  {#if impactDelta}
    <span class="text-[9px] font-mono text-neon-green animate-fade-in">
      {impactDelta.label} {impactDelta.value}
    </span>
  {/if}

  <!-- Spacer -->
  <div class="flex-1" aria-hidden="true"></div>

  <!-- Details button -->
  <button
    class="inline-flex items-center gap-1 px-1.5 py-0.5 text-[10px] font-mono border
           border-border-subtle text-text-secondary
           hover:border-neon-cyan/30 hover:text-neon-cyan
           transition-colors"
    style="border-radius: 4px;"
    onclick={handleDetails}
    aria-label="Open adaptation details"
    title="View adaptation intelligence"
  >
    Details
  </button>

  <!-- Refine ghost button -->
  <button
    class="inline-flex items-center gap-1 px-2 py-0.5 text-[10px] font-mono border
           border-border-subtle text-text-secondary
           hover:border-neon-cyan/30 hover:text-neon-cyan
           transition-colors"
    style="border-radius: 4px;"
    onclick={handleRefine}
    aria-label="Open refinement panel"
    title="Open refinement panel"
  >
    {#if refinement.branchCount > 0}
      <span class="text-neon-purple">&#x25C8;</span>
      <span>{refinement.branchCount}</span>
    {:else}
      <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
        <path stroke-linecap="round" stroke-linejoin="round" d="M11 5H6a2 2 0 0 0-2 2v11a2 2 0 0 0 2 2h11a2 2 0 0 0 2-2v-5" />
        <path stroke-linecap="round" stroke-linejoin="round" d="M17.5 2.5a2.121 2.121 0 0 1 3 3L12 14l-4 1 1-4 7.5-7.5z" />
      </svg>
    {/if}
    Refine
  </button>
</div>
