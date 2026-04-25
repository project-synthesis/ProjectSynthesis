<script lang="ts">
  import { clustersStore } from '$lib/stores/clusters.svelte';
  import { taxonomyColor } from '$lib/utils/colors';

  const suggestion = $derived(clustersStore.suggestion);
  const hasSuggestion = $derived(suggestion !== null);
</script>

<!-- svelte-ignore a11y_no_redundant_roles -->
<aside
  class="context-panel"
  role="complementary"
  aria-label="Pattern context"
>
  <header class="panel-header">
    <span class="panel-title">CONTEXT</span>
  </header>

  {#if !hasSuggestion}
    <div class="empty-state">
      <p class="empty-copy">Start typing to see related clusters and patterns.</p>
      <p class="empty-sub">Waiting for prompt — at least 30 characters.</p>
    </div>
  {:else if suggestion}
    <section class="identity-row" aria-label="Matched cluster">
      <div class="identity-primary">
        <span
          class="domain-dot"
          data-test="domain-dot"
          style="background-color: {taxonomyColor(suggestion.cluster.domain)};"
        ></span>
        <span class="cluster-label">{suggestion.cluster.label}</span>
      </div>
      <div class="identity-meta">
        <span class="similarity">matched {Math.round(suggestion.similarity * 100)}%</span>
        <span class="meta-sep">·</span>
        <span class="match-level">{suggestion.match_level}</span>
      </div>
    </section>
  {/if}
</aside>

<style>
  .context-panel {
    display: flex;
    flex-direction: column;
    width: 240px;
    height: 100%;
    background: var(--color-bg-secondary);
    border-left: 1px solid var(--color-border-subtle);
    font-family: var(--font-sans);
  }

  .panel-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    height: 24px;
    padding: 0 6px;
    font-family: var(--font-display);
    font-size: 11px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    color: var(--color-text-dim);
    border-bottom: 1px solid var(--color-border-subtle);
  }

  .empty-state {
    padding: 6px;
    color: var(--color-text-secondary);
    font-size: 11px;
  }

  .empty-copy { margin: 0 0 4px 0; }
  .empty-sub { margin: 0; color: var(--color-text-dim); font-size: 10px; }

  .identity-row {
    padding: 4px 6px;
    border-bottom: 1px solid var(--color-border-subtle);
  }
  .identity-primary {
    display: flex;
    align-items: center;
    gap: 6px;
    height: 20px;
    color: var(--color-text-primary);
  }
  .domain-dot {
    display: inline-block;
    width: 6px;
    height: 6px;
    flex-shrink: 0;
  }
  .cluster-label {
    font-size: 11px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .identity-meta {
    height: 18px;
    display: flex;
    align-items: center;
    gap: 4px;
    font-family: var(--font-mono);
    font-size: 10px;
    color: var(--color-text-dim);
  }
  .meta-sep { color: var(--color-text-dim); }
  .match-level { font-variant: tabular-nums; }
</style>
