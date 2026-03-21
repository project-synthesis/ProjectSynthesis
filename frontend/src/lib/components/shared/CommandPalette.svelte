<script lang="ts">
  import { forgeStore } from '$lib/stores/forge.svelte';
  import { editorStore } from '$lib/stores/editor.svelte';
  import { copyToClipboard } from '$lib/utils/formatting';

  // Actions available in the palette
  interface PaletteAction {
    id: string;
    label: string;
    shortcut?: string;
    run: () => void;
  }

  let open = $state(false);
  let query = $state('');
  let selectedIndex = $state(0);
  let inputEl = $state<HTMLInputElement | null>(null);
  let forgeCheckInterval = $state<ReturnType<typeof setInterval> | null>(null);

  // Lifecycle-safe cleanup: clear polling interval if component unmounts mid-forge
  $effect(() => {
    return () => {
      if (forgeCheckInterval) {
        clearInterval(forgeCheckInterval);
      }
    };
  });

  const allActions: PaletteAction[] = [
    {
      id: 'new-prompt',
      label: 'New Prompt',
      run: () => {
        forgeStore.reset();
        window.dispatchEvent(new CustomEvent('switch-activity', { detail: 'editor' }));
        close();
      },
    },
    {
      id: 'forge',
      label: 'Forge',
      run: () => {
        forgeStore.forge();
        // Watch for traceId to open result tab (lifecycle-safe via $state)
        forgeCheckInterval = setInterval(() => {
          if (forgeStore.traceId) {
            clearInterval(forgeCheckInterval!);
            forgeCheckInterval = null;
            editorStore.openResult(forgeStore.traceId);
          }
          if (forgeStore.status === 'error' || forgeStore.status === 'idle') {
            clearInterval(forgeCheckInterval!);
            forgeCheckInterval = null;
          }
        }, 200);
        window.dispatchEvent(new CustomEvent('switch-activity', { detail: 'editor' }));
        close();
      },
    },
    {
      id: 'view-history',
      label: 'View History',
      run: () => {
        window.dispatchEvent(new CustomEvent('switch-activity', { detail: 'history' }));
        close();
      },
    },
    {
      id: 'link-repo',
      label: 'Link Repo',
      run: () => {
        window.dispatchEvent(new CustomEvent('switch-activity', { detail: 'github' }));
        close();
      },
    },
    {
      id: 'toggle-diff',
      label: 'Toggle Diff',
      run: () => {
        const id = forgeStore.result?.id ?? 'current';
        editorStore.openDiff(id);
        close();
      },
    },
    {
      id: 'copy-result',
      label: 'Copy Result',
      run: () => {
        const text = forgeStore.result?.optimized_prompt;
        if (text) copyToClipboard(text);
        close();
      },
    },
  ];

  const filtered = $derived(
    query.trim()
      ? allActions.filter((a) =>
          a.label.toLowerCase().includes(query.trim().toLowerCase()),
        )
      : allActions,
  );

  function openPalette() {
    open = true;
    query = '';
    selectedIndex = 0;
    // Focus input on next tick
    setTimeout(() => inputEl?.focus(), 0);
  }

  function close() {
    open = false;
    query = '';
    selectedIndex = 0;
  }

  function select(action: PaletteAction) {
    action.run();
  }

  function handleKeydown(e: KeyboardEvent) {
    if (e.ctrlKey && e.key === 'k') {
      e.preventDefault();
      if (open) {
        close();
      } else {
        openPalette();
      }
      return;
    }
    if (!open) return;
    if (e.key === 'Escape') {
      e.preventDefault();
      close();
    } else if (e.key === 'ArrowDown') {
      e.preventDefault();
      selectedIndex = Math.min(selectedIndex + 1, filtered.length - 1);
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      selectedIndex = Math.max(selectedIndex - 1, 0);
    } else if (e.key === 'Enter') {
      e.preventDefault();
      const action = filtered[selectedIndex];
      if (action) select(action);
    }
  }

  // Reset selectedIndex when filtered list changes
  $effect(() => {
    // Accessing filtered.length triggers reactivity
    void filtered.length;
    selectedIndex = 0;
  });
</script>

<svelte:window onkeydown={handleKeydown} />

{#if open}
  <!-- Overlay -->
  <div
    class="overlay"
    role="dialog"
    aria-modal="true"
    aria-label="Command palette"
    tabindex="-1"
    onclick={(e) => { if (e.target === e.currentTarget) close(); }}
    onkeydown={(e) => { if (e.key === 'Escape') close(); }}
  >
    <div class="palette">
      <!-- Search input -->
      <div class="search-row">
        <span class="search-icon" aria-hidden="true">&gt;</span>
        <input
          bind:this={inputEl}
          bind:value={query}
          class="search-input"
          type="text"
          placeholder="Search commands..."
          aria-label="Command search"
          autocomplete="off"
          spellcheck={false}
        />
      </div>

      <!-- Action list -->
      <ul class="action-list" role="listbox" aria-label="Commands">
        {#each filtered as action, i (action.id)}
          <li
            class="action-item"
            class:selected={i === selectedIndex}
            role="option"
            aria-selected={i === selectedIndex}
            onmouseenter={() => { selectedIndex = i; }}
            onclick={() => select(action)}
            onkeydown={(e) => { if (e.key === 'Enter') select(action); }}
            tabindex="-1"
          >
            <span class="action-label">{action.label}</span>
            {#if action.shortcut}
              <span class="action-shortcut">{action.shortcut}</span>
            {/if}
          </li>
        {/each}

        {#if filtered.length === 0}
          <li class="no-results">No commands match "{query}"</li>
        {/if}
      </ul>
    </div>
  </div>
{/if}

<style>
  .overlay {
    position: fixed;
    inset: 0;
    background: rgba(6, 6, 12, 0.8);
    display: flex;
    align-items: flex-start;
    justify-content: center;
    padding-top: 120px;
    z-index: 9999;
  }

  .palette {
    width: 480px;
    background: var(--color-bg-card);
    border: 1px solid var(--color-border-subtle);
    overflow: hidden;
  }

  .search-row {
    display: flex;
    align-items: center;
    gap: 6px;
    padding: 0 10px;
    height: 36px;
    border-bottom: 1px solid var(--color-border-subtle);
  }

  .search-icon {
    font-size: 11px;
    font-family: var(--font-mono);
    color: var(--color-neon-cyan);
    flex-shrink: 0;
    user-select: none;
  }

  .search-input {
    flex: 1;
    background: transparent;
    border: none;
    outline: none;
    font-size: 11px;
    font-family: var(--font-mono);
    color: var(--color-text-primary);
    caret-color: var(--color-neon-cyan);
  }

  .search-input::placeholder {
    color: var(--color-text-dim);
  }

  .action-list {
    list-style: none;
    margin: 0;
    padding: 4px 0;
    max-height: 280px;
    overflow-y: auto;
  }

  .action-item {
    display: flex;
    align-items: center;
    justify-content: space-between;
    height: 32px; /* h-8 */
    padding: 0 10px;
    cursor: pointer;
    transition: background 200ms cubic-bezier(0.16, 1, 0.3, 1);
    border-left: 1px solid transparent;
  }

  .action-item:hover,
  .action-item.selected {
    background: var(--color-bg-hover);
    border-left-color: var(--color-neon-cyan);
  }

  .action-label {
    font-size: 11px;
    font-family: var(--font-sans);
    color: var(--color-text-primary);
  }

  .action-shortcut {
    font-size: 10px;
    font-family: var(--font-mono);
    color: var(--color-text-dim);
  }

  .no-results {
    display: flex;
    align-items: center;
    justify-content: center;
    height: 40px;
    font-size: 11px;
    font-family: var(--font-sans);
    color: var(--color-text-dim);
  }
</style>
