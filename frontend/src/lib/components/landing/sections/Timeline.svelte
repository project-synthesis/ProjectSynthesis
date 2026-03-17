<script lang="ts">
  import type { TimelineSection } from '$lib/content/types';

  interface Props {
    versions: TimelineSection['versions'];
  }

  let { versions }: Props = $props();
</script>

<div class="timeline">
  {#each versions as ver, i}
    <div class="timeline__entry" data-reveal style="--i:{i}">
      <div class="timeline__meta">
        <span class="timeline__version font-mono">{ver.version}</span>
        <span class="timeline__date font-mono">{ver.date}</span>
      </div>
      <div class="timeline__categories">
        {#each ver.categories as cat}
          <div class="timeline__category">
            <span class="timeline__cat-label" style="color:{cat.color}">{cat.label}</span>
            <ul class="timeline__items">
              {#each cat.items as item}
                <li class="timeline__item">{item}</li>
              {/each}
            </ul>
          </div>
        {/each}
      </div>
    </div>
  {/each}
</div>

<style>
  .timeline {
    display: flex;
    flex-direction: column;
    gap: 0;
    border-left: 1px solid var(--color-border-subtle);
    padding-left: 16px;
  }

  .timeline__entry {
    padding-bottom: 24px;
  }

  .timeline__meta {
    display: flex;
    align-items: baseline;
    gap: 10px;
    margin-bottom: 10px;
  }

  .timeline__version {
    font-size: 12px;
    font-weight: 600;
    color: var(--color-neon-cyan);
  }

  .timeline__date {
    font-size: 10px;
    color: var(--color-text-dim);
  }

  .timeline__categories {
    display: flex;
    flex-direction: column;
    gap: 8px;
  }

  .timeline__cat-label {
    display: block;
    font-size: 10px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    margin-bottom: 4px;
  }

  .timeline__items {
    margin: 0;
    padding: 0;
    list-style: none;
    display: flex;
    flex-direction: column;
    gap: 2px;
  }

  .timeline__item {
    font-size: 11px;
    color: var(--color-text-secondary);
    line-height: 1.5;
    padding-left: 10px;
    position: relative;
  }

  .timeline__item::before {
    content: '–';
    position: absolute;
    left: 0;
    color: var(--color-text-dim);
  }
</style>
