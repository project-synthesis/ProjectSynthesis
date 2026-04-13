<script lang="ts">
  import { clustersStore } from '$lib/stores/clusters.svelte';
  import { taxonomyColor } from '$lib/utils/colors';

  interface Props {
    onApply: (result: { ids: string[]; clusterLabel: string }) => void;
  }

  let { onApply }: Props = $props();

  function handleApply() {
    const result = clustersStore.applySuggestion();
    if (result) onApply(result);
  }

  function handleSkip() {
    clustersStore.dismissSuggestion();
  }
</script>

{#if clustersStore.suggestionVisible && clustersStore.suggestion}
  {@const match = clustersStore.suggestion}
  <div class="suggestion-banner" role="alert">
    <div class="suggestion-content">
      <div class="suggestion-header">
        <span class="domain-dot" style="background: {taxonomyColor(match.cluster.domain)};"></span>
        <span class="suggestion-label">
          <strong>{match.cluster.label}</strong>
          <span class="match-pct">{Math.round(match.similarity * 100)}%</span>
        </span>
      </div>
      {#if match.meta_patterns.length > 0}
        <ul class="pattern-preview">
          {#each match.meta_patterns.slice(0, 3) as p}
            <li>{p.pattern_text.length > 80 ? p.pattern_text.slice(0, 80) + '...' : p.pattern_text}</li>
          {/each}
          {#if match.meta_patterns.length > 3}
            <li class="more">+{match.meta_patterns.length - 3} more</li>
          {/if}
        </ul>
      {/if}
    </div>
    <div class="suggestion-actions">
      <button class="action-btn action-btn--primary" onclick={handleApply}>Apply {match.meta_patterns.length}</button>
      <button class="action-btn" onclick={handleSkip}>Skip</button>
    </div>
  </div>
{/if}

<style>
  .suggestion-banner {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    padding: 6px 8px;
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

  .domain-dot {
    width: 6px;
    height: 6px;
    flex-shrink: 0;
  }

  .suggestion-label strong {
    color: var(--tier-accent, var(--color-neon-cyan));
  }

  .match-pct {
    font-size: 10px;
    font-family: var(--font-mono);
    color: var(--color-text-muted);
    margin-left: 4px;
  }

  .pattern-preview {
    list-style: none;
    padding: 0;
    margin: 4px 0 0;
  }

  .pattern-preview li {
    font-size: 10px;
    color: var(--color-text-secondary);
    padding: 1px 0;
    padding-left: 12px;
    position: relative;
  }

  .pattern-preview li::before {
    content: '\2022';
    position: absolute;
    left: 4px;
    color: var(--color-text-muted);
  }

  .pattern-preview .more {
    color: var(--color-text-muted);
    font-style: italic;
  }

  .pattern-preview .more::before {
    content: '';
  }

  .suggestion-actions {
    display: flex;
    gap: 6px;
    flex-shrink: 0;
    padding-top: 2px;
  }

  .action-btn {
    padding: 2px 8px;
    background: transparent;
    border: 1px solid var(--color-border-subtle);
    color: var(--color-text-dim);
    font-family: var(--font-mono);
    font-size: 10px;
    cursor: pointer;
    transition: color 200ms, border-color 200ms;
  }

  .action-btn:hover {
    color: var(--color-text);
    border-color: var(--color-text-dim);
  }

  .action-btn--primary {
    color: var(--tier-accent, var(--color-neon-cyan));
    border-color: var(--tier-accent, var(--color-neon-cyan));
  }

  .action-btn--primary:hover {
    background: color-mix(in srgb, var(--tier-accent, var(--color-neon-cyan)) 8%, transparent);
  }
</style>
