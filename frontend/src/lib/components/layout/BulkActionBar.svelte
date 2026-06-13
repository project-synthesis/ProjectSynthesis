<!-- frontend/src/lib/components/layout/BulkActionBar.svelte -->
<!--
  v0.4.32 — sticky bulk actions bar.
  Pure declarative component (no async); RunsPanel owns the state +
  callback wiring. Hides when count === 0.
  v0.4.39 (RU-032 / RU-033 / R-19) — height 28px + padding 0 6px (toolbar
  bar spec parity with HistoryPanel's `.selection-toolbar`), explicit
  type="button" on every button, scoped reduced-motion override.
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
    <span class="bulk-count font-mono">{count} selected</span>
    <button
      type="button"
      class="btn-outline-danger"
      onclick={onDelete}
      disabled={inFlight}
    >
      Delete {count}
    </button>
    <button
      type="button"
      class="btn-outline-secondary"
      onclick={onExport}
      disabled={inFlight}
    >
      Export {count} (JSON)
    </button>
    <button
      type="button"
      class="btn-icon"
      onclick={onClear}
      aria-label="Clear selection"
    >×</button>
  </div>
{/if}

<style>
  /* RU-032 — toolbar bar spec: 28px height, 6px horizontal padding.
     Mirrors HistoryPanel's `.selection-toolbar`. */
  .bulk-action-bar {
    height: 28px;
    padding: 0 6px;
    display: flex;
    align-items: center;
    gap: 6px;
    background: color-mix(in srgb, var(--color-neon-cyan) 8%, transparent);
    border-bottom: 1px solid var(--color-border-subtle);
  }
  .bulk-count {
    font-size: 10px;
    color: var(--color-neon-cyan);
  }

  /* R-19 — reduced-motion scoped override (this surface has no scoped
     transitions of its own, but consumers of `.btn-outline-*` inherit
     from app.css — the global enforcement there already handles them). */
  @media (prefers-reduced-motion: reduce) {
    .bulk-action-bar {
      transition: none !important;
      animation: none !important;
    }
  }
</style>
