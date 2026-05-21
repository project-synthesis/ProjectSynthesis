<!-- frontend/src/lib/components/layout/RunActionMenu.svelte -->
<!--
  v0.4.32 — kebab-triggered popover with Rename + Delete items.
  RED phase: structural skeleton only. ESC keydown + focus return wiring
  lands in GREEN.
-->
<script lang="ts">
  import { onMount } from 'svelte';

  interface Props {
    onRename: () => void;
    onDelete: () => void;
    onClose: () => void;
  }
  let { onRename, onDelete, onClose }: Props = $props();

  onMount(() => {
    function handleKey(e: KeyboardEvent) {
      if (e.key === 'Escape') {
        e.preventDefault();
        onClose();
      }
    }
    document.addEventListener('keydown', handleKey);
    return () => document.removeEventListener('keydown', handleKey);
  });
</script>

<div
  class="run-action-menu"
  role="menu"
  aria-label="Run actions"
>
  <button class="menu-item btn-icon" role="menuitem" onclick={onRename}>Rename</button>
  <button class="menu-item btn-icon menu-item-danger" role="menuitem" onclick={onDelete}>Delete</button>
</div>

<style>
  .run-action-menu {
    position: absolute;
    z-index: 100;
    background: var(--color-bg-card);
    border: 1px solid var(--color-border-subtle);
    padding: 4px 0;
    min-width: 120px;
    animation: dropdown-enter 200ms cubic-bezier(0.16, 1, 0.3, 1);
  }
  .menu-item {
    display: block;
    width: 100%;
    text-align: left;
    padding: 4px 12px;
    font-size: 11px;
    height: 24px;
  }
  .menu-item:hover {
    background: color-mix(in srgb, var(--color-neon-cyan) 8%, transparent);
  }
  .menu-item-danger { color: var(--color-neon-red); }
</style>
