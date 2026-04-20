<script lang="ts">
  import type { EffectiveTier } from '$lib/stores/routing.svelte';
  import { tooltip } from '$lib/actions/tooltip';
  import { TIER_TOOLTIPS } from '$lib/utils/ui-tooltips';

  interface Props {
    tier: EffectiveTier;
    provider?: string | null;
    degradedFrom?: EffectiveTier | null;
  }

  let { tier, provider = null, degradedFrom = null }: Props = $props();

  /** Resolve display label — internal tier splits into CLI/API sub-types. */
  const label = $derived.by(() => {
    if (tier === 'internal' && provider) {
      const p = provider.toLowerCase();
      if (p.includes('cli')) return 'CLI';
      if (p.includes('api') || p.includes('anthropic')) return 'API';
    }
    if (tier === 'internal') return 'INTERNAL';
    if (tier === 'sampling') return 'SAMPLING';
    return 'PASSTHROUGH';
  });

  const TIER_LABELS: Record<EffectiveTier, string> = {
    internal: 'INTERNAL',
    sampling: 'SAMPLING',
    passthrough: 'PASSTHROUGH',
  };

  const degradedLabel = $derived(degradedFrom ? TIER_LABELS[degradedFrom] : null);

  const ariaLabel = $derived(
    degradedFrom
      ? `Execution tier: ${label} (degraded from ${degradedFrom})`
      : `Execution tier: ${label}`
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
    <span class="tier-degraded" use:tooltip={TIER_TOOLTIPS.degraded}>{degradedLabel}</span>
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
    transition: color var(--duration-hover) var(--ease-spring),
                border-color var(--duration-hover) var(--ease-spring);
  }

  .tier-internal {
    border-color: color-mix(in srgb, var(--color-neon-cyan) 30%, transparent);
    color: var(--color-neon-cyan);
  }

  .tier-sampling {
    border-color: color-mix(in srgb, var(--color-neon-green) 30%, transparent);
    color: var(--color-neon-green);
  }

  .tier-passthrough {
    border-color: color-mix(in srgb, var(--color-neon-yellow) 30%, transparent);
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
