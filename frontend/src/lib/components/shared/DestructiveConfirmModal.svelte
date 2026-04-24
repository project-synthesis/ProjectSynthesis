<script lang="ts">
  import type { Snippet } from 'svelte';
  import { onDestroy, onMount } from 'svelte';

  type Props = {
    open: boolean;
    title: string;
    body?: Snippet;
    sideEffectHint?: string;
    confirmLiteral?: string;
    confirmLabel: string;
    onConfirm: () => Promise<void>;
    onCancel?: () => void;
  };
  const {
    open,
    title,
    body,
    sideEffectHint,
    confirmLiteral = 'DELETE',
    confirmLabel,
    onConfirm,
    onCancel,
  }: Props = $props();

  let typed = $state('');
  let committing = $state(false);
  let errorMessage = $state<string | null>(null);
  let inputEl = $state<HTMLInputElement | null>(null);

  const canConfirm = $derived(typed === confirmLiteral && !committing);

  function handleKeyDown(e: KeyboardEvent) {
    if (!open) return;
    if (e.key === 'Escape' && !committing) {
      e.preventDefault();
      onCancel?.();
    }
  }

  async function handleConfirm() {
    if (!canConfirm) return;
    committing = true;
    errorMessage = null;
    try {
      await onConfirm();
    } catch (e) {
      errorMessage = (e as Error).message || 'Delete failed';
    } finally {
      committing = false;
    }
  }

  function handleInputKey(e: KeyboardEvent) {
    if (e.key === 'Enter' && canConfirm) {
      e.preventDefault();
      void handleConfirm();
    }
  }

  function onScrimClick() {
    if (committing) return;
    onCancel?.();
  }

  $effect(() => {
    if (open) {
      // Focus the confirm input on next tick
      queueMicrotask(() => inputEl?.focus());
    } else {
      typed = '';
      errorMessage = null;
      committing = false;
    }
  });

  onMount(() => {
    if (typeof document !== 'undefined') {
      document.addEventListener('keydown', handleKeyDown);
    }
  });
  onDestroy(() => {
    if (typeof document !== 'undefined') {
      document.removeEventListener('keydown', handleKeyDown);
    }
  });
</script>

{#if open}
  <div class="scrim" onclick={onScrimClick} role="presentation"></div>
  <div
    class="modal"
    class:committing
    class:has-error={errorMessage !== null}
    role="dialog"
    aria-modal="true"
    aria-labelledby="confirm-modal-title"
  >
    <header class="modal-header">
      <h2 id="confirm-modal-title" class="modal-title">{title}</h2>
      <button
        class="close-btn"
        onclick={() => !committing && onCancel?.()}
        aria-label="Close"
        disabled={committing}
      >×</button>
    </header>

    <div class="modal-body">
      {#if body}
        {@render body()}
      {/if}
      {#if sideEffectHint}
        <p class="side-effect">{sideEffectHint}</p>
      {/if}
    </div>

    <div class="confirm-gate">
      <label class="confirm-label" for="confirm-literal-input">
        Type {confirmLiteral} to confirm
      </label>
      <input
        bind:this={inputEl}
        bind:value={typed}
        onkeydown={handleInputKey}
        id="confirm-literal-input"
        type="text"
        class="confirm-input"
        autocomplete="off"
        spellcheck="false"
        disabled={committing}
      />
    </div>

    {#if errorMessage !== null}
      <div class="error-banner" data-testid="confirm-modal-error">
        <span>{errorMessage}</span>
      </div>
    {/if}

    <footer class="modal-footer">
      <button
        class="btn-cancel"
        onclick={() => !committing && onCancel?.()}
        disabled={committing}
      >Cancel</button>
      <button
        class="btn-confirm"
        onclick={handleConfirm}
        disabled={!canConfirm}
        aria-disabled={!canConfirm}
      >{confirmLabel}</button>
    </footer>
  </div>
{/if}

<style>
  .scrim {
    position: fixed;
    inset: 0;
    background: rgba(6, 6, 12, 0.6);
    z-index: 900;
    animation: scrim-in 200ms var(--ease-spring);
  }
  .modal {
    position: fixed;
    top: 50%;
    left: 50%;
    transform: translate(-50%, -50%);
    width: 420px;
    background: var(--color-bg-glass);
    backdrop-filter: blur(8px);
    border: 1px solid var(--color-border-subtle);
    border-radius: 6px;
    z-index: 901;
    animation: modal-in 200ms var(--ease-spring);
    font-family: var(--font-sans);
    color: var(--color-text-primary);
  }
  .modal:not(.has-error) {
    border-color: rgba(255, 51, 102, 0.25);
  }
  .modal.has-error {
    border-color: var(--color-neon-red);
  }

  .modal-header {
    height: 28px;
    padding: 0 8px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    border-bottom: 1px solid var(--color-border-subtle);
  }
  .modal-title {
    font-family: var(--font-display);
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    margin: 0;
  }
  .close-btn {
    height: 20px;
    width: 20px;
    border: none;
    background: transparent;
    color: var(--color-text-secondary);
    font-size: 14px;
    cursor: pointer;
  }
  .close-btn:hover:not(:disabled) {
    color: var(--color-text-primary);
  }

  .modal-body {
    padding: 6px;
    font-size: 12px;
  }
  .side-effect {
    margin-top: 4px;
    font-size: 10px;
    color: var(--color-text-dim);
  }

  .confirm-gate {
    padding: 6px;
    border-top: 1px solid var(--color-border-subtle);
  }
  .confirm-label {
    display: block;
    font-family: var(--font-display);
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    color: var(--color-text-secondary);
    margin-bottom: 4px;
  }
  .confirm-input {
    width: 100%;
    height: 20px;
    padding: 0 6px;
    background: var(--color-bg-input);
    border: 1px solid var(--color-border-subtle);
    border-radius: 2px;
    font-family: var(--font-mono);
    font-size: 11px;
    font-weight: 500;
    color: var(--color-text-primary);
  }
  .confirm-input:focus-visible {
    outline: 1px solid rgba(0, 229, 255, 0.3);
    outline-offset: 2px;
  }

  .error-banner {
    margin: 0 6px;
    padding: 4px 6px;
    background: color-mix(in srgb, var(--color-neon-red) 12%, transparent);
    border-top: 1px solid rgba(255, 51, 102, 0.4);
    border-bottom: 1px solid rgba(255, 51, 102, 0.4);
    font-family: var(--font-mono);
    font-size: 10px;
    color: var(--color-neon-red);
  }

  .modal-footer {
    height: 28px;
    padding: 0 8px;
    display: flex;
    justify-content: flex-end;
    align-items: center;
    gap: 6px;
    border-top: 1px solid var(--color-border-subtle);
  }
  .btn-cancel,
  .btn-confirm {
    height: 20px;
    padding: 0 8px;
    font-family: var(--font-sans);
    font-size: 10px;
    font-weight: 500;
    border-radius: 4px;
    cursor: pointer;
    transition: background 200ms var(--ease-spring), border-color 200ms var(--ease-spring);
  }
  .btn-cancel {
    background: transparent;
    border: 1px solid transparent;
    color: var(--color-text-secondary);
  }
  .btn-cancel:hover:not(:disabled) {
    background: var(--color-bg-hover);
    border-color: var(--color-border-subtle);
  }
  .btn-confirm {
    background: transparent;
    border: 1px solid var(--color-neon-red);
    color: var(--color-neon-red);
  }
  .btn-confirm:not(:disabled):hover {
    background: color-mix(in srgb, var(--color-neon-red) 12%, transparent);
  }
  .btn-cancel:disabled,
  .btn-confirm:disabled {
    opacity: 0.4;
    cursor: not-allowed;
    transition: none;
  }
  .btn-confirm:focus-visible,
  .btn-cancel:focus-visible {
    outline: 1px solid rgba(0, 229, 255, 0.3);
    outline-offset: 2px;
  }

  @keyframes scrim-in { from { opacity: 0; } to { opacity: 1; } }
  @keyframes modal-in {
    from { opacity: 0; transform: translate(-50%, -50%) scale(0.96); }
    to   { opacity: 1; transform: translate(-50%, -50%) scale(1); }
  }

  @media (prefers-reduced-motion: reduce) {
    .scrim, .modal {
      animation-duration: 0.01ms;
    }
    .btn-cancel, .btn-confirm { transition-duration: 0.01ms; }
  }
</style>
