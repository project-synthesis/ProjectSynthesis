<script lang="ts">
  import type { CardGridSection } from '$lib/content/types';

  interface Props {
    columns: CardGridSection['columns'];
    cards: CardGridSection['cards'];
  }

  let { columns, cards }: Props = $props();
</script>

<div class="card-grid-wrapper" data-reveal>
  <div class="card-grid" style="--cols:{columns}">
    {#each cards as card, i}
      <article class="card" style="--i:{i}">
        {#if card.icon}
          <div class="card__icon" style="color:{card.color};border-color:{card.color}">
            {@html card.icon}
          </div>
        {/if}
        <h3 class="card__title">{card.title}</h3>
        <p class="card__desc">{card.description}</p>
      </article>
    {/each}
  </div>
</div>

<style>
  .card-grid-wrapper {
    container-type: inline-size;
  }

  .card-grid {
    display: grid;
    grid-template-columns: repeat(var(--cols, 3), 1fr);
    gap: 1px;
  }

  @container (max-width: 900px) {
    .card-grid[style*="--cols:5"] {
      grid-template-columns: repeat(3, 1fr);
    }
  }

  @container (max-width: 640px) {
    .card-grid {
      grid-template-columns: 1fr;
    }
  }

  .card {
    padding: 12px;
    background: var(--color-bg-card);
    border: 1px solid var(--color-border-subtle);
    transition: all var(--duration-hover) var(--ease-spring);
    animation: reveal-up 1s var(--ease-spring) both;
    animation-delay: calc(var(--i, 0) * 80ms);
  }

  @supports (animation-timeline: view()) {
    .card {
      animation-timeline: view();
      animation-range: entry 0% entry 100%;
    }
  }

  .card:hover {
    background: var(--color-bg-hover);
    border-color: var(--color-border-accent);
  }

  .card__icon {
    width: 28px;
    height: 28px;
    display: flex;
    align-items: center;
    justify-content: center;
    border: 1px solid;
    margin-bottom: 8px;
    flex-shrink: 0;
  }

  .card__icon :global(svg) {
    width: 14px;
    height: 14px;
  }

  .card__title {
    font-family: var(--font-sans);
    font-size: 12px;
    font-weight: 600;
    color: var(--color-text-primary);
    margin: 0 0 4px 0;
  }

  .card__desc {
    font-size: 12px;
    color: var(--color-text-secondary);
    margin: 0;
    line-height: 1.5;
  }

  @keyframes reveal-up {
    from { opacity: 0; transform: translateY(16px); }
  }
</style>
