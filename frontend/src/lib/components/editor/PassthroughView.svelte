<script lang="ts">
  import { forgeStore } from '$lib/stores/forge.svelte';
  import { editorStore } from '$lib/stores/editor.svelte';
  import { addToast } from '$lib/stores/toast.svelte';
  import { formatScore, copyToClipboard } from '$lib/utils/formatting';
  import { passthroughGuide } from '$lib/stores/passthrough-guide.svelte';
  import { tooltip } from '$lib/actions/tooltip';
  import { PASSTHROUGH_TOOLTIPS } from '$lib/utils/ui-tooltips';

  let optimizedPrompt = $state('');
  let changesSummary = $state('');
  let copying = $state(false);
  let saving = $state(false);

  const isLoading = $derived(!forgeStore.assembledPrompt);
  const canSave = $derived(optimizedPrompt.trim().length > 0 && !saving && !isLoading);

  // Toast when assembled prompt arrives
  $effect(() => {
    if (forgeStore.assembledPrompt && forgeStore.status === 'passthrough') {
      const strategy = forgeStore.passthroughStrategy ?? 'auto';
      addToast('created', `Prompt assembled — ${strategy}`);
    }
  });

  async function copyAssembled() {
    if (!forgeStore.assembledPrompt) return;
    const ok = await copyToClipboard(forgeStore.assembledPrompt);
    if (ok) {
      copying = true;
      addToast('created', 'Copied to clipboard');
      setTimeout(() => { copying = false; }, 1200);
    } else {
      addToast('deleted', 'Copy failed — select and copy manually');
    }
  }

  async function handleSave() {
    if (!canSave) return;
    saving = true;
    await forgeStore.submitPassthrough(optimizedPrompt.trim(), changesSummary.trim() || undefined);
    saving = false;

    // Toast + open result tab on success
    if (forgeStore.status === 'complete' && forgeStore.result) {
      const score = forgeStore.result.overall_score != null ? formatScore(forgeStore.result.overall_score) : '—';
      addToast('created', `Passthrough saved — ${score}`);
      editorStore.openResult(forgeStore.result.id);
    }
  }
</script>

<div class="passthrough-view">
  <!-- Header -->
  <div class="passthrough-header">
    <span class="header-label">MANUAL PASSTHROUGH</span>
    {#if forgeStore.passthroughStrategy}
      <span class="header-strategy">strategy: {forgeStore.passthroughStrategy}</span>
    {/if}
    <div class="spacer"></div>
    <button
      class="guide-btn"
      onclick={() => passthroughGuide.show(false)}
      aria-label="Open passthrough workflow guide"
      use:tooltip={PASSTHROUGH_TOOLTIPS.guide_btn}
    >?</button>
    <button class="cancel-btn" onclick={() => forgeStore.cancel()}>CANCEL</button>
  </div>

  <div class="passthrough-body">
    <!-- Assembled prompt panel -->
    <div class="section">
      <div class="section-bar">
        <span class="section-label">ASSEMBLED PROMPT</span>
        <span class="section-hint">Copy this to your LLM of choice</span>
        <div class="spacer"></div>
        <button class="copy-btn" class:copied={copying} disabled={isLoading} onclick={copyAssembled}>
          {copying ? 'COPIED' : 'COPY'}
        </button>
      </div>
      {#if isLoading}
        <div class="loading-state"><span class="loading-label">Preparing prompt...</span></div>
      {:else}
        <pre class="assembled-prompt">{forgeStore.assembledPrompt ?? ''}</pre>
      {/if}
    </div>

    <!-- Result input -->
    <div class="section">
      <div class="section-bar">
        <span class="section-label">OPTIMIZED RESULT</span>
        <span class="section-hint">Paste your LLM's optimized output below</span>
      </div>
      <textarea
        class="result-textarea"
        placeholder="Paste the optimized prompt here..."
        bind:value={optimizedPrompt}
        spellcheck="false"
        aria-label="Optimized prompt result"
      ></textarea>
    </div>

    <!-- Changes summary + save -->
    <div class="save-bar">
      <input
        class="summary-input"
        type="text"
        placeholder="Changes summary (optional)"
        bind:value={changesSummary}
        aria-label="Changes summary"
      />
      <button class="save-btn" disabled={!canSave} onclick={handleSave}>
        {saving ? 'SAVING...' : 'SAVE'}
      </button>
    </div>
  </div>
</div>

<style>
  .passthrough-view {
    display: flex;
    flex-direction: column;
    height: 100%;
    overflow: hidden;
  }

  .passthrough-header {
    display: flex;
    align-items: center;
    gap: 6px;
    height: 24px;
    padding: 0 4px;
    background: var(--color-bg-secondary);
    border-bottom: 1px solid var(--color-neon-yellow);
    flex-shrink: 0;
  }

  .header-label {
    font-size: 10px;
    font-family: var(--font-display);
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    color: var(--color-neon-yellow);
  }

  .header-strategy {
    font-size: 10px;
    font-family: var(--font-mono);
    color: var(--color-text-dim);
  }

  .guide-btn {
    display: flex;
    align-items: center;
    justify-content: center;
    font-family: var(--font-mono);
    font-size: 10px;
    font-weight: 700;
    color: var(--color-neon-yellow);
    border: 1px solid var(--color-neon-yellow);
    background: transparent;
    width: 16px;
    height: 16px;
    padding: 0;
    cursor: pointer;
    transition: all 200ms cubic-bezier(0.16, 1, 0.3, 1);
    line-height: 1;
  }

  .guide-btn:hover {
    background: rgba(251, 191, 36, 0.06);
  }

  .cancel-btn {
    font-family: var(--font-display);
    font-size: 9px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    color: var(--color-text-dim);
    border: 1px solid var(--color-border-subtle);
    background: transparent;
    padding: 0 6px;
    height: 16px;
    line-height: 14px;
    cursor: pointer;
    transition: all 200ms cubic-bezier(0.16, 1, 0.3, 1);
    white-space: nowrap;
  }

  .cancel-btn:hover {
    color: var(--color-text-primary);
    border-color: var(--color-text-dim);
  }

  .passthrough-body {
    flex: 1;
    display: flex;
    flex-direction: column;
    overflow: hidden;
    gap: 0;
  }

  .section {
    display: flex;
    flex-direction: column;
    flex: 1;
    min-height: 0;
    overflow: hidden;
  }

  .section-bar {
    display: flex;
    align-items: center;
    gap: 4px;
    height: 22px;
    padding: 0 4px;
    background: var(--color-bg-secondary);
    border-bottom: 1px solid var(--color-border-subtle);
    flex-shrink: 0;
  }

  .section-label {
    font-size: 10px;
    font-family: var(--font-display);
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    color: var(--color-text-dim);
  }

  .section-hint {
    font-size: 10px;
    font-family: var(--font-sans);
    color: var(--color-text-dim);
    opacity: 0.6;
  }

  .spacer {
    flex: 1;
  }

  .copy-btn {
    font-family: var(--font-display);
    font-size: 9px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    color: var(--color-neon-yellow);
    border: 1px solid var(--color-neon-yellow);
    background: transparent;
    padding: 0 6px;
    height: 16px;
    line-height: 14px;
    cursor: pointer;
    transition: all 200ms cubic-bezier(0.16, 1, 0.3, 1);
    white-space: nowrap;
  }

  .copy-btn:hover:not(:disabled) {
    background: rgba(251, 191, 36, 0.06);
    transform: translateY(-1px);
  }

  .copy-btn:active:not(:disabled) {
    transform: translateY(0);
  }

  .copy-btn:disabled {
    opacity: 0.4;
    cursor: not-allowed;
  }

  .copy-btn.copied {
    color: var(--color-neon-green);
    border-color: var(--color-neon-green);
  }

  .loading-state {
    flex: 1;
    display: flex;
    align-items: center;
    justify-content: center;
    background: var(--color-bg-input);
    border-bottom: 1px solid var(--color-border-subtle);
  }

  .loading-label {
    font-size: 11px;
    font-family: var(--font-mono);
    color: var(--color-text-dim);
  }

  .assembled-prompt {
    flex: 1;
    overflow: auto;
    margin: 0;
    padding: 6px;
    background: var(--color-bg-input);
    border-bottom: 1px solid var(--color-border-subtle);
    color: var(--color-text-primary);
    font-family: var(--font-mono);
    font-size: 11px;
    line-height: 1.5;
    white-space: pre-wrap;
    word-break: break-word;
  }

  .result-textarea {
    flex: 1;
    width: 100%;
    resize: none;
    background: var(--color-bg-input);
    border: none;
    border-bottom: 1px solid var(--color-border-subtle);
    color: var(--color-text-primary);
    font-family: var(--font-sans);
    font-size: 12px;
    line-height: 1.6;
    padding: 6px;
    outline: none;
    transition: border-color 200ms cubic-bezier(0.16, 1, 0.3, 1);
    box-sizing: border-box;
  }

  .result-textarea::placeholder {
    color: var(--color-text-dim);
  }

  .result-textarea:focus {
    border-color: rgba(251, 191, 36, 0.3);
  }

  .save-bar {
    display: flex;
    align-items: center;
    gap: 4px;
    height: 28px;
    padding: 0 4px;
    background: var(--color-bg-secondary);
    border-top: 1px solid var(--color-border-subtle);
    flex-shrink: 0;
  }

  .summary-input {
    flex: 1;
    height: 20px;
    padding: 0 4px;
    background: var(--color-bg-input);
    border: 1px solid var(--color-border-subtle);
    color: var(--color-text-primary);
    font-size: 11px;
    font-family: var(--font-sans);
    outline: none;
    transition: border-color 200ms cubic-bezier(0.16, 1, 0.3, 1);
  }

  .summary-input::placeholder {
    color: var(--color-text-dim);
  }

  .summary-input:focus {
    border-color: rgba(251, 191, 36, 0.3);
  }

  .save-btn {
    font-family: var(--font-display);
    font-size: 10px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    color: var(--color-neon-yellow);
    border: 1px solid var(--color-neon-yellow);
    background: transparent;
    padding: 0 8px;
    height: 20px;
    line-height: 18px;
    cursor: pointer;
    transition: all 200ms cubic-bezier(0.16, 1, 0.3, 1);
    white-space: nowrap;
  }

  .save-btn:hover:not(:disabled) {
    transform: translateY(-1px);
    background: rgba(251, 191, 36, 0.06);
  }

  .save-btn:active:not(:disabled) {
    transform: translateY(0);
  }

  .save-btn:disabled {
    opacity: 0.4;
    cursor: not-allowed;
  }
</style>
