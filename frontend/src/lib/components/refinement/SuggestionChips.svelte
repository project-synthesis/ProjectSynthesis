<script lang="ts">
  interface Props {
    suggestions: Array<Record<string, string>>;
    onSelect: (text: string) => void;
  }

  import { tooltip } from '$lib/actions/tooltip';

  let { suggestions, onSelect }: Props = $props();
  let expanded = $state(false);

  function chipText(chip: Record<string, string>): string {
    return chip.text || chip.action || JSON.stringify(chip);
  }
</script>

{#if suggestions.length > 0}
  <div class="suggestions-section">
    <button
      class="suggestions-toggle"
      onclick={() => expanded = !expanded}
      aria-expanded={expanded}
    >
      <span class="toggle-indicator">{expanded ? '▾' : '▸'}</span>
      <span class="suggestions-title">SUGGESTIONS</span>
    </button>
    {#if expanded}
      <div class="suggestion-list" aria-label="Refinement suggestions">
        {#each suggestions.slice(0, 3) as chip}
          <button
            class="suggestion-card"
            use:tooltip={chipText(chip)}
            onclick={() => onSelect(chipText(chip))}
          >
            {chipText(chip)}
          </button>
        {/each}
      </div>
    {/if}
  </div>
{/if}

<style>
  .suggestions-section {
    display: flex;
    flex-direction: column;
  }

  .suggestions-toggle {
    display: flex;
    align-items: center;
    gap: 4px;
    height: 22px;
    padding: 0 6px;
    margin: 0 -6px;
    width: calc(100% + 12px);
    background: transparent;
    border: none;
    border-top: 1px solid var(--color-border-subtle);
    color: inherit;
    font: inherit;
    cursor: pointer;
    transition: background var(--duration-hover) var(--ease-spring);
  }

  .suggestions-toggle:hover {
    background: var(--color-bg-hover);
  }

  .suggestions-toggle:focus-visible {
    outline: 1px solid color-mix(in srgb, var(--tier-accent, var(--color-neon-cyan)) 30%, transparent);
    outline-offset: -1px;
  }

  .suggestions-title {
    font-size: 10px;
    font-family: var(--font-display);
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    color: var(--tier-accent, var(--color-text-dim));
  }

  .suggestion-list {
    display: flex;
    flex-direction: column;
    gap: 3px;
    padding: 4px 0;
  }

  .suggestion-card {
    font-size: 10px;
    font-family: var(--font-sans);
    color: var(--color-text-primary);
    background: var(--color-bg-card);
    border: 1px solid var(--color-border-subtle);
    border-radius: 0;
    padding: 4px 6px;
    cursor: pointer;
    text-align: left;
    line-height: 1.4;
    /* 2-line clamp — full text accessible via tooltip */
    display: -webkit-box;
    -webkit-line-clamp: 2;
    line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
    transition: border-color var(--duration-hover) var(--ease-spring),
                background var(--duration-hover) var(--ease-spring);
  }

  .suggestion-card:hover {
    border-color: var(--tier-accent, var(--color-neon-cyan));
    background: var(--color-bg-hover);
  }

  .suggestion-card:focus-visible {
    outline: 1px solid color-mix(in srgb, var(--tier-accent, var(--color-neon-cyan)) 30%, transparent);
    outline-offset: -1px;
  }
</style>
