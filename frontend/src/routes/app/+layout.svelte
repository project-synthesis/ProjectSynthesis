<script lang="ts">
  import ActivityBar from '$lib/components/layout/ActivityBar.svelte';
  import Navigator from '$lib/components/layout/Navigator.svelte';
  import Inspector from '$lib/components/layout/Inspector.svelte';
  import StatusBar from '$lib/components/layout/StatusBar.svelte';
  import CommandPalette from '$lib/components/shared/CommandPalette.svelte';
  import Toast from '$lib/components/shared/Toast.svelte';
  import { preferencesStore } from '$lib/stores/preferences.svelte';
  import { forgeStore } from '$lib/stores/forge.svelte';

  let { children } = $props();

  type Activity = 'editor' | 'history' | 'patterns' | 'github' | 'settings';
  let activeActivity = $state<Activity>('editor');

  $effect(() => {
    preferencesStore.init();
    forgeStore.restoreSession();
  });

  $effect(() => {
    const handler = (e: Event) => {
      const detail = (e as CustomEvent).detail;
      if (detail === 'editor' || detail === 'history' || detail === 'patterns' || detail === 'github' || detail === 'settings') {
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

<div class="workbench">
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
  .editor-area { grid-row: 1 / 2; grid-column: 3; overflow: hidden; }
  .inspector { grid-row: 1 / 2; grid-column: 4; overflow: hidden; }
  .status-bar { grid-row: 2; grid-column: 1 / -1; }
</style>
