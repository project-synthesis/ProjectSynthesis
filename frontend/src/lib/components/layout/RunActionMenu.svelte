<!-- frontend/src/lib/components/layout/RunActionMenu.svelte -->
<!--
  v0.4.32 — kebab-triggered popover with Rename + Delete items.
  v0.4.39 (R-04 / R-03 / R-06 / R-19) — animation + transition tokens,
  z-index token (--z-dropdown), :focus-visible + :active states on
  menu-items, first-item auto-focus on mount, reduced-motion override.
-->
<script lang="ts">
  import { onMount } from 'svelte';

  interface Props {
    onRename: () => void;
    onDelete: () => void;
    onClose: () => void;
  }
  let { onRename, onDelete, onClose }: Props = $props();

  let menuEl: HTMLDivElement | undefined = $state();
  let firstItemEl: HTMLButtonElement | undefined = $state();

  onMount(() => {
    // RU-024 / RU-025 — auto-focus the first menuitem on open so keyboard
    // users land directly in the menu instead of having to Tab into it.
    queueMicrotask(() => firstItemEl?.focus());

    function handleKey(e: KeyboardEvent) {
      if (e.key === 'Escape') {
        e.preventDefault();
        onClose();
      }
    }
    function handleClick(e: MouseEvent) {
      // Outside-click closes the popover. Skip when the click target is
      // inside the menu OR matches the kebab trigger (the kebab's onclick
      // sets menuOpen=true and we shouldn't immediately close it again).
      const target = e.target as Node | null;
      if (target && menuEl && menuEl.contains(target)) return;
      // The capture-phase listener fires BEFORE the kebab button's
      // synchronous re-open, so we delay a tick to let same-tick events settle.
      queueMicrotask(() => onClose());
    }
    document.addEventListener('keydown', handleKey);
    // Use the capture phase so we observe clicks before stopPropagation
    // anywhere else in the tree could swallow them.
    document.addEventListener('mousedown', handleClick, true);
    return () => {
      document.removeEventListener('keydown', handleKey);
      document.removeEventListener('mousedown', handleClick, true);
    };
  });
</script>

<div
  bind:this={menuEl}
  class="run-action-menu"
  role="menu"
  aria-label="Run actions"
>
  <button
    bind:this={firstItemEl}
    type="button"
    class="menu-item"
    role="menuitem"
    onclick={onRename}
  >Rename</button>
  <button
    type="button"
    class="menu-item menu-item-danger"
    role="menuitem"
    onclick={onDelete}
  >Delete</button>
</div>

<style>
  .run-action-menu {
    /* Anchor to the bottom-right of the kebab's positioned parent
       (`.run-row-content` has `position: relative`). The menu drops
       directly below the kebab and aligns to the row's right edge. */
    position: absolute;
    top: 100%;
    right: 0;
    /* R-03 / R-14 — z-index token for the dropdown tier. */
    z-index: var(--z-dropdown);
    min-width: 96px;
    background: var(--color-bg-card);
    border: 1px solid var(--color-border-subtle);
    /* Flat per layout-and-accessibility.md "Any border-radius on sidebar
       elements ... is banned". No rounding. */
    border-radius: 0;
    padding: 2px 0;
    /* R-04 — animation runs on the spring/hover token tuple. */
    animation: dropdown-enter var(--duration-hover) var(--ease-spring);
  }
  .menu-item {
    display: block;
    width: 100%;
    text-align: left;
    /* Tight ultra-compact density: same 20px height as other panel chrome
       (select-toggle, btn-icon, chip), 8px horizontal padding mirrors
       .btn-outline-secondary. */
    height: 20px;
    padding: 0 8px;
    background: transparent;
    border: none;
    border-radius: 0;
    cursor: pointer;
    color: var(--color-text-primary);
    font-family: var(--font-sans);
    font-size: 11px;
    line-height: 20px;
    /* R-04 — transition tokens drive background + color on hover. */
    transition: background-color var(--duration-hover) var(--ease-spring),
                color var(--duration-hover) var(--ease-spring);
  }
  .menu-item:hover {
    background: color-mix(in srgb, var(--color-neon-cyan) 8%, transparent);
  }
  .menu-item:focus-visible {
    outline: 1px solid var(--color-focus-ring);
    outline-offset: var(--focus-offset-inset);
  }
  .menu-item:active {
    box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--color-neon-cyan) 40%, transparent);
  }
  .menu-item-danger { color: var(--color-neon-red); }
  .menu-item-danger:hover {
    background: color-mix(in srgb, var(--color-neon-red) 8%, transparent);
  }
  .menu-item-danger:active {
    box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--color-neon-red) 40%, transparent);
  }

  /* R-19 — reduced-motion scoped override. */
  @media (prefers-reduced-motion: reduce) {
    .run-action-menu,
    .menu-item {
      transition: none !important;
      animation: none !important;
    }
  }
</style>
