<script lang="ts">
  import { slide } from 'svelte/transition';
  import { onMount, onDestroy, untrack } from 'svelte';
  import { toastsStore, type Toast } from '$lib/stores/toasts.svelte';
  import { navSlide } from '$lib/utils/transitions';

  type Props = { toast: Toast };
  const { toast }: Props = $props();

  // Capture initial duration once — intentional snapshot, not reactive.
  // untrack signals to Svelte that this is a deliberate one-time read.
  const initialDurationMs = untrack(() => toast.durationMs);
  let remainingMs = $state(initialDurationMs);
  let startedAt = Date.now();
  let rafHandle: number | null = null;

  function tick() {
    const elapsed = Date.now() - startedAt;
    remainingMs = Math.max(0, initialDurationMs - elapsed);
    if (remainingMs > 0) {
      rafHandle = requestAnimationFrame(tick);
    }
  }

  onMount(() => {
    rafHandle = requestAnimationFrame(tick);
    if (typeof window !== 'undefined') {
      window.addEventListener('offline', onOffline);
      window.addEventListener('online', onOnline);
    }
  });

  onDestroy(() => {
    if (rafHandle !== null) cancelAnimationFrame(rafHandle);
    if (typeof window !== 'undefined') {
      window.removeEventListener('offline', onOffline);
      window.removeEventListener('online', onOnline);
    }
  });

  function onOffline() {
    toastsStore.pause(toast.id);
    if (rafHandle !== null) cancelAnimationFrame(rafHandle);
  }
  function onOnline() {
    toastsStore.resume(toast.id);
    startedAt = Date.now() - (initialDurationMs - remainingMs);
    rafHandle = requestAnimationFrame(tick);
  }

  function onEnter() {
    toastsStore.pause(toast.id);
    if (rafHandle !== null) cancelAnimationFrame(rafHandle);
  }
  function onLeave() {
    toastsStore.resume(toast.id);
    startedAt = Date.now() - (initialDurationMs - remainingMs);
    rafHandle = requestAnimationFrame(tick);
  }

  function onUndo() {
    // Call undo directly on the prop (works even when toast isn't in store's
    // internal array, e.g. during unit tests) then dismiss via the store.
    toast.undo?.();
    toastsStore.dismiss(toast.id);
  }

  // Clamp the displayed countdown at 1s so the transition from "1" → dismiss
  // doesn't flash "0s" during the final animation frames.
  const remainingSeconds = $derived(Math.max(1, Math.ceil(remainingMs / 1000)));
  const progressScale = $derived(remainingMs / initialDurationMs);
</script>

<div
  class="undo-toast"
  data-testid="undo-toast"
  role="status"
  aria-live="polite"
  onmouseenter={onEnter}
  onmouseleave={onLeave}
  transition:slide={navSlide}
>
  <div class="row row-primary">
    <span class="message">{toast.message}</span>
    <button class="undo-btn" onclick={onUndo} aria-label="Undo delete">Undo</button>
  </div>
  {#if toast.meta}
    <div class="row meta">{toast.meta}</div>
  {/if}
  <div class="row progress-row">
    <div class="progress-bar" style="transform: scaleX({progressScale})"></div>
    <!-- AT-hidden: live-region announcement of the whole toast on mount is
         enough; we don't want each second re-announced as state changes. -->
    <span class="countdown" aria-hidden="true">{remainingSeconds}s</span>
  </div>
</div>

<style>
  .undo-toast {
    position: relative;
    width: 320px;
    padding: 6px;
    background: var(--color-bg-glass);
    backdrop-filter: blur(8px);
    /* Neon tube model: uniform 1px border, single declaration (no double
       `border` + `border-color` override). Red at 40% alpha signals the
       pending-destructive surface context. */
    border: 1px solid color-mix(in srgb, var(--color-neon-red) 40%, transparent);
    /* Brand: flat edges are the default for everything. */
    border-radius: 0;
    font-family: var(--font-sans);
    color: var(--color-text-primary);
    display: flex;
    flex-direction: column;
    gap: 2px;
  }
  .row {
    display: flex;
    align-items: center;
  }
  .row-primary {
    height: 20px;
    gap: 6px;
    justify-content: space-between;
  }
  .message {
    font-size: 12px;
    font-weight: 400;
  }
  .undo-btn {
    /* Cyan, not red: Undo is the SAFE primary action inside an already-red
       destructive context. Clicking Undo rescues data from deletion, so it
       takes the brand's "primary action" chromatic encoding. The toast's
       outer red border carries the "destructive context" signal. */
    height: 20px;
    padding: 0 8px;
    line-height: 18px;
    background: transparent;
    border: 1px solid transparent;
    border-radius: 0;
    color: var(--color-neon-cyan);
    font-family: var(--font-sans);
    font-size: 10px;
    font-weight: 500;
    cursor: pointer;
    transition: background 200ms var(--ease-spring), border-color 200ms var(--ease-spring);
  }
  .undo-btn:hover {
    background: color-mix(in srgb, var(--color-neon-cyan) 12%, transparent);
    border-color: color-mix(in srgb, var(--color-neon-cyan) 40%, transparent);
  }
  .undo-btn:active {
    /* Brand: Active is a contraction — border mutes back toward subtle. */
    border-color: var(--color-border-subtle);
  }
  .undo-btn:focus-visible {
    outline: 1px solid rgba(0, 229, 255, 0.3);
    outline-offset: 2px;
  }
  .meta {
    height: 18px;
    font-size: 10px;
    color: var(--color-text-dim);
  }
  .progress-row {
    height: 16px;
    gap: 6px;
  }
  .progress-bar {
    /* Countdown visualisation. RAF updates transform each frame via
       `progressScale` — no CSS transition needed (it would be ~0-ms-
       effective anyway, but the extra compositor work is avoidable). */
    flex: 1;
    height: 1px;
    background: var(--color-neon-red);
    transform-origin: left center;
    will-change: transform;
  }
  .countdown {
    font-family: var(--font-mono);
    font-size: 10px;
    font-weight: 500;
    color: var(--color-text-secondary);
    min-width: 20px;
    text-align: right;
    font-variant-numeric: tabular-nums;
  }

  /* Reduced-motion is handled globally in app.css via the universal
     `*` selector with `!important`, so no component-local override
     is needed — keeping one here would just be dead weight. */
</style>
