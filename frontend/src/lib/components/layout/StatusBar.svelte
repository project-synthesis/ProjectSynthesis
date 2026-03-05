<script lang="ts">
  import { workbench } from '$lib/stores/workbench.svelte';
  import { editor } from '$lib/stores/editor.svelte';
  import { forge } from '$lib/stores/forge.svelte';
  import { github } from '$lib/stores/github.svelte';
  import ProviderBadge from '$lib/components/shared/ProviderBadge.svelte';
</script>

<footer
  class="h-[24px] flex items-center justify-between px-2 bg-bg-secondary border-t border-border-subtle text-[10px] select-none shrink-0"
  aria-label="Status Bar"
>
  <div class="flex items-center gap-3">
    <!-- Connection status -->
    <div class="flex items-center gap-1">
      <span class="w-1.5 h-1.5 rounded-full {workbench.isConnected ? 'bg-neon-green' : 'bg-neon-red'}"></span>
      <span class="text-text-dim">{workbench.isConnected ? 'Connected' : 'Disconnected'}</span>
    </div>

    <!-- Provider info -->
    {#if workbench.provider !== 'unknown'}
      <ProviderBadge provider={workbench.provider} />
    {/if}

    <!-- Linked repo -->
    <div class="flex items-center gap-1 text-text-dim" title="Linked repository" data-testid="repo-badge">
      <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="1.5">
        <path stroke-linecap="round" stroke-linejoin="round" d="M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z"></path>
      </svg>
      {#if github.selectedRepo}
        <span class="text-neon-purple">⬡ {github.selectedRepo}</span>
      {:else}
        <span class="text-text-dim/50">No repo</span>
      {/if}
    </div>

    <!-- Strategy chip (always visible, clickable to open strategy picker in Edit tab) -->
    <button
      class="flex items-center gap-1 text-neon-purple hover:text-neon-purple/80 transition-colors cursor-pointer capitalize"
      onclick={() => { workbench.inspectorCollapsed = false; editor.activeSubTab = 'edit'; }}
      title="Strategy — click to open picker"
      data-testid="statusbar-strategy"
    >
      {#if forge.stageResults?.strategy?.data?.primary_framework}
        {forge.stageResults.strategy.data.primary_framework}
      {:else}
        auto
      {/if}
    </button>

    <!-- Forge status -->
    {#if forge.isForging}
      <div class="flex items-center gap-1 text-neon-cyan">
        <span class="animate-status-pulse">Forging</span>
        <span class="capitalize">{forge.currentStage || '...'}</span>
      </div>
    {:else if forge.overallScore != null}
      <button
        class="flex items-center gap-1 text-neon-green hover:text-neon-green/80 transition-colors cursor-pointer"
        onclick={() => { workbench.inspectorCollapsed = false; }}
        title="Score — click to show breakdown in Inspector"
        data-testid="statusbar-score"
      >
        <span>Score: {forge.overallScore}/10</span>
      </button>
    {/if}
  </div>

  <div class="flex items-center gap-3">
    <!-- Ctrl+K shortcut hint -->
    <button
      class="flex items-center gap-1 text-text-dim hover:text-text-secondary transition-colors cursor-pointer"
      onclick={() => { document.dispatchEvent(new KeyboardEvent('keydown', { key: 'k', ctrlKey: true })); }}
      title="Open Command Palette"
    >
      <kbd class="px-1 py-0.5 bg-bg-card rounded border border-border-subtle text-[9px] text-text-secondary">Ctrl+K</kbd>
    </button>

    <!-- Active tab info -->
    {#if editor.activeTab}
      <span class="text-text-dim">{editor.activeTab.label}</span>
    {/if}

    <!-- Tab count -->
    <span class="text-text-dim">{editor.openTabs.length} tab{editor.openTabs.length !== 1 ? 's' : ''}</span>

    <!-- Model -->
    {#if workbench.providerModel}
      <span class="text-text-dim font-mono">{workbench.providerModel}</span>
    {/if}
  </div>
</footer>
