<!-- frontend/src/lib/components/suites/SuiteActionMenu.svelte -->
<!--
  v0.4.39 — kebab-triggered popover for SuiteRow with Rename / Retire / Delete
  items. Mirrors the API of `layout/RunActionMenu.svelte` (Rename + Delete +
  Close) plus a Retire item for suite-specific lifecycle. ESC + outside-click
  dismiss the menu; capture-phase mousedown observation matches the RunActionMenu
  contract so per-row kebabs reopen without flicker.
-->
<script lang="ts">
  import { onMount } from 'svelte';

  interface Props {
    onRename: () => void;
    onRetire: () => void;
    onDelete: () => void;
    onClose: () => void;
  }
  let { onRename, onRetire, onDelete, onClose }: Props = $props();

  let menuEl: HTMLDivElement | undefined = $state();

  onMount(() => {
    function handleKey(e: KeyboardEvent) {
      if (e.key === 'Escape') {
        e.preventDefault();
        onClose();
      }
    }
    function handleClick(e: MouseEvent) {
      // Outside-click closes the popover. Skip when the click target is
      // inside the menu — same defensive pattern as RunActionMenu.
      const target = e.target as Node | null;
      if (target && menuEl && menuEl.contains(target)) return;
      queueMicrotask(() => onClose());
    }
    document.addEventListener('keydown', handleKey);
    document.addEventListener('mousedown', handleClick, true);
    return () => {
      document.removeEventListener('keydown', handleKey);
      document.removeEventListener('mousedown', handleClick, true);
    };
  });
</script>

<div
  bind:this={menuEl}
  class="suite-action-menu"
  role="menu"
  aria-label="Suite actions"
  data-test="suite-action-menu"
>
  <button
    type="button"
    class="menu-item"
    role="menuitem"
    data-test="suite-menu-rename"
    onclick={onRename}
  >Rename</button>
  <button
    type="button"
    class="menu-item"
    role="menuitem"
    data-test="suite-menu-retire"
    onclick={onRetire}
  >Retire</button>
  <button
    type="button"
    class="menu-item menu-item-danger"
    role="menuitem"
    data-test="suite-menu-delete"
    onclick={onDelete}
  >Delete</button>
</div>

<style>
  .suite-action-menu {
    /* Anchored by the kebab button's positioned parent (SuiteRow). The menu
       drops directly below the kebab and aligns to the row's right edge.
       Z-index uses the shared dropdown token so it sits above sibling rows
       without competing with the modal scrim. */
    position: absolute;
    top: 100%;
    right: 0;
    z-index: var(--z-dropdown);
    min-width: 96px;
    background: var(--color-bg-card);
    border: 1px solid var(--color-border-subtle);
    /* Flat per layout-and-accessibility.md — sidebar elements never round. */
    border-radius: 0;
    padding: 2px 0;
  }
  .menu-item {
    display: block;
    width: 100%;
    text-align: left;
    /* Tight ultra-compact density: 20px row height matches sibling chrome
       (btn-icon, chip, action-btn) plus 8px horizontal padding aligned with
       .btn-outline-secondary so the menu reads as part of the same density
       grammar. */
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
    transition:
      background-color var(--duration-hover) var(--ease-spring),
      color var(--duration-hover) var(--ease-spring);
  }
  .menu-item:hover {
    background: color-mix(in srgb, var(--color-neon-cyan) 8%, transparent);
  }
  .menu-item-danger { color: var(--color-neon-red); }
  .menu-item-danger:hover {
    background: color-mix(in srgb, var(--color-neon-red) 8%, transparent);
  }
  @media (prefers-reduced-motion: reduce) {
    .menu-item {
      transition: none !important;
    }
  }
</style>
