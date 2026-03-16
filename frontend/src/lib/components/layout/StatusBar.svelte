<script lang="ts">
  // ProviderBadge and RepoBadge will be wired in a later task
</script>

<div
  class="status-bar"
  role="status"
  aria-label="Status bar"
  style="background: var(--color-bg-secondary); border-top: 1px solid var(--color-border-subtle);"
>
  <!-- Left side: provider badge + repo badge placeholders -->
  <div class="status-left">
    <span class="status-item"><!-- ProviderBadge --></span>
    <span class="status-item"><!-- RepoBadge --></span>
  </div>

  <!-- Right side: keyboard shortcut hint -->
  <div class="status-right">
    <span class="status-item" aria-label="Open command palette with Ctrl+K">Ctrl+K</span>
  </div>
</div>

<style>
  .status-bar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    height: 24px; /* h-[24px] */
    padding: 0 8px;
    overflow: hidden;
  }

  .status-left,
  .status-right {
    display: flex;
    align-items: center;
    gap: 8px;
  }

  .status-item {
    font-size: 10px; /* text-[10px] */
    font-family: var(--font-mono); /* font-mono */
    color: var(--color-text-dim);
    white-space: nowrap;
  }
</style>
