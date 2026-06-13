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
   * Field surfacing (plan task 12.4 A3 — every `ValidationSuiteListItem`
   * field must be rendered visibly OR surfaced through a tooltip):
   *
   *   - id              → `data-suite-id` attribute (test hook + UI-debug)
   *   - source_run_id   → tooltip (`run: …`)
   *   - label           → visible (.suite-label, ellipsis-truncated)
   *   - tolerance_abs   → tooltip (`tol ±…`)
   *   - project_id      → tooltip when set (`proj: …`)
   *   - repo_full_name  → tooltip when set (`repo: …`)
   *   - created_at      → tooltip (`created …`)
   *   - retired_at      → `data-retired` attribute (chromatic dimming) +
   *                       tooltip (`retired …`)
   *   - prompts_count   → visible (.suite-prompts, `<N>p`)
   *   - baseline_mean   → visible (.suite-baseline)
   *
   * `delta` + `status` arrive from the parent panel because they require
   * cross-row state (the regression-alarm block from the suites store)
   * not present on the list-row payload itself.
   *
   * v0.4.39 brand-compliance — R-06 Row Recipe v1 (h-5, 1px contour,
   * 5-state) + R-22 aria-label composition + R-18 typography anchor.
   * Border collapses to `border-bottom` only (per-row 4-sided borders are
   * banned by R-06). Focus uses the shared `--color-focus-ring` token.
   * The status dot is `aria-hidden` because the composed row aria-label
   * already includes the status word — no double-announce for SR users.
   *
   * Selection: a re-click on a selected row toggles the selection off
   * (`onClick` then `suitesStore.select(null)` in the parent); first
   * click selects the row. `[data-selected]` paints the active-state
   * inset contour. The actual selection state lives in the parent panel
   * so list-wide single-select is enforced.
   *
   * Per-row kebab opens `SuiteActionMenu` (Rename / Retire / Delete). The
   * kebab `stopPropagation`s so it never fires the row's select handler.
   */
  import type { ValidationSuiteListItem } from '$lib/api/suites';
  import { formatSignedDelta } from '$lib/utils/formatting';
  import { tooltip } from '$lib/actions/tooltip';
  import SuiteActionMenu from './SuiteActionMenu.svelte';

  export type SuiteRowStatus = 'nominal' | 'firing' | 'none';

  interface Props {
    suite: ValidationSuiteListItem;
    /** Latest-replay delta vs baseline (signed; null when no replay yet). */
    delta: number | null;
    status: SuiteRowStatus;
    /** True when this row is the currently-selected suite. Drives the
     *  `data-selected` chromatic-active state per R-06 5-state lifecycle. */
    selected?: boolean;
    onClick: (suite: ValidationSuiteListItem) => void;
    /** Optional menu actions. Parent decides routing — Rename/Retire/Delete
     *  may dispatch into `suitesStore` or open modals. Undefined handlers
     *  hide the kebab so a parent that doesn't wire menu actions still
     *  renders a clean row. */
    onRename?: (suite: ValidationSuiteListItem) => void;
    onRetire?: (suite: ValidationSuiteListItem) => void;
    onDelete?: (suite: ValidationSuiteListItem) => void;
  }

  let {
    suite,
    delta,
    status,
    selected = false,
    onClick,
    onRename,
    onRetire,
    onDelete,
  }: Props = $props();

  const statusLabel = $derived(
    status === 'firing' ? 'firing' : status === 'nominal' ? 'nominal' : 'no replay',
  );

  // Format an ISO timestamp into a compact `YYYY-MM-DD` form for tooltip
  // density. Falls back to the raw string on parse failure.
  function fmtDate(ts: string | null): string {
    if (!ts) return '—';
    try {
      const d = new Date(ts);
      const pad = (n: number) => n.toString().padStart(2, '0');
      return `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}`;
    } catch {
      return ts;
    }
  }

  // Tooltip lines — every secondary field that doesn't get its own cell.
  // Joined with ` · ` for a single-line hint at the 9-10px caption scale.
  const tooltipText = $derived.by(() => {
    const parts: string[] = [];
    parts.push(`tol ±${suite.tolerance_abs.toFixed(2)}`);
    if (suite.source_run_id) parts.push(`run: ${suite.source_run_id}`);
    if (suite.repo_full_name) parts.push(`repo: ${suite.repo_full_name}`);
    if (suite.project_id) parts.push(`proj: ${suite.project_id}`);
    parts.push(`created ${fmtDate(suite.created_at)}`);
    if (suite.retired_at) parts.push(`retired ${fmtDate(suite.retired_at)}`);
    return parts.join(' · ');
  });

  // R-22 aria-label composition: identity + status + baseline + prompts
  // + delta. A single descriptive label per row replaces the prior
  // "Open suite <label>" stub so a screen reader user gets the same
  // numeric context the sighted reader gets from the cells.
  const composedAriaLabel = $derived.by(() => {
    const parts: string[] = [`Suite ${suite.label}`];
    parts.push(statusLabel);
    parts.push(`baseline ${suite.baseline_mean.toFixed(1)}`);
    parts.push(`${suite.prompts_count} prompts`);
    if (delta != null) {
      parts.push(`delta ${formatSignedDelta(delta)}`);
    } else {
      parts.push('no delta');
    }
    return parts.join(', ');
  });

  // ── Kebab menu state ────────────────────────────────────────────────
  let menuOpen = $state(false);
  let kebabBtn: HTMLButtonElement | undefined = $state();
  const hasMenu = $derived(
    onRename != null || onRetire != null || onDelete != null,
  );
  function openMenu(): void {
    menuOpen = true;
  }
  function closeMenu(): void {
    menuOpen = false;
    kebabBtn?.focus();
  }
</script>

<div class="suite-row-wrap" data-test="suite-row-wrap">
  <button
    type="button"
    class="suite-row h-5 px-1"
    data-test="suite-row"
    data-suite-id={suite.id}
    data-status={status}
    data-retired={suite.retired_at != null}
    data-selected={selected}
    onclick={() => onClick(suite)}
    use:tooltip={tooltipText}
    aria-label={composedAriaLabel}
    aria-pressed={selected}
  >
    <span
      class="status-dot"
      data-test="suite-row-dot"
      aria-hidden="true"
    ></span>
    <span class="suite-label">{suite.label}</span>
    <span class="suite-baseline" data-test="suite-row-baseline">{suite.baseline_mean.toFixed(1)}</span>
    <span class="suite-prompts">{suite.prompts_count}p</span>
    <span class="suite-delta" data-test="suite-row-delta">{formatSignedDelta(delta)}</span>
  </button>
  {#if hasMenu}
    <button
      bind:this={kebabBtn}
      type="button"
      class="suite-kebab"
      data-test="suite-row-kebab"
      aria-label="Open actions for suite {suite.label}"
      aria-haspopup="menu"
      aria-expanded={menuOpen}
      onclick={(e) => { e.stopPropagation(); openMenu(); }}
    >⋮</button>
    {#if menuOpen}
      <SuiteActionMenu
        onRename={() => { closeMenu(); onRename?.(suite); }}
        onRetire={() => { closeMenu(); onRetire?.(suite); }}
        onDelete={() => { closeMenu(); onDelete?.(suite); }}
        onClose={closeMenu}
      />
    {/if}
  {/if}
</div>

<style>
  /* h-5 / px-1 / R-06 Row Recipe v1 — border-bottom only (per-row 4-side
     borders are banned). Selection state painted via [data-selected] +
     the canonical 5-state lifecycle: resting / hover / focus-visible /
     active / disabled. */
  .suite-row-wrap {
    position: relative;
    display: flex;
    align-items: stretch;
    gap: 2px;
    width: 100%;
  }

  .suite-row {
    /* 6px status dot · 1fr label · auto baseline · auto prompts · auto delta */
    display: grid;
    grid-template-columns: 6px 1fr auto auto auto;
    align-items: center;
    gap: 4px;
    flex: 1;
    min-width: 0;
    height: 20px;            /* h-5 */
    padding: 0 4px;          /* px-1 */
    background: transparent;
    /* R-06 — `border-bottom` only. Sibling rows stack into a single
       hairline grid; the focus/hover/active states paint via outline +
       inset box-shadow without competing contour weight. */
    border: none;
    border-bottom: 1px solid var(--color-border-subtle);
    color: var(--color-text-primary);
    /* R-18 — container row defaults to sans; only data cells (.suite-
       baseline, .suite-prompts, .suite-delta) carry the mono override. */
    font-family: var(--font-sans);
    font-size: 10px;
    cursor: pointer;
    text-align: left;
    /* R-04 — token-tuple transition. Hover/state changes use --duration-
       hover with --ease-spring; the bare `ease` keyword (browser-default
       cubic-bezier(0.25,0.1,0.25,1)) is banned. */
    transition:
      background-color var(--duration-hover) var(--ease-spring),
      border-color var(--duration-hover) var(--ease-spring),
      color var(--duration-hover) var(--ease-spring);
  }

  /* Retired rows dim — they remain in the list for audit but the visual
     weight shifts down so live rows surface first. */
  .suite-row[data-retired='true'] {
    color: var(--color-text-dim);
    opacity: 0.7;
  }

  .suite-row:hover {
    border-bottom-color: var(--color-border-accent);
    background: color-mix(in srgb, var(--color-bg-hover) 40%, transparent);
  }

  /* R-06 focus-visible — explicit outline so keyboard navigation reads
     even when the global app.css :focus-visible token shifts. Uses the
     shared --color-focus-ring + --focus-offset-inset tokens. */
  .suite-row:focus-visible {
    outline: 1px solid var(--color-focus-ring);
    outline-offset: var(--focus-offset-inset);
    border-bottom-color: var(--color-border-accent);
  }

  /* R-06 active / selected — inset cyan contour. The row is the selected
     suite; pairs with parent's SuiteDetailView mount so the visual
     selection matches the data-driven detail surface. */
  .suite-row[data-selected='true'] {
    box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--color-neon-cyan) 40%, transparent);
    background: color-mix(in srgb, var(--color-neon-cyan) 6%, transparent);
  }

  .status-dot {
    width: 6px;
    height: 6px;
    flex-shrink: 0;
    background: var(--color-text-dim);
  }

  .suite-row[data-status='nominal'] .status-dot {
    background: var(--color-neon-green);
  }

  .suite-row[data-status='firing'] .status-dot {
    background: var(--color-neon-red);
  }

  .suite-label {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    min-width: 0;
  }

  /* R-18 — data cells (numerics) stay mono; container is sans. */
  .suite-prompts {
    color: var(--color-text-dim);
    flex-shrink: 0;
    font-family: var(--font-mono);
    font-variant-numeric: tabular-nums;
  }

  .suite-baseline {
    color: var(--color-text-dim);
    flex-shrink: 0;
    font-family: var(--font-mono);
    font-variant-numeric: tabular-nums;
  }

  .suite-delta {
    color: var(--color-text-primary);
    flex-shrink: 0;
    font-family: var(--font-mono);
    font-variant-numeric: tabular-nums;
  }

  /* Kebab — sized to the 20px row, no border so the rail reads as quiet
     until hover. Uses the shared text-dim → text-primary hover token. */
  .suite-kebab {
    width: 16px;
    height: 20px;
    padding: 0;
    background: transparent;
    border: none;
    border-radius: 0;
    color: var(--color-text-dim);
    font-size: 12px;
    line-height: 20px;
    text-align: center;
    cursor: pointer;
    flex-shrink: 0;
    transition: color var(--duration-hover) var(--ease-spring);
  }
  .suite-kebab:hover,
  .suite-kebab:focus-visible {
    color: var(--color-text-primary);
  }
  .suite-kebab:focus-visible {
    outline: 1px solid var(--color-focus-ring);
    outline-offset: var(--focus-offset-inset);
  }

  /* R-19 — reduced-motion neutralization covers every transition site
     declared above. */
  @media (prefers-reduced-motion: reduce) {
    .suite-row,
    .suite-kebab {
      transition: none !important;
    }
  }
</style>
