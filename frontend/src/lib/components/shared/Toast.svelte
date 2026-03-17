<script lang="ts">
  import { toastStore } from '$lib/stores/toast.svelte';
</script>

{#if toastStore.toasts.length > 0}
  <div class="toast-container" aria-live="polite">
    {#each toastStore.toasts as toast (toast.id)}
      <div class="toast-item">
        <span class="toast-symbol" style="color: {toast.color};">{toast.symbol}</span>
        <span class="toast-message">{toast.message}</span>
      </div>
    {/each}
  </div>
{/if}

<style>
  .toast-container {
    position: fixed;
    bottom: 28px;
    right: 8px;
    z-index: 50;
    display: flex;
    flex-direction: column;
    gap: 4px;
    pointer-events: none;
  }

  .toast-item {
    display: flex;
    align-items: center;
    gap: 6px;
    height: 22px;
    padding: 0 8px;
    background: var(--color-bg-card);
    border: 1px solid var(--color-border-subtle);
    font-family: var(--font-mono);
    font-size: 10px;
    color: var(--color-text-dim);
    animation: toast-in 300ms cubic-bezier(0.16, 1, 0.3, 1) forwards;
    pointer-events: auto;
  }

  .toast-symbol {
    font-weight: 700;
    flex-shrink: 0;
  }

  .toast-message {
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  @keyframes toast-in {
    from {
      opacity: 0;
      transform: translateY(8px);
    }
    to {
      opacity: 1;
      transform: translateY(0);
    }
  }

  @media (prefers-reduced-motion: reduce) {
    .toast-item {
      animation-duration: 0.01ms;
    }
  }
</style>
