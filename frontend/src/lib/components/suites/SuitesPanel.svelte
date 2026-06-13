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
  // Track the last project id we loaded for so the $effect can detect a
  // project switch + clear the detail surface before the new list loads.
  let lastProjectId: string | null | undefined = undefined;

  // Lazy-load on first activation. Project scope is captured at load time;
  // re-fires when the user switches projects. Per Svelte-5 invariant
  // (CLAUDE.md "Critical Svelte 5 invariant"), `projectStore
  // .currentProjectId` is read BEFORE the early-return gate so the
  // effect subscribes to project switches regardless of the active flag.
  $effect(() => {
    const projectId = projectStore.currentProjectId;
    if (!active) return;
    // SU-035 (v0.4.39) — clear the detail surface before the new project
    // list loads. If the previously-rendered detail row belongs to
    // project A and the operator switches to project B, the detail must
    // not survive into the new context. `select(null)` cascades the
    // clear into replays/latestReplay/aliveOriginalIds too.
    if (lastProjectId !== undefined && lastProjectId !== projectId) {
      void suitesStore.select(null);
    }
    lastProjectId = projectId;
    loaded = true;
    void suitesStore.load(projectId ?? undefined);
  });

  function onRowClick(suite: ValidationSuiteListItem): void {
    // R-06 5-state — re-click on the selected row toggles selection off
    // so the operator can collapse the SuiteDetailView surface in-place.
    if (suitesStore.selectedSuiteId === suite.id) {
      void suitesStore.select(null);
    } else {
      void suitesStore.select(suite.id);
    }
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
  <div
    class="panel"
    data-test="suites-panel"
    aria-label="Suites"
  >
    <header class="panel-header">
      <span class="section-heading">Suites</span>
      {#if suitesStore.suites.length > 0}
        <span class="panel-count font-mono">{suitesStore.suites.length}</span>
      {/if}
    </header>

    <div class="panel-body">
      {#if !loaded || suitesStore.loading}
        <!-- R-12 — loading state renders 4 .skeleton-row using the shared
             shimmer keyframe. Replaces the prior "Loading…" prose row. -->
        <div class="skeleton-list" data-test="suites-skeleton" aria-busy="true">
          {#each Array(4) as _, i (i)}
            <div class="skeleton-row">
              <span class="skeleton-bar"></span>
            </div>
          {/each}
        </div>
      {:else if suitesStore.error}
        <p class="empty-note panel-error">{suitesStore.error}</p>
      {:else if suitesStore.suites.length === 0}
        <p class="empty-note">No suites. Save a probe to create one.</p>
      {:else}
        <div class="suite-list" role="list">
          {#each suitesStore.suites as suite (suite.id)}
            <div role="listitem">
              <SuiteRow
                {suite}
                delta={deltaFor(suite)}
                status={statusFor(suite)}
                selected={suitesStore.selectedSuiteId === suite.id}
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
            latestReplay={suitesStore.latestReplay}
          />
        </div>
      {/if}
    </div>
  </div>
{/if}

<style>
  /* `.panel` + `.panel-header` + `.section-heading` are intentionally
     inherited from `app.css` — the canonical IDE-density chrome must
     visually unify across StrategiesPanel/HistoryPanel/GitHubPanel/
     SettingsPanel and this surface. The block below ONLY pins the
     overrides specific to the suites surface (count badge typography,
     body padding density, detail-wrapper rule). No local `.suites-panel`
     rule — the canon `.panel` shape suffices. */

  .panel-count {
    /* Canonical IDE count chip — mono numerics matching the
       SeedModal "Recent runs" chip + StatusBar metric pattern. */
    font-size: 10px;
    color: var(--color-text-dim);
    margin-left: auto;
  }

  .panel-body {
    /* Spec § 6 line 1107: `SuitesPanel container | p-1.5 | space-y-1.5`.
       6px horizontal padding + 6px gap between body sections. Vertical
       padding inherits from the canonical `.panel-body` rule in app.css
       (`padding: 6px 0`) so the bottom of the list isn't flush with the
       panel edge when SuiteDetailView is hidden. */
    padding: 6px;
    display: flex;
    flex-direction: column;
    gap: 6px;
  }

  /* `.panel-error` is now a global utility in app.css (.empty-note + .panel-error
     applies the neon-red color universally across sidebar panels). Local
     definition removed to avoid duplication. */

  .suite-list {
    display: flex;
    flex-direction: column;
    gap: 1px;
  }

  /* R-12 — skeleton list density. `.skeleton-row` + `.skeleton-bar` are
     promoted to app.css; only the list-level gap is local. */
  .skeleton-list {
    display: flex;
    flex-direction: column;
    gap: 2px;
  }

  .detail-wrapper {
    border-top: 1px solid var(--color-border-subtle);
    padding-top: 6px;
  }
</style>
