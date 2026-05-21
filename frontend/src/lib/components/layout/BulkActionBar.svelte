<!-- frontend/src/lib/components/layout/BulkActionBar.svelte -->
<!--
  v0.4.32 — sticky bulk actions bar.
  Pure declarative component (no async); RunsPanel owns the state +
  callback wiring. Hides when count === 0.
-->
<script lang="ts">
  interface Props {
    count: number;
    onDelete: () => void;
    onExport: () => void;
    onClear: () => void;
    inFlight: boolean;
  }
  let { count, onDelete, onExport, onClear, inFlight }: Props = $props();
</script>

{#if count > 0}
  <div class="bulk-action-bar" role="toolbar" aria-label="Bulk actions">
    <span class="bulk-count text-[10px]">{count} selected</span>
    <button
      class="btn-outline-danger"
      onclick={onDelete}
      disabled={inFlight}
    >
      Delete {count}
    </button>
    <button
      class="btn-outline-secondary"
      onclick={onExport}
      disabled={inFlight}
    >
      Export {count} (JSON)
    </button>
    <button class="btn-icon" onclick={onClear} aria-label="Clear selection">×</button>
  </div>
{/if}

<style>
  .bulk-action-bar {
    display: flex;
    align-items: center;
    gap: 6px;
    padding: 6px;
    background: color-mix(in srgb, var(--color-neon-cyan) 8%, transparent);
    border-bottom: 1px solid var(--color-border-subtle);
  }
  .bulk-count {
    font-family: var(--font-mono);
    color: var(--color-neon-cyan);
  }
</style>
