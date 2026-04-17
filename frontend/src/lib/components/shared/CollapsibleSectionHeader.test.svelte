<script lang="ts">
  /**
   * Test harness — supplies snippet props to CollapsibleSectionHeader so the
   * split/actions modes can be exercised in Vitest. Not shipped to users.
   */
  import CollapsibleSectionHeader from './CollapsibleSectionHeader.svelte';

  interface Props {
    open: boolean;
    onToggle: () => void;
    mode: 'whole' | 'split' | 'actions';
    label?: string;
    count?: number | string;
    headerClick?: () => void;
    actionClick?: () => void;
  }

  let {
    open,
    onToggle,
    mode,
    label = 'Section',
    count,
    headerClick,
    actionClick,
  }: Props = $props();
</script>

{#if mode === 'split'}
  <CollapsibleSectionHeader {open} {onToggle} ariaLabel="Toggle section">
    {#snippet header()}
      <button
        type="button"
        class="test-header-btn"
        onclick={(e) => {
          e.stopPropagation();
          headerClick?.();
        }}
      >{label}</button>
    {/snippet}
  </CollapsibleSectionHeader>
{:else if mode === 'actions'}
  <CollapsibleSectionHeader {open} {onToggle} {label} {count}>
    {#snippet actions()}
      <button
        type="button"
        class="test-action-btn"
        onclick={(e) => {
          e.stopPropagation();
          actionClick?.();
        }}
      >SYNC</button>
    {/snippet}
  </CollapsibleSectionHeader>
{:else}
  <CollapsibleSectionHeader {open} {onToggle} {label} {count} />
{/if}
