<script lang="ts">
  /**
   * TemplatesSection — Proven Templates collapsible section.
   *
   * Reads from `templatesStore` and renders templates grouped by their
   * frozen domain_label. Spawning a template pre-fills the forge store;
   * retiring removes it from the visible set.
   *
   * Extracted from ClusterNavigator for single-responsibility clarity.
   */
  import { editorStore, PROMPT_TAB_ID } from '$lib/stores/editor.svelte';
  import { forgeStore } from '$lib/stores/forge.svelte';
  import { addToast } from '$lib/stores/toast.svelte';
  import { scoreColor, taxonomyColor } from '$lib/utils/colors';
  import { formatScore } from '$lib/utils/formatting';
  import { tooltip } from '$lib/actions/tooltip';
  import { CLUSTER_NAV_TOOLTIPS } from '$lib/utils/ui-tooltips';
  import CollapsibleSectionHeader from '$lib/components/shared/CollapsibleSectionHeader.svelte';
  import { navCollapse } from '$lib/stores/nav_collapse.svelte';
  import { templatesStore } from '$lib/stores/templates.svelte';
  import { slide } from 'svelte/transition';
  import { navSlide } from '$lib/utils/transitions';

  interface TemplateGroup {
    domain: string;
    items: typeof templatesStore.templates;
  }

  const templateGroups = $derived.by<TemplateGroup[]>(() => {
    const by: Record<string, typeof templatesStore.templates> = {};
    for (const t of templatesStore.templates) {
      if (t.retired_at) continue;
      (by[t.domain_label] ||= []).push(t);
    }
    return Object.entries(by)
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([domain, items]) => ({
        domain,
        items: [...items].sort((a, b) => (b.score ?? 0) - (a.score ?? 0)),
      }));
  });

  const hasVisibleTemplates = $derived(templateGroups.some((g) => g.items.length > 0));
  const totalCount = $derived(templateGroups.reduce((n, g) => n + g.items.length, 0));

  async function handleSpawn(tpl: typeof templatesStore.templates[number]): Promise<void> {
    const result = await templatesStore.spawn(tpl.id);
    if (!result) {
      addToast('deleted', 'Failed to spawn template');
      return;
    }
    forgeStore.prompt = result.prompt;
    if (tpl.strategy) forgeStore.strategy = tpl.strategy;
    if (tpl.pattern_ids.length > 0) {
      forgeStore.appliedPatternIds = tpl.pattern_ids;
      forgeStore.appliedPatternLabel = tpl.label;
    }
    editorStore.activeTabId = PROMPT_TAB_ID;
    addToast('created', `Template loaded: ${tpl.label}`);
  }

  async function handleRetire(tpl: typeof templatesStore.templates[number]): Promise<void> {
    const ok = await templatesStore.retire(tpl.id);
    if (!ok) {
      addToast('deleted', 'Failed to retire template');
      return;
    }
    addToast('created', `Retired: ${tpl.label}`);
  }
</script>

{#if hasVisibleTemplates}
  <div class="section-wrapper">
    <CollapsibleSectionHeader
      open={navCollapse.isOpen('templates')}
      onToggle={() => navCollapse.toggle('templates')}
      label="PROVEN TEMPLATES"
      count={totalCount}
    />
    {#if navCollapse.isOpen('templates')}
      <div transition:slide={navSlide}>
        {#each templateGroups as group (group.domain)}
          <div class="template-group" data-group-header={group.domain}>
            <span
              class="domain-dot"
              data-group-dot={group.domain}
              style="background: {taxonomyColor(group.domain)};"
            ></span>
            <span class="template-group-label">{group.domain}</span>
            <span class="template-group-count">{group.items.length}</span>
          </div>
          {#each group.items as tpl (tpl.id)}
            <div class="template-row">
              <div class="template-info">
                <span class="template-label">{tpl.label}</span>
                <span class="template-meta">
                  {#if tpl.score != null}
                    <span class="badge-score font-mono" style="color: {scoreColor(tpl.score)};">{formatScore(tpl.score)}</span>
                  {/if}
                  {#if tpl.pattern_ids.length}
                    <span class="badge-dim">{tpl.pattern_ids.length} patterns</span>
                  {/if}
                  {#if tpl.usage_count > 0}
                    <span class="badge-dim">{tpl.usage_count}× used</span>
                  {/if}
                  {#if tpl.strategy}
                    <span class="template-strategy">{tpl.strategy}</span>
                  {/if}
                </span>
              </div>
              <button
                class="use-template-btn"
                onclick={() => handleSpawn(tpl)}
                use:tooltip={CLUSTER_NAV_TOOLTIPS.use_template}
                aria-label={`Use template ${tpl.label}`}
              >Use</button>
              <button
                class="retire-template-btn"
                onclick={() => handleRetire(tpl)}
                use:tooltip={'Retire this template'}
                aria-label={`Retire template ${tpl.label}`}
              >⋯</button>
            </div>
          {/each}
        {/each}
      </div>
    {/if}
  </div>
{/if}

<style>
  .section-wrapper {
    padding: 0 0 4px;
    margin-bottom: 4px;
  }

  .template-row {
    display: flex;
    align-items: center;
    gap: 4px;
    padding: 2px 6px;
    min-height: 28px;
    transition: background var(--duration-hover) var(--ease-spring);
  }

  .template-row:hover {
    background: var(--color-bg-hover);
  }

  .template-info {
    display: flex;
    flex-direction: column;
    gap: 2px;
    flex: 1;
    min-width: 0;
  }

  .template-label {
    font-size: 10px;
    color: var(--color-text-primary);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .template-meta {
    display: flex;
    align-items: center;
    gap: 4px;
    flex-wrap: wrap;
  }

  .template-strategy {
    font-size: 9px;
    font-family: var(--font-mono);
    color: var(--color-text-dim);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    max-width: 80px;
  }

  .use-template-btn {
    display: flex;
    align-items: center;
    justify-content: center;
    height: 20px;
    padding: 0 6px;
    border: 1px solid var(--color-border-subtle);
    background: transparent;
    color: var(--color-text-secondary);
    font-size: 10px;
    font-family: var(--font-sans);
    font-weight: 600;
    cursor: pointer;
    border-radius: 0;
    flex-shrink: 0;
    transition: color var(--duration-hover) var(--ease-spring),
                border-color var(--duration-hover) var(--ease-spring),
                background var(--duration-hover) var(--ease-spring);
  }

  .use-template-btn:hover {
    color: var(--tier-accent, var(--color-neon-cyan));
    border-color: var(--tier-accent, var(--color-neon-cyan));
    background: color-mix(in srgb, var(--tier-accent, var(--color-neon-cyan)) 8%, transparent);
  }

  .use-template-btn:active {
    border-color: color-mix(in srgb, var(--tier-accent, var(--color-neon-cyan)) 20%, transparent);
  }

  .template-group {
    display: flex;
    align-items: center;
    gap: 4px;
    padding: 4px 6px 2px;
    border-top: 1px solid var(--color-border-subtle);
    margin-top: 2px;
  }

  .template-group:first-child {
    border-top: none;
    margin-top: 0;
  }

  .template-group-label {
    font-size: 9px;
    font-family: var(--font-mono);
    color: var(--color-text-dim);
    text-transform: uppercase;
    letter-spacing: 0.08em;
    flex: 1;
  }

  .template-group-count {
    font-size: 9px;
    font-family: var(--font-mono);
    color: var(--color-text-muted);
  }

  .retire-template-btn {
    display: flex;
    align-items: center;
    justify-content: center;
    height: 20px;
    width: 20px;
    padding: 0;
    border: 1px solid var(--color-border-subtle);
    background: transparent;
    color: var(--color-text-dim);
    font-size: 12px;
    font-family: var(--font-sans);
    cursor: pointer;
    flex-shrink: 0;
    transition: color var(--duration-hover) var(--ease-spring),
                border-color var(--duration-hover) var(--ease-spring);
  }

  .retire-template-btn:hover {
    color: var(--color-neon-red);
    border-color: color-mix(in srgb, var(--color-neon-red) 40%, transparent);
  }

  .badge-dim {
    font-size: 9px;
    color: var(--color-text-muted);
    font-family: var(--font-mono);
  }

  .domain-dot {
    width: 8px;
    height: 8px;
    flex-shrink: 0;
    outline: 1px solid color-mix(in srgb, var(--color-text-primary) 15%, transparent);
    outline-offset: -1px;
  }

  .badge-score {
    font-size: 9px;
    width: 24px;
    text-align: right;
  }
</style>
