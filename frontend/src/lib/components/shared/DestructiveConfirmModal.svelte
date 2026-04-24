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
      <div
        class="error-banner"
        data-testid="confirm-modal-error"
        role="alert"
        aria-live="polite"
      >
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
    /* Token mix instead of hardcoded rgba so the scrim follows the
       bg-primary palette if it's ever retuned. */
    background: color-mix(in srgb, var(--color-bg-primary) 60%, transparent);
    /* Brand z-index tier 50 (Modal). Source order places scrim below
       the modal div that follows it in the DOM, so they share the tier. */
    z-index: 50;
    animation: scrim-in 200ms var(--ease-spring);
  }
  .modal {
    position: fixed;
    top: 50%;
    left: 50%;
    transform: translate(-50%, -50%);
    width: 420px;
    /* Narrow-viewport fallback: keep a 16px gutter on each side so the
       modal never overflows on small windows / mobile. */
    max-width: calc(100vw - 32px);
    /* Full height fallback: tall body content (future consumers with
       long preview lists) stays scrollable instead of clipping. */
    max-height: calc(100vh - 48px);
    display: flex;
    flex-direction: column;
    background: var(--color-bg-glass);
    backdrop-filter: blur(8px);
    border: 1px solid var(--color-border-subtle);
    /* Brand: flat edges are the default. */
    border-radius: 0;
    /* Modal tier (50). Stacks above scrim via source order. */
    z-index: 50;
    animation: modal-in 200ms var(--ease-spring);
    font-family: var(--font-sans);
    color: var(--color-text-primary);
  }
  .modal:not(.has-error) {
    /* Pre-destructive context tint — subtle red suggestion only. */
    border-color: color-mix(in srgb, var(--color-neon-red) 25%, transparent);
  }
  .modal.has-error {
    border-color: var(--color-neon-red);
  }

  .modal-header {
    /* 28px height + px-1.5 — matches the brand "Toolbar bar" spec so
       chrome stays consistent with every other horizontal control strip
       in the app. */
    height: 28px;
    padding: 0 6px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 6px;
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
    padding: 0;
    display: flex;
    align-items: center;
    justify-content: center;
    border: 1px solid transparent;
    border-radius: 0;
    background: transparent;
    color: var(--color-text-secondary);
    font-size: 14px;
    line-height: 1;
    cursor: pointer;
    transition: background 200ms var(--ease-spring), border-color 200ms var(--ease-spring), color 200ms var(--ease-spring);
  }
  .close-btn:hover:not(:disabled) {
    color: var(--color-text-primary);
    background: var(--color-bg-hover);
    border-color: var(--color-border-subtle);
  }
  .close-btn:active:not(:disabled) {
    /* Contraction — border mutes back toward resting. */
    border-color: transparent;
  }
  .close-btn:focus-visible {
    outline: 1px solid rgba(0, 229, 255, 0.3);
    outline-offset: 2px;
  }

  .modal-body {
    padding: 6px;
    font-size: 12px;
    /* Body is the only growable region; header/footer/gate stay fixed
       so the confirm controls never scroll out of view. */
    overflow-y: auto;
    min-height: 0;
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
    /* Brand input spec: px-1 (4px), 11px text. */
    padding: 0 4px;
    background: var(--color-bg-input);
    border: 1px solid var(--color-border-subtle);
    border-radius: 0;
    font-family: var(--font-mono);
    font-size: 11px;
    font-weight: 500;
    color: var(--color-text-primary);
    transition: border-color 200ms var(--ease-spring);
  }
  .confirm-input:focus {
    /* Input focus border — brand uses accent-at-30% for input focus. */
    border-color: color-mix(in srgb, var(--color-neon-cyan) 30%, transparent);
  }
  .confirm-input:focus-visible {
    outline: 1px solid rgba(0, 229, 255, 0.3);
    outline-offset: 2px;
  }

  .error-banner {
    margin: 0 6px;
    padding: 4px 6px;
    background: color-mix(in srgb, var(--color-neon-red) 12%, transparent);
    border-top: 1px solid color-mix(in srgb, var(--color-neon-red) 40%, transparent);
    border-bottom: 1px solid color-mix(in srgb, var(--color-neon-red) 40%, transparent);
    font-family: var(--font-mono);
    font-size: 10px;
    color: var(--color-neon-red);
  }

  .modal-footer {
    /* Matches modal-header spec: toolbar bar conventions. */
    height: 28px;
    padding: 0 6px;
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
    line-height: 18px;
    font-family: var(--font-sans);
    font-size: 10px;
    font-weight: 500;
    /* Flat edges — brand default. */
    border-radius: 0;
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
  .btn-cancel:active:not(:disabled) {
    border-color: transparent;
  }
  .btn-confirm {
    background: transparent;
    border: 1px solid var(--color-neon-red);
    color: var(--color-neon-red);
  }
  .btn-confirm:not(:disabled):hover {
    background: color-mix(in srgb, var(--color-neon-red) 12%, transparent);
  }
  .btn-confirm:active:not(:disabled) {
    /* Active contracts toward a muted border while preserving destructive
       chroma — signals press commitment without amplifying intensity. */
    border-color: color-mix(in srgb, var(--color-neon-red) 40%, transparent);
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

  /* Reduced-motion is enforced globally in app.css with `!important` on
     the universal selector — no component-local override needed. */
</style>
