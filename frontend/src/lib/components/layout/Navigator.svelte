<script lang="ts">
  import { workbench } from '$lib/stores/workbench.svelte';
  import NavigatorFiles from './NavigatorFiles.svelte';
  import NavigatorHistory from './NavigatorHistory.svelte';
  import NavigatorChains from './NavigatorChains.svelte';
  import NavigatorTemplates from './NavigatorTemplates.svelte';
  import NavigatorGitHub from './NavigatorGitHub.svelte';
  import NavigatorSettings from './NavigatorSettings.svelte';

  const titles: Record<string, string> = {
    files: 'Files',
    history: 'History',
    chains: 'Chains',
    templates: 'Templates',
    github: 'GitHub',
    search: 'Search',
    settings: 'Settings'
  };
</script>

<nav
  class="bg-bg-secondary border-r border-border-subtle flex flex-col overflow-hidden transition-all duration-200"
  class:w-0={workbench.navigatorCollapsed}
  class:opacity-0={workbench.navigatorCollapsed}
  style="width: {workbench.navCssWidth}"
  aria-label="Navigator"
>
  {#if !workbench.navigatorCollapsed}
    <div class="h-9 flex items-center px-3 border-b border-border-subtle shrink-0">
      <span class="text-xs font-semibold uppercase tracking-wider text-text-secondary">
        {titles[workbench.activeActivity] || workbench.activeActivity}
      </span>
    </div>

    <div class="flex-1 overflow-y-auto">
      {#if workbench.activeActivity === 'files'}
        <NavigatorFiles />
      {:else if workbench.activeActivity === 'history'}
        <NavigatorHistory />
      {:else if workbench.activeActivity === 'chains'}
        <NavigatorChains />
      {:else if workbench.activeActivity === 'templates'}
        <NavigatorTemplates />
      {:else if workbench.activeActivity === 'github'}
        <NavigatorGitHub />
      {:else if workbench.activeActivity === 'search'}
        <div class="p-3">
          <input
            type="text"
            placeholder="Search prompts..."
            class="w-full bg-bg-input border border-border-subtle rounded px-2 py-1.5 text-sm text-text-primary placeholder:text-text-dim focus:outline-none focus:border-neon-cyan/30"
          />
        </div>
      {:else if workbench.activeActivity === 'settings'}
        <NavigatorSettings />
      {/if}
    </div>
  {/if}
</nav>
