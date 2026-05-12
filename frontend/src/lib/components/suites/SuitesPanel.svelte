<script lang="ts">
  /**
   * SuitesPanel — Navigator entry body for the SUITES surface.
   *
   * Top-level mount per spec § 6 NEW components table. Renders the suites
   * list scoped to `projectStore.currentProjectId` (ADR-005). Empty state
   * uses the canonical copy from spec § 6 voice table:
   *
   *   "No suites. Save a probe to create one."
   *
   * Title-case prose, no all-caps shouting — matches the SeedModal voice
   * anchor for explanatory surfaces (vs UPPERCASE action buttons).
   *
   * Lazy-mount contract: the body only renders when `active=true`. Mirrors
   * the existing pattern for StrategiesPanel / HistoryPanel / GitHubPanel /
   * SettingsPanel — when the nav entry deactivates the body is removed.
   *
   * Row clicks dispatch `suitesStore.select(id)`; SuiteDetailView mounts
   * inline when a suite is selected.
   */
  import { suitesStore } from '$lib/stores/suites.svelte';
  import { projectStore } from '$lib/stores/project.svelte';
  import type { ValidationSuiteListItem } from '$lib/api/suites';
  import SuiteRow from './SuiteRow.svelte';
  import SuiteDetailView from './SuiteDetailView.svelte';

  interface Props {
    active?: boolean;
  }

  let { active = true }: Props = $props();

  let loaded = $state(false);

  // Lazy-load on first activation. Project scope is captured at load time;
  // re-fires when the user switches projects.
  $effect(() => {
    if (!active) return;
    const projectId = projectStore.currentProjectId;
    loaded = true;
    void suitesStore.load(projectId ?? undefined);
  });

  function onRowClick(suite: ValidationSuiteListItem): void {
    void suitesStore.select(suite.id);
  }

  // Status + delta are derived from the alarm block. A suite present in
  // `latest_alarms` is firing; otherwise it's nominal (or `none` if no
  // replay yet). Delta defaults to null when no replay has run.
  function statusFor(suite: ValidationSuiteListItem): 'nominal' | 'firing' | 'none' {
    const block = suitesStore.regressionAlarmBlock;
    if (!block) return 'none';
    if (block.latest_alarms.some((a) => a.suite_id === suite.id)) return 'firing';
    return 'nominal';
  }

  function deltaFor(suite: ValidationSuiteListItem): number | null {
    const block = suitesStore.regressionAlarmBlock;
    if (!block) return null;
    const alarm = block.latest_alarms.find((a) => a.suite_id === suite.id);
    if (alarm) return alarm.delta_abs;
    return null;
  }
</script>

{#if active}
  <section
    class="suites-panel"
    data-test="suites-panel"
    aria-label="Suites"
  >
    <header class="panel-header">
      <span class="section-heading">Suites</span>
      {#if suitesStore.suites.length > 0}
        <span class="panel-count">{suitesStore.suites.length}</span>
      {/if}
    </header>

    <div class="panel-body">
      {#if !loaded || suitesStore.loading}
        <p class="panel-note">Loading…</p>
      {:else if suitesStore.error}
        <p class="panel-error">{suitesStore.error}</p>
      {:else if suitesStore.suites.length === 0}
        <p class="panel-empty">No suites. Save a probe to create one.</p>
      {:else}
        <div class="suite-list" role="list">
          {#each suitesStore.suites as suite (suite.id)}
            <div role="listitem">
              <SuiteRow
                {suite}
                delta={deltaFor(suite)}
                status={statusFor(suite)}
                onClick={onRowClick}
              />
            </div>
          {/each}
        </div>
      {/if}

      {#if suitesStore.detail}
        <div class="detail-wrapper">
          <SuiteDetailView
            suite={suitesStore.detail}
            replays={suitesStore.replays}
          />
        </div>
      {/if}
    </div>
  </section>
{/if}

<style>
  .suites-panel {
    display: flex;
    flex-direction: column;
    flex: 1;
    min-height: 0;
    height: 100%;
    overflow: hidden;
  }

  .panel-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    height: 20px;
    padding: 0 8px;
    flex-shrink: 0;
    background: var(--color-bg-secondary);
    border-bottom: 1px solid var(--color-border-subtle);
  }

  .section-heading {
    font-family: var(--font-mono);
    font-size: 8px;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--color-text-dim);
  }

  .panel-count {
    font-family: var(--font-mono);
    font-size: 9px;
    color: var(--color-text-dim);
  }

  .panel-body {
    flex: 1;
    min-height: 0;
    overflow: auto;
    display: flex;
    flex-direction: column;
    gap: 8px;
  }

  .panel-note,
  .panel-error,
  .panel-empty {
    padding: 8px;
    font-family: var(--font-mono);
    font-size: 10px;
    color: var(--color-text-dim);
  }

  .panel-error {
    color: var(--color-neon-red, #ff3366);
  }

  .suite-list {
    display: flex;
    flex-direction: column;
    gap: 1px;
  }

  .detail-wrapper {
    border-top: 1px solid var(--color-border-subtle);
  }
</style>
