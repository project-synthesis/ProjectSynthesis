<script lang="ts">
  interface Props {
    suggestions: Array<Record<string, string>>;
    onSelect: (text: string) => void;
  }

  import { tooltip } from '$lib/actions/tooltip';

  let { suggestions, onSelect }: Props = $props();

  function chipText(chip: Record<string, string>): string {
    return chip.text || chip.action || JSON.stringify(chip);
  }
</script>

{#if suggestions.length > 0}
  <div class="chips" aria-label="Refinement suggestions">
    {#each suggestions.slice(0, 3) as chip}
      <button
        class="chip"
        use:tooltip={chipText(chip)}
        onclick={() => onSelect(chipText(chip))}
      >
        {chipText(chip)}
      </button>
    {/each}
  </div>
{/if}

<style>
  .chips {
    display: flex;
    flex-direction: column;
    gap: 4px;
    padding: 4px 0;
  }

  .chip {
    font-size: 10px;
    font-family: var(--font-sans);
    color: var(--color-text-secondary);
    background: transparent;
    border: 1px solid var(--color-border-subtle);
    padding: 4px 8px;
    cursor: pointer;
    text-align: left;
    line-height: 1.5;
    transition: border-color 200ms cubic-bezier(0.16, 1, 0.3, 1),
                background 200ms cubic-bezier(0.16, 1, 0.3, 1),
                color 200ms cubic-bezier(0.16, 1, 0.3, 1);
  }

  .chip:hover {
    border-color: var(--tier-accent, var(--color-neon-cyan));
    background: rgba(var(--tier-accent-rgb, 0, 229, 255), 0.04);
    color: var(--color-text-primary);
  }

  .chip:focus-visible {
    outline: 1px solid rgba(0, 229, 255, 0.3);
    outline-offset: 2px;
  }
</style>
