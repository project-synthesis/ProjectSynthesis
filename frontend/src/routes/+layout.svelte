<script lang="ts">
  import '../app.css';
  import favicon from '$lib/assets/favicon.svg';
  import ActivityBar from '$lib/components/layout/ActivityBar.svelte';
  import Navigator from '$lib/components/layout/Navigator.svelte';
  import Inspector from '$lib/components/layout/Inspector.svelte';
  import StatusBar from '$lib/components/layout/StatusBar.svelte';

  let { children } = $props();

  type Activity = 'editor' | 'history' | 'github' | 'settings';
  let activeActivity = $state<Activity>('editor');
</script>

<svelte:head>
  <link rel="icon" href={favicon} />
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
</div>

<style>
  .workbench {
    display: grid;
    grid-template-columns: 48px 240px 1fr 280px;
    grid-template-rows: 1fr 24px;
    height: 100vh;
    width: 100vw;
    overflow: hidden;
  }
  .activity-bar { grid-row: 1 / 2; grid-column: 1; }
  .navigator { grid-row: 1 / 2; grid-column: 2; }
  .editor-area { grid-row: 1 / 2; grid-column: 3; }
  .inspector { grid-row: 1 / 2; grid-column: 4; }
  .status-bar { grid-row: 2; grid-column: 1 / -1; }
</style>
