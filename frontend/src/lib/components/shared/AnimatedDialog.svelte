<script lang="ts">
  /**
   * AnimatedDialog — shared modal scrim + dialog primitive.
   *
   * Pre-2026-05-09 every modal-shaped component (DestructiveConfirmModal,
   * TierGuide, RebuildSubDomainsModal, SeedModal, CommandPalette) re-rolled
   * its own `{#if open}` over a `.scrim` + `.dialog` pair. Two of those
   * animated only the dialog, not the scrim — so the user saw the dialog
   * pop in over a still-empty viewport, then the scrim pop in beneath it.
   *
   * This primitive coordinates both:
   *   - Scrim: fade in via dialogIn (200ms spring), out via dialogOut (150ms exit)
   *   - Dialog: fly+fade from y=8px via dialogIn, out via dialogOut
   *   - ESC handler (when dismissible)
   *   - Click-outside on scrim (when dismissible)
   *   - Body-scroll lock while open
   *
   * The dialog itself is positioned absolutely, centered via translate(-50%,-50%).
   * Content is rendered via the default slot — consumers retain full control
   * over header/body/footer layout.
   *
   * Reduced-motion is enforced globally in app.css with `!important` on the
   * universal selector — the Svelte `transition:` directives inherit it.
   *
   * Copyright 2026 Project Synthesis contributors.
   */
  import type { Snippet } from 'svelte';
  import { onDestroy } from 'svelte';
  import { fade, fly } from 'svelte/transition';
  import { dialogIn, dialogOut, easeSpring, easeExit } from '$lib/utils/transitions';

  interface Props {
    /** Controls visibility. Parent owns the state. */
    open: boolean;
    /** Invoked when the user dismisses (ESC, click-outside) and ``dismissible``
     *  is true. NOT called when parent flips ``open=false`` directly. */
    onClose: () => void;
    /** When false, ESC + click-outside are ignored. Use for in-progress
     *  destructive operations (e.g. while a delete is committing). */
    dismissible?: boolean;
    /** Accessible label / describer for the dialog role. */
    ariaLabel?: string;
    /** ID of the element labelling the dialog (preferred over ariaLabel
     *  when the dialog has a visible title). */
    ariaLabelledby?: string;
    /** Optional CSS class merged onto the dialog container. */
    class?: string;
    /** Default slot — dialog content. */
    children?: Snippet;
  }

  const {
    open,
    onClose,
    dismissible = true,
    ariaLabel,
    ariaLabelledby,
    class: className,
    children,
  }: Props = $props();

  function handleKeyDown(e: KeyboardEvent): void {
    if (!open || !dismissible) return;
    if (e.key === 'Escape') {
      e.preventDefault();
      onClose();
    }
  }

  function handleScrimClick(): void {
    if (!dismissible) return;
    onClose();
  }

  // Mount the global keydown listener while open. Re-runs on `open` flip
  // so we don't keep a listener attached after unmount.
  $effect(() => {
    if (typeof document === 'undefined') return;
    if (!open) return;
    document.addEventListener('keydown', handleKeyDown);
    return () => {
      document.removeEventListener('keydown', handleKeyDown);
    };
  });

  // Body-scroll lock while open. Snapshots the prior `overflow` value at
  // open time and restores it on close so we don't trample whatever the
  // host page had set (e.g. landing routes that lock scroll independently
  // of any modal). Per v0.4.39 risk callout: never restore unconditionally
  // to `'auto'` — that would silently override pre-existing page state.
  $effect(() => {
    if (typeof document === 'undefined') return;
    if (!open) return;
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.body.style.overflow = prevOverflow;
    };
  });

  onDestroy(() => {
    if (typeof document !== 'undefined') {
      document.removeEventListener('keydown', handleKeyDown);
    }
  });
</script>

{#if open}
  <div
    class="ad-scrim"
    role="presentation"
    onclick={handleScrimClick}
    in:fade={dialogIn}
    out:fade={dialogOut}
  ></div>
  <div
    class="ad-dialog {className ?? ''}"
    role="dialog"
    aria-modal="true"
    aria-label={ariaLabel}
    aria-labelledby={ariaLabelledby}
    in:fly={{ y: 8, duration: dialogIn.duration, easing: easeSpring, opacity: 0 }}
    out:fly={{ y: 4, duration: dialogOut.duration, easing: easeExit, opacity: 0 }}
  >
    {@render children?.()}
  </div>
{/if}

<style>
  .ad-scrim {
    position: fixed;
    inset: 0;
    background: var(--color-scrim);
    z-index: var(--z-modal-scrim);
  }

  .ad-dialog {
    position: fixed;
    top: 50%;
    left: 50%;
    transform: translate(-50%, -50%);
    background: var(--color-bg-card);
    border: 1px solid var(--color-border-subtle);
    z-index: var(--z-modal);
    /* Consumers control max-width / padding / inner layout via the default
       slot. The primitive only owns the chrome wrapper + transitions. */
  }
</style>
