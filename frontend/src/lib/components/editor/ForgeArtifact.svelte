<script lang="ts">
  import { forgeStore } from '$lib/stores/forge.svelte';
  import { editorStore } from '$lib/stores/editor.svelte';

  let copied = $state(false);

  async function copyToClipboard() {
    if (!forgeStore.result?.optimized_prompt) return;
    try {
      await navigator.clipboard.writeText(forgeStore.result.optimized_prompt);
      copied = true;
      setTimeout(() => { copied = false; }, 2000);
    } catch {
      // fallback: create a temporary textarea
      const el = document.createElement('textarea');
      el.value = forgeStore.result.optimized_prompt;
      document.body.appendChild(el);
      el.select();
      document.execCommand('copy');
      document.body.removeChild(el);
      copied = true;
      setTimeout(() => { copied = false; }, 2000);
    }
  }

  function viewDiff() {
    if (!forgeStore.result?.id) return;
    editorStore.openDiff(forgeStore.result.id);
  }
</script>

<div class="forge-artifact">
  {#if !forgeStore.result}
    <div class="empty-result">
      <span class="empty-label">No result yet — run FORGE to optimize your prompt</span>
    </div>
  {:else}
    <!-- Header bar -->
    <div class="artifact-header">
      <span class="section-title">OPTIMIZED PROMPT</span>
      <div class="header-actions">
        <button
          class="action-btn"
          onclick={viewDiff}
          title="View diff"
        >
          VIEW DIFF
        </button>
        <button
          class="action-btn action-btn--primary"
          onclick={copyToClipboard}
          title="Copy to clipboard"
        >
          {copied ? 'COPIED' : 'COPY'}
        </button>
      </div>
    </div>

    <!-- Optimized prompt -->
    <div class="prompt-output-wrap">
      <pre class="prompt-output">{forgeStore.result.optimized_prompt}</pre>
    </div>

    <!-- Changes summary -->
    {#if forgeStore.result.changes_summary}
      <div class="changes-section">
        <div class="changes-header">
          <span class="section-title">CHANGES</span>
        </div>
        <div class="changes-body">
          <p class="changes-text">{forgeStore.result.changes_summary}</p>
        </div>
      </div>
    {/if}

    <!-- Feedback -->
    <div class="feedback-section">
      <span class="section-title">FEEDBACK</span>
      <div class="feedback-buttons">
        <button
          class="feedback-btn"
          class:feedback-btn--active={forgeStore.feedback === 'thumbs_up'}
          onclick={() => forgeStore.submitFeedback('thumbs_up')}
          aria-label="Thumbs up"
          title="Good result"
        >
          <span class="feedback-icon">▲</span>
        </button>
        <button
          class="feedback-btn"
          class:feedback-btn--active={forgeStore.feedback === 'thumbs_down'}
          onclick={() => forgeStore.submitFeedback('thumbs_down')}
          aria-label="Thumbs down"
          title="Poor result"
        >
          <span class="feedback-icon">▼</span>
        </button>
      </div>
    </div>
  {/if}
</div>

<style>
  .forge-artifact {
    display: flex;
    flex-direction: column;
    height: 100%;
    overflow: hidden;
  }

  .empty-result {
    display: flex;
    align-items: center;
    justify-content: center;
    height: 100%;
  }

  .empty-label {
    font-size: 11px;
    font-family: var(--font-mono);
    color: var(--color-text-dim);
  }

  .artifact-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    height: 32px;
    padding: 0 8px;
    background: var(--color-bg-secondary);
    border-bottom: 1px solid var(--color-border-subtle);
    flex-shrink: 0;
    gap: 8px;
  }

  .section-title {
    font-size: 10px;
    font-family: var(--font-display);
    text-transform: uppercase;
    letter-spacing: 0.1em;
    color: var(--color-text-dim);
  }

  .header-actions {
    display: flex;
    align-items: center;
    gap: 6px;
  }

  .action-btn {
    height: 22px;
    padding: 0 8px;
    background: transparent;
    border: 1px solid var(--color-border-subtle);
    color: var(--color-text-dim);
    font-family: var(--font-display);
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    cursor: pointer;
    transition: background 200ms cubic-bezier(0.16, 1, 0.3, 1),
                border-color 200ms cubic-bezier(0.16, 1, 0.3, 1),
                color 200ms cubic-bezier(0.16, 1, 0.3, 1);
    white-space: nowrap;
  }

  .action-btn:hover {
    background: var(--color-bg-hover);
    border-color: var(--color-border-default);
    color: var(--color-text-primary);
  }

  .action-btn--primary {
    border-color: var(--color-neon-cyan);
    color: var(--color-neon-cyan);
  }

  .action-btn--primary:hover {
    background: rgba(0, 229, 255, 0.08);
    border-color: var(--color-neon-cyan);
    color: var(--color-neon-cyan);
  }

  .prompt-output-wrap {
    flex: 1;
    overflow: auto;
    background: var(--color-bg-input);
    border-bottom: 1px solid var(--color-border-subtle);
    min-height: 0;
  }

  .prompt-output {
    margin: 0;
    padding: 8px;
    font-family: var(--font-sans);
    font-size: 12px;
    line-height: 1.6;
    color: var(--color-text-primary);
    white-space: pre-wrap;
    word-break: break-word;
  }

  .changes-section {
    flex-shrink: 0;
    border-bottom: 1px solid var(--color-border-subtle);
    max-height: 120px;
    overflow: hidden;
    display: flex;
    flex-direction: column;
  }

  .changes-header {
    display: flex;
    align-items: center;
    height: 24px;
    padding: 0 8px;
    background: var(--color-bg-secondary);
    flex-shrink: 0;
  }

  .changes-body {
    overflow: auto;
    padding: 6px 8px;
    flex: 1;
  }

  .changes-text {
    margin: 0;
    font-size: 12px;
    font-family: var(--font-sans);
    color: var(--color-text-secondary);
    line-height: 1.5;
  }

  .feedback-section {
    display: flex;
    align-items: center;
    gap: 8px;
    height: 36px;
    padding: 0 8px;
    background: var(--color-bg-secondary);
    border-top: 1px solid var(--color-border-subtle);
    flex-shrink: 0;
  }

  .feedback-buttons {
    display: flex;
    gap: 4px;
  }

  .feedback-btn {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 28px;
    height: 24px;
    background: transparent;
    border: 1px solid var(--color-border-subtle);
    color: var(--color-text-dim);
    cursor: pointer;
    transition: background 200ms cubic-bezier(0.16, 1, 0.3, 1),
                border-color 200ms cubic-bezier(0.16, 1, 0.3, 1),
                color 200ms cubic-bezier(0.16, 1, 0.3, 1);
  }

  .feedback-btn:hover {
    background: var(--color-bg-hover);
    border-color: var(--color-border-default);
    color: var(--color-text-primary);
  }

  .feedback-btn--active {
    border-color: var(--color-neon-cyan);
    color: var(--color-neon-cyan);
  }

  .feedback-btn--active:hover {
    background: rgba(0, 229, 255, 0.08);
    border-color: var(--color-neon-cyan);
    color: var(--color-neon-cyan);
  }

  .feedback-icon {
    font-size: 1rem;
    line-height: 1;
  }
</style>
