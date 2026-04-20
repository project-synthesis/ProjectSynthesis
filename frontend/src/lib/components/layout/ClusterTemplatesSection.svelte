<script lang="ts">
  import type { Template } from '$lib/stores/templates.svelte';

  interface Props {
    templates: Template[];
    familyDomain: string;
  }

  const { templates, familyDomain }: Props = $props();
</script>

{#if templates.length > 0}
  <div class="family-section">
    <div class="section-heading" style="margin-bottom: 4px;">
      Templates ({templates.length})
    </div>
    <div class="template-list">
      {#each templates as tpl (tpl.id)}
        <div class="template-row-compact">
          <span class="template-label-compact">{tpl.label}</span>
          <span class="template-origin-compact">
            {tpl.domain_label}
            {#if tpl.domain_label !== familyDomain}
              <em class="template-reparented">(reparented)</em>
            {/if}
          </span>
        </div>
      {/each}
    </div>
  </div>
{/if}

<style>
  .family-section {
    display: flex;
    flex-direction: column;
  }
  .template-list {
    display: flex;
    flex-direction: column;
    gap: 2px;
  }
  .template-row-compact {
    display: flex;
    align-items: baseline;
    gap: 8px;
    padding: 3px 4px;
    font-size: 10px;
    border-left: 1px solid var(--color-border-subtle);
  }
  .template-label-compact {
    flex: 1;
    min-width: 0;
    color: var(--color-text-primary);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .template-origin-compact {
    font-size: 9px;
    font-family: var(--font-mono);
    color: var(--color-text-dim);
    flex-shrink: 0;
  }
  .template-reparented {
    font-style: italic;
    color: var(--color-neon-amber, var(--color-text-muted));
    margin-left: 4px;
  }
</style>
