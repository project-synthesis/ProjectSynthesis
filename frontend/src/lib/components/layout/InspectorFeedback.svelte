<script lang="ts">
  import { feedback } from '$lib/stores/feedback.svelte';
  import { forge } from '$lib/stores/forge.svelte';
  import ScoreBar from '$lib/components/shared/ScoreBar.svelte';
  import { getScoreColor } from '$lib/utils/colors';

  const DIMENSIONS = ['clarity_score', 'specificity_score', 'structure_score', 'faithfulness_score', 'conciseness_score'];

  function clamp(val: number, min: number, max: number): number {
    return Math.max(min, Math.min(max, val));
  }

  function stepDimension(dim: string, delta: number) {
    const current = feedback.currentFeedback.dimensionOverrides[dim] ?? getBaseScore(dim);
    const next = clamp(current + delta, 0, 10);
    feedback.setDimensionOverride(dim, next);
  }

  function getBaseScore(dim: string): number {
    const scores = (forge.stageResults['validate']?.data as Record<string, unknown>)?.scores as Record<string, number> | undefined;
    if (!scores) return 5;
    return typeof scores[dim] === 'number' ? scores[dim] : 5;
  }

  function getDisplayScore(dim: string): number {
    return feedback.currentFeedback.dimensionOverrides[dim] ?? getBaseScore(dim);
  }

  function handleSave() {
    const optId = forge.optimizationId;
    if (optId) feedback.submit(optId);
  }
</script>

<div class="space-y-3">
  <h3 class="font-display text-[12px] font-bold uppercase text-text-dim">Feedback</h3>

  <!-- Verdict thumbs -->
  <div class="flex items-center gap-2">
    <button
      class="flex items-center justify-center w-8 h-8 border text-xs transition-colors {feedback.currentFeedback.rating === 1
        ? 'border-neon-green text-neon-green bg-neon-green/10'
        : 'border-border-subtle text-text-dim hover:border-neon-green/50 hover:text-neon-green'}"
      onclick={() => feedback.setRating(1)}
      aria-label="Thumbs up"
      aria-pressed={feedback.currentFeedback.rating === 1}
    >
      <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor">
        <path d="M1 21h4V9H1v12zm22-11c0-1.1-.9-2-2-2h-6.31l.95-4.57.03-.32c0-.41-.17-.79-.44-1.06L14.17 1 7.59 7.59C7.22 7.95 7 8.45 7 9v10c0 1.1.9 2 2 2h9c.83 0 1.54-.5 1.84-1.22l3.02-7.05c.09-.23.14-.47.14-.73v-2z"/>
      </svg>
    </button>

    <button
      class="flex items-center justify-center w-8 h-8 border text-xs transition-colors {feedback.currentFeedback.rating === 0
        ? 'border-text-secondary text-text-secondary bg-text-secondary/10'
        : 'border-border-subtle text-text-dim hover:border-text-secondary/50 hover:text-text-secondary'}"
      onclick={() => feedback.setRating(0)}
      aria-label="Neutral"
      aria-pressed={feedback.currentFeedback.rating === 0}
    >
      <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor">
        <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm0 18c-4.42 0-8-3.58-8-8s3.58-8 8-8 8 3.58 8 8-3.58 8-8 8zm-3-9h6v2H9z"/>
      </svg>
    </button>

    <button
      class="flex items-center justify-center w-8 h-8 border text-xs transition-colors {feedback.currentFeedback.rating === -1
        ? 'border-neon-red text-neon-red bg-neon-red/10'
        : 'border-border-subtle text-text-dim hover:border-neon-red/50 hover:text-neon-red'}"
      onclick={() => feedback.setRating(-1)}
      aria-label="Thumbs down"
      aria-pressed={feedback.currentFeedback.rating === -1}
    >
      <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor">
        <path d="M15 3H6c-.83 0-1.54.5-1.84 1.22l-3.02 7.05c-.09.23-.14.47-.14.73v2c0 1.1.9 2 2 2h6.31l-.95 4.57-.03.32c0 .41.17.79.44 1.06L9.83 23l6.59-6.59c.36-.36.58-.86.58-1.41V5c0-1.1-.9-2-2-2zm4 0v12h4V3h-4z"/>
      </svg>
    </button>
  </div>

  <!-- Per-dimension score overrides -->
  <div class="space-y-2">
    {#each DIMENSIONS as dim}
      {@const score = getDisplayScore(dim)}
      {@const isOverridden = dim in feedback.currentFeedback.dimensionOverrides}
      <div class="space-y-1">
        <div class="flex items-center justify-between">
          <span class="font-mono text-[10px] {isOverridden ? 'text-neon-cyan' : 'text-text-dim'} capitalize">{dim.replace('_score', '')}</span>
          <div class="flex items-center gap-1">
            <button
              class="w-6 h-6 flex items-center justify-center border border-border-subtle text-text-dim hover:border-neon-cyan/50 hover:text-neon-cyan text-[10px] leading-none transition-colors"
              onclick={() => stepDimension(dim, -1)}
              aria-label="Decrease {dim.replace('_score', '')} score"
            >−</button>
            <span class="font-mono text-[10px] text-text-primary w-6 text-center">{score}/10</span>
            <button
              class="w-6 h-6 flex items-center justify-center border border-border-subtle text-text-dim hover:border-neon-cyan/50 hover:text-neon-cyan text-[10px] leading-none transition-colors"
              onclick={() => stepDimension(dim, 1)}
              aria-label="Increase {dim.replace('_score', '')} score"
            >+</button>
          </div>
        </div>
        <div class="relative h-[20px] bg-bg-primary overflow-hidden" style="--bar-accent: {getScoreColor(score)}33;">
          <ScoreBar score={score} max={10} />
        </div>
      </div>
    {/each}
  </div>

  <!-- Comment textarea -->
  <textarea
    class="w-full border border-border-subtle bg-bg-input text-xs text-text-primary font-mono p-2 resize-none placeholder-text-dim outline-none focus:border-neon-cyan/40"
    style="height: 80px;"
    placeholder="Optional comment…"
    bind:value={feedback.currentFeedback.comment}
  ></textarea>

  <!-- Save button -->
  <button
    class="w-full h-7 border border-neon-cyan text-neon-cyan text-xs font-display uppercase tracking-wider hover:bg-neon-cyan/10 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
    onclick={handleSave}
    disabled={feedback.currentFeedback.rating === null || feedback.currentFeedback.submitting}
  >
    {feedback.currentFeedback.submitting ? 'Saving…' : 'Save Feedback'}
  </button>
</div>
