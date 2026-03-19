<script lang="ts">
  import '$lib/styles/content-animations.css';
  import { onNavigate } from '$app/navigation';

  let { children } = $props();

  onNavigate((navigation) => {
    if (!document.startViewTransition) return;
    return new Promise((resolve) => {
      document.startViewTransition(async () => {
        resolve();
        await navigation.complete;
      });
    });
  });
</script>

<svelte:head>
  <title>Project Synthesis — AI-Powered Prompt Optimization</title>
  <meta name="description" content="Real-time prompt optimization pipeline. Analyze, rewrite, and score prompts with 5-dimension quality metrics." />
  <link href="https://cdn.jsdelivr.net/npm/geist@1/dist/fonts/geist-mono/style.css" rel="stylesheet">
</svelte:head>

<div class="landing-root">
  {@render children()}
</div>

<style>
  .landing-root {
    min-height: 100vh;
    width: 100%;
    max-width: 100vw;
    overflow-y: auto;
    overflow-x: hidden;
    position: relative;
    background: var(--color-bg-primary);
  }

  :global(html),
  :global(body) {
    overflow-x: hidden;
    width: 100%;
    margin: 0;
    padding: 0;
  }
</style>
