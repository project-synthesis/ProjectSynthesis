<script lang="ts">
  import { forgeStore } from '$lib/stores/forge.svelte';
  import { editorStore } from '$lib/stores/editor.svelte';
  import { clustersStore } from '$lib/stores/clusters.svelte';

  import { getStrategies } from '$lib/api/client';
  import PatternSuggestion from './PatternSuggestion.svelte';
  import { getPhaseLabel } from '$lib/utils/dimensions';
  import { strategyListToOptions, type StrategyOption } from '$lib/utils/strategies';

  // Dynamic strategy list — fetched from disk via API
  let strategyOptions = $state<StrategyOption[]>([{ value: '', label: 'auto' }]);

  let strategiesLoaded = false;
  $effect(() => {
    if (strategiesLoaded) return;
    strategiesLoaded = true;
    getStrategies()
      .then((list) => { strategyOptions = strategyListToOptions(list); })
      .catch(() => {});
  });

  // React to strategy file changes (sync dropdown with disk state)
  $effect(() => {
    const handler = () => {
      getStrategies().then((list) => {
        strategyOptions = strategyListToOptions(list);

        // Reset if active strategy was deleted
        if (forgeStore.strategy && !list.some(s => s.name === forgeStore.strategy)) {
          forgeStore.strategy = null;
        }
      }).catch(() => {});
    };
    window.addEventListener('strategy-changed', handler);
    return () => window.removeEventListener('strategy-changed', handler);
  });

  // Defense-in-depth: re-fetch strategies when tab becomes visible.
  // Covers missed SSE events (e.g., connection loss during background tab).
  $effect(() => {
    const handler = () => {
      if (document.visibilityState === 'visible') {
        getStrategies()
          .then((list) => { strategyOptions = strategyListToOptions(list); })
          .catch(() => {});
      }
    };
    document.addEventListener('visibilitychange', handler);
    return () => document.removeEventListener('visibilitychange', handler);
  });

  const isSynthesizing = $derived(
    forgeStore.status !== 'idle' &&
    forgeStore.status !== 'complete' &&
    forgeStore.status !== 'error' &&
    forgeStore.status !== 'passthrough'
  );

  const isPassthroughMode = $derived(forgeStore.status === 'passthrough');

  const buttonLabel = $derived(
    isSynthesizing ? 'CANCEL'
    : isPassthroughMode ? 'PREPARE'
    : 'SYNTHESIZE'
  );

  const phaseLabel = $derived.by(() => {
    const label = getPhaseLabel(forgeStore.status);
    return label ? `${label}...` : null;
  });

  // Derived select value: null = auto = ''
  const selectValue = $derived(forgeStore.strategy ?? '');

  function handleStrategyChange(e: Event) {
    const val = (e.target as HTMLSelectElement).value;
    forgeStore.strategy = val === '' ? null : val;
  }

  function handleInput(e: Event) {
    const target = e.target as HTMLTextAreaElement;
    clustersStore.checkForPatterns(target.value);
  }

  // Track detached effect so we can clean up on re-invocation or unmount
  let pendingResultEffect: (() => void) | null = null;

  // Ensure cleanup on component unmount (e.g. if forge is still in-flight)
  $effect(() => () => { pendingResultEffect?.(); pendingResultEffect = null; });

  function handleButtonClick() {
    if (isSynthesizing) {
      forgeStore.cancel();
      return;
    }
    handleSynthesize();
  }

  async function handleSynthesize() {
    // Clean up any orphaned detached effect from a previous call
    pendingResultEffect?.();
    pendingResultEffect = null;

    forgeStore.forge();

    // Passthrough mode stays on the prompt tab (which renders PassthroughView)
    if (forgeStore.status === 'passthrough') return;

    // Wait for result.id (set on optimization_complete) instead of traceId
    // (set on optimization_start). This ensures the tab's optimizationId
    // matches the cache key from loadFromRecord → cacheResult(opt.id).
    pendingResultEffect = $effect.root(() => {
      $effect(() => {
        if (forgeStore.result?.id) {
          editorStore.openResult(forgeStore.result.id); // data already cached by loadFromRecord
          pendingResultEffect?.();
          pendingResultEffect = null;
        }
      });
    });
  }
</script>

<div class="prompt-edit">
  <!-- Editor area (top — takes all available space) -->
  <div class="editor-area">
    <PatternSuggestion onApply={(result) => {
      forgeStore.appliedPatternIds = result.ids;
      forgeStore.appliedPatternLabel = result.clusterLabel;
    }} />
    <textarea
      class="prompt-textarea"
      placeholder="Enter your prompt here..."
      bind:value={forgeStore.prompt}
      oninput={handleInput}
      spellcheck="false"
      aria-label="Prompt editor"
    ></textarea>
    {#if forgeStore.appliedPatternIds && forgeStore.appliedPatternIds.length > 0}
      <div class="applied-chip">
        <span class="chip-text">{forgeStore.appliedPatternIds.length} patterns{forgeStore.appliedPatternLabel ? ` from "${forgeStore.appliedPatternLabel}"` : ''}</span>
        <button
          class="chip-clear"
          onclick={() => { forgeStore.appliedPatternIds = null; forgeStore.appliedPatternLabel = null; }}
          aria-label="Clear applied patterns"
        >&times;</button>
      </div>
    {/if}
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
      class:synthesize-btn--cancel={isSynthesizing}
      onclick={handleButtonClick}
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
    padding: 6px;
    outline: none;
    box-sizing: border-box;
  }

  .prompt-textarea::placeholder {
    color: var(--color-text-dim);
  }

  /* Applied patterns chip — persistent indicator below textarea */
  .applied-chip {
    display: flex;
    align-items: center;
    gap: 4px;
    padding: 3px 6px;
    background: color-mix(in srgb, var(--color-neon-cyan) 8%, transparent);
    border-top: 1px solid color-mix(in srgb, var(--color-neon-cyan) 25%, transparent);
    font-size: 10px;
    font-family: var(--font-mono);
    color: var(--color-neon-cyan);
    flex-shrink: 0;
  }

  .chip-text {
    flex: 1;
  }

  .chip-clear {
    background: none;
    border: none;
    color: var(--color-text-muted);
    cursor: pointer;
    font-size: 14px;
    line-height: 1;
    padding: 0 2px;
  }

  .chip-clear:hover {
    color: var(--color-text-primary);
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
    border-color: rgba(var(--tier-accent-rgb, 0, 229, 255), 0.3);
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
    color: var(--tier-accent, var(--color-neon-cyan));
  }

  .synthesize-btn {
    font-family: var(--font-display);
    font-size: 10px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    color: var(--tier-accent, var(--color-neon-cyan));
    border: 1px solid var(--tier-accent, var(--color-neon-cyan));
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
    background: rgba(var(--tier-accent-rgb, 0, 229, 255), 0.06);
  }

  .synthesize-btn:active:not(:disabled) {
    transform: translateY(0);
  }

  .synthesize-btn:disabled {
    opacity: 0.4;
    cursor: not-allowed;
  }

  /* Cancel state — inherits tier accent from base .synthesize-btn.
     Class kept as a semantic hook for tests and future differentiation. */
</style>
