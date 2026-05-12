<script lang="ts">
  /**
   * SuiteRow — h-5 data row in the SuitesPanel list.
   *
   * Density pins (spec § 6 density-pins table):
   *   - h-5 (20px) row height, `px-1` padding
   *   - 6px chromatic status dot leads the row
   *     · `nominal` (green) — latest replay within tolerance
   *     · `firing` (red)    — latest replay regressed beyond tolerance
   *     · `none`   (dim)    — no replay run yet
   *   - Signed delta column (positive +, negative −, Unicode U+2212 minus)
   *
   * Recipe A hover (component-patterns.md:128-141):
   *   - resting: `border-border-subtle`, transparent bg
   *   - hover:   `border-border-accent` + `bg-bg-hover/40`
   *   - 200ms transition on all properties together
   *
   * NO Recipe E (translateY lift) — h-5 is too compact for the physical
   * lift reserved for Hero buttons per spec § 6 contour-tier table.
   *
   * Click delegates to `onClick(suite)` — parent panel calls
   * `suitesStore.select(id)` to surface the SuiteDetailView. The element
   * is a real `<button>` so keyboard navigation works without
   * `tabindex/role/onkeydown` boilerplate.
   */
  import type { ValidationSuiteListItem } from '$lib/api/suites';

  export type SuiteRowStatus = 'nominal' | 'firing' | 'none';

  interface Props {
    suite: ValidationSuiteListItem;
    /** Latest-replay delta vs baseline (signed; null when no replay yet). */
    delta: number | null;
    status: SuiteRowStatus;
    onClick: (suite: ValidationSuiteListItem) => void;
  }

  let { suite, delta, status, onClick }: Props = $props();

  // Format signed delta with Unicode U+2212 minus (per SKILL.md numeric
  // voice). Two decimals match the scoring convention shown in the spec
  // ("−0.64 vs baseline").
  function formatDelta(d: number | null): string {
    if (d == null) return '—';
    const abs = Math.abs(d).toFixed(2);
    if (d > 0) return `+${abs}`;
    if (d < 0) return `−${abs}`;
    return '0.00';
  }

  const statusLabel = $derived(
    status === 'firing' ? 'firing' : status === 'nominal' ? 'nominal' : 'no replay',
  );
</script>

<button
  type="button"
  class="suite-row h-5 px-1"
  data-test="suite-row"
  data-suite-id={suite.id}
  data-status={status}
  onclick={() => onClick(suite)}
  aria-label="Open suite {suite.label}"
>
  <span
    class="status-dot"
    data-test="suite-row-dot"
    role="img"
    aria-label={`Suite ${statusLabel}`}
  ></span>
  <span class="suite-label">{suite.label}</span>
  <span class="suite-prompts">{suite.prompts_count}p</span>
  <span class="suite-delta" data-test="suite-row-delta">{formatDelta(delta)}</span>
</button>

<style>
  /* h-5 / px-1 / Recipe A — kept in CSS so non-Tailwind audits + raw-source
     regex tests can both see the canonical class names AND the underlying
     declarations. */
  .suite-row {
    display: grid;
    grid-template-columns: 6px 1fr auto auto;
    align-items: center;
    gap: 4px;
    width: 100%;
    height: 20px;            /* h-5 */
    padding: 0 4px;          /* px-1 */
    background: transparent;
    border: 1px solid var(--color-border-subtle);
    color: var(--color-text-primary);
    font-family: var(--font-mono);
    font-size: 10px;
    cursor: pointer;
    text-align: left;
    /* Recipe A — uniform 200ms transition on every animated property. No
       translateY: h-5 is too compact for the Recipe E lift reserved for
       Hero buttons. */
    transition:
      background-color 200ms ease,
      border-color 200ms ease,
      color 200ms ease;
  }

  .suite-row:hover {
    border-color: var(--color-border-accent);
    background: color-mix(in srgb, var(--color-bg-hover) 40%, transparent);
  }

  .suite-row:focus-visible {
    outline: none;
    border-color: var(--color-border-accent);
  }

  .status-dot {
    width: 6px;
    height: 6px;
    flex-shrink: 0;
    background: var(--color-text-dim);
  }

  .suite-row[data-status='nominal'] .status-dot {
    background: var(--color-neon-green, #22ff88);
  }

  .suite-row[data-status='firing'] .status-dot {
    background: var(--color-neon-red, #ff3366);
  }

  .suite-label {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    min-width: 0;
  }

  .suite-prompts {
    color: var(--color-text-dim);
    flex-shrink: 0;
  }

  .suite-delta {
    color: var(--color-text-primary);
    flex-shrink: 0;
    font-variant-numeric: tabular-nums;
  }
</style>
