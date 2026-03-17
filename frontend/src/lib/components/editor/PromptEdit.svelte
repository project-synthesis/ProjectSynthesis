<script lang="ts">
  import { forgeStore } from '$lib/stores/forge.svelte';
  import { editorStore } from '$lib/stores/editor.svelte';
  import { preferencesStore } from '$lib/stores/preferences.svelte';
  import { getStrategies } from '$lib/api/client';
  import type { StrategyInfo } from '$lib/api/client';

  // Dynamic strategy list — fetched from disk via API
  let strategyOptions = $state<{ value: string; label: string }[]>([{ value: '', label: 'Auto' }]);

  let strategiesLoaded = false;
  $effect(() => {
    if (strategiesLoaded) return;
    strategiesLoaded = true;
    getStrategies().then((list: StrategyInfo[]) => {
      // Auto first (value='' means "let analyzer decide"), rest by filename
      const auto = list.find(s => s.name === 'auto');
      const rest = list.filter(s => s.name !== 'auto');
      strategyOptions = [
        { value: '', label: auto ? 'auto' : 'auto' },
        ...rest.map(s => ({ value: s.name, label: s.name })),
      ];
    }).catch(() => {});
  });

  const isForging = $derived(
    forgeStore.status !== 'idle' &&
    forgeStore.status !== 'complete' &&
    forgeStore.status !== 'error'
  );

  const phaseLabel = $derived.by(() => {
    switch (forgeStore.status) {
      case 'analyzing': return 'Analyzing...';
      case 'optimizing': return 'Optimizing...';
      case 'scoring': return 'Scoring...';
      default: return null;
    }
  });

  // Derived select value: null = auto = ''
  const selectValue = $derived(forgeStore.strategy ?? '');

  function handleStrategyChange(e: Event) {
    const val = (e.target as HTMLSelectElement).value;
    forgeStore.strategy = val === '' ? null : val;
  }

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
      value={selectValue}
      onchange={handleStrategyChange}
    >
      {#each strategyOptions as s}
        <option value={s.value}>{s.label}</option>
      {/each}
    </select>

    <div class="spacer"></div>

    {#if phaseLabel}
      <span class="phase-label">{phaseLabel}</span>
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
    gap: 4px;
    height: 24px;
    padding: 0 4px;
    background: var(--color-bg-secondary);
    border-bottom: 1px solid var(--color-border-subtle);
    flex-shrink: 0;
  }

  .toolbar-label {
    font-size: 10px;
    font-family: var(--font-display);
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    color: var(--color-text-dim);
  }

  .strategy-select {
    height: 18px;
    padding: 0 3px;
    background: var(--color-bg-input);
    border: 1px solid var(--color-border-subtle);
    color: var(--color-text-primary);
    font-size: 10px;
    font-family: var(--font-mono);
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
    font-family: var(--font-mono);
    color: var(--color-neon-cyan);
  }

  .forge-btn {
    font-family: var(--font-display);
    font-size: 10px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    color: var(--color-neon-cyan);
    border: 1px solid var(--color-neon-cyan);
    background: transparent;
    padding: 0 6px;
    height: 18px;
    line-height: 16px;
    cursor: pointer;
    transition: all 200ms cubic-bezier(0.16, 1, 0.3, 1);
    white-space: nowrap;
  }

  .forge-btn:hover:not(:disabled) {
    transform: translateY(-1px);
    background: rgba(0, 229, 255, 0.06);
  }

  .forge-btn:active:not(:disabled) {
    transform: translateY(0);
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
