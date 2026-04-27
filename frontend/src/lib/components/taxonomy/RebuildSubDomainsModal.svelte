<script lang="ts">
  /**
   * RebuildSubDomainsModal — operator UI for the R6 recovery endpoint.
   *
   * Calls `POST /api/domains/{id}/rebuild-sub-domains` with optional
   * `min_consistency` override (≥ 0.25 floor) and `dry_run` toggle.
   * Brand: 1px contour grammar, neon-cyan primary action, neon-yellow
   * during request, neon-green on success, neon-red on error. Zero glow.
   *
   * Audit: docs/audits/sub-domain-regression-2026-04-27.md §R6
   * Spec:  docs/specs/sub-domain-dissolution-hardening-r4-r6.md §R6
   */
  import {
    rebuildSubDomains,
    REBUILD_MIN_CONSISTENCY_FLOOR,
    type RebuildSubDomainsResult,
  } from '$lib/api/domains';
  import { readinessStore } from '$lib/stores/readiness.svelte';

  interface Props {
    /** When `null`, the modal is hidden. Set to a domain id to open. */
    domainId: string | null;
    /** Display label for the title — falls back to id slice if absent. */
    domainLabel?: string;
    onClose: () => void;
  }

  let { domainId, domainLabel, onClose }: Props = $props();

  // -- Form state --
  //
  // `useOverride=false` sends `min_consistency=null` (server-side adaptive
  // formula). When toggled on, the slider value (0.25 - 1.00, step 0.05)
  // is sent literally. The Pydantic floor + engine runtime check both
  // enforce ≥ 0.25 — duplicating the floor in `<input min>` keeps the UI
  // honest with the backend contract.

  let useOverride = $state(false);
  let overrideValue = $state(0.30); // recommended default per spec
  let dryRun = $state(true); // safe default: preview first
  let busy = $state(false);
  let result = $state<RebuildSubDomainsResult | null>(null);
  let error = $state<string | null>(null);

  // Reset transient state on open. The `domainId` change is the open signal.
  $effect(() => {
    if (domainId !== null) {
      result = null;
      error = null;
      busy = false;
      // Keep dry_run sticky to TRUE on open — preview-first is the
      // operationally-safe default. Override toggle resets per-open so a
      // stale slider value doesn't leak into a second domain's modal.
      useOverride = false;
      overrideValue = 0.30;
      dryRun = true;
    }
  });

  async function handleRebuild() {
    if (!domainId) return;
    busy = true;
    error = null;
    result = null;
    try {
      result = await rebuildSubDomains(domainId, {
        min_consistency: useOverride ? overrideValue : null,
        dry_run: dryRun,
      });
      // Successful non-dry creates → invalidate readiness so the panel
      // re-renders. Idempotent re-runs (created=[]) skip the refresh —
      // nothing changed.
      if (!dryRun && result.created.length > 0) {
        readinessStore.invalidate();
      }
    } catch (err) {
      error = err instanceof Error ? err.message : 'Rebuild failed';
    } finally {
      busy = false;
    }
  }

  function handleOverlayClick(e: MouseEvent) {
    if (e.target === e.currentTarget && !busy) onClose();
  }

  function handleKeyDown(e: KeyboardEvent) {
    if (e.key === 'Escape' && !busy) onClose();
  }

  const titleLabel = $derived(
    domainLabel ?? (domainId ? `${domainId.slice(0, 8)}…` : ''),
  );
</script>

<svelte:window onkeydown={handleKeyDown} />

{#if domainId !== null}
  <!-- svelte-ignore a11y_click_events_have_key_events a11y_no_static_element_interactions -->
  <div
    class="rsd-overlay"
    onclick={handleOverlayClick}
    role="dialog"
    aria-modal="true"
    aria-labelledby="rsd-title"
    tabindex="-1"
  >
    <div class="rsd-modal">
      <div class="rsd-header">
        <span class="rsd-title" id="rsd-title">REBUILD SUB-DOMAINS</span>
        <button
          type="button"
          class="rsd-close"
          onclick={onClose}
          disabled={busy}
          aria-label="Close"
        >×</button>
      </div>

      <div class="rsd-body">
        <div class="rsd-field rsd-field--row">
          <span class="rsd-label">DOMAIN</span>
          <span class="rsd-value">{titleLabel}</span>
        </div>

        <!-- Threshold override toggle + slider -->
        <div class="rsd-field">
          <label class="rsd-toggle">
            <input
              type="checkbox"
              class="rsd-checkbox"
              bind:checked={useOverride}
              disabled={busy}
            />
            <span class="rsd-toggle-text">
              OVERRIDE THRESHOLD
              <span class="rsd-toggle-hint">
                (default: adaptive max(0.40, 0.60−0.004·N))
              </span>
            </span>
          </label>
          {#if useOverride}
            <div class="rsd-slider-row">
              <input
                id="rsd-threshold"
                type="range"
                class="rsd-slider"
                min={REBUILD_MIN_CONSISTENCY_FLOOR}
                max="1.0"
                step="0.05"
                bind:value={overrideValue}
                disabled={busy}
                aria-label="Minimum consistency override"
              />
              <span class="rsd-slider-val">{overrideValue.toFixed(2)}</span>
            </div>
            <span class="rsd-floor-hint">
              Floor 0.25 = SUB_DOMAIN_DISSOLUTION_CONSISTENCY_FLOOR — sub-domains created
              at or below would dissolve on next Phase 5.
            </span>
          {/if}
        </div>

        <!-- Dry-run toggle -->
        <div class="rsd-field">
          <label class="rsd-toggle">
            <input
              type="checkbox"
              class="rsd-checkbox"
              bind:checked={dryRun}
              disabled={busy}
            />
            <span class="rsd-toggle-text">
              DRY RUN
              <span class="rsd-toggle-hint">
                (preview proposals without mutating state)
              </span>
            </span>
          </label>
        </div>

        {#if error}
          <div class="rsd-error" role="alert">{error}</div>
        {/if}

        {#if result}
          {@const empty = result.proposed.length === 0 && result.created.length === 0 && result.skipped_existing.length === 0}
          <div
            class="rsd-result"
            class:rsd-result--dry={result.dry_run}
            class:rsd-result--applied={!result.dry_run && result.created.length > 0}
            class:rsd-result--noop={empty}
          >
            <div class="rsd-result-header">
              <span class="rsd-result-status">
                {#if result.dry_run}
                  DRY RUN
                {:else if result.created.length > 0}
                  APPLIED
                {:else}
                  NO-OP
                {/if}
              </span>
              <span class="rsd-result-thr">
                threshold = <span class="rsd-mono">{result.threshold_used.toFixed(2)}</span>
              </span>
            </div>
            <div class="rsd-result-grid">
              <div class="rsd-stat">
                <span class="rsd-stat-val">{result.proposed.length}</span>
                <span class="rsd-stat-label">proposed</span>
              </div>
              <div class="rsd-stat">
                <span class="rsd-stat-val rsd-stat-val--accent">{result.created.length}</span>
                <span class="rsd-stat-label">{result.dry_run ? 'would create' : 'created'}</span>
              </div>
              <div class="rsd-stat">
                <span class="rsd-stat-val rsd-stat-val--dim">{result.skipped_existing.length}</span>
                <span class="rsd-stat-label">skipped</span>
              </div>
            </div>
            {#if result.proposed.length > 0}
              <div class="rsd-tags">
                {#each result.proposed as label}
                  <span
                    class="rsd-tag"
                    class:rsd-tag--created={!result.dry_run && result.created.includes(label)}
                    class:rsd-tag--skipped={result.skipped_existing.includes(label)}
                  >{label}</span>
                {/each}
              </div>
            {/if}
          </div>
        {/if}
      </div>

      <div class="rsd-footer">
        <button
          type="button"
          class="rsd-btn rsd-btn--secondary"
          onclick={onClose}
          disabled={busy}
        >Close</button>
        <button
          type="button"
          class="rsd-btn rsd-btn--primary"
          class:rsd-btn--busy={busy}
          onclick={handleRebuild}
          disabled={busy}
        >
          {#if busy}
            REBUILDING…
          {:else if dryRun}
            PREVIEW
          {:else}
            REBUILD
          {/if}
        </button>
      </div>
    </div>
  </div>
{/if}

<style>
  /*
   * Brand grammar: 1px contours, no glow/shadow, ultra-compact density.
   * Neon palette: cyan = primary action, yellow = busy, green = applied,
   * red = error. Zero `box-shadow` with blur/spread anywhere.
   */
  .rsd-overlay {
    position: fixed;
    inset: 0;
    z-index: 9999;
    display: flex;
    align-items: center;
    justify-content: center;
    background: color-mix(in srgb, var(--color-bg-primary) 70%, transparent);
  }

  .rsd-modal {
    background: var(--color-bg-secondary);
    border: 1px solid var(--color-border-subtle);
    max-width: 440px;
    width: 90vw;
    max-height: 80vh;
    display: flex;
    flex-direction: column;
    font-family: var(--font-mono);
  }

  /* Header */
  .rsd-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 8px 12px;
    border-bottom: 1px solid var(--color-border-subtle);
    flex-shrink: 0;
  }

  .rsd-title {
    font-family: var(--font-display);
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    color: var(--color-neon-cyan);
  }

  .rsd-close {
    background: transparent;
    border: none;
    color: var(--color-text-secondary);
    font-size: 14px;
    cursor: pointer;
    padding: 0 4px;
    line-height: 1;
    font-family: var(--font-mono);
    transition: color var(--duration-micro) var(--ease-spring);
  }

  .rsd-close:hover:not(:disabled) {
    color: var(--color-text-primary);
  }

  .rsd-close:disabled {
    opacity: 0.4;
    cursor: not-allowed;
  }

  /* Body */
  .rsd-body {
    flex: 1;
    overflow-y: auto;
    padding: 12px;
    display: flex;
    flex-direction: column;
    gap: 10px;
  }

  .rsd-field {
    display: flex;
    flex-direction: column;
    gap: 4px;
  }

  .rsd-field--row {
    flex-direction: row;
    justify-content: space-between;
    align-items: baseline;
    padding: 4px 6px;
    background: var(--color-bg-input);
    border: 1px solid var(--color-border-subtle);
  }

  .rsd-label {
    font-size: 10px;
    font-weight: 600;
    letter-spacing: 0.08em;
    color: var(--color-text-dim);
    text-transform: uppercase;
  }

  .rsd-value {
    font-size: 11px;
    color: var(--color-text-primary);
    font-family: var(--font-mono);
  }

  /* Toggles */
  .rsd-toggle {
    display: flex;
    align-items: flex-start;
    gap: 6px;
    padding: 4px 6px;
    cursor: pointer;
    border: 1px solid transparent;
    transition: border-color var(--duration-micro) var(--ease-spring);
  }

  .rsd-toggle:hover {
    border-color: var(--color-border-subtle);
  }

  .rsd-checkbox {
    margin-top: 2px;
    accent-color: var(--color-neon-cyan);
    flex-shrink: 0;
    cursor: pointer;
  }

  .rsd-toggle-text {
    display: flex;
    flex-direction: column;
    font-size: 10px;
    font-weight: 600;
    letter-spacing: 0.06em;
    color: var(--color-text-primary);
    text-transform: uppercase;
    gap: 2px;
  }

  .rsd-toggle-hint {
    font-size: 9px;
    font-weight: 400;
    letter-spacing: 0;
    color: var(--color-text-dim);
    text-transform: none;
  }

  /* Slider */
  .rsd-slider-row {
    display: flex;
    align-items: center;
    gap: 8px;
    padding-left: 18px;
  }

  .rsd-slider {
    flex: 1;
    accent-color: var(--color-neon-cyan);
    cursor: pointer;
  }

  .rsd-slider:disabled {
    opacity: 0.4;
    cursor: not-allowed;
  }

  .rsd-slider-val {
    font-size: 10px;
    color: var(--color-neon-cyan);
    font-weight: 600;
    min-width: 30px;
    text-align: right;
  }

  .rsd-floor-hint {
    font-size: 9px;
    color: var(--color-text-dim);
    padding-left: 18px;
    line-height: 1.4;
  }

  /* Error */
  .rsd-error {
    font-size: 10px;
    color: var(--color-neon-red);
    border: 1px solid color-mix(in srgb, var(--color-neon-red) 40%, transparent);
    padding: 6px 8px;
    line-height: 1.4;
  }

  /* Result */
  .rsd-result {
    border: 1px solid var(--color-border-subtle);
    background: var(--color-bg-input);
    padding: 8px;
    display: flex;
    flex-direction: column;
    gap: 6px;
  }

  .rsd-result--dry {
    border-color: color-mix(in srgb, var(--color-neon-cyan) 40%, transparent);
  }

  .rsd-result--applied {
    border-color: color-mix(in srgb, var(--color-neon-green) 40%, transparent);
  }

  .rsd-result--noop {
    border-color: color-mix(in srgb, var(--color-text-dim) 40%, transparent);
  }

  .rsd-result-header {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
  }

  .rsd-result-status {
    font-family: var(--font-display);
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    color: var(--color-text-primary);
  }

  .rsd-result--dry .rsd-result-status {
    color: var(--color-neon-cyan);
  }

  .rsd-result--applied .rsd-result-status {
    color: var(--color-neon-green);
  }

  .rsd-result--noop .rsd-result-status {
    color: var(--color-text-dim);
  }

  .rsd-result-thr {
    font-size: 9px;
    color: var(--color-text-dim);
  }

  .rsd-mono {
    font-family: var(--font-mono);
    color: var(--color-text-secondary);
  }

  .rsd-result-grid {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 6px;
  }

  .rsd-stat {
    display: flex;
    flex-direction: column;
    align-items: center;
    padding: 4px 0;
    border: 1px solid color-mix(in srgb, var(--color-border-subtle) 60%, transparent);
  }

  .rsd-stat-val {
    font-size: 14px;
    font-weight: 700;
    color: var(--color-text-primary);
    line-height: 1;
  }

  .rsd-stat-val--accent {
    color: var(--color-neon-green);
  }

  .rsd-stat-val--dim {
    color: var(--color-text-dim);
  }

  .rsd-stat-label {
    font-size: 8px;
    letter-spacing: 0.05em;
    color: var(--color-text-dim);
    text-transform: uppercase;
    margin-top: 2px;
  }

  /* Tags */
  .rsd-tags {
    display: flex;
    flex-wrap: wrap;
    gap: 4px;
  }

  .rsd-tag {
    font-size: 9px;
    font-family: var(--font-mono);
    padding: 1px 5px;
    border: 1px solid var(--color-border-subtle);
    color: var(--color-text-secondary);
    height: 14px;
    display: inline-flex;
    align-items: center;
  }

  .rsd-tag--created {
    border-color: color-mix(in srgb, var(--color-neon-green) 40%, transparent);
    color: var(--color-neon-green);
  }

  .rsd-tag--skipped {
    border-color: color-mix(in srgb, var(--color-text-dim) 40%, transparent);
    color: var(--color-text-dim);
  }

  /* Footer */
  .rsd-footer {
    display: flex;
    justify-content: flex-end;
    gap: 6px;
    padding: 8px 12px;
    border-top: 1px solid var(--color-border-subtle);
    flex-shrink: 0;
  }

  .rsd-btn {
    font-family: var(--font-mono);
    font-size: 10px;
    font-weight: 600;
    letter-spacing: 0.08em;
    padding: 0 10px;
    height: 20px;
    background: transparent;
    cursor: pointer;
    border: 1px solid var(--color-border-subtle);
    color: var(--color-text-secondary);
    text-transform: uppercase;
    transition: color var(--duration-micro) var(--ease-spring),
      border-color var(--duration-micro) var(--ease-spring);
  }

  .rsd-btn--secondary:hover:not(:disabled) {
    color: var(--color-text-primary);
    border-color: var(--color-text-secondary);
  }

  .rsd-btn--primary {
    color: var(--color-neon-cyan);
    border-color: color-mix(in srgb, var(--color-neon-cyan) 50%, transparent);
  }

  .rsd-btn--primary:hover:not(:disabled) {
    color: var(--color-neon-cyan);
    border-color: var(--color-neon-cyan);
  }

  .rsd-btn--busy {
    color: var(--color-neon-yellow);
    border-color: color-mix(in srgb, var(--color-neon-yellow) 60%, transparent);
  }

  .rsd-btn:disabled {
    opacity: 0.4;
    cursor: not-allowed;
  }

  .rsd-btn:focus-visible {
    outline: 1px solid color-mix(in srgb, var(--color-neon-cyan) 40%, transparent);
    outline-offset: -1px;
  }

  @media (prefers-reduced-motion: reduce) {
    .rsd-close,
    .rsd-toggle,
    .rsd-btn {
      transition: none;
    }
  }
</style>
