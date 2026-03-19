<script lang="ts">
  import { patternsStore } from '$lib/stores/patterns.svelte';

  interface Props {
    onApply: (patterns: string[]) => void;
  }

  let { onApply }: Props = $props();

  function handleApply() {
    const patterns = patternsStore.applySuggestion();
    if (patterns) onApply(patterns);
  }

  function handleSkip() {
    patternsStore.dismissSuggestion();
  }
</script>

{#if patternsStore.suggestionVisible && patternsStore.suggestion}
  {@const match = patternsStore.suggestion}
  <div class="suggestion-banner" role="alert">
    <div class="suggestion-content">
      <div class="suggestion-header">
        <span class="suggestion-icon">&#x27E1;</span>
        <span class="suggestion-label">
          Matches "<strong>{match.family.intent_label}</strong>" pattern ({Math.round(match.similarity * 100)}%)
        </span>
      </div>
      <div class="suggestion-meta">
        {match.meta_patterns.length} meta-pattern{match.meta_patterns.length !== 1 ? 's' : ''} available
        {#if match.family.avg_score != null}
          &middot; avg score {match.family.avg_score.toFixed(1)}
        {/if}
      </div>
    </div>
    <div class="suggestion-actions">
      <button class="btn-apply" onclick={handleApply}>Apply</button>
      <button class="btn-skip" onclick={handleSkip}>Skip</button>
    </div>
  </div>
{/if}

<style>
  .suggestion-banner {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 4px 6px;
    background: var(--color-bg-secondary);
    border: 1px solid var(--color-border-accent);
    margin: 4px 0;
    animation: slide-up-in 200ms cubic-bezier(0.16, 1, 0.3, 1) forwards;
    font-size: 11px;
    font-family: var(--font-sans);
  }

  @keyframes slide-up-in {
    from { opacity: 0; transform: translateY(-4px); }
    to { opacity: 1; transform: translateY(0); }
  }

  .suggestion-content {
    flex: 1;
    min-width: 0;
  }

  .suggestion-header {
    display: flex;
    align-items: center;
    gap: 6px;
    color: var(--color-text-primary);
  }

  .suggestion-icon {
    color: var(--color-neon-cyan);
    font-size: 14px;
  }

  .suggestion-label strong {
    color: var(--color-neon-cyan);
  }

  .suggestion-meta {
    color: var(--color-text-dim);
    font-size: 10px;
    font-family: var(--font-mono);
    margin-top: 2px;
    padding-left: 20px;
  }

  .suggestion-actions {
    display: flex;
    gap: 6px;
    flex-shrink: 0;
  }

  .btn-apply {
    background: transparent;
    border: 1px solid var(--color-neon-cyan);
    color: var(--color-neon-cyan);
    padding: 0 8px;
    height: 20px;
    line-height: 18px;
    font-size: 10px;
    font-family: var(--font-display);
    font-weight: 700;
    cursor: pointer;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    transition: all 200ms cubic-bezier(0.16, 1, 0.3, 1);
  }

  .btn-apply:hover {
    background: rgba(0, 229, 255, 0.06);
    transform: translateY(-1px);
  }

  .btn-apply:active {
    transform: translateY(0);
  }

  .btn-skip {
    background: transparent;
    border: 1px solid var(--color-border-subtle);
    color: var(--color-text-dim);
    padding: 0 8px;
    height: 20px;
    line-height: 18px;
    font-size: 10px;
    font-family: var(--font-display);
    font-weight: 700;
    cursor: pointer;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    transition: all 200ms cubic-bezier(0.16, 1, 0.3, 1);
  }

  .btn-skip:hover {
    border-color: var(--color-border-accent);
    color: var(--color-text-secondary);
    background: var(--color-bg-hover);
  }

  .btn-skip:active {
    transform: translateY(0);
  }
</style>
