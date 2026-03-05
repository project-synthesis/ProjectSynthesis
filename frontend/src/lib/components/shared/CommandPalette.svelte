<script lang="ts">
  import { commandPalette } from '$lib/stores/commandPalette.svelte';
  import { editor } from '$lib/stores/editor.svelte';
  import { workbench } from '$lib/stores/workbench.svelte';
  import { onMount } from 'svelte';

  let inputEl: HTMLInputElement | undefined = $state();

  // Register default commands
  onMount(() => {
    commandPalette.registerCommands([
      {
        id: 'new-prompt',
        label: 'New Prompt',
        shortcut: 'Ctrl+N',
        category: 'File',
        action: () => {
          editor.openTab({
            id: `prompt-${Date.now()}`,
            label: 'New Prompt',
            type: 'prompt',
            promptText: '',
            dirty: false
          });
        }
      },
      {
        id: 'toggle-navigator',
        label: 'Toggle Navigator',
        shortcut: 'Ctrl+B',
        category: 'View',
        action: () => workbench.toggleNavigator()
      },
      {
        id: 'toggle-inspector',
        label: 'Toggle Inspector',
        shortcut: 'Ctrl+I',
        category: 'View',
        action: () => workbench.toggleInspector()
      },
      {
        id: 'show-history',
        label: 'Show History',
        category: 'View',
        action: () => workbench.setActivity('history')
      },
      {
        id: 'show-github',
        label: 'Show GitHub',
        category: 'View',
        action: () => workbench.setActivity('github')
      },
      {
        id: 'save-prompt',
        label: 'Save Prompt',
        shortcut: 'Ctrl+S',
        category: 'File',
        action: () => editor.saveActiveTab()
      },
      {
        id: 'close-tab',
        label: 'Close Tab',
        shortcut: 'Ctrl+W',
        category: 'File',
        action: () => {
          if (editor.activeTabId) editor.closeTab(editor.activeTabId);
        }
      },
      {
        id: 'forge-prompt',
        label: 'Forge Current Prompt',
        shortcut: 'Ctrl+Enter',
        category: 'Forge',
        action: () => {
          // Trigger forge via the edit tab
          editor.setSubTab('edit');
        }
      }
    ]);

    // Parse shortcut string (e.g. "Ctrl+Shift+B") into a matcher
    function matchesShortcut(e: KeyboardEvent, shortcut: string): boolean {
      const parts = shortcut.split('+');
      const key = parts[parts.length - 1];
      const needsCtrl = parts.includes('Ctrl');
      const needsShift = parts.includes('Shift');
      const needsAlt = parts.includes('Alt');
      const hasCtrl = e.ctrlKey || e.metaKey;
      if (needsCtrl !== hasCtrl) return false;
      if (needsShift !== e.shiftKey) return false;
      if (needsAlt !== e.altKey) return false;
      // Match the key (case-insensitive, handle special keys)
      const eventKey = e.key === ' ' ? 'Space' : e.key;
      return eventKey.toLowerCase() === key.toLowerCase() ||
        (key === 'Enter' && e.key === 'Enter') ||
        (key === 'Escape' && e.key === 'Escape');
    }

    // Global keyboard shortcut
    const handleGlobalKeydown = (e: KeyboardEvent) => {
      // Ctrl+K → toggle palette
      if ((e.ctrlKey || e.metaKey) && e.key === 'k') {
        e.preventDefault();
        commandPalette.toggle();
        return;
      }
      // Escape → close palette
      if (e.key === 'Escape' && commandPalette.isOpen) {
        commandPalette.close();
        return;
      }
      // Don't dispatch shortcuts while palette is open (use palette UI instead)
      if (commandPalette.isOpen) return;
      // Match registered command shortcuts
      for (const cmd of commandPalette.commands) {
        if (cmd.shortcut && matchesShortcut(e, cmd.shortcut)) {
          e.preventDefault();
          cmd.action();
          return;
        }
      }
    };

    document.addEventListener('keydown', handleGlobalKeydown);
    return () => document.removeEventListener('keydown', handleGlobalKeydown);
  });

  $effect(() => {
    if (commandPalette.isOpen && inputEl) {
      inputEl.focus();
    }
  });

  function handleKeydown(e: KeyboardEvent) {
    if (e.key === 'ArrowUp') {
      e.preventDefault();
      commandPalette.moveUp();
    } else if (e.key === 'ArrowDown') {
      e.preventDefault();
      commandPalette.moveDown();
    } else if (e.key === 'Enter') {
      e.preventDefault();
      commandPalette.executeSelected();
    }
  }
</script>

{#if commandPalette.isOpen}
  <!-- Backdrop -->
  <div
    class="fixed inset-0 bg-black/50 z-[800]"
    onclick={() => commandPalette.close()}
    role="presentation"
  ></div>

  <!-- Palette -->
  <div class="fixed top-[20%] left-1/2 -translate-x-1/2 w-[480px] max-w-[90vw] bg-bg-card border border-border-subtle rounded-xl z-[800] overflow-hidden animate-dialog-in">
    <!-- Input -->
    <div class="flex items-center gap-2 px-4 py-3 border-b border-border-subtle">
      <svg class="w-4 h-4 text-text-dim shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2">
        <path stroke-linecap="round" stroke-linejoin="round" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"></path>
      </svg>
      <input
        bind:this={inputEl}
        type="text"
        placeholder="Type a command..."
        class="flex-1 bg-transparent text-sm text-text-primary placeholder:text-text-dim focus:outline-none"
        value={commandPalette.query}
        oninput={(e) => commandPalette.setQuery((e.target as HTMLInputElement).value)}
        onkeydown={handleKeydown}
      />
      <kbd class="text-[10px] px-1.5 py-0.5 bg-bg-secondary rounded border border-border-subtle text-text-dim">ESC</kbd>
    </div>

    <!-- Results -->
    <div class="max-h-[300px] overflow-y-auto py-1">
      {#each commandPalette.filteredCommands as cmd, i (cmd.id)}
        <button
          class="w-full flex items-center justify-between px-4 h-[40px] text-[13px] transition-colors
            {i === commandPalette.selectedIndex
              ? 'bg-bg-hover text-text-primary'
              : 'text-text-secondary hover:bg-bg-hover/50'}"
          onclick={() => { commandPalette.selectedIndex = i; commandPalette.executeSelected(); }}
        >
          <div class="flex items-center gap-2">
            <span class="text-[10px] text-text-dim px-1 py-0.5 rounded bg-bg-secondary">{cmd.category}</span>
            <span>{cmd.label}</span>
          </div>
          {#if cmd.shortcut}
            <kbd class="text-[10px] font-mono px-1.5 py-0.5 bg-bg-secondary rounded border border-border-subtle text-text-dim">{cmd.shortcut}</kbd>
          {/if}
        </button>
      {/each}

      {#if commandPalette.filteredCommands.length === 0}
        <div class="px-4 py-6 text-center text-sm text-text-dim">No commands found.</div>
      {/if}
    </div>
  </div>
{/if}
