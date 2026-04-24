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

  const remainingSeconds = $derived(Math.ceil(remainingMs / 1000));
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
    <span class="countdown">{remainingSeconds}s</span>
  </div>
</div>

<style>
  .undo-toast {
    position: relative;
    width: 320px;
    padding: 6px 8px;
    background: var(--color-bg-glass);
    backdrop-filter: blur(8px);
    border: 1px solid var(--color-border-subtle);
    border-color: rgba(255, 51, 102, 0.4);
    border-radius: 4px;
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
    height: 20px;
    padding: 0 8px;
    background: transparent;
    border: 1px solid transparent;
    color: var(--color-neon-red);
    font-family: var(--font-sans);
    font-size: 10px;
    font-weight: 500;
    border-radius: 4px;
    cursor: pointer;
    transition: background 200ms var(--ease-spring), border-color 200ms var(--ease-spring);
  }
  .undo-btn:hover {
    background: color-mix(in srgb, var(--color-neon-red) 12%, transparent);
    border-color: rgba(255, 51, 102, 0.4);
  }
  .undo-btn:active {
    transform: translateY(0);
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
    flex: 1;
    height: 1px;
    background: var(--color-neon-red);
    transform-origin: left center;
    transition: transform 16ms linear;
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

  @media (prefers-reduced-motion: reduce) {
    .undo-btn { transition-duration: 0.01ms; }
    .progress-bar { transition: none; }
  }
</style>
