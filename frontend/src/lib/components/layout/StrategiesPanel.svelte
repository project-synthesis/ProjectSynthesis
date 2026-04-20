<script lang="ts">
  /**
   * StrategiesPanel — Editor tab sidebar, strategy chooser + inline editor.
   *
   * Consumes `strategies` from the parent (shared with SettingsPanel's Defaults
   * dropdown — one authoritative list, no duplicate fetches). Owns the inline
   * strategy-editor state (which strategy is open, dirty flag, save spinner)
   * because it's entirely local to this panel.
   *
   * Extracted from Navigator.svelte to restore module boundaries. The strategy
   * file watcher lives in the parent so both StrategiesPanel and SettingsPanel
   * stay in sync without redundant subscriptions.
   */
  import type { StrategyInfo } from '$lib/api/client';
  import { getStrategy, updateStrategy } from '$lib/api/client';
  import { forgeStore } from '$lib/stores/forge.svelte';
  import { addToast } from '$lib/stores/toast.svelte';
  import { tooltip } from '$lib/actions/tooltip';
  import { STRATEGY_TOOLTIPS } from '$lib/utils/ui-tooltips';

  interface Props {
    strategies: StrategyInfo[];
    onSaved: (name: string) => Promise<void> | void;
  }

  let { strategies, onSaved }: Props = $props();

  let editingStrategy = $state<string | null>(null);
  let editContent = $state('');
  let editSaving = $state(false);
  let editDirty = $state(false);

  function selectStrategy(id: string): void {
    if (id === 'auto') {
      forgeStore.strategy = null;
    } else {
      forgeStore.strategy = forgeStore.strategy === id ? null : id;
    }
  }

  function isStrategyActive(name: string): boolean {
    if (name === 'auto') return forgeStore.strategy === null;
    return forgeStore.strategy === name;
  }

  async function openStrategyEditor(name: string): Promise<void> {
    if (editingStrategy === name) {
      editingStrategy = null;
      return;
    }
    try {
      const detail = await getStrategy(name);
      editContent = detail.content;
      editingStrategy = name;
      editDirty = false;
    } catch {
      editingStrategy = null;
      addToast('deleted', 'Failed to load strategy template');
    }
  }

  async function saveStrategyEdit(): Promise<void> {
    if (!editingStrategy || !editDirty) return;
    editSaving = true;
    try {
      await updateStrategy(editingStrategy, editContent);
      editDirty = false;
      await onSaved(editingStrategy);
    } catch {
      addToast('deleted', 'Strategy save failed');
    }
    editSaving = false;
  }

  function discardStrategyEdit(): void {
    editingStrategy = null;
    editDirty = false;
  }
</script>

<div class="panel">
  <header class="panel-header">
    <span class="section-heading">Strategies</span>
  </header>
  <div class="panel-body">
    {#if strategies.length === 0}
      <p class="empty-note">No strategy files found. Add .md files to prompts/strategies/ to define optimization strategies.</p>
    {/if}
    {#each strategies as strat (strat.name)}
      <div
        class="strat-row"
        class:strat-row--active={isStrategyActive(strat.name)}
        role="button"
        tabindex="0"
        onclick={() => selectStrategy(strat.name)}
        onkeydown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); selectStrategy(strat.name); } }}
        use:tooltip={strat.description}
      >
        <span class="strat-name">{strat.name}</span>
        <span class="strat-tag">{strat.tagline ?? ''}</span>
        <button
          class="strat-edit"
          onclick={(e: MouseEvent) => { e.stopPropagation(); openStrategyEditor(strat.name); }}
          use:tooltip={STRATEGY_TOOLTIPS.edit_template}
          aria-label="Edit template"
        >
          {editingStrategy === strat.name ? '×' : '⋮'}
        </button>
      </div>

      {#if editingStrategy === strat.name}
        <div class="strategy-editor">
          <span class="strategy-file-path">prompts/strategies/{strat.name}.md</span>
          <textarea
            class="strategy-textarea"
            value={editContent}
            oninput={(e) => { editContent = (e.target as HTMLTextAreaElement).value; editDirty = true; }}
            spellcheck="false"
          ></textarea>
          <div class="strategy-editor-actions">
            <button
              class="action-btn action-btn--primary"
              onclick={saveStrategyEdit}
              disabled={editSaving || !editDirty}
            >
              {editSaving ? 'Saving...' : 'SAVE'}
            </button>
            <button class="action-btn" onclick={discardStrategyEdit}>
              DISCARD
            </button>
          </div>
        </div>
      {/if}
    {/each}
  </div>
</div>

<style>
  .strat-row {
    display: flex;
    align-items: center;
    gap: 6px;
    height: 20px;
    padding: 0 6px;
    background: transparent;
    border: 1px solid transparent;
    cursor: pointer;
    transition: border-color var(--duration-hover) var(--ease-spring),
                background var(--duration-hover) var(--ease-spring);
  }

  .strat-row:hover {
    background: var(--color-bg-hover);
    border-color: var(--color-border-accent);
  }

  .strat-row--active {
    border-color: var(--tier-accent, var(--color-neon-cyan));
    background: rgba(var(--tier-accent-rgb, 0, 229, 255), 0.04);
  }

  .strat-name {
    font-size: 11px;
    font-family: var(--font-sans);
    font-weight: 400;
    color: var(--color-text-primary);
    white-space: nowrap;
    flex-shrink: 0;
  }

  .strat-row--active .strat-name {
    color: var(--tier-accent, var(--color-neon-cyan));
  }

  .strat-tag {
    font-size: 9px;
    font-family: var(--font-mono);
    color: color-mix(in srgb, var(--color-text-dim) 60%, transparent);
    white-space: nowrap;
    flex: 1;
    min-width: 0;
  }

  .strat-edit {
    font-size: 11px;
    font-family: var(--font-mono);
    color: var(--color-text-dim);
    background: transparent;
    border: none;
    padding: 0 2px;
    height: 16px;
    line-height: 16px;
    cursor: pointer;
    opacity: 0;
    flex-shrink: 0;
    transition: opacity var(--duration-micro) var(--ease-spring);
  }

  .strat-row:hover .strat-edit {
    opacity: 1;
  }

  .strat-edit:hover {
    color: var(--tier-accent, var(--color-neon-cyan));
  }

  .strategy-editor {
    display: flex;
    flex-direction: column;
    gap: 4px;
    padding: 4px 6px 6px;
    border: 1px solid var(--color-border-subtle);
    border-top: none;
    background: var(--color-bg-card);
  }

  .strategy-file-path {
    font-family: var(--font-mono);
    font-size: 9px;
    color: var(--color-text-dim);
    padding: 2px 0;
  }

  .strategy-textarea {
    width: 100%;
    min-height: 200px;
    max-height: 400px;
    resize: vertical;
    font-family: var(--font-mono);
    font-size: 10px;
    line-height: 1.5;
    background: var(--color-bg-input);
    border: 1px solid var(--color-border-subtle);
    color: var(--color-text-primary);
    padding: 6px;
    tab-size: 2;
  }

  .strategy-textarea:focus {
    border-color: rgba(var(--tier-accent-rgb, 0, 229, 255), 0.3);
    outline: none;
  }

  .strategy-editor-actions {
    display: flex;
    align-items: center;
    gap: 4px;
  }
</style>
