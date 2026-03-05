<script lang="ts">
  import { editor } from '$lib/stores/editor.svelte';
  import PromptDocument from '$lib/components/editor/PromptDocument.svelte';
  import ForgeArtifact from '$lib/components/editor/ForgeArtifact.svelte';
  import ChainComposer from '$lib/components/editor/ChainComposer.svelte';

  function handleNewTab() {
    const id = `prompt-${Date.now()}`;
    editor.openTab({
      id,
      label: 'New Prompt',
      type: 'prompt',
      promptText: '',
      dirty: false
    });
  }

  function handleTabKeydown(e: KeyboardEvent, tabIndex: number) {
    const tabs = editor.openTabs;
    if (!tabs.length) return;

    let newIndex = tabIndex;
    if (e.key === 'ArrowRight' || e.key === 'ArrowDown') {
      e.preventDefault();
      newIndex = (tabIndex + 1) % tabs.length;
    } else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') {
      e.preventDefault();
      newIndex = (tabIndex - 1 + tabs.length) % tabs.length;
    } else if (e.key === 'Home') {
      e.preventDefault();
      newIndex = 0;
    } else if (e.key === 'End') {
      e.preventDefault();
      newIndex = tabs.length - 1;
    } else {
      return;
    }
    editor.activeTabId = tabs[newIndex].id;
    // Focus the newly active tab element
    const tabEl = document.querySelector(`[data-tab-id="${tabs[newIndex].id}"]`) as HTMLElement;
    tabEl?.focus();
  }
</script>

<main class="flex flex-col h-full overflow-hidden bg-bg-primary" aria-label="Editor">
  <!-- Tab bar -->
  <div class="h-9 flex items-center border-b border-border-subtle bg-bg-secondary shrink-0 overflow-x-auto" role="tablist" aria-label="Open documents">
    {#each editor.openTabs as tab, i (tab.id)}
      <div
        class="flex items-center gap-1.5 px-3 h-full text-xs border-r border-border-subtle transition-colors whitespace-nowrap cursor-pointer select-none
          {editor.activeTabId === tab.id
            ? 'bg-bg-primary text-text-primary border-b-2 border-b-neon-cyan'
            : 'text-text-dim hover:text-text-secondary hover:bg-bg-hover'}"
        role="tab"
        aria-selected={editor.activeTabId === tab.id}
        tabindex={editor.activeTabId === tab.id ? 0 : -1}
        data-tab-id={tab.id}
        onclick={() => { editor.activeTabId = tab.id; }}
        onkeydown={(e: KeyboardEvent) => handleTabKeydown(e, i)}
      >
        {#if tab.dirty}
          <span class="w-1.5 h-1.5 rounded-full bg-neon-yellow"></span>
        {/if}
        <span>{tab.label}</span>
        <button
          class="ml-1 w-4 h-4 flex items-center justify-center rounded hover:bg-bg-hover text-text-dim hover:text-text-secondary"
          onclick={(e: MouseEvent) => { e.stopPropagation(); editor.closeTab(tab.id); }}
          aria-label="Close tab"
          tabindex={-1}
        >
          <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2">
            <path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12"></path>
          </svg>
        </button>
      </div>
    {/each}

    <button
      class="w-8 h-full flex items-center justify-center text-text-dim hover:text-text-secondary hover:bg-bg-hover"
      onclick={handleNewTab}
      aria-label="New tab"
    >
      <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2">
        <path stroke-linecap="round" stroke-linejoin="round" d="M12 4v16m8-8H4"></path>
      </svg>
    </button>
  </div>

  <!-- Document area -->
  <div class="flex-1 min-h-0 overflow-y-auto" role="tabpanel" aria-label={editor.activeTab ? editor.activeTab.label : 'No document open'}>
    {#if editor.activeTab}
      {#if editor.activeTab.type === 'prompt'}
        <PromptDocument tab={editor.activeTab} />
      {:else if editor.activeTab.type === 'artifact'}
        <ForgeArtifact />
      {:else if editor.activeTab.type === 'chain'}
        <ChainComposer />
      {/if}
    {:else}
      <!-- Welcome screen -->
      <div class="flex flex-col items-center justify-center h-full text-center p-8 animate-fade-in">
        <div class="text-4xl font-bold bg-clip-text text-transparent mb-4" style="background-image: var(--gradient-forge)">
          PromptForge
        </div>
        <p class="text-text-secondary text-sm mb-6 max-w-md">
          AI-powered prompt optimization workbench. Write your prompt and forge it into perfection.
        </p>
        <button
          class="px-4 py-2 rounded-lg text-sm font-medium bg-bg-card border border-border-subtle text-text-primary hover:bg-bg-hover hover:border-neon-cyan/20 transition-all"
          onclick={handleNewTab}
        >
          New Prompt
        </button>
        <p class="mt-4 text-xs text-text-dim">
          Press <kbd class="px-1.5 py-0.5 bg-bg-card rounded border border-border-subtle text-text-secondary">Ctrl+K</kbd> for command palette
        </p>
      </div>
    {/if}
  </div>
</main>
