<script lang="ts">
  interface Props {
    onSubmit: (text: string) => void;
    disabled?: boolean;
  }

  let { onSubmit, disabled = false }: Props = $props();
  let value = $state('');

  function handleSubmit() {
    const trimmed = value.trim();
    if (!trimmed || disabled) return;
    onSubmit(trimmed);
    value = '';
  }

  function handleKeydown(e: KeyboardEvent) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSubmit();
    }
  }
</script>

<div class="refinement-input">
  <input
    type="text"
    class="input-field"
    placeholder="Describe refinement..."
    bind:value
    onkeydown={handleKeydown}
    {disabled}
    aria-label="Refinement request"
  />
  <button
    class="submit-btn"
    onclick={handleSubmit}
    disabled={disabled || !value.trim()}
    aria-label="Submit refinement"
  >
    REFINE
  </button>
</div>

<style>
  .refinement-input {
    display: flex;
    gap: 4px;
    align-items: center;
  }

  .input-field {
    flex: 1;
    height: 20px;
    padding: 0 8px;
    font-size: 11px;
    font-family: var(--font-sans);
    color: var(--color-text-primary);
    background: var(--color-bg-input);
    border: 1px solid var(--color-border-subtle);
    outline: none;
    transition: border-color 200ms cubic-bezier(0.16, 1, 0.3, 1);
  }

  .input-field::placeholder {
    color: var(--color-text-dim);
  }

  .input-field:focus {
    border-color: var(--tier-accent, var(--color-neon-cyan));
  }

  .input-field:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }

  .submit-btn {
    height: 20px;
    padding: 0 8px;
    line-height: 18px;
    background: transparent;
    border: 1px solid var(--tier-accent, var(--color-neon-cyan));
    color: var(--tier-accent, var(--color-neon-cyan));
    font-family: var(--font-display);
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    cursor: pointer;
    white-space: nowrap;
    flex-shrink: 0;
    transition: background 200ms cubic-bezier(0.16, 1, 0.3, 1),
                color 200ms cubic-bezier(0.16, 1, 0.3, 1);
  }

  .submit-btn:hover:not(:disabled) {
    background: rgba(var(--tier-accent-rgb, 0, 229, 255), 0.08);
  }

  .submit-btn:disabled {
    opacity: 0.4;
    cursor: not-allowed;
  }
</style>
