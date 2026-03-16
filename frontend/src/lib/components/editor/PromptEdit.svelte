<script lang="ts">
  import { forgeStore } from '$lib/stores/forge.svelte';
  import { editorStore } from '$lib/stores/editor.svelte';

  const strategies = [
    { value: '', label: 'Auto' },
    { value: 'chain-of-thought', label: 'chain-of-thought' },
    { value: 'few-shot', label: 'few-shot' },
    { value: 'role-playing', label: 'role-playing' },
    { value: 'structured-output', label: 'structured-output' },
    { value: 'meta-prompting', label: 'meta-prompting' },
  ];

  const isForging = $derived(
    forgeStore.status !== 'idle' &&
    forgeStore.status !== 'complete' &&
    forgeStore.status !== 'error'
  );

  const phaseLabel = $derived(() => {
    switch (forgeStore.status) {
      case 'analyzing': return 'Analyzing...';
      case 'optimizing': return 'Optimizing...';
      case 'scoring': return 'Scoring...';
      default: return null;
    }
  });

  function handleForge() {
    forgeStore.forge();
    if (forgeStore.traceId) {
      editorStore.openResult(forgeStore.traceId);
    } else {
      // Watch for traceId to be set after forge starts
      const unsub = $effect.root(() => {
        $effect(() => {
          if (forgeStore.traceId) {
            editorStore.openResult(forgeStore.traceId);
            unsub();
          }
        });
      });
    }
  }
</script>

<div class="prompt-edit">
  <!-- Toolbar -->
  <div class="toolbar">
    <span class="toolbar-label">STRATEGY</span>
    <select
      class="strategy-select"
      bind:value={forgeStore.strategy}
    >
      {#each strategies as s}
        <option value={s.value}>{s.label}</option>
      {/each}
    </select>

    <div class="spacer"></div>

    {#if phaseLabel()}
      <span class="phase-label">{phaseLabel()}</span>
    {/if}

    <button
      class="forge-btn"
      disabled={isForging}
      onclick={handleForge}
    >
      FORGE
    </button>
  </div>

  <!-- Editor area -->
  <div class="editor-area">
    <textarea
      class="prompt-textarea"
      placeholder="Enter your prompt here..."
      bind:value={forgeStore.prompt}
      spellcheck="false"
      aria-label="Prompt editor"
    ></textarea>
  </div>
</div>

<style>
  .prompt-edit {
    display: flex;
    flex-direction: column;
    height: 100%;
    overflow: hidden;
  }

  .toolbar {
    display: flex;
    align-items: center;
    gap: 8px;
    height: 32px;
    padding: 0 8px;
    background: var(--color-bg-secondary);
    border-bottom: 1px solid var(--color-border-subtle);
    flex-shrink: 0;
  }

  .toolbar-label {
    font-size: 10px;
    font-family: var(--font-display);
    text-transform: uppercase;
    letter-spacing: 0.1em;
    color: var(--color-text-dim);
  }

  .strategy-select {
    height: 28px;
    padding: 0 6px;
    background: var(--color-bg-input);
    border: 1px solid var(--color-border-subtle);
    color: var(--color-text-primary);
    font-size: 11px;
    font-family: var(--font-sans);
    cursor: pointer;
    transition: border-color 200ms cubic-bezier(0.16, 1, 0.3, 1);
    outline: none;
  }

  .strategy-select:focus {
    border-color: rgba(0, 229, 255, 0.3);
  }

  .strategy-select option {
    background: var(--color-bg-secondary);
    color: var(--color-text-primary);
  }

  .spacer {
    flex: 1;
  }

  .phase-label {
    font-size: 10px;
    font-family: var(--font-sans);
    color: var(--color-neon-cyan);
    letter-spacing: 0.05em;
  }

  .forge-btn {
    height: 24px;
    padding: 0 12px;
    background: transparent;
    border: 1px solid var(--color-neon-cyan);
    color: var(--color-neon-cyan);
    font-family: var(--font-display);
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    cursor: pointer;
    transition: background 200ms cubic-bezier(0.16, 1, 0.3, 1),
                border-color 200ms cubic-bezier(0.16, 1, 0.3, 1);
    white-space: nowrap;
  }

  .forge-btn:hover:not(:disabled) {
    background: rgba(0, 229, 255, 0.08);
  }

  .forge-btn:disabled {
    opacity: 0.4;
    cursor: not-allowed;
  }

  .editor-area {
    flex: 1;
    overflow: hidden;
    display: flex;
    flex-direction: column;
  }

  .prompt-textarea {
    flex: 1;
    width: 100%;
    height: 100%;
    resize: none;
    background: var(--color-bg-input);
    border: none;
    border-right: 1px solid var(--color-border-subtle);
    color: var(--color-text-primary);
    font-family: var(--font-sans);
    font-size: 12px;
    line-height: 1.6;
    padding: 8px;
    outline: none;
    transition: border-color 200ms cubic-bezier(0.16, 1, 0.3, 1);
    box-sizing: border-box;
  }

  .prompt-textarea::placeholder {
    color: var(--color-text-dim);
  }

  .prompt-textarea:focus {
    border-color: rgba(0, 229, 255, 0.3);
  }
</style>
