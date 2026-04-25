<script lang="ts">
  import { clustersStore } from '$lib/stores/clusters.svelte';

  const hasSuggestion = $derived(clustersStore.suggestion !== null);
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
</style>
