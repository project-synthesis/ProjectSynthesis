<script lang="ts">
  import { feedback } from '$lib/stores/feedback.svelte';
  import { refinement } from '$lib/stores/refinement.svelte';
  import { toast } from '$lib/stores/toast.svelte';

  let {
    optimizationId,
    onexpandTier2,
    onopenTier3,
  }: {
    optimizationId: string;
    onexpandTier2?: () => void;
    onopenTier3?: () => void;
  } = $props();

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
      onexpandTier2?.();
    }
  }

  function handleDetails() {
    onopenTier3?.();
  }

  function handleRefine() {
    refinement.openRefinement();
  }

  // Pulse dot class
  let pulseDotClass = $derived(
    pulse?.status === 'active'
      ? 'bg-neon-cyan'
      : pulse?.status === 'learning'
        ? 'bg-neon-yellow'
        : 'bg-text-dim'
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
    class="inline-flex items-center justify-center w-6 h-6 transition-colors duration-200 disabled:opacity-40 disabled:cursor-not-allowed
           {currentRating === 1
             ? 'border border-neon-green bg-neon-green/8 text-neon-green'
             : 'border border-border-subtle text-text-dim hover:border-neon-green/40 hover:text-neon-green'}"
    onclick={handleThumbUp}
    disabled={feedback.currentFeedback.submitting}
    role="radio"
    aria-label="Thumbs up"
    aria-pressed={currentRating === 1}
    title="Positive feedback"
    data-testid="feedback-thumb-up"
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
    class="inline-flex items-center justify-center w-6 h-6 transition-colors duration-200 disabled:opacity-40 disabled:cursor-not-allowed
           {currentRating === -1
             ? 'border border-neon-red bg-neon-red/8 text-neon-red'
             : 'border border-border-subtle text-text-dim hover:border-neon-red/40 hover:text-neon-red'}"
    onclick={handleThumbDown}
    disabled={feedback.currentFeedback.submitting}
    role="radio"
    aria-label="Thumbs down"
    aria-pressed={currentRating === -1}
    title="Negative feedback"
    data-testid="feedback-thumb-down"
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
        class="inline-block w-[5px] h-[5px] rounded-full {pulseDotClass}"
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
    class="btn-ghost inline-flex items-center gap-1 px-1.5 py-0.5 text-[10px] font-mono"
    onclick={handleDetails}
    aria-label="Open adaptation details"
    title="View adaptation intelligence"
    data-testid="feedback-details"
  >
    Details
  </button>

  <!-- Refine ghost button -->
  <button
    class="btn-ghost inline-flex items-center gap-1 px-2 py-0.5 text-[10px] font-mono"
    onclick={handleRefine}
    aria-label="Open refinement panel"
    title="Open refinement panel"
    data-testid="feedback-refine"
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
