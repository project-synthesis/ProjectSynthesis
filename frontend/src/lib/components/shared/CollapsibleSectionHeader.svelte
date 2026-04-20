<script lang="ts">
  /**
   * CollapsibleSectionHeader — shared 20px-height bar used by every
   * collapsible region in the navigator sidebar (readiness, templates,
   * per-domain groups). Two render modes:
   *
   *   Whole-bar mode (default): the entire bar is a single toggle button.
   *     Caret + label (+ count) on the left, `actions` snippet on the right.
   *     The actions snippet sits OUTSIDE the toggle so its clicks do not
   *     bubble as collapse-toggles (e.g., SYNC button on readiness).
   *
   *   Split mode: `header` snippet replaces the built-in label region and
   *     owns its own click handler. The caret is a discrete button. Used
   *     for per-domain groups where the header both toggles collapse AND
   *     toggles the domain-highlight independently.
   *
   * Brand rules: 1px contours only, no glow/shadow. Caret is a single `▸`
   * glyph rotated via CSS transform when open — GPU-accelerated, zero
   * layout jitter. All typography comes from brand tokens.
   */
  import type { Snippet } from 'svelte';

  interface Props {
    /** Controlled open state — parent owns truth, this component only dispatches. */
    open: boolean;
    /** Fired when the user activates the toggle (bar in whole-bar mode, caret in split mode). */
    onToggle: () => void;
    /** Rendered when `header` snippet absent. */
    label?: string;
    /** Optional compact count (e.g., member count, `+N more`). */
    count?: number | string;
    /** Split mode — replaces the built-in label region entirely. */
    header?: Snippet;
    /** Trailing controls (whole-bar mode); sits outside the toggle button. */
    actions?: Snippet;
    /** Override the auto-composed aria-label. */
    ariaLabel?: string;
  }

  let { open, onToggle, label, count, header, actions, ariaLabel }: Props = $props();

  const splitMode = $derived(header !== undefined);
  const composedAriaLabel = $derived(
    ariaLabel ?? (label ? `Toggle ${label}` : 'Toggle section'),
  );

  // `all: unset` on our buttons drops the implicit keyboard activation
  // (Enter/Space) browsers attach to real <button>s, so we re-add it.
  function handleKeydown(event: KeyboardEvent) {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      onToggle();
    }
  }
</script>

<div class="nsh-bar" class:nsh-bar--split={splitMode}>
  {#if splitMode}
    <button
      type="button"
      class="nsh-caret-btn"
      onclick={onToggle}
      onkeydown={handleKeydown}
      aria-expanded={open}
      aria-label={composedAriaLabel}
    >
      <span class="nsh-caret" class:nsh-caret--open={open} aria-hidden="true">▸</span>
    </button>
    <div class="nsh-header-slot">
      {@render header!()}
    </div>
  {:else}
    <button
      type="button"
      class="nsh-toggle"
      onclick={onToggle}
      onkeydown={handleKeydown}
      aria-expanded={open}
      aria-label={composedAriaLabel}
    >
      <span class="nsh-caret" class:nsh-caret--open={open} aria-hidden="true">▸</span>
      {#if label}
        <span class="nsh-label">{label}</span>
      {/if}
      {#if count != null}
        <span class="nsh-count">{count}</span>
      {/if}
    </button>
    {#if actions}
      <div class="nsh-actions" role="group">
        {@render actions()}
      </div>
    {/if}
  {/if}
</div>

<style>
  .nsh-bar {
    display: flex;
    align-items: center;
    gap: 4px;
    height: 20px;
    padding: 0 6px;
    box-sizing: border-box;
    border-bottom: 1px solid var(--color-border-subtle);
  }

  .nsh-bar--split {
    padding-left: 0;
  }

  .nsh-toggle {
    all: unset;
    display: flex;
    align-items: center;
    gap: 6px;
    flex: 1;
    min-width: 0;
    height: 100%;
    cursor: pointer;
  }

  /*
    Hover affordance is scoped to the actual interactive elements (`.nsh-toggle`
    and `.nsh-caret-btn`) so non-interactive header-slot content in split mode
    — e.g., a sub-domain label row that has no secondary click action — does
    NOT light up on hover. Consumer-provided buttons inside the `header`
    snippet own their own hover state.
  */
  .nsh-toggle:hover {
    background: color-mix(in srgb, var(--color-bg-hover) 40%, transparent);
  }

  .nsh-toggle:hover .nsh-label {
    color: var(--color-text-primary);
  }

  .nsh-toggle:focus-visible,
  .nsh-caret-btn:focus-visible {
    outline: 1px solid color-mix(in srgb, var(--color-neon-cyan) 30%, transparent);
    outline-offset: -1px;
  }

  .nsh-caret-btn {
    all: unset;
    display: flex;
    align-items: center;
    justify-content: center;
    width: 18px;
    height: 100%;
    flex-shrink: 0;
    cursor: pointer;
  }

  .nsh-caret-btn:hover {
    background: color-mix(in srgb, var(--color-bg-hover) 40%, transparent);
  }

  .nsh-caret-btn:hover .nsh-caret {
    color: var(--color-text-primary);
  }

  .nsh-header-slot {
    display: flex;
    align-items: center;
    gap: 4px;
    flex: 1;
    min-width: 0;
    height: 100%;
  }

  .nsh-caret {
    font-family: var(--font-mono);
    font-size: 9px;
    color: var(--color-text-dim);
    width: 8px;
    display: inline-flex;
    justify-content: center;
    transform-origin: center;
    transition: transform var(--duration-micro) var(--ease-spring);
  }

  .nsh-caret--open {
    transform: rotate(90deg);
  }

  .nsh-label {
    font-family: var(--font-display, var(--font-sans));
    font-size: 11px;
    font-weight: 700;
    color: var(--color-text-primary);
    text-transform: uppercase;
    letter-spacing: 0.1em;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .nsh-count {
    font-family: var(--font-mono);
    font-size: 9px;
    color: var(--color-text-dim);
    letter-spacing: 0.05em;
    flex-shrink: 0;
    margin-left: auto;
  }

  .nsh-actions {
    display: flex;
    align-items: center;
    gap: 4px;
    flex-shrink: 0;
  }

  @media (prefers-reduced-motion: reduce) {
    .nsh-bar {
      transition: none;
    }
  }
</style>
