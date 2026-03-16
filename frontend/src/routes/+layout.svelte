<script lang="ts">
  import '../app.css';
  import favicon from '$lib/assets/favicon.svg';

  let { children } = $props();
</script>

<svelte:head>
  <link rel="icon" href={favicon} />
  <title>Project Synthesis</title>
</svelte:head>

<div class="workbench">
  <div class="activity-bar">
    <!-- ActivityBar component will go here -->
    <div class="h-full" style="background: var(--color-bg-secondary); border-right: 1px solid var(--color-border-subtle);">
      <div class="p-2 text-[10px] text-center" style="color: var(--color-text-dim);">ACT</div>
    </div>
  </div>
  <div class="navigator">
    <!-- Navigator component will go here -->
    <div class="h-full" style="background: var(--color-bg-secondary); border-right: 1px solid var(--color-border-subtle);">
      <div class="p-2 text-[10px]" style="color: var(--color-text-dim);">NAV</div>
    </div>
  </div>
  <div class="editor-area">
    {@render children()}
  </div>
  <div class="inspector">
    <!-- Inspector component will go here -->
    <div class="h-full" style="background: var(--color-bg-secondary); border-left: 1px solid var(--color-border-subtle);">
      <div class="p-2 text-[10px]" style="color: var(--color-text-dim);">INSPECT</div>
    </div>
  </div>
  <div class="status-bar">
    <div class="h-full flex items-center px-2 text-[10px]" style="background: var(--color-bg-secondary); border-top: 1px solid var(--color-border-subtle); color: var(--color-text-dim);">
      Project Synthesis
    </div>
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
