<script lang="ts">
  /**
   * RegressionBadge — StatusBar surface for Topic Probe Tier 2 regression
   * alarm.
   *
   * Mounted inside `StatusBar.svelte` (right cluster, alongside the `domains`
   * / `clusters` / `Q` / `UpdateBadge` / `SSE` items). Surfaces the count of
   * suites in regression alarm, chromatic-encoded:
   *
   *   - Nominal — neon-green `#22ff88`, copy `"<N> ok"`
   *   - Firing  — neon-red `#ff3366`,   copy `"<N> alarm"`
   *
   * Voice (spec § 6 voice table): lower-case noun (matches the shipped
   * `statusLabel()` lower-case pattern in `SeedModal.svelte:227-233`). The
   * chromatic color carries urgency, not capitalisation. We deliberately
   * render `"alarm"` not `"alarms"` — the noun is status-condition
   * (uncountable use), not a plural count of events.
   *
   * Density (spec § 6 density-pins table): `px-1.5 py-0` — edge-to-edge
   * inside the 20px status bar. `text-[10px] font-mono` — matches the
   * `TierBadge` / `UpdateBadge` / `ProviderBadge` precedent.
   *
   * Motion (spec § 6 forge motion personality): nominal → firing transition
   * fires `forge-spark` ONCE on the badge (250ms ease-out), then settles to
   * static red. The keyframe is defined in
   * `lib/styles/shared-keyframes.css:128-142` (250ms ease-out, 3 anchor
   * stops). On firing → nominal we settle without re-sparking — the user
   * just witnessed the green recovery, no need to re-stamp.
   *
   * The spark is re-armed each time the badge transitions from nominal back
   * into firing — repeated regressions across a long session must each
   * surface visually.
   */
  import { suitesStore } from '$lib/stores/suites.svelte';
  import { tooltip } from '$lib/actions/tooltip';

  const block = $derived(suitesStore.regressionAlarmBlock);
  const isFiring = $derived(
    (block?.suites_in_alarm ?? 0) > 0,
  );

  // Track previous state so the nominal → firing transition can re-arm
  // the forge-spark animation each time it occurs.
  let prevFiring = $state(false);
  let sparkKey = $state(0);

  $effect(() => {
    const firing = isFiring;
    if (firing && !prevFiring) {
      // Bumping the key forces a remount so the CSS animation replays.
      sparkKey += 1;
    }
    prevFiring = firing;
  });

  const tooltipText = $derived.by(() => {
    if (!block) return 'Regression alarm: no data yet';
    if (block.suites_in_alarm === 0) {
      return `${block.suites_total} suite${block.suites_total === 1 ? '' : 's'}, no regressions`;
    }
    const top = block.latest_alarms?.[0];
    if (top && top.label && typeof top.delta_abs === 'number') {
      return `${block.suites_in_alarm} regression${block.suites_in_alarm === 1 ? '' : 's'} — most recent: ${top.label} (Δ ${top.delta_abs.toFixed(2)})`;
    }
    return `${block.suites_in_alarm} of ${block.suites_total} suites in alarm`;
  });
</script>

{#if block && block.suites_total > 0}
  {#key sparkKey}
    <span
      class="regression-badge font-mono text-[10px] px-1.5 py-0"
      class:firing={isFiring}
      class:nominal={!isFiring}
      class:spark={isFiring && sparkKey > 0}
      use:tooltip={tooltipText}
      aria-label={isFiring
        ? `${block.suites_in_alarm} suites in regression alarm`
        : `${block.suites_total} suites nominal`}
    >
      {#if isFiring}
        {block.suites_in_alarm} alarm
      {:else}
        {block.suites_total} ok
      {/if}
    </span>
  {/key}
{/if}

<style>
  .regression-badge {
    /* px-1.5 py-0 + text-[10px] + font-mono are applied via Tailwind utility
       classes on the host element — the rule-set below pins the chromatic
       encoding + reserves the animation slot. */
    font-family: var(--font-mono);
    font-size: 10px;
    padding: 0 6px;
    white-space: nowrap;
    display: inline-flex;
    align-items: center;
    line-height: 20px;
    cursor: default;
  }

  .regression-badge.nominal {
    color: var(--color-neon-green, #22ff88);
  }

  .regression-badge.firing {
    color: var(--color-neon-red, #ff3366);
  }

  /* Nominal → Firing transition: forge-spark fires once (250ms ease-out)
     then settles to static red. Per spec § 6 personality table:
     "once, then static red". The `#key sparkKey` block above forces a
     remount of the badge element each time the transition re-arms, so the
     animation replays naturally on every nominal→firing edge. */
  .regression-badge.spark {
    animation: forge-spark 250ms ease-out;
  }
</style>
