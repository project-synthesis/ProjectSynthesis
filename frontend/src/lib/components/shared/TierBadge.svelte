<script lang="ts">
  import type { EffectiveTier } from '$lib/stores/routing.svelte';

  interface Props {
    tier: EffectiveTier;
    degradedFrom?: EffectiveTier | null;
  }

  let { tier, degradedFrom = null }: Props = $props();

  const TIER_LABELS: Record<EffectiveTier, string> = {
    internal: 'INTERNAL',
    sampling: 'SAMPLING',
    passthrough: 'PASSTHROUGH',
  };

  const label = $derived(TIER_LABELS[tier]);
  const degradedLabel = $derived(degradedFrom ? TIER_LABELS[degradedFrom] : null);

  const ariaLabel = $derived(
    degradedFrom
      ? `Execution tier: ${tier} (degraded from ${degradedFrom})`
      : `Execution tier: ${tier}`
  );
</script>

<span class="tier-badge-group" aria-label={ariaLabel}>
  <span
    class="tier-badge"
    class:tier-internal={tier === 'internal'}
    class:tier-sampling={tier === 'sampling'}
    class:tier-passthrough={tier === 'passthrough'}
  >
    {label}
  </span>
  {#if degradedLabel}
    <span class="tier-degraded" title="Requested tier unavailable">{degradedLabel}</span>
  {/if}
</span>

<style>
  .tier-badge-group {
    display: inline-flex;
    align-items: center;
    gap: 3px;
  }

  .tier-badge {
    display: inline-flex;
    align-items: center;
    font-size: 10px;
    font-family: var(--font-mono);
    padding: 1px 6px;
    border: 1px solid var(--color-border-subtle);
    border-radius: 0;
    color: var(--color-text-dim);
    white-space: nowrap;
    transition: color 200ms cubic-bezier(0.16, 1, 0.3, 1),
                border-color 200ms cubic-bezier(0.16, 1, 0.3, 1);
  }

  .tier-internal {
    border-color: rgba(0, 229, 255, 0.3);
    color: var(--color-neon-cyan);
  }

  .tier-sampling {
    border-color: rgba(34, 255, 136, 0.3);
    color: var(--color-neon-green);
  }

  .tier-passthrough {
    border-color: rgba(251, 191, 36, 0.3);
    color: var(--color-neon-yellow);
  }

  .tier-degraded {
    font-size: 9px;
    font-family: var(--font-mono);
    color: var(--color-neon-orange);
    text-decoration: line-through;
    white-space: nowrap;
    opacity: 0.8;
  }
</style>
