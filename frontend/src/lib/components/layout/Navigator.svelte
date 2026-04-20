<script lang="ts">
  /**
   * Navigator — thin orchestrator for the left sidebar.
   *
   * After the 2.7k-line monolith split, this shell owns only what is genuinely
   * shared between tabs: the project-scope selector (F2), the strategies list
   * (StrategiesPanel + SettingsPanel both consume it), and the strategy file
   * watcher. Each tab is its own panel component managing its own state.
   *
   * Tab routing is prop-driven (`active`). Panels receive `active` so they can
   * lazy-load on first activation without any parent-side orchestration.
   */
  import ClusterNavigator from './ClusterNavigator.svelte';
  import StrategiesPanel from './StrategiesPanel.svelte';
  import HistoryPanel from './HistoryPanel.svelte';
  import GitHubPanel from './GitHubPanel.svelte';
  import SettingsPanel from './SettingsPanel.svelte';
  import { getStrategies } from '$lib/api/client';
  import type { StrategyInfo } from '$lib/api/client';
  import { projectStore } from '$lib/stores/project.svelte';
  import { addToast, type ToastAction } from '$lib/stores/toast.svelte';
  import { tooltip } from '$lib/actions/tooltip';

  type Activity = 'editor' | 'history' | 'clusters' | 'github' | 'settings';

  let { active }: { active: Activity } = $props();

  const projects = $derived(projectStore.projects);
  const showProjectScope = $derived(projects.length > 1);

  let strategiesList = $state<StrategyInfo[]>([]);
  let suppressedNames = $state<Set<string>>(new Set());

  let strategiesLoaded = false;
  $effect(() => {
    if (strategiesLoaded) return;
    strategiesLoaded = true;
    getStrategies()
      .then((list) => {
        strategiesList = list;
      })
      .catch(() => {});
  });

  let projectsLoaded = false;
  $effect(() => {
    if (projectsLoaded) return;
    projectsLoaded = true;
    projectStore.refresh();
  });

  $effect(() => {
    const handler = () => projectStore.refresh();
    window.addEventListener('taxonomy-changed', handler);
    return () => window.removeEventListener('taxonomy-changed', handler);
  });

  $effect(() => {
    const handler = (e: Event) => {
      const detail = (e as CustomEvent).detail;
      if (!detail?.name) return;
      if (suppressedNames.has(detail.name)) return;
      const verb =
        detail.action === 'created' ? 'added' : detail.action === 'deleted' ? 'removed' : 'updated';
      addToast(detail.action as ToastAction, `Strategy ${verb}: ${detail.name}`);
      getStrategies()
        .then((list) => {
          strategiesList = list;
        })
        .catch(() => {});
    };
    window.addEventListener('strategy-changed', handler);
    return () => window.removeEventListener('strategy-changed', handler);
  });

  async function onStrategiesSaved(name: string): Promise<void> {
    suppressedNames = new Set([...suppressedNames, name]);
    setTimeout(() => {
      suppressedNames = new Set([...suppressedNames].filter((n) => n !== name));
    }, 2000);
    try {
      strategiesList = await getStrategies();
    } catch {
      /* swallow — list stays at last-known state */
    }
  }
</script>

<aside
  class="navigator"
  style="background: var(--color-bg-secondary); border-right: 1px solid var(--color-border-subtle);"
  aria-label="Navigator"
>
  {#if showProjectScope}
    <div class="project-scope-bar" role="region" aria-label="Project scope">
      <span class="project-scope-label">Project</span>
      <select
        class="project-scope-select"
        value={projectStore.currentProjectId ?? ''}
        onchange={(e) => {
          const v = (e.target as HTMLSelectElement).value;
          projectStore.setCurrent(v || null);
        }}
        use:tooltip={'Scope tree, topology, and history to a project. Select "All projects" for a global view.'}
      >
        <option value="">All projects</option>
        {#each projects as proj (proj.id)}
          <option value={proj.id}
            >{proj.label} ({proj.prompt_count ?? proj.member_count} prompts)</option
          >
        {/each}
      </select>
    </div>
  {/if}

  {#if active === 'editor'}
    <StrategiesPanel strategies={strategiesList} onSaved={onStrategiesSaved} />
  {:else if active === 'history'}
    <HistoryPanel active={true} />
  {:else if active === 'clusters'}
    <ClusterNavigator />
  {:else if active === 'github'}
    <GitHubPanel active={true} />
  {:else if active === 'settings'}
    <SettingsPanel active={true} strategies={strategiesList} />
  {/if}
</aside>

<style>
  .navigator {
    height: 100%;
    overflow: hidden;
    display: flex;
    flex-direction: column;
  }

  /* ---- ADR-005 F2 — global project scope selector ---- */
  .project-scope-bar {
    display: flex;
    align-items: center;
    gap: 6px;
    padding: 4px 8px;
    background: var(--color-bg-tertiary, var(--color-bg-secondary));
    border-bottom: 1px solid var(--color-border-subtle);
    flex-shrink: 0;
  }

  .project-scope-label {
    font-family: var(--font-mono);
    font-size: 8px;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--color-text-dim);
  }

  .project-scope-select {
    flex: 1;
    font-family: var(--font-mono);
    font-size: 9px;
    color: var(--color-text-primary);
    background: transparent;
    border: 1px solid var(--color-border-subtle);
    padding: 2px 4px;
    cursor: pointer;
    outline: none;
    min-width: 0;
    text-overflow: ellipsis;
    transition:
      color var(--duration-hover) var(--ease-spring),
      border-color var(--duration-hover) var(--ease-spring);
  }

  .project-scope-select:hover,
  .project-scope-select:focus {
    border-color: var(--tier-accent, var(--color-neon-cyan));
  }

  .project-scope-select option {
    background: var(--color-bg-secondary);
    color: var(--color-text-primary);
  }
</style>
