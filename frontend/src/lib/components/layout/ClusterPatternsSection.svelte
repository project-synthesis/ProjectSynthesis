<script lang="ts">
  import type { MetaPatternItem } from '$lib/api/clusters';
  import { CLUSTER_TOOLTIPS } from '$lib/utils/metric-tooltips';
  import { tooltip } from '$lib/actions/tooltip';

  interface Props {
    metaPatterns: MetaPatternItem[];
    familyState: string;
    memberCount: number;
  }

  const { metaPatterns, familyState, memberCount }: Props = $props();

  function dedupe<T extends { id: string }>(items: T[]): T[] {
    const seen = new Set<string>();
    return items.filter((item) => {
      if (seen.has(item.id)) return false;
      seen.add(item.id);
      return true;
    });
  }
</script>

{#if metaPatterns.length > 0}
  <div class="family-section">
    <div class="section-heading" style="margin-bottom: 4px;">
      {#if familyState === 'domain'}
        Top Patterns ({memberCount} {memberCount === 1 ? 'cluster' : 'clusters'})
      {:else if familyState === 'archived'}
        Meta-patterns (archived)
      {:else}
        Meta-patterns
      {/if}
    </div>
    <div class="pattern-list">
      {#each dedupe(metaPatterns) as mp (mp.id)}
        <div class="pattern-item">
          <span class="pattern-text">{mp.pattern_text}</span>
          <span class="source-badge" use:tooltip={CLUSTER_TOOLTIPS.source_count}>{mp.source_count}</span>
        </div>
      {/each}
    </div>
  </div>
{:else if familyState === 'domain' && memberCount > 0}
  <div class="family-section">
    <p class="empty-note">Patterns emerge as optimizations accumulate</p>
  </div>
{:else if familyState === 'domain' && memberCount === 0}
  <div class="family-section">
    <p class="empty-note">No clusters in this domain yet</p>
  </div>
{:else if familyState === 'candidate'}
  <div class="family-section">
    <p class="empty-note">Patterns extracted after promotion to active</p>
  </div>
{:else if familyState === 'archived'}
  <div class="family-section">
    <p class="empty-note">No meta-patterns were extracted</p>
  </div>
{/if}

<style>
  .family-section {
    display: flex;
    flex-direction: column;
  }

  .pattern-list {
    display: flex;
    flex-direction: column;
    gap: 2px;
  }

  .pattern-item {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 6px;
    padding: 3px 6px;
    background: var(--color-bg-card);
    border: 1px solid var(--color-border-subtle);
  }

  .pattern-text {
    font-size: 10px;
    font-family: var(--font-sans);
    color: var(--color-text-secondary);
    line-height: 1.4;
    flex: 1;
    min-width: 0;
  }

  .source-badge {
    font-size: 9px;
    font-family: var(--font-mono);
    color: var(--tier-accent, var(--color-neon-cyan));
    background: var(--color-bg-secondary);
    border: 1px solid var(--color-border-subtle);
    padding: 0 4px;
    flex-shrink: 0;
    line-height: 1.6;
  }
</style>
