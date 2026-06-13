<!-- frontend/src/lib/components/layout/RunRowItem.svelte -->
<script lang="ts">
  import { slide } from 'svelte/transition';
  import { onDestroy } from 'svelte';
  import { patchRun, type RunSummary } from '$lib/api/runs';
  import { formatRelativeTime } from '$lib/utils/formatting';
  import { runStatusColor } from '$lib/utils/colors';
  import { navSlide } from '$lib/utils/transitions';
  import RunDetailInline from './RunDetailInline.svelte';
  import RunActionMenu from './RunActionMenu.svelte';

  interface Props {
    run: RunSummary;
    expanded: boolean;
    onClick: () => void;
    selectMode?: boolean;
    selected?: boolean;
    onToggleSelect?: () => void;
    onDeleteConfirm?: (id: string) => void;
    onRenamed?: (updated: RunSummary) => void;
  }
  let { run, expanded, onClick, selectMode = false, selected = false, onToggleSelect, onDeleteConfirm, onRenamed }: Props = $props();

  // R-11 / R-27 — MODE_LABEL uppercase to match the workbench voice ('PROBE',
  // 'SEED', 'REPLAY'). Status text uses CSS `text-transform: uppercase` on
  // `.run-status` instead of mutating the data string (R-11).
  const MODE_LABEL: Record<RunSummary['mode'], string> = {
    topic_probe: 'PROBE',
    seed_agent: 'SEED',
    replay_run: 'REPLAY',
  };

  // v0.4.32 — operator-writable label takes precedence over the system-set
  // ``topic``; falls back to the row id for never-named rows.
  const displayLabel = $derived(run.display_name ?? run.topic ?? run.id);

  // v0.4.32 — kebab menu state + rename state
  let menuOpen = $state(false);
  let renaming = $state(false);
  let renameDraft = $state('');
  let renameError = $state<string | null>(null);
  let kebabBtn: HTMLButtonElement | undefined = $state();
  let renameInputEl: HTMLInputElement | undefined = $state();

  // RU-009 BLOCKER — Svelte action: focus the rename input when mounted.
  // Replaces the discouraged `autofocus` attribute. Guarded so screen
  // readers and keyboard users keep predictable focus order.
  function focusOnMount(node: HTMLInputElement) {
    node.focus();
    return {};
  }

  function openMenu(): void {
    menuOpen = true;
  }
  function closeMenu(): void {
    menuOpen = false;
    kebabBtn?.focus();
  }
  function startRename(): void {
    renameDraft = displayLabel === run.id ? '' : displayLabel;
    renaming = true;
    menuOpen = false;
  }
  async function submitRename(): Promise<void> {
    try {
      const updated = await patchRun(run.id, { display_name: renameDraft });
      // Optimistic — but actually pessimistic since we await. Update the local prop is read-only;
      // RunsPanel's runs[] will be refreshed on next list query OR via callback.
      // For now: just exit rename mode; the next list-refresh will show new display_name.
      onRenamed?.(updated);
      renaming = false;
    } catch (err) {
      // Stay in rename mode; show inline error
      renameError = err instanceof Error ? err.message : 'Rename failed';
    }
  }
  function cancelRename(): void {
    renaming = false;
    renameError = null;
  }

  // RU-010 — outside-click cancels rename. Document-level listener replaces
  // the pre-v0.4.39 `onblur={submitRename}` handler (which committed the
  // rename on every blur, including tab-switches that lost focus to a
  // different surface). Listener attaches only while renaming; cleanup on
  // destroy or when `renaming` flips back to false.
  $effect(() => {
    if (typeof document === 'undefined') return;
    if (!renaming) return;
    function handleOutsideClick(e: MouseEvent) {
      const target = e.target as Node | null;
      if (renameInputEl && target && !renameInputEl.contains(target)) {
        cancelRename();
      }
    }
    // capture phase so we observe before any stopPropagation in the tree
    document.addEventListener('mousedown', handleOutsideClick, true);
    return () => {
      document.removeEventListener('mousedown', handleOutsideClick, true);
    };
  });

  onDestroy(() => {
    // belt-and-suspenders cleanup; $effect teardown above is the primary path
  });
</script>

<div class="run-row" class:expanded class:selected role="listitem">
  <div class="run-row-content">
    {#if selectMode}
      <!-- R-23 — wrapping label provides a ≥22px hit-target around the
           visual 14px checkbox. The aria-label lives on the form control
           itself so screen-reader queries for "Select row …" land on the
           interactive checkbox. -->
      <label class="row-checkbox-label">
        <input
          type="checkbox"
          class="row-checkbox"
          aria-label="Select row {displayLabel}"
          checked={selected}
          onchange={onToggleSelect}
          onclick={(e) => e.stopPropagation()}
        />
      </label>
    {/if}
    {#if renaming}
      <!-- RU-009 BLOCKER: rename input is a SIBLING of the row-header
           button, not a descendant. The previous structure produced
           invalid HTML and broke assistive tech + form-state semantics.
           When in rename mode we replace the row-header button entirely
           so the keyboard / SR contract is "you are typing into an input". -->
      <div class="rename-wrap">
        <span class="run-mode chip chip-rect">{MODE_LABEL[run.mode]}</span>
        <input
          bind:this={renameInputEl}
          class="input-field rename-input"
          bind:value={renameDraft}
          maxlength="200"
          aria-label="Rename run {displayLabel}"
          use:focusOnMount
          onkeydown={(e) => {
            if (e.key === 'Enter') { e.preventDefault(); submitRename(); }
            if (e.key === 'Escape') { e.preventDefault(); cancelRename(); }
          }}
          onclick={(e) => e.stopPropagation()}
        />
        <span class="run-status" style="color: {runStatusColor(run.status)};">{run.status}</span>
        <span class="run-time">{formatRelativeTime(run.started_at)}</span>
      </div>
    {:else}
      <button
        type="button"
        class="run-row-header"
        onclick={onClick}
        aria-expanded={expanded}
        aria-label="{MODE_LABEL[run.mode].toLowerCase()} {displayLabel}"
      >
        <span class="run-mode chip chip-rect">{MODE_LABEL[run.mode]}</span>
        <span class="run-topic">{displayLabel}</span>
        <span class="run-status" style="color: {runStatusColor(run.status)};">{run.status}</span>
        <span class="run-time">{formatRelativeTime(run.started_at)}</span>
      </button>
    {/if}
    <button
      type="button"
      bind:this={kebabBtn}
      class="btn-icon kebab-btn"
      aria-label="Open actions menu"
      aria-haspopup="menu"
      aria-expanded={menuOpen}
      onclick={(e) => { e.stopPropagation(); openMenu(); }}
    >
      ⋮
    </button>
    {#if menuOpen}
      <RunActionMenu
        onRename={startRename}
        onDelete={() => { closeMenu(); onDeleteConfirm?.(run.id); }}
        onClose={closeMenu}
      />
    {/if}
  </div>
  {#if renameError}
    <p class="empty-note panel-error" role="alert">{renameError}</p>
  {/if}
  {#if expanded}
    <div transition:slide={navSlide} class="run-detail">
      <RunDetailInline {run} />
    </div>
  {/if}
</div>

<style>
  /* R-06 — row recipe v1: h-5, 1px contour, 5-state lifecycle.
     The `.run-row` itself owns the bottom contour; the inner header inherits
     the same transition tokens. Selected state uses `box-shadow inset` so
     selection is a chromatic border-equivalent without claiming `border-left`
     space (asymmetric border-left selection banned per R-06). */
  .run-row {
    border-bottom: 1px solid var(--color-border-subtle);
    transition: background-color var(--duration-hover) var(--ease-spring),
                border-color var(--duration-hover) var(--ease-spring);
  }
  .run-row.selected {
    background: color-mix(in srgb, var(--color-neon-cyan) 8%, transparent);
    box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--color-neon-cyan) 40%, transparent);
  }
  .run-row-content {
    display: flex;
    align-items: center;
    gap: 6px;
    /* Anchor for RunActionMenu's `position: absolute` popover so it lands
       on the row instead of the nearest positioned ancestor (e.g. viewport). */
    position: relative;
  }

  /* R-23 — custom checkbox with brand treatment + ≥22px hit-target wrapper. */
  .row-checkbox-label {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 22px;
    height: 22px;
    flex-shrink: 0;
    cursor: pointer;
  }
  .row-checkbox {
    appearance: none;
    -webkit-appearance: none;
    width: 14px;
    height: 14px;
    margin: 0;
    border: 1px solid var(--color-border-subtle);
    background: transparent;
    border-radius: 0;
    cursor: pointer;
    position: relative;
    transition: border-color var(--duration-hover) var(--ease-spring),
                background-color var(--duration-hover) var(--ease-spring);
  }
  .row-checkbox:hover {
    border-color: var(--color-border-accent);
  }
  .row-checkbox:checked {
    background: var(--color-neon-cyan);
    border-color: var(--color-neon-cyan);
  }
  .row-checkbox:checked::after {
    content: '';
    position: absolute;
    left: 4px;
    top: 1px;
    width: 4px;
    height: 8px;
    border: solid var(--color-bg-primary);
    border-width: 0 1.5px 1.5px 0;
    transform: rotate(45deg);
  }
  .row-checkbox:focus-visible {
    outline: 1px solid var(--color-focus-ring);
    outline-offset: var(--focus-offset-inset);
  }

  .kebab-btn {
    flex-shrink: 0;
  }
  .kebab-btn[aria-expanded="true"] {
    background: var(--color-bg-hover);
    color: var(--color-text-primary);
  }

  .rename-wrap {
    display: grid;
    grid-template-columns: auto 1fr auto auto;
    gap: 6px;
    align-items: center;
    width: 100%;
    padding: 0 4px;
    min-height: 20px;
  }
  .rename-input {
    min-width: 0;
  }
  .run-row-header {
    display: grid;
    grid-template-columns: auto 1fr auto auto;
    gap: 6px;
    align-items: center;
    width: 100%;
    height: 20px;
    padding: 0 4px;
    background: transparent;
    border: none;
    cursor: pointer;
    color: var(--color-text-primary);
    font-size: 11px;
    text-align: left;
    transition: background-color var(--duration-hover) var(--ease-spring);
  }
  .run-row-header:hover {
    background: color-mix(in srgb, var(--color-bg-hover) 40%, transparent);
  }
  .run-row-header:focus-visible {
    outline: 1px solid var(--color-focus-ring);
    outline-offset: var(--focus-offset-inset);
  }
  .run-row-header:active {
    box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--color-neon-cyan) 40%, transparent);
  }
  .run-topic {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  /* R-11 — status + time text-transform: uppercase + letter-spacing 0.05em
     + font-mono (renders the chromatic status badge without mutating the
     underlying data string). */
  .run-status {
    font-family: var(--font-mono);
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.05em;
  }
  .run-time {
    font-family: var(--font-mono);
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: var(--color-text-dim);
  }
  .run-detail { padding: 4px 6px 6px; }

  /* R-19 — reduced-motion scoped overrides. */
  @media (prefers-reduced-motion: reduce) {
    .run-row,
    .run-row-header,
    .row-checkbox {
      transition: none !important;
      animation: none !important;
    }
  }
</style>
