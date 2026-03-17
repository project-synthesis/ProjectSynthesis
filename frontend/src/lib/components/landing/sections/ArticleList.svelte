<script lang="ts">
  import type { ArticleListSection } from '$lib/content/types';

  interface Props {
    articles: ArticleListSection['articles'];
  }

  let { articles }: Props = $props();
</script>

<div class="article-list">
  {#each articles as article, i}
    <article class="article-card" data-reveal style="--i:{i}">
      {#if !article.slug}
        <span class="article-card__coming-soon font-mono">SOON</span>
      {/if}
      <div class="article-card__main">
        <div class="article-card__text">
          {#if article.slug}
            <a class="article-card__title" href="/content/{article.slug}">{article.title}</a>
          {:else}
            <span class="article-card__title article-card__title--inactive">{article.title}</span>
          {/if}
          <p class="article-card__excerpt">{article.excerpt}</p>
        </div>
        <div class="article-card__meta">
          <span class="article-card__date font-mono">{article.date}</span>
          <span class="article-card__read-time font-mono">{article.readTime}</span>
        </div>
      </div>
    </article>
  {/each}
</div>

<style>
  .article-list {
    display: flex;
    flex-direction: column;
    gap: 1px;
  }

  .article-card {
    position: relative;
    padding: 12px;
    background: var(--color-bg-card);
    border: 1px solid var(--color-border-subtle);
    transition: all var(--duration-hover) var(--ease-spring);
  }

  .article-card:hover {
    background: var(--color-bg-hover);
    border-color: var(--color-border-accent);
    transform: translateY(-1px);
  }

  .article-card__coming-soon {
    position: absolute;
    top: 8px;
    right: 8px;
    font-size: 9px;
    color: var(--color-text-dim);
    border: 1px solid var(--color-border-subtle);
    padding: 1px 4px;
    pointer-events: none;
  }

  .article-card__main {
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    gap: 16px;
  }

  .article-card__text {
    flex: 1;
    min-width: 0;
  }

  .article-card__title {
    display: block;
    font-size: 12px;
    font-weight: 600;
    color: var(--color-text-primary);
    text-decoration: none;
    margin-bottom: 4px;
  }

  .article-card__title--inactive {
    color: var(--color-text-secondary);
  }

  a.article-card__title:hover {
    color: var(--color-neon-cyan);
  }

  .article-card__excerpt {
    font-size: 12px;
    color: var(--color-text-secondary);
    margin: 0;
    line-height: 1.5;
  }

  .article-card__meta {
    display: flex;
    flex-direction: column;
    align-items: flex-end;
    gap: 2px;
    flex-shrink: 0;
    white-space: nowrap;
  }

  .article-card__date {
    font-size: 10px;
    color: var(--color-text-dim);
  }

  .article-card__read-time {
    font-size: 9px;
    color: var(--color-text-dim);
  }
</style>
