<script lang="ts">
  import { editorStore } from '$lib/stores/editor.svelte';
  import { forgeStore } from '$lib/stores/forge.svelte';
  import PromptEdit from '$lib/components/editor/PromptEdit.svelte';
  import ForgeArtifact from '$lib/components/editor/ForgeArtifact.svelte';
  import DiffView from '$lib/components/shared/DiffView.svelte';
</script>

<div class="editor-groups">
  <!-- Tab bar -->
  <div class="tab-bar" role="tablist" aria-label="Editor tabs">
    {#each editorStore.tabs as tab (tab.id)}
      <button
        class="tab"
        class:active={editorStore.activeTabId === tab.id}
        role="tab"
        aria-selected={editorStore.activeTabId === tab.id}
        aria-controls="editor-panel-{tab.id}"
        onclick={() => editorStore.setActive(tab.id)}
      >
        <span class="tab-title">{tab.title}</span>
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
      </button>
    {/each}
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
          <PromptEdit />
        {:else if tab.type === 'result'}
          <ForgeArtifact />
        {:else if tab.type === 'diff'}
          {#if forgeStore.prompt && forgeStore.result?.optimized_prompt}
            <DiffView
              original={forgeStore.prompt}
              optimized={forgeStore.result.optimized_prompt}
            />
          {:else}
            <div class="placeholder-panel">
              <span class="placeholder-label">No diff available — forge a prompt first</span>
            </div>
          {/if}
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

<style>
  .editor-groups {
    display: flex;
    flex-direction: column;
    height: 100%;
    background: var(--color-bg-primary);
    overflow: hidden;
  }

  .tab-bar {
    display: flex;
    align-items: stretch;
    height: 32px; /* h-8 */
    background: var(--color-bg-secondary);
    border-bottom: 1px solid var(--color-border-subtle);
    overflow-x: auto;
    overflow-y: hidden;
    flex-shrink: 0;
    gap: 4px; /* gap-1 */
    padding: 0 4px;
  }

  .tab-bar::-webkit-scrollbar {
    height: 0;
  }

  .tab {
    display: flex;
    align-items: center;
    gap: 4px; /* gap-1 */
    height: 100%;
    padding: 0 10px; /* px-2.5 */
    border: none;
    border-bottom: 1px solid transparent;
    background: transparent;
    color: var(--color-text-dim);
    font-size: 11px; /* text-[11px] */
    font-family: var(--font-sans);
    white-space: nowrap;
    cursor: pointer;
    position: relative;
    transition: color 200ms cubic-bezier(0.16, 1, 0.3, 1),
                background 200ms cubic-bezier(0.16, 1, 0.3, 1);
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
    border-bottom-color: var(--color-neon-cyan);
  }

  .tab.active:hover {
    background: var(--color-bg-primary);
    border-bottom-color: var(--color-neon-cyan);
  }

  .tab-title {
    pointer-events: none;
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
    font-size: 12px;
    line-height: 1;
    cursor: pointer;
    opacity: 0;
    transition: opacity 200ms cubic-bezier(0.16, 1, 0.3, 1),
                color 200ms cubic-bezier(0.16, 1, 0.3, 1);
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
</style>
