<script lang="ts">
  interface Props {
    original: string;
    optimized: string;
  }

  let { original, optimized }: Props = $props();
</script>

<div class="diffview">
  <div class="diff-col">
    <div class="section-heading col-header">ORIGINAL</div>
    <div class="diff-pane">
      <pre class="diff-content">{original}</pre>
    </div>
  </div>
  <div class="diff-divider" aria-hidden="true"></div>
  <div class="diff-col">
    <div class="section-heading col-header">OPTIMIZED</div>
    <div class="diff-pane">
      <pre class="diff-content">{optimized}</pre>
    </div>
  </div>
</div>

<style>
  .diffview {
    display: flex;
    height: 100%;
    overflow: hidden;
  }

  .diff-col {
    flex: 1;
    display: flex;
    flex-direction: column;
    min-width: 0;
    overflow: hidden;
  }

  .col-header {
    flex-shrink: 0;
    padding: 4px 8px;
    border-bottom: 1px solid var(--color-border-subtle);
    background: var(--color-bg-secondary);
  }

  .diff-pane {
    flex: 1;
    overflow: auto;
    border: 1px solid var(--color-border-subtle);
    background: var(--color-bg-input);
  }

  .diff-content {
    margin: 0;
    padding: 8px;
    font-size: 12px; /* text-xs */
    font-family: var(--font-sans);
    line-height: 1.5;
    white-space: pre-wrap;
    word-break: break-word;
    color: var(--color-text-primary);
  }

  .diff-divider {
    width: 1px;
    background: var(--color-border-subtle);
    flex-shrink: 0;
  }
</style>
