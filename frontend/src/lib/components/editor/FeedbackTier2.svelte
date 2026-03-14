<script lang="ts">
  import { feedback } from '$lib/stores/feedback.svelte';
  import { forge } from '$lib/stores/forge.svelte';
  import { toast } from '$lib/stores/toast.svelte';

  let {
    optimizationId,
    issueSuggestions = [],
    onclose,
  }: {
    optimizationId: string;
    issueSuggestions?: Array<{ issue_id: string; reason: string; confidence: number }>;
    onclose?: () => void;
  } = $props();

  // Correctable issues grouped by category
  const FIDELITY_ISSUES = [
    { id: 'lost_key_terms', label: 'Lost key terms' },
    { id: 'changed_meaning', label: 'Changed meaning' },
    { id: 'hallucinated_content', label: 'Hallucinated content' },
    { id: 'lost_examples', label: 'Lost examples' },
  ];

  const QUALITY_ISSUES = [
    { id: 'too_verbose', label: 'Too verbose' },
    { id: 'too_vague', label: 'Too vague' },
    { id: 'wrong_tone', label: 'Wrong tone' },
    { id: 'broken_structure', label: 'Broken structure' },
  ];

  // Dimension definitions for override grid
  const DIMENSIONS: { abbr: string; key: string; label: string }[] = [
    { abbr: 'CLR', key: 'clarity_score', label: 'Clarity' },
    { abbr: 'SPC', key: 'specificity_score', label: 'Specificity' },
    { abbr: 'STR', key: 'structure_score', label: 'Structure' },
    { abbr: 'FTH', key: 'faithfulness_score', label: 'Faithfulness' },
    { abbr: 'CNC', key: 'conciseness_score', label: 'Conciseness' },
  ];

  // Scores from the validate stage result
  let validateScores = $derived(
    (forge.stageResults['validate']?.data?.scores as Record<string, number> | undefined) ?? {}
  );

  let currentRating = $derived(feedback.currentFeedback.rating);
  let correctedIssues = $derived(feedback.currentFeedback.correctedIssues);
  let dimensionOverrides = $derived(feedback.currentFeedback.dimensionOverrides);

  // Suggested issue IDs for pre-highlighting
  let suggestedIds = $derived(new Set(issueSuggestions.map((s) => s.issue_id)));

  // Entrance animation
  let visible = $state(false);
  $effect(() => {
    // Trigger spring entrance on mount
    requestAnimationFrame(() => {
      visible = true;
    });
  });

  function setRating(value: -1 | 0 | 1) {
    feedback.setRating(value);
  }

  function toggleIssue(issueId: string) {
    feedback.toggleCorrectedIssue(issueId);
  }

  function isIssueChecked(issueId: string): boolean {
    return correctedIssues.includes(issueId);
  }

  function adjustOverride(key: string, delta: number) {
    const current = dimensionOverrides[key] ?? Math.round(validateScores[key] ?? 5);
    const next = Math.max(1, Math.min(10, current + delta));
    feedback.setDimensionOverride(key, next);
  }

  async function handleSave() {
    const confirmation = await feedback.submit(optimizationId);
    if (confirmation) {
      const msg = confirmation.effects.length > 0
        ? `${confirmation.summary} ${confirmation.effects.join(', ')}`
        : confirmation.summary;
      toast.success(msg, 5000);
      onclose?.();
    } else if (feedback.error) {
      toast.error(`Feedback failed: ${feedback.error}`);
    }
  }

  function handleClose() {
    visible = false;
    setTimeout(() => {
      onclose?.();
    }, 200);
  }
</script>

<!--
  FeedbackTier2 — detailed feedback panel.
  Rating bar + issue checkboxes + dimension overrides + comment + save.
  Spring entrance (300ms), accelerating exit (200ms).
-->
<div
  class="border-t border-border-subtle bg-bg-card overflow-hidden transition-all"
  class:tier2-enter={visible}
  class:tier2-exit={!visible}
  style="transform-origin: top;"
>
  <div class="p-2 space-y-3">
    <!-- Rating bar: 3 buttons -->
    <div class="flex items-center gap-2">
      <span class="text-[10px] font-mono text-text-dim uppercase">Rating</span>
      <div class="flex items-center gap-1">
        <button
          class="inline-flex items-center justify-center w-7 h-6 text-[10px] font-mono border transition-colors
                 {currentRating === 1
                   ? 'border-neon-green bg-neon-green/8 text-neon-green'
                   : 'border-border-subtle text-text-dim hover:border-neon-green/40 hover:text-neon-green'}"
          style="border-radius: 4px;"
          onclick={() => setRating(1)}
          role="radio"
          aria-pressed={currentRating === 1}
          aria-label="Positive"
        >+</button>
        <button
          class="inline-flex items-center justify-center w-7 h-6 text-[10px] font-mono border transition-colors
                 {currentRating === 0
                   ? 'border-neon-cyan bg-neon-cyan/8 text-neon-cyan'
                   : 'border-border-subtle text-text-dim hover:border-neon-cyan/40 hover:text-neon-cyan'}"
          style="border-radius: 4px;"
          onclick={() => setRating(0)}
          role="radio"
          aria-pressed={currentRating === 0}
          aria-label="Neutral"
        >=</button>
        <button
          class="inline-flex items-center justify-center w-7 h-6 text-[10px] font-mono border transition-colors
                 {currentRating === -1
                   ? 'border-neon-red bg-neon-red/8 text-neon-red'
                   : 'border-border-subtle text-text-dim hover:border-neon-red/40 hover:text-neon-red'}"
          style="border-radius: 4px;"
          onclick={() => setRating(-1)}
          role="radio"
          aria-pressed={currentRating === -1}
          aria-label="Negative"
        >-</button>
      </div>
    </div>

    <!-- Issue checkboxes in 2-column grid -->
    <div class="grid grid-cols-2 gap-x-4" style="gap-y: 3px;">
      <!-- Fidelity group -->
      <div role="group" aria-label="Fidelity issues">
        <p class="text-[9px] font-mono uppercase mb-1 text-neon-yellow">Fidelity</p>
        {#each FIDELITY_ISSUES as issue}
          {@const checked = isIssueChecked(issue.id)}
          {@const suggested = suggestedIds.has(issue.id)}
          <label
            class="flex items-center gap-1.5 py-0.5 cursor-pointer group"
          >
            <span
              class="inline-flex items-center justify-center w-3.5 h-3.5 border transition-colors
                     {checked
                       ? 'border-neon-purple bg-neon-purple/15 text-neon-purple'
                       : suggested
                         ? 'border-neon-yellow/50 text-transparent'
                         : 'border-border-subtle text-transparent group-hover:border-text-dim'}"
              style="border-radius: 2px;"
              role="checkbox"
              aria-checked={checked}
            >
              {#if checked}
                <svg width="8" height="8" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3">
                  <polyline points="20 6 9 17 4 12" />
                </svg>
              {/if}
            </span>
            <input
              type="checkbox"
              class="sr-only"
              checked={checked}
              onchange={() => toggleIssue(issue.id)}
            />
            <span class="text-[10px] font-mono {checked ? 'text-text-primary' : 'text-text-dim'}
                         {suggested && !checked ? 'text-neon-yellow/70' : ''}">
              {issue.label}
            </span>
          </label>
        {/each}
      </div>

      <!-- Quality group -->
      <div role="group" aria-label="Quality issues">
        <p class="text-[9px] font-mono uppercase mb-1 text-neon-blue">Quality</p>
        {#each QUALITY_ISSUES as issue}
          {@const checked = isIssueChecked(issue.id)}
          {@const suggested = suggestedIds.has(issue.id)}
          <label
            class="flex items-center gap-1.5 py-0.5 cursor-pointer group"
          >
            <span
              class="inline-flex items-center justify-center w-3.5 h-3.5 border transition-colors
                     {checked
                       ? 'border-neon-purple bg-neon-purple/15 text-neon-purple'
                       : suggested
                         ? 'border-neon-yellow/50 text-transparent'
                         : 'border-border-subtle text-transparent group-hover:border-text-dim'}"
              style="border-radius: 2px;"
              role="checkbox"
              aria-checked={checked}
            >
              {#if checked}
                <svg width="8" height="8" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3">
                  <polyline points="20 6 9 17 4 12" />
                </svg>
              {/if}
            </span>
            <input
              type="checkbox"
              class="sr-only"
              checked={checked}
              onchange={() => toggleIssue(issue.id)}
            />
            <span class="text-[10px] font-mono {checked ? 'text-text-primary' : 'text-text-dim'}
                         {suggested && !checked ? 'text-neon-yellow/70' : ''}">
              {issue.label}
            </span>
          </label>
        {/each}
      </div>
    </div>

    <!-- Dimension override grid: 5 columns -->
    <div>
      <p class="text-[9px] font-mono text-text-dim uppercase mb-1">Dimension Overrides</p>
      <div class="grid grid-cols-5 gap-1">
        {#each DIMENSIONS as dim}
          {@const baseScore = validateScores[dim.key]}
          {@const overrideVal = dimensionOverrides[dim.key]}
          {@const displayVal = overrideVal ?? (baseScore != null ? Math.round(baseScore) : '—')}
          {@const isOverridden = dim.key in dimensionOverrides}
          <div
            class="flex flex-col items-center p-1 border transition-colors
                   {isOverridden
                     ? 'border-neon-purple/50 bg-neon-purple/5'
                     : 'border-border-subtle'}"
            style="border-radius: 6px; padding: 8px 4px;"
          >
            <span class="text-[9px] font-mono text-text-dim">{dim.abbr}</span>
            <span class="text-xs font-mono {isOverridden ? 'text-neon-purple' : 'text-text-primary'}">
              {displayVal}
            </span>
            <div class="flex items-center gap-0.5 mt-1">
              <button
                class="w-4 h-4 inline-flex items-center justify-center text-[10px] border border-border-subtle
                       text-text-dim hover:border-neon-cyan/30 hover:text-neon-cyan transition-colors"
                style="border-radius: 2px;"
                onclick={() => adjustOverride(dim.key, -1)}
                aria-label="Decrease {dim.label}"
              >-</button>
              <button
                class="w-4 h-4 inline-flex items-center justify-center text-[10px] border border-border-subtle
                       text-text-dim hover:border-neon-cyan/30 hover:text-neon-cyan transition-colors"
                style="border-radius: 2px;"
                onclick={() => adjustOverride(dim.key, 1)}
                aria-label="Increase {dim.label}"
              >+</button>
            </div>
          </div>
        {/each}
      </div>
    </div>

    <!-- Comment textarea -->
    <div>
      <textarea
        class="w-full bg-bg-primary border border-border-subtle text-[11px] font-mono text-text-primary
               p-1.5 focus:outline-none focus:border-neon-cyan/40 resize-none"
        style="border-radius: 4px;"
        rows="2"
        placeholder="Optional comment..."
        bind:value={feedback.currentFeedback.comment}
      ></textarea>
    </div>

    <!-- Action bar -->
    <div class="flex items-center gap-2">
      <button
        class="flex-1 py-1.5 text-[11px] font-mono uppercase tracking-wider border
               border-neon-cyan/40 text-neon-cyan
               hover:bg-neon-cyan/10 transition-colors
               disabled:opacity-40 disabled:cursor-not-allowed"
        style="border-radius: 4px;"
        onclick={handleSave}
        disabled={feedback.currentFeedback.submitting || currentRating === null}
      >
        {feedback.currentFeedback.submitting ? 'Saving...' : 'Save Feedback'}
      </button>
      <button
        class="px-3 py-1.5 text-[11px] font-mono text-text-dim hover:text-text-secondary transition-colors"
        onclick={handleClose}
      >
        Cancel
      </button>
    </div>
  </div>
</div>

<style>
  .tier2-enter {
    animation: tier2SlideIn 300ms cubic-bezier(0.16, 1, 0.3, 1) forwards;
  }
  .tier2-exit {
    animation: tier2SlideOut 200ms cubic-bezier(0.4, 0, 1, 1) forwards;
  }
  @keyframes tier2SlideIn {
    from {
      opacity: 0;
      max-height: 0;
      transform: scaleY(0.8);
    }
    to {
      opacity: 1;
      max-height: 500px;
      transform: scaleY(1);
    }
  }
  @keyframes tier2SlideOut {
    from {
      opacity: 1;
      max-height: 500px;
      transform: scaleY(1);
    }
    to {
      opacity: 0;
      max-height: 0;
      transform: scaleY(0.8);
    }
  }
  @media (prefers-reduced-motion: reduce) {
    .tier2-enter, .tier2-exit {
      animation-duration: 0.01ms !important;
    }
  }
</style>
