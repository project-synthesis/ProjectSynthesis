<script lang="ts">
  import '../app.css';
  import { onMount } from 'svelte';
  import { workbench } from '$lib/stores/workbench.svelte';
  import { editor } from '$lib/stores/editor.svelte';
  import { github } from '$lib/stores/github.svelte';
  import { fetchHealth, fetchGitHubAuthStatus, fetchGitHubRepos, fetchLinkedRepo } from '$lib/api/client';
  import ActivityBar from '$lib/components/layout/ActivityBar.svelte';
  import Navigator from '$lib/components/layout/Navigator.svelte';
  import EditorGroups from '$lib/components/layout/EditorGroups.svelte';
  import Inspector from '$lib/components/layout/Inspector.svelte';
  import StatusBar from '$lib/components/layout/StatusBar.svelte';
  import CommandPalette from '$lib/components/shared/CommandPalette.svelte';
  import ToastContainer from '$lib/components/shared/ToastContainer.svelte';

  import type { Snippet } from 'svelte';
  let { children }: { children: Snippet } = $props();

  // Resize handle logic
  let resizing = $state<'nav' | 'inspector' | null>(null);
  let startX = 0;
  let startWidth = 0;

  function startNavResize(e: MouseEvent) {
    if (workbench.navigatorCollapsed) return;
    resizing = 'nav';
    startX = e.clientX;
    startWidth = workbench.navigatorWidth;
    e.preventDefault();
  }

  function startInspectorResize(e: MouseEvent) {
    if (workbench.inspectorCollapsed) return;
    resizing = 'inspector';
    startX = e.clientX;
    startWidth = workbench.inspectorWidth;
    e.preventDefault();
  }

  function handleMouseMove(e: MouseEvent) {
    if (!resizing) return;
    if (resizing === 'nav') {
      const delta = e.clientX - startX;
      workbench.setNavigatorWidth(startWidth + delta);
    } else if (resizing === 'inspector') {
      const delta = startX - e.clientX;
      workbench.setInspectorWidth(startWidth + delta);
    }
  }

  function handleMouseUp() {
    resizing = null;
  }

  onMount(() => {
    // Detect provider on mount
    fetchHealth()
      .then((data) => {
        workbench.isConnected = true;
        workbench.provider = (data.provider as 'anthropic' | 'openai' | 'claude_cli' | 'anthropic_api') || 'unknown';
        workbench.providerModel = data.model_routing?.optimize || '';
      })
      .catch(() => {
        workbench.isConnected = false;
      });

    // Hydrate GitHub connection state (persists across page refresh)
    fetchGitHubAuthStatus()
      .then(async (auth) => {
        if (auth.connected && auth.login) {
          try {
            const repos = await fetchGitHubRepos();
            github.setConnected(
              auth.login,
              repos.map((r: Record<string, unknown>) => ({
                full_name: r.full_name as string,
                description: (r.description || '') as string,
                default_branch: (r.default_branch || 'main') as string,
                private: !!r.private
              }))
            );
            // Restore linked repo selection
            const linked = await fetchLinkedRepo();
            if (linked && linked.full_name) {
              github.selectRepo(linked.full_name);
            }
          } catch {
            // Repos fetch failed — mark connected but without repos
            github.setConnected(auth.login, []);
          }
        }
      })
      .catch(() => {
        // Not connected or auth check failed — leave github store in default state
      });

    // Ensure at least one tab is open
    editor.ensureWelcomeTab();
  });
</script>

<!-- svelte-ignore a11y_no_static_element_interactions -->
<div
  class="h-screen w-screen overflow-hidden grid"
  style="
    grid-template-columns: 40px {workbench.navCssWidth} 1fr {workbench.inspectorCssWidth};
    grid-template-rows: 1fr 24px;
    {resizing ? '' : 'transition: grid-template-columns 0.2s ease;'}
  "
  onmousemove={handleMouseMove}
  onmouseup={handleMouseUp}
  onmouseleave={handleMouseUp}
>
  <!-- Row 1: Activity Bar -->
  <div class="row-span-1" style="grid-row: 1; grid-column: 1;">
    <ActivityBar />
  </div>

  <!-- Row 1: Navigator -->
  <div class="row-span-1 overflow-hidden relative" style="grid-row: 1; grid-column: 2;">
    <Navigator />
    <!-- Navigator resize handle (right edge) -->
    {#if !workbench.navigatorCollapsed}
      <!-- svelte-ignore a11y_no_static_element_interactions -->
      <div
        class="absolute top-0 right-0 w-1 h-full cursor-col-resize hover:bg-neon-cyan/30 transition-colors z-20
          {resizing === 'nav' ? 'bg-neon-cyan/40' : ''}"
        data-testid="nav-resize-handle"
        onmousedown={startNavResize}
      ></div>
    {/if}
  </div>

  <!-- Row 1: Editor (main) -->
  <div class="row-span-1 overflow-hidden" style="grid-row: 1; grid-column: 3;">
    <EditorGroups />
    <!-- Page slot (empty for workbench since layout handles everything) -->
    <div class="hidden">
      {@render children()}
    </div>
  </div>

  <!-- Row 1: Inspector -->
  <div class="row-span-1 overflow-hidden relative" style="grid-row: 1; grid-column: 4;">
    <!-- Inspector resize handle (left edge) -->
    {#if !workbench.inspectorCollapsed}
      <!-- svelte-ignore a11y_no_static_element_interactions -->
      <div
        class="absolute top-0 left-0 w-1 h-full cursor-col-resize hover:bg-neon-cyan/30 transition-colors z-20
          {resizing === 'inspector' ? 'bg-neon-cyan/40' : ''}"
        data-testid="inspector-resize-handle"
        onmousedown={startInspectorResize}
      ></div>
    {/if}
    <Inspector />
  </div>

  <!-- Row 2: Status Bar spans full width -->
  <div style="grid-row: 2; grid-column: 1 / -1;">
    <StatusBar />
  </div>
</div>

<!-- Global overlays -->
<CommandPalette />
<ToastContainer />
