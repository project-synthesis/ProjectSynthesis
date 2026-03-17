<script lang="ts">
  import type { ProseSection } from '$lib/content/types';

  interface Props {
    blocks: ProseSection['blocks'];
  }

  let { blocks }: Props = $props();
</script>

<div class="prose-section">
  {#each blocks as block, i}
    <div class="prose-block" data-reveal style="--i:{i}">
      {#if block.heading}
        <h2 class="prose-block__heading">{block.heading}</h2>
      {/if}
      <div class="prose-block__content">
        {@html block.content}
      </div>
    </div>
  {/each}
</div>

<style>
  .prose-section {
    display: flex;
    flex-direction: column;
    gap: 20px;
  }

  .prose-block__heading {
    font-family: var(--font-sans);
    font-size: 12px;
    font-weight: 600;
    color: var(--color-text-primary);
    margin: 0 0 8px 0;
  }

  .prose-block__content {
    font-size: 12px;
    color: var(--color-text-secondary);
    line-height: 1.6;
  }

  .prose-block__content :global(p) {
    margin: 0 0 8px 0;
  }

  .prose-block__content :global(p:last-child) {
    margin-bottom: 0;
  }

  .prose-block__content :global(a) {
    color: var(--color-neon-cyan);
    text-decoration: none;
  }

  .prose-block__content :global(a:hover) {
    text-decoration: underline;
  }

  .prose-block__content :global(code) {
    font-family: var(--font-mono);
    font-size: 11px;
    background: var(--color-bg-input);
    padding: 1px 4px;
  }
</style>
