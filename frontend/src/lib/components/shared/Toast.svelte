<script lang="ts">
  import { toastStore, type ToastActionButton, type ToastItem } from '$lib/stores/toast.svelte';

  async function runAction(toast: ToastItem, action: ToastActionButton): Promise<void> {
    try {
      await action.onClick();
    } finally {
      toastStore.dismiss(toast.id);
    }
  }
</script>

{#if toastStore.toasts.length > 0}
  <div class="toast-container" aria-live="polite">
    {#each toastStore.toasts as toast (toast.id)}
      <div class="toast-item" class:toast-with-actions={toast.actions && toast.actions.length > 0}>
        <span class="toast-symbol" style="color: {toast.color};">{toast.symbol}</span>
        <span class="toast-message">{toast.message}</span>
        {#if toast.actions && toast.actions.length > 0}
          <div class="toast-actions">
            {#each toast.actions as action (action.label)}
              <button
                type="button"
                class="toast-action"
                class:toast-action-primary={action.variant === 'primary'}
                onclick={() => runAction(toast, action)}
              >
                {action.label}
              </button>
            {/each}
            <button
              type="button"
              class="toast-action toast-action-dismiss"
              aria-label="Dismiss"
              onclick={() => toastStore.dismiss(toast.id)}
            >
              ×
            </button>
          </div>
        {/if}
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
    animation: toast-in var(--duration-structural) var(--ease-spring) forwards;
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

  .toast-with-actions {
    height: auto;
    min-height: 22px;
    padding: 4px 8px;
    gap: 8px;
  }

  .toast-with-actions .toast-message {
    white-space: normal;
    max-width: 320px;
  }

  .toast-actions {
    display: flex;
    align-items: center;
    gap: 4px;
    flex-shrink: 0;
  }

  .toast-action {
    height: 18px;
    padding: 0 6px;
    background: transparent;
    border: 1px solid var(--color-border-subtle);
    color: var(--color-text-dim);
    font-family: var(--font-mono);
    font-size: 10px;
    cursor: pointer;
    transition: border-color var(--duration-micro) var(--ease-spring), color var(--duration-micro) var(--ease-spring);
  }

  .toast-action:hover {
    border-color: var(--color-border-strong);
    color: var(--color-text);
  }

  .toast-action-primary {
    border-color: var(--color-neon-cyan);
    color: var(--color-neon-cyan);
  }

  .toast-action-primary:hover {
    background: color-mix(in srgb, var(--color-neon-cyan) 12%, transparent);
    color: var(--color-neon-cyan);
  }

  .toast-action-dismiss {
    padding: 0 4px;
    min-width: 18px;
    font-size: 12px;
    line-height: 1;
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
