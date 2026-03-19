<script lang="ts">
  import { forgeStore } from '$lib/stores/forge.svelte';
  import { editorStore } from '$lib/stores/editor.svelte';
  import { patternsStore } from '$lib/stores/patterns.svelte';
  import { getStrategies, getHealth } from '$lib/api/client';
  import type { StrategyInfo } from '$lib/api/client';
  import PatternSuggestion from './PatternSuggestion.svelte';

  // Dynamic strategy list — fetched from disk via API
  let strategyOptions = $state<{ value: string; label: string }[]>([{ value: '', label: 'Auto' }]);

  let strategiesLoaded = false;
  $effect(() => {
    if (strategiesLoaded) return;
    strategiesLoaded = true;
    getStrategies().then((list: StrategyInfo[]) => {
      const rest = list.filter(s => s.name !== 'auto');
      strategyOptions = [
        { value: '', label: 'auto' },
        ...rest.map(s => ({
          value: s.name,
          label: s.tagline ? `${s.name} — ${s.tagline}` : s.name,
        })),
      ];
    }).catch(() => {});
  });

  // React to strategy file changes (sync dropdown with disk state)
  $effect(() => {
    const handler = () => {
      getStrategies().then((list: StrategyInfo[]) => {
        const rest = list.filter(s => s.name !== 'auto');
        strategyOptions = [
          { value: '', label: 'auto' },
          ...rest.map(s => ({
            value: s.name,
            label: s.tagline ? `${s.name} — ${s.tagline}` : s.name,
          })),
        ];

        // Reset if active strategy was deleted
        if (forgeStore.strategy && !list.some(s => s.name === forgeStore.strategy)) {
          forgeStore.strategy = null;
        }
      }).catch(() => {});
    };
    window.addEventListener('strategy-changed', handler);
    return () => window.removeEventListener('strategy-changed', handler);
  });

  const isSynthesizing = $derived(
    forgeStore.status !== 'idle' &&
    forgeStore.status !== 'complete' &&
    forgeStore.status !== 'error' &&
    forgeStore.status !== 'passthrough'
  );

  const isPassthroughMode = $derived(forgeStore.noProvider);

  const buttonLabel = $derived(isPassthroughMode ? 'PREPARE' : 'SYNTHESIZE');

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

  function handleInput(e: Event) {
    const target = e.target as HTMLTextAreaElement;
    patternsStore.checkForPatterns(target.value);
  }

  async function handleSynthesize() {
    // Re-check provider status before synthesizing — handles mid-session API key changes
    try {
      const h = await getHealth();
      forgeStore.noProvider = !h.provider;
    } catch { /* backend unreachable — forge() will fail with its own error */ }

    forgeStore.forge();

    // Passthrough mode stays on the prompt tab (which renders PassthroughView)
    if (forgeStore.status === 'passthrough') return;

    if (forgeStore.traceId) {
      editorStore.openResult(forgeStore.traceId);
    } else {
      // Watch for traceId to be set after SSE stream starts
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
  <!-- Editor area (top — takes all available space) -->
  <div class="editor-area">
    <PatternSuggestion onApply={(patternIds) => {
      forgeStore.appliedPatternIds = patternIds;
    }} />
    <textarea
      class="prompt-textarea"
      placeholder="Enter your prompt here..."
      bind:value={forgeStore.prompt}
      oninput={handleInput}
      spellcheck="false"
      aria-label="Prompt editor"
    ></textarea>
  </div>

  <!-- Action bar (bottom — strategy select + phase label + synthesize button) -->
  <div class="action-bar">
    <span class="action-label">STRATEGY</span>
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
      class="synthesize-btn"
      class:passthrough-mode={isPassthroughMode}
      disabled={isSynthesizing}
      onclick={handleSynthesize}
    >
      {buttonLabel}
    </button>
  </div>
</div>

<style>
  .prompt-edit {
    display: flex;
    flex-direction: column;
    height: 100%;
    overflow: hidden;
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
    color: var(--color-text-primary);
    font-family: var(--font-sans);
    font-size: 12px;
    line-height: 1.6;
    padding: 8px;
    outline: none;
    box-sizing: border-box;
  }

  .prompt-textarea::placeholder {
    color: var(--color-text-dim);
  }

  /* Action bar — bottom of the prompt editor */
  .action-bar {
    display: flex;
    align-items: center;
    gap: 4px;
    height: 28px;
    padding: 0 4px;
    background: var(--color-bg-secondary);
    border-top: 1px solid var(--color-border-subtle);
    flex-shrink: 0;
  }

  .action-label {
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

  .synthesize-btn {
    font-family: var(--font-display);
    font-size: 10px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    color: var(--color-neon-cyan);
    border: 1px solid var(--color-neon-cyan);
    background: transparent;
    padding: 0 8px;
    height: 20px;
    line-height: 18px;
    cursor: pointer;
    transition: all 200ms cubic-bezier(0.16, 1, 0.3, 1);
    white-space: nowrap;
  }

  .synthesize-btn:hover:not(:disabled) {
    transform: translateY(-1px);
    background: rgba(0, 229, 255, 0.06);
  }

  .synthesize-btn:active:not(:disabled) {
    transform: translateY(0);
  }

  .synthesize-btn:disabled {
    opacity: 0.4;
    cursor: not-allowed;
  }

  .synthesize-btn.passthrough-mode {
    color: var(--color-neon-yellow);
    border-color: var(--color-neon-yellow);
  }

  .synthesize-btn.passthrough-mode:hover:not(:disabled) {
    background: rgba(251, 191, 36, 0.06);
  }
</style>
