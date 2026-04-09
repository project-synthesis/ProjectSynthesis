<script lang="ts">
  import ActivityBar from '$lib/components/layout/ActivityBar.svelte';
  import Navigator from '$lib/components/layout/Navigator.svelte';
  import Inspector from '$lib/components/layout/Inspector.svelte';
  import StatusBar from '$lib/components/layout/StatusBar.svelte';
  import CommandPalette from '$lib/components/shared/CommandPalette.svelte';
  import Toast from '$lib/components/shared/Toast.svelte';
  import PassthroughGuide from '$lib/components/shared/PassthroughGuide.svelte';
  import SamplingGuide from '$lib/components/shared/SamplingGuide.svelte';
  import InternalGuide from '$lib/components/shared/InternalGuide.svelte';
  import { preferencesStore } from '$lib/stores/preferences.svelte';
  import { forgeStore } from '$lib/stores/forge.svelte';
  import { clustersStore } from '$lib/stores/clusters.svelte';
  import { routing } from '$lib/stores/routing.svelte';
  import { githubStore } from '$lib/stores/github.svelte';
  import { addToast } from '$lib/stores/toast.svelte';

  let { children } = $props();

  type Activity = 'editor' | 'history' | 'clusters' | 'github' | 'settings';
  let activeActivity = $state<Activity>('editor');

  $effect(() => {
    preferencesStore.init();
    forgeStore.restoreSession();
    clustersStore.loadTree();

    // Check GitHub auth state on mount
    githubStore.checkAuth();

    // Handle OAuth callback redirect (?github_auth=success)
    const params = new URLSearchParams(window.location.search);
    if (params.get('github_auth') === 'success') {
      // Clean URL without reloading
      const url = new URL(window.location.href);
      url.searchParams.delete('github_auth');
      window.history.replaceState({}, '', url.toString());
      // Switch to GitHub panel and show toast
      activeActivity = 'github';
      addToast('created', 'GitHub connected');
    }
  });

  $effect(() => {
    const handler = (e: Event) => {
      const detail = (e as CustomEvent).detail;
      if (detail === 'editor' || detail === 'history' || detail === 'clusters' || detail === 'github' || detail === 'settings') {
        activeActivity = detail;
      }
    };
    window.addEventListener('switch-activity', handler);
    return () => window.removeEventListener('switch-activity', handler);
  });
</script>

<svelte:head>
  <title>Project Synthesis</title>
</svelte:head>

<div class="workbench" style:--tier-accent={routing.tierColor} style:--tier-accent-rgb={routing.tierColorRgb}>
  <div class="activity-bar">
    <ActivityBar bind:active={activeActivity} />
  </div>
  <div class="navigator">
    <Navigator active={activeActivity} />
  </div>
  <div class="editor-area">
    {@render children()}
  </div>
  <div class="inspector">
    <Inspector />
  </div>
  <div class="status-bar">
    <StatusBar />
  </div>
  <CommandPalette />
  <Toast />
  <PassthroughGuide />
  <SamplingGuide />
  <InternalGuide provider={forgeStore.provider} />
</div>

<style>
  .workbench {
    display: grid;
    grid-template-columns: 36px 240px 1fr 280px;
    grid-template-rows: 1fr 20px;
    height: 100vh;
    width: 100vw;
    overflow: hidden;
  }
  .activity-bar { grid-row: 1 / 2; grid-column: 1; overflow: hidden; }
  .navigator { grid-row: 1 / 2; grid-column: 2; overflow: hidden; }
  .editor-area { grid-row: 1 / 2; grid-column: 3; overflow: hidden; min-width: 0; }
  .inspector { grid-row: 1 / 2; grid-column: 4; overflow: hidden; }
  .status-bar { grid-row: 2; grid-column: 1 / -1; }
</style>
