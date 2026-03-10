<script lang="ts">
  import { workbench } from '$lib/stores/workbench.svelte';
  import { editor } from '$lib/stores/editor.svelte';
  import { forge } from '$lib/stores/forge.svelte';
  import { github } from '$lib/stores/github.svelte';
  import { auth } from '$lib/stores/auth.svelte';
  import { getStrategyHex } from '$lib/utils/strategy';
  import ProviderBadge from '$lib/components/shared/ProviderBadge.svelte';
  import { commandPalette } from '$lib/stores/commandPalette.svelte';
  import { user } from '$lib/stores/user.svelte';
  import { history } from '$lib/stores/history.svelte';

  let setupSteps = $derived(
    (workbench.isConnected ? 1 : 0) +
    (workbench.mcpConnected ? 1 : 0) +
    (github.isConnected ? 1 : 0) +
    (github.selectedRepo ? 1 : 0) +
    (history.totalCount > 0 ? 1 : 0)
  );

  function openWelcomeTab() {
    editor.openTab({ id: 'welcome', label: 'Welcome', type: 'prompt', promptText: '', dirty: false });
  }
</script>

<footer
  class="h-[24px] flex items-center justify-between bg-bg-secondary border-t border-border-subtle text-[10px] font-mono select-none shrink-0 overflow-hidden"
  aria-label="Status Bar"
>
  <!-- Left group -->
  <div class="flex items-center h-full">
    <!-- Provider info -->
    <button
      class="flex items-center gap-1 h-full px-2 hover:bg-bg-hover transition-colors cursor-pointer"
      onclick={() => workbench.setActivity('settings')}
      title="{workbench.isConnected ? 'Backend API: connected · Provider: ' + workbench.provider : 'Backend API: unreachable'} — click to open settings"
      data-testid="statusbar-provider"
    >
      <ProviderBadge provider={workbench.provider} isConnected={workbench.isConnected} />
    </button>

    <span class="h-3 w-px bg-border-subtle/50 shrink-0"></span>

    <!-- MCP dot -->
    <div
      class="flex items-center gap-1 h-full px-2"
      title="MCP server (port 8001) — {workbench.mcpConnected ? 'online' : 'offline'}"
      data-testid="statusbar-mcp"
    >
      <span class="w-1.5 h-1.5 rounded-full shrink-0 {workbench.mcpConnected ? 'bg-neon-cyan' : 'bg-neon-red/70'}"></span>
      {#if !workbench.mcpConnected}
        <span class="text-text-dim">MCP offline</span>
      {/if}
    </div>

    <span class="h-3 w-px bg-border-subtle/50 shrink-0"></span>

    <!-- Redis cache dot -->
    <div
      class="flex items-center gap-1 h-full px-2"
      title="Redis cache — {workbench.redisConnected ? 'connected' : 'offline (in-memory fallback)'}"
      data-testid="statusbar-redis"
    >
      <span class="w-1.5 h-1.5 rounded-full shrink-0 {workbench.redisConnected ? 'bg-neon-green' : 'bg-neon-yellow/50'}"></span>
      {#if !workbench.redisConnected}
        <span class="text-text-dim">Cache: mem</span>
      {/if}
    </div>

    <span class="h-3 w-px bg-border-subtle/50 shrink-0"></span>

    <!-- Linked repo -->
    <button
      class="flex items-center gap-1 h-full px-2 text-text-dim hover:bg-bg-hover transition-colors cursor-pointer max-w-[220px]"
      onclick={() => workbench.setActivity('github')}
      title={github.selectedRepo ? `${github.selectedRepo}${github.selectedBranch ? ' @ ' + github.selectedBranch : ''}` : 'No repo linked'}
      data-testid="repo-badge"
    >
      <svg class="w-3 h-3 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="1.5">
        <path stroke-linecap="round" stroke-linejoin="round" d="M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z"></path>
      </svg>
      {#if github.selectedRepo}
        <span class="text-neon-purple truncate">{github.selectedRepo}</span>
        {#if github.selectedBranch}
          <span class="text-neon-purple/60 shrink-0">@ {github.selectedBranch}</span>
        {/if}
      {:else}
        <span class="text-text-dim/50">No repo</span>
      {/if}
    </button>

    <span class="h-3 w-px bg-border-subtle/50 shrink-0"></span>

    <!-- Strategy chip -->
    <button
      class="flex items-center h-full px-2 hover:bg-bg-hover transition-colors cursor-pointer"
      style="color: {getStrategyHex(forge.stageResults?.strategy?.data?.primary_framework as string | undefined)}"
      onclick={() => { workbench.setInspectorCollapsed(false); editor.setSubTab('edit'); }}
      title="Strategy — click to open picker"
      data-testid="statusbar-strategy"
    >
      {((forge.stageResults?.strategy?.data?.primary_framework as string | undefined) ?? 'auto').toUpperCase().replace(/-/g, ' ')}
    </button>

    <span class="h-3 w-px bg-border-subtle/50 shrink-0"></span>

    <!-- Forge status -->
    {#if forge.isForging}
      <button
        class="flex items-center gap-1 h-full px-2 text-neon-cyan hover:bg-bg-hover transition-colors cursor-pointer"
        onclick={() => editor.setSubTab('pipeline')}
        title="Forging — click to open Pipeline sub-tab"
        data-testid="statusbar-forging"
      >
        <span class="animate-status-pulse">Forging</span>
        <span class="capitalize">{forge.currentStage || '...'}</span>
      </button>
    {:else if forge.overallScore != null}
      <button
        class="flex items-center h-full px-2 text-neon-green hover:text-neon-green/80 hover:bg-bg-hover transition-colors cursor-pointer"
        onclick={() => { workbench.setInspectorCollapsed(false); editor.setSubTab('pipeline'); }}
        title="Score — click to show breakdown in Inspector"
        data-testid="statusbar-score"
      >
        {forge.overallScore}/10
      </button>
    {:else if !forge.isForging && user.isNewUser && setupSteps < 5}
      <button
        onclick={openWelcomeTab}
        class="flex items-center gap-1 h-full px-2 text-neon-cyan/60 hover:text-neon-cyan transition-colors cursor-pointer"
        title="Setup progress — click to open guide"
        data-testid="statusbar-setup"
      >
        <span class="w-1.5 h-1.5 bg-neon-cyan animate-status-pulse shrink-0"></span>
        <span class="font-mono text-[10px]">Setup {setupSteps}/5</span>
      </button>
    {:else}
      <span class="px-2 text-text-dim/50" data-testid="forge-hint">Ctrl+Enter to synthesize</span>
    {/if}
  </div>

  <!-- Right group -->
  <div class="flex items-center h-full">
    <!-- Ctrl+K -->
    <button
      class="flex items-center h-full px-2 text-text-dim hover:text-text-secondary hover:bg-bg-hover transition-colors cursor-pointer"
      onclick={() => { commandPalette.open(); }}
      title="Open Command Palette"
    >
      <kbd class="px-1 bg-bg-card border border-border-subtle text-[9px] text-text-secondary" style="padding-top:1px;padding-bottom:1px;">Ctrl+K</kbd>
    </button>

    <span class="h-3 w-px bg-border-subtle/50 shrink-0"></span>

    <!-- Active tab label -->
    {#if editor.activeTab}
      <span class="h-full flex items-center px-2 text-text-dim truncate max-w-[120px]">{editor.activeTab.label}</span>
      <span class="h-3 w-px bg-border-subtle/50 shrink-0"></span>
    {/if}

    <!-- Tab count -->
    <span class="h-full flex items-center px-2 text-text-dim">{editor.openTabs.length} tab{editor.openTabs.length !== 1 ? 's' : ''}</span>

    <span class="h-3 w-px bg-border-subtle/50 shrink-0"></span>

    <!-- Model -->
    {#if workbench.providerModel}
      <span class="h-full flex items-center px-2 text-text-dim">{workbench.providerModel}</span>
      <span class="h-3 w-px bg-border-subtle/50 shrink-0"></span>
    {/if}

    <!-- JWT Auth indicator -->
    <button
      class="flex items-center gap-1 h-full px-2 transition-colors
        {auth.isAuthenticated
          ? 'text-neon-cyan hover:bg-bg-hover'
          : 'text-neon-red/70 hover:bg-bg-hover hover:text-neon-red'}"
      onclick={() => {
        if (auth.isAuthenticated) workbench.setActivity('settings'); else workbench.setActivity('github');
      }}
      title={auth.isAuthenticated
        ? `Authenticated as ${user.label ?? github.username ?? 'JWT'} · JWT active`
        : 'Not authenticated — click to sign in'}
      data-testid="statusbar-auth"
    >
      {#if auth.isAuthenticated}
        {#if user.avatarUrl}
          <img src={user.avatarUrl} class="w-4 h-4 border border-neon-cyan/30 object-cover" alt="" />
        {:else}
          <!-- lock-open icon -->
          <svg class="w-3 h-3 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="1.5">
            <path stroke-linecap="round" stroke-linejoin="round"
              d="M13.5 10.5V6.75a4.5 4.5 0 1 1 9 0v3.75M3.75 21.75h10.5a2.25 2.25 0 0 0 2.25-2.25v-6.75a2.25 2.25 0 0 0-2.25-2.25H3.75a2.25 2.25 0 0 0-2.25 2.25v6.75a2.25 2.25 0 0 0 2.25 2.25Z" />
          </svg>
        {/if}
        <span class="truncate max-w-[80px]">{user.label ?? github.username ?? 'JWT'}</span>
      {:else}
        <!-- lock-closed icon -->
        <svg class="w-3 h-3 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="1.5">
          <path stroke-linecap="round" stroke-linejoin="round"
            d="M16.5 10.5V6.75a4.5 4.5 0 1 0-9 0v3.75m-.75 11.25h10.5a2.25 2.25 0 0 0 2.25-2.25v-6.75a2.25 2.25 0 0 0-2.25-2.25H6.75a2.25 2.25 0 0 0-2.25 2.25v6.75a2.25 2.25 0 0 0 2.25 2.25Z" />
        </svg>
        <span>Sign in</span>
      {/if}
    </button>

    <span class="h-3 w-px bg-border-subtle/50 shrink-0"></span>

    <!-- Inspector toggle -->
    <button
      class="flex items-center justify-center h-full px-2 hover:bg-bg-hover transition-colors cursor-pointer {workbench.inspectorCollapsed ? 'text-text-dim' : 'text-neon-cyan'}"
      onclick={() => workbench.toggleInspector()}
      title="Toggle Inspector panel"
      data-testid="statusbar-inspector-toggle"
    >
      <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="1.5">
        <path stroke-linecap="round" stroke-linejoin="round" d="M9 4.5v15m6-15v15M3 8.25h18M3 15.75h18" />
      </svg>
    </button>
  </div>
</footer>
