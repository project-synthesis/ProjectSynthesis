<script lang="ts">
  /**
   * ClusterRow — the shared row primitive used by domain-direct clusters
   * and sub-domain-nested clusters alike.
   *
   * Renders the compact family button plus the slide-in expanded detail
   * (linked optimizations, skeleton, error states). `nested` adds a left
   * indent so sub-domain children visually tuck under their parent.
   *
   * Extracted from ClusterNavigator's `clusterRow` snippet to restore
   * module boundaries without sacrificing the single-source row contract.
   */
  import type { ClusterNode } from '$lib/api/clusters';
  import { clustersStore } from '$lib/stores/clusters.svelte';
  import { scoreColor, stateColor } from '$lib/utils/colors';
  import { formatScore, formatRelativeTime } from '$lib/utils/formatting';
  import { tooltip } from '$lib/actions/tooltip';
  import { CLUSTER_NAV_TOOLTIPS } from '$lib/utils/ui-tooltips';
  import { slide } from 'svelte/transition';
  import { navSlide } from '$lib/utils/transitions';

  interface Props {
    family: ClusterNode;
    nested: boolean;
    expandedId: string | null;
    onToggleExpand: (family: ClusterNode) => void;
    onOpenLinkedOpt: (traceId: string, optId: string) => void;
  }

  let { family, nested, expandedId, onToggleExpand, onOpenLinkedOpt }: Props = $props();

  const OPT_DISPLAY_LIMIT = 8;

  const mbrSuffix = $derived(
    family.state === 'project' ? 'd'
      : family.state === 'domain' ? 'c'
      : 'm',
  );

  const mbrUnit = $derived(
    family.state === 'project'
      ? (family.member_count === 1 ? 'domain' : 'domains')
      : family.state === 'domain'
      ? (family.member_count === 1 ? 'cluster' : 'clusters')
      : (family.member_count === 1 ? 'member' : 'members'),
  );

  const expanded = $derived(expandedId === family.id);
</script>

<button
  class="family-row"
  class:family-row--subdomain={nested}
  class:family-row--expanded={expanded}
  data-cluster-id={family.id}
  onclick={() => onToggleExpand(family)}
  style="--state-color: {stateColor(family.state)};"
>
  <span class="family-label">{family.label}</span>
  <span class="family-badges">
    <span class="member-count font-mono" use:tooltip={`${family.member_count} ${mbrUnit}`}>{family.member_count}{mbrSuffix}</span>
    <span
      class="badge-usage font-mono"
      class:badge-usage--active={family.usage_count > 0}
      use:tooltip={CLUSTER_NAV_TOOLTIPS.usage_count}
    >{family.usage_count}</span>
    <span
      class="badge-score font-mono"
      style="color: {scoreColor(family.avg_score)};"
      use:tooltip={CLUSTER_NAV_TOOLTIPS.avg_score}
    >
      {formatScore(family.avg_score)}
    </span>
  </span>
</button>
{#if expanded}
  <div class="family-detail" transition:slide={navSlide}>
    {#if clustersStore.clusterDetailLoading}
      <p class="detail-note">Loading...</p>
    {:else if clustersStore.clusterDetail}
      <div class="detail-stats">
        <span style="color: {stateColor(clustersStore.clusterDetail.state)}">{clustersStore.clusterDetail.state}</span>
        <span>{clustersStore.clusterDetail.member_count} members</span>
        {#if clustersStore.clusterDetail.preferred_strategy}
          <span>{clustersStore.clusterDetail.preferred_strategy}</span>
        {/if}
      </div>
      {#if clustersStore.clusterDetail.optimizations.length > 0}
        {@const allOpts = clustersStore.clusterDetail.optimizations}
        {@const visibleOpts = allOpts.slice(0, OPT_DISPLAY_LIMIT)}
        <div class="linked-opts">
          {#each visibleOpts as opt (opt.id)}
            <button
              class="linked-opt-row"
              onclick={() => onOpenLinkedOpt(opt.trace_id, opt.id)}
              use:tooltip={opt.raw_prompt}
            >
              <span class="linked-opt-label">{opt.intent_label || (opt.raw_prompt ? opt.raw_prompt.slice(0, 40) + '..' : 'Untitled')}</span>
              {#if opt.created_at}
                <span class="linked-opt-time font-mono">{formatRelativeTime(opt.created_at)}</span>
              {/if}
              <span
                class="linked-opt-score font-mono"
                style="color: {scoreColor(opt.overall_score)};"
              >{formatScore(opt.overall_score)}</span>
            </button>
          {/each}
          {#if allOpts.length > OPT_DISPLAY_LIMIT}
            <p class="detail-note">{allOpts.length - OPT_DISPLAY_LIMIT} more in Inspector</p>
          {/if}
        </div>
      {:else}
        <p class="detail-note">No linked optimizations yet.</p>
      {/if}
    {:else if clustersStore.clusterDetailError}
      <p class="detail-note">Failed to load detail.</p>
    {:else}
      <div class="skeleton-row">
        <div class="skeleton-bar skeleton-wide"></div>
        <div class="skeleton-bar skeleton-narrow"></div>
      </div>
    {/if}
  </div>
{/if}

<style>
  .family-row {
    display: flex;
    align-items: center;
    gap: 4px;
    height: 22px;
    padding: 0 6px 0 16px;
    background: transparent;
    border: 1px solid transparent;
    border-left: 1px solid var(--state-color, transparent);
    cursor: pointer;
    width: 100%;
    text-align: left;
    transition: color var(--duration-hover) var(--ease-spring),
                border-color var(--duration-hover) var(--ease-spring),
                background var(--duration-hover) var(--ease-spring);
  }

  .family-row:hover {
    background: var(--color-bg-hover);
    border-color: var(--color-border-accent);
    border-left-color: var(--state-color, transparent);
  }

  .family-row:active {
    background: var(--color-bg-hover);
  }

  .family-row--expanded {
    border-color: color-mix(in srgb, var(--tier-accent, var(--color-neon-cyan)) 40%, transparent);
    border-left-color: var(--state-color, var(--tier-accent, var(--color-neon-cyan)));
    background: color-mix(in srgb, var(--tier-accent, var(--color-neon-cyan)) 4%, transparent);
  }

  .family-row--subdomain {
    padding-left: 24px;
  }

  .family-label {
    font-size: 10px;
    color: var(--color-text-primary);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    flex: 1;
    min-width: 0;
  }

  .family-row--expanded .family-label {
    color: var(--tier-accent, var(--color-neon-cyan));
  }

  .family-badges {
    display: flex;
    align-items: center;
    gap: 6px;
    flex-shrink: 0;
  }

  .badge-score {
    font-size: 9px;
    width: 24px;
    text-align: right;
  }

  .badge-usage {
    font-size: 9px;
    color: var(--color-text-dim);
    flex-shrink: 0;
    width: 14px;
    text-align: right;
  }

  .badge-usage--active {
    color: var(--color-neon-teal);
  }

  .member-count {
    font-size: 9px;
    color: var(--color-text-dim);
    flex-shrink: 0;
    width: 22px;
    text-align: right;
  }

  .family-detail {
    padding: 2px 6px 4px 16px;
    border: 1px solid var(--color-border-subtle);
    border-top: none;
    background: var(--color-bg-card, color-mix(in srgb, var(--color-bg-primary) 50%, transparent));
  }

  .detail-note {
    font-size: 9px;
    color: var(--color-text-dim);
    padding: 2px 0;
    margin: 0;
  }

  .linked-opts {
    display: flex;
    flex-direction: column;
    gap: 1px;
  }

  .linked-opt-row {
    display: flex;
    align-items: center;
    gap: 4px;
    height: 20px;
    padding: 0 2px;
    background: transparent;
    border: none;
    cursor: pointer;
    width: 100%;
    text-align: left;
    transition: color var(--duration-hover) var(--ease-spring),
                background var(--duration-hover) var(--ease-spring);
  }

  .linked-opt-row:hover {
    background: var(--color-bg-hover);
    color: var(--color-text-primary);
  }

  .linked-opt-row:active {
    background: var(--color-bg-hover);
  }

  .linked-opt-label {
    font-size: 9px;
    color: var(--color-text-secondary);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    flex: 1;
    min-width: 0;
  }

  .linked-opt-row:hover .linked-opt-label {
    color: var(--tier-accent, var(--color-neon-cyan));
  }

  .linked-opt-time {
    font-size: 8px;
    color: var(--color-text-dim);
    flex-shrink: 0;
  }

  .linked-opt-score {
    font-size: 9px;
    flex-shrink: 0;
    width: 22px;
    text-align: right;
  }

  .detail-stats {
    display: flex;
    gap: 6px;
    flex-wrap: wrap;
    padding: 2px 0 4px;
    font-size: 8px;
    font-family: var(--font-mono);
    color: var(--color-text-dim);
    border-bottom: 1px solid var(--color-border-subtle);
    margin-bottom: 4px;
  }

  .skeleton-row {
    display: flex;
    flex-direction: column;
    gap: 3px;
    padding: 3px 0;
  }

  .skeleton-bar {
    height: 6px;
    background: var(--color-bg-card);
    animation: skeleton-pulse var(--duration-skeleton) ease-in-out infinite;
  }

  .skeleton-wide { width: 80%; }
  .skeleton-narrow { width: 50%; }

  @keyframes skeleton-pulse {
    0%, 100% { opacity: 0.4; }
    50% { opacity: 1; }
  }
</style>
