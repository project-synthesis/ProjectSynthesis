<script lang="ts">
  import { editorStore } from '$lib/stores/editor.svelte';
  import { forgeStore } from '$lib/stores/forge.svelte';

  import { refinementStore } from '$lib/stores/refinement.svelte';
  import PromptEdit from '$lib/components/editor/PromptEdit.svelte';
  import ForgeArtifact from '$lib/components/editor/ForgeArtifact.svelte';
  import PassthroughView from '$lib/components/editor/PassthroughView.svelte';
  import DiffView from '$lib/components/shared/DiffView.svelte';
  import RefinementTimeline from '$lib/components/refinement/RefinementTimeline.svelte';
  import SemanticTopology from '$lib/components/taxonomy/SemanticTopology.svelte';
  import ContextPanel from '$lib/components/editor/ContextPanel.svelte';
  import { isPassthroughResult } from '$lib/utils/formatting';
  import { tooltip } from '$lib/actions/tooltip';
  import { EDITOR_TOOLTIPS } from '$lib/utils/ui-tooltips';

  // Initialize refinement when result tab is active.
  // Uses tab-scoped activeResult (not global forgeStore.result) to avoid
  // cross-contamination when multiple result tabs are open.
  // Skip for passthrough results — refinement requires a local provider.
  let lastInitId = $state<string | null>(null);
  $effect(() => {
    const tab = editorStore.activeTab;
    const result = editorStore.activeResult ?? forgeStore.result;
    if (
      tab?.type === 'result' &&
      result?.id &&
      result.id !== lastInitId &&
      !isPassthroughResult(result)
    ) {
      lastInitId = result.id;
      refinementStore.init(result.id);
    }
  });

  const showRefinement = $derived(
    editorStore.activeTab?.type === 'result' &&
    forgeStore.status === 'complete' &&
    !isPassthroughResult(editorStore.activeResult ?? forgeStore.result)
  );

  const hasRefinementTurns = $derived(refinementStore.turns.length > 0);

  function handleNewPrompt() {
    forgeStore.reset();
    editorStore.closeAllResults();
  }

  // Tier 1 — viewport-aware ContextPanel rail. Below 1400px we force the
  // panel to render as a 28px rail regardless of the user's persisted
  // open/closed preference so the editor doesn't lose horizontal space.
  let innerWidth = $state(typeof window !== 'undefined' ? window.innerWidth : 1920);
  $effect(() => {
    if (typeof window === 'undefined') return;
    const handler = () => { innerWidth = window.innerWidth; };
    window.addEventListener('resize', handler);
    return () => window.removeEventListener('resize', handler);
  });
  const narrowViewport = $derived(innerWidth < 1400);
</script>

<div class="editor-shell">
<div class="editor-groups">
  <!-- Tab bar -->
  <div
    class="tab-bar"
    role="tablist"
    aria-label="Editor tabs"
    onwheel={(e) => {
      if (e.deltaY !== 0) {
        e.currentTarget.scrollLeft += e.deltaY;
        e.preventDefault();
      }
    }}
  >
    {#each editorStore.tabs as tab (tab.id)}
      <button
        class="tab"
        class:active={editorStore.activeTabId === tab.id}
        role="tab"
        aria-selected={editorStore.activeTabId === tab.id}
        aria-controls="editor-panel-{tab.id}"
        use:tooltip={tab.title}
        onclick={() => editorStore.setActive(tab.id)}
      >
        <span class="tab-title">{tab.title}</span>
        {#if !tab.pinned}
          <span
            class="tab-close"
            role="button"
            tabindex="-1"
            aria-label="Close {tab.title}"
            onclick={(e) => {
              e.stopPropagation();
              editorStore.closeTab(tab.id);
            }}
            onkeydown={(e) => { if (e.key === 'Enter') { e.stopPropagation(); editorStore.closeTab(tab.id); } }}
          >×</span>
        {/if}
      </button>
    {/each}
    <button
      class="tab-new"
      aria-label="New prompt"
      use:tooltip={EDITOR_TOOLTIPS.new_prompt}
      onclick={handleNewPrompt}
    >+</button>
  </div>

  <!-- Content area -->
  <div class="editor-content">
    {#each editorStore.tabs as tab (tab.id)}
      <div
        id="editor-panel-{tab.id}"
        class="editor-panel"
        class:visible={editorStore.activeTabId === tab.id}
        role="tabpanel"
        aria-label="{tab.title} panel"
      >
        {#if tab.type === 'prompt'}
          {#if forgeStore.status === 'passthrough'}
            <PassthroughView />
          {:else}
            <PromptEdit />
          {/if}
        {:else if tab.type === 'result'}
          {#if showRefinement}
            <div class="result-split">
              <div class="result-split-top">
                <ForgeArtifact />
              </div>
              <div class="result-split-bottom" class:collapsed={!hasRefinementTurns}>
                <RefinementTimeline />
              </div>
            </div>
          {:else}
            <ForgeArtifact />
          {/if}
        {:else if tab.type === 'diff'}
          {@const diffResult = editorStore.activeResult ?? forgeStore.result}
          {#if diffResult?.raw_prompt && diffResult?.optimized_prompt}
            <DiffView
              original={diffResult.raw_prompt}
              optimized={diffResult.optimized_prompt}
            />
          {:else}
            <div class="placeholder-panel">
              <span class="placeholder-label">No diff available — forge a prompt first</span>
            </div>
          {/if}
        {:else if tab.type === 'mindmap'}
          <SemanticTopology />
        {/if}
      </div>
    {/each}

    {#if editorStore.tabs.length === 0}
      <div class="empty-state">
        <span class="placeholder-label">No open tabs</span>
      </div>
    {/if}
  </div>
</div>
<ContextPanel forceCollapsed={narrowViewport} />
</div>

<style>
  /* Tier 1 — horizontal shell so ContextPanel sits beside the editor groups */
  .editor-shell {
    display: flex;
    flex-direction: row;
    height: 100%;
    min-width: 0;
  }

  .editor-groups {
    display: flex;
    flex-direction: column;
    height: 100%;
    background: var(--color-bg-primary);
    overflow: hidden;
    min-width: 0;
    flex: 1 1 auto;
  }

  .tab-bar {
    display: flex;
    align-items: stretch;
    height: 24px;
    background: var(--color-bg-secondary);
    border-bottom: 1px solid var(--color-border-subtle);
    overflow-x: auto;
    overflow-y: hidden;
    flex-shrink: 0;
    gap: 0;
    padding: 0 1px;
  }

  .tab-bar::-webkit-scrollbar {
    height: 2px;
  }

  .tab-bar::-webkit-scrollbar-thumb {
    background: var(--color-border-subtle);
  }

  .tab-bar::-webkit-scrollbar-thumb:hover {
    background: var(--color-text-dim);
  }

  .tab-bar::-webkit-scrollbar-track {
    background: transparent;
  }

  .tab {
    display: flex;
    align-items: center;
    gap: 2px;
    height: 100%;
    padding: 0 4px;
    border: none;
    border-bottom: 1px solid transparent;
    background: transparent;
    color: var(--color-text-dim);
    font-size: 11px; /* text-[11px] */
    font-family: var(--font-sans);
    white-space: nowrap;
    cursor: pointer;
    user-select: none;
    position: relative;
    outline: none;
    transition: color var(--duration-hover) var(--ease-spring),
                background var(--duration-hover) var(--ease-spring);
  }

  .tab:hover {
    color: var(--color-text-primary);
    background: var(--color-bg-hover);
    border-color: transparent;
  }

  .tab:active {
    transform: none;
    border-color: transparent;
  }

  .tab.active {
    color: var(--color-text-primary);
    background: var(--color-bg-primary);
    border-bottom-color: var(--tier-accent, var(--color-neon-cyan));
  }

  .tab.active:hover {
    background: var(--color-bg-primary);
    border-bottom-color: var(--tier-accent, var(--color-neon-cyan));
  }

  .tab-title {
    pointer-events: none;
    max-width: 100px;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .tab-close {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 14px;
    height: 14px;
    padding: 0;
    border: none;
    background: transparent;
    color: var(--color-text-dim);
    font-size: 10px;
    line-height: 1;
    cursor: pointer;
    opacity: 0;
    transition: opacity var(--duration-hover) var(--ease-spring),
                color var(--duration-hover) var(--ease-spring);
  }

  .tab:hover .tab-close,
  .tab.active .tab-close {
    opacity: 1;
  }

  .tab-close:hover {
    color: var(--color-text-primary);
    background: transparent;
    border-color: transparent;
  }

  .tab-close:active {
    transform: none;
    border-color: transparent;
  }

  .tab-new {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 20px;
    height: 100%;
    border: none;
    background: transparent;
    color: var(--color-text-dim);
    font-size: 14px;
    font-family: var(--font-mono);
    cursor: pointer;
    transition: color var(--duration-hover) var(--ease-spring);
    flex-shrink: 0;
    outline: none;
    user-select: none;
  }

  .tab-new:hover {
    color: var(--color-text-primary);
  }

  .editor-content {
    flex: 1;
    position: relative;
    overflow: hidden;
  }

  .editor-panel {
    position: absolute;
    inset: 0;
    display: none;
    overflow: auto;
  }

  .editor-panel.visible {
    display: block;
  }

  .placeholder-panel,
  .empty-state {
    display: flex;
    align-items: center;
    justify-content: center;
    height: 100%;
  }

  .placeholder-label {
    font-size: 11px;
    color: var(--color-text-dim);
    font-family: var(--font-mono);
  }

  /* Result split pane — ForgeArtifact top, RefinementTimeline bottom */
  .result-split {
    display: flex;
    flex-direction: column;
    height: 100%;
    overflow: hidden;
  }

  .result-split-top {
    flex: 1 1 0;
    min-height: 0;
    overflow: hidden;
  }

  .result-split-bottom {
    flex: 0 0 auto;
    max-height: 50%;
    overflow: hidden;
  }

  .result-split-bottom.collapsed {
    max-height: none;
  }
</style>
