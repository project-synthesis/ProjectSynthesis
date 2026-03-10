<script lang="ts">
  import { editor } from '$lib/stores/editor.svelte';
  import PromptDocument from '$lib/components/editor/PromptDocument.svelte';
  import ForgeArtifact from '$lib/components/editor/ForgeArtifact.svelte';
  import ChainComposer from '$lib/components/editor/ChainComposer.svelte';
  import WelcomeTab from '$lib/components/editor/WelcomeTab.svelte';
  import StrategyExplainer from '$lib/components/editor/StrategyExplainer.svelte';
  import { workbench } from '$lib/stores/workbench.svelte';

  let tabBarEl = $state<HTMLElement | null>(null);
  let overflowCount = $state(0);
  let showOverflowMenu = $state(false);

  // ResizeObserver to detect tab overflow
  $effect(() => {
    if (!tabBarEl) return;
    const obs = new ResizeObserver(() => {
      if (!tabBarEl) return;
      // Subtract the width of the + button (32px) and overflow button area (32px)
      const available = tabBarEl.clientWidth - 64;
      let used = 0;
      let overflow = 0;
      for (const tab of tabBarEl.querySelectorAll('[role="tab"]')) {
        used += (tab as HTMLElement).offsetWidth;
        if (used > available) overflow++;
      }
      overflowCount = overflow;
    });
    obs.observe(tabBarEl);
    return () => obs.disconnect();
  });

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
  <div bind:this={tabBarEl} class="h-9 flex items-center border-b border-border-subtle bg-bg-secondary shrink-0 overflow-hidden relative" role="tablist" aria-label="Open documents">
    {#each editor.openTabs as tab, i (tab.id)}
      <div
        class="flex items-center gap-1.5 px-3 h-full text-xs border-r border-border-subtle transition-colors whitespace-nowrap cursor-pointer select-none
          {editor.activeTabId === tab.id
            ? 'bg-bg-primary text-text-primary border-b border-b-neon-cyan'
            : 'text-text-dim hover:text-text-secondary hover:bg-bg-hover'}"
        role="tab"
        aria-selected={editor.activeTabId === tab.id}
        tabindex={editor.activeTabId === tab.id ? 0 : -1}
        data-tab-id={tab.id}
        onclick={() => { editor.activeTabId = tab.id; }}
        onkeydown={(e: KeyboardEvent) => handleTabKeydown(e, i)}
      >
        <!-- Tab type icon -->
        {#if tab.type === 'prompt'}
          <svg class="w-3 h-3 shrink-0 {editor.activeTabId === tab.id ? 'opacity-100' : 'opacity-50'}" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="1.5">
            <path stroke-linecap="round" stroke-linejoin="round" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"></path>
          </svg>
        {:else if tab.type === 'artifact'}
          <svg class="w-3 h-3 shrink-0 {editor.activeTabId === tab.id ? 'opacity-100' : 'opacity-50'}" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="1.5">
            <path stroke-linecap="round" stroke-linejoin="round" d="M11.049 2.927c.3-.921 1.603-.921 1.902 0l1.519 4.674a1 1 0 00.95.69h4.915c.969 0 1.371 1.24.588 1.81l-3.976 2.888a1 1 0 00-.363 1.118l1.518 4.674c.3.922-.755 1.688-1.538 1.118l-3.976-2.888a1 1 0 00-1.176 0l-3.976 2.888c-.783.57-1.838-.197-1.538-1.118l1.518-4.674a1 1 0 00-.363-1.118l-3.976-2.888c-.784-.57-.38-1.81.588-1.81h4.914a1 1 0 00.951-.69l1.519-4.674z"></path>
          </svg>
        {:else if tab.type === 'chain'}
          <svg class="w-3 h-3 shrink-0 {editor.activeTabId === tab.id ? 'opacity-100' : 'opacity-50'}" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="1.5">
            <path stroke-linecap="round" stroke-linejoin="round" d="M13.828 10.172a4 4 0 00-5.656 0l-4 4a4 4 0 105.656 5.656l1.102-1.101m-.758-4.899a4 4 0 005.656 0l4-4a4 4 0 00-5.656-5.656l-1.1 1.1"></path>
          </svg>
        {/if}
        {#if tab.dirty}
          <span class="w-1.5 h-1.5 rounded-full bg-neon-yellow" title="Unsaved changes"></span>
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

    {#if overflowCount > 0}
      <div class="relative shrink-0">
        <button
          class="w-8 h-full flex items-center justify-center text-text-dim hover:text-neon-cyan hover:bg-bg-hover"
          onclick={() => showOverflowMenu = !showOverflowMenu}
          aria-label="{overflowCount} more tabs"
          title="{overflowCount} more tabs"
        >
          <span class="text-[10px] font-mono">›{overflowCount}</span>
        </button>
        {#if showOverflowMenu}
          <div
            class="absolute top-full right-0 mt-0.5 min-w-40 bg-bg-card border border-border-subtle z-[200] py-1 animate-dropdown-enter"
            role="menu"
            tabindex="-1"
            onmouseleave={() => showOverflowMenu = false}
          >
            {#each editor.openTabs.slice(editor.openTabs.length - overflowCount) as tab}
              <button
                class="w-full flex items-center gap-2 px-3 py-1.5 text-xs text-text-secondary hover:bg-bg-hover hover:text-text-primary"
                role="menuitem"
                onclick={() => { editor.activeTabId = tab.id; showOverflowMenu = false; }}
              >
                {tab.label}
              </button>
            {/each}
          </div>
        {/if}
      </div>
    {/if}

    <button
      class="w-8 h-full flex items-center justify-center text-text-dim hover:text-text-secondary hover:bg-bg-hover shrink-0"
      onclick={handleNewTab}
      aria-label="New tab"
    >
      <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2">
        <path stroke-linecap="round" stroke-linejoin="round" d="M12 4v16m8-8H4"></path>
      </svg>
    </button>
  </div>

  <!-- Document area -->
  <div class="flex-1 min-h-0 overflow-y-auto" data-tour="editor" style="overscroll-behavior: contain;" role="tabpanel" aria-label={editor.activeTab ? editor.activeTab.label : 'No document open'}>
    {#if editor.activeTab}
      {#if editor.activeTab.id === 'welcome' && !editor.activeTab.promptText}
        <WelcomeTab tab={editor.activeTab} />
      {:else if editor.activeTab.type === 'strategy-ref'}
        <StrategyExplainer />
      {:else if editor.activeTab.type === 'prompt'}
        <PromptDocument tab={editor.activeTab} />
      {:else if editor.activeTab.type === 'artifact'}
        <ForgeArtifact />
      {:else if editor.activeTab.type === 'chain'}
        <ChainComposer />
      {/if}
    {:else}
      <!-- Empty state -->
      <div class="flex flex-col items-center justify-center h-full gap-3 animate-fade-in select-none">
        <span class="text-gradient-forge font-display text-5xl font-bold opacity-[0.12] leading-none">PF</span>
        <span class="text-[12px] text-text-dim">No prompt open</span>
        <span class="text-[11px] text-text-dim/60">
          Press <kbd>Ctrl+N</kbd> to create a new prompt
        </span>
        <div class="flex items-center gap-2 mt-1">
          <button class="btn-outline-cyan px-3 py-1.5 text-xs" onclick={handleNewTab}>
            New Prompt
          </button>
          <button
            class="px-3 py-1.5 text-xs border border-border-subtle text-text-dim hover:border-neon-cyan/30 hover:text-text-secondary transition-colors font-mono"
            onclick={() => editor.openTab({ id: 'welcome', label: 'Welcome', type: 'prompt', promptText: '', dirty: false })}
          >Open Welcome Guide</button>
          <button
            class="px-3 py-1.5 text-xs border border-border-subtle text-text-dim hover:border-neon-cyan/30 hover:text-text-secondary transition-colors font-mono"
            onclick={() => workbench.setActivity('templates')}
          >Browse Templates</button>
        </div>
      </div>
    {/if}
  </div>
</main>
