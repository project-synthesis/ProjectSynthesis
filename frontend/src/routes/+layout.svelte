<script lang="ts">
  import '../app.css';
  import { onMount } from 'svelte';
  import { workbench } from '$lib/stores/workbench.svelte';
  import { editor } from '$lib/stores/editor.svelte';
  import { fetchHealth } from '$lib/api/client';
  import ActivityBar from '$lib/components/layout/ActivityBar.svelte';
  import Navigator from '$lib/components/layout/Navigator.svelte';
  import EditorGroups from '$lib/components/layout/EditorGroups.svelte';
  import Inspector from '$lib/components/layout/Inspector.svelte';
  import StatusBar from '$lib/components/layout/StatusBar.svelte';
  import CommandPalette from '$lib/components/shared/CommandPalette.svelte';

  import type { Snippet } from 'svelte';
  let { children }: { children: Snippet } = $props();

  onMount(() => {
    // Detect provider on mount
    fetchHealth()
      .then((data) => {
        workbench.isConnected = true;
        workbench.provider = (data.provider as 'anthropic' | 'openai') || 'unknown';
        workbench.providerModel = data.model_routing?.optimize || '';
      })
      .catch(() => {
        workbench.isConnected = false;
      });

    // Ensure at least one tab is open
    editor.ensureWelcomeTab();
  });
</script>

<div
  class="h-screen w-screen overflow-hidden grid"
  style="
    grid-template-columns: 40px {workbench.navCssWidth} 1fr {workbench.inspectorCssWidth};
    grid-template-rows: 1fr 24px;
    transition: grid-template-columns 0.2s ease;
  "
>
  <!-- Row 1: Activity Bar -->
  <div class="row-span-1" style="grid-row: 1; grid-column: 1;">
    <ActivityBar />
  </div>

  <!-- Row 1: Navigator -->
  <div class="row-span-1 overflow-hidden" style="grid-row: 1; grid-column: 2;">
    <Navigator />
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
  <div class="row-span-1 overflow-hidden" style="grid-row: 1; grid-column: 4;">
    <Inspector />
  </div>

  <!-- Row 2: Status Bar spans full width -->
  <div style="grid-row: 2; grid-column: 1 / -1;">
    <StatusBar />
  </div>
</div>

<!-- Global overlays -->
<CommandPalette />
