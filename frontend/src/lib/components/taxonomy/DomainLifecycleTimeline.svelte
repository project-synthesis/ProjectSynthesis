<script lang="ts">
  /**
   * DomainLifecycleTimeline — Observatory tier-1 surface for taxonomy activity.
   *
   * Renders a merged view from two stores:
   *   - `observatoryStore.historicalEvents` — period-scoped JSONL backfill,
   *     refreshed when the user picks a chip (24h / 7d / 30d).
   *   - `clustersStore.activityEvents` — live SSE ring shared with the
   *     ActivityPanel terminal in the topology view; prepends new events
   *     via `pushActivityEvent` as warm-path / hot-path operations fire.
   *
   * The split is deliberate: writing into the SSE ring from a period chip
   * silently disturbed the ActivityPanel — we now own our own buffer and
   * merge at render time, deduping by `ts|op|decision` and capping at 200.
   *
   * Three filter dimensions: hot/warm/cold execution path, op-family
   * (domain | cluster | pattern | readiness), and an `errors only` toggle.
   * Rows expand to reveal the raw context payload + a one-line `keyMetric`
   * summary (shared with ActivityPanel via `$lib/utils/activity-summary.ts`).
   */
  import { clustersStore } from '$lib/stores/clusters.svelte';
  import { observatoryStore } from '$lib/stores/observatory.svelte';
  import { pathColor, type ActivityPath } from '$lib/utils/activity-colors';
  import { isErrorEvent, opFamily, type OpFamily } from '$lib/utils/activity-filters';
  import { keyMetric } from '$lib/utils/activity-summary';
  import type { TaxonomyActivityEvent } from '$lib/api/clusters';
  import type { ObservatoryPeriod } from '$lib/api/observatory';

  // Period chips live in this filter-bar (NOT in TaxonomyObservatory's shell
  // header) because Readiness is current-state and the asymmetry is owned
  // by the period-aware panels. See TaxonomyObservatory legend (TO3).
  const PERIODS: readonly ObservatoryPeriod[] = ['24h', '7d', '30d'];
  const ALL_FAMILIES: readonly OpFamily[] = ['domain', 'cluster', 'pattern', 'readiness', 'operator_action'];
  const TIMELINE_EVENT_CAP = 200;

  /**
   * Merge live SSE ring + Observatory historical buffer.
   *
   * Live events take priority on collision (same `ts|op|decision` key)
   * because they are the freshest source — duplicates are filtered out
   * of the historical buffer when present in the ring. Output is sorted
   * newest-first to match the `ActivityPanel` ordering convention.
   */
  const events = $derived.by<TaxonomyActivityEvent[]>(() => {
    const seen = new Set<string>();
    const merged: TaxonomyActivityEvent[] = [];
    const eventKey = (e: TaxonomyActivityEvent) => `${e.ts}|${e.op}|${e.decision}`;
    for (const e of clustersStore.activityEvents) {
      const k = eventKey(e);
      if (!seen.has(k)) { seen.add(k); merged.push(e); }
    }
    for (const e of observatoryStore.historicalEvents) {
      const k = eventKey(e);
      if (!seen.has(k)) { seen.add(k); merged.push(e); }
    }
    merged.sort((a, b) => (b.ts ?? '').localeCompare(a.ts ?? ''));
    return merged.slice(0, TIMELINE_EVENT_CAP);
  });

  let activePaths = $state<Set<ActivityPath>>(new Set(['hot', 'warm', 'cold']));
  let activeFamilies = $state<Set<OpFamily>>(new Set(['domain', 'cluster', 'pattern', 'readiness', 'operator_action']));
  let errorsOnly = $state(false);
  let expandedId = $state<string | null>(null);

  function togglePath(p: ActivityPath) {
    const next = new Set(activePaths);
    if (next.has(p)) next.delete(p); else next.add(p);
    activePaths = next;
  }
  function toggleFamily(f: OpFamily) {
    const next = new Set(activeFamilies);
    if (next.has(f)) next.delete(f); else next.add(f);
    activeFamilies = next;
  }
  function toggleExpand(id: string) {
    expandedId = expandedId === id ? null : id;
  }

  /**
   * Stable per-row key for #each blocks.
   *
   * Backend `TaxonomyActivityEvent` has no native `id` field, but tests inject
   * a synthetic `id` to make assertions readable. Honour that when present;
   * otherwise fall back to a composite of `ts + op + decision + cluster_id`,
   * matching the convention already used by ActivityPanel.
   */
  function eventKey(e: { ts: string; op: string; decision: string; cluster_id?: string | null }): string {
    const synthetic = (e as unknown as { id?: string }).id;
    if (typeof synthetic === 'string' && synthetic.length > 0) return synthetic;
    return `${e.ts}::${e.op}::${e.decision}::${e.cluster_id ?? ''}`;
  }

  const visibleEvents = $derived(events.filter((e) => {
    if (errorsOnly) return isErrorEvent(e);
    if (!activePaths.has(e.path as ActivityPath)) return false;
    // 'error' op is included whenever any family is active.
    if (e.op === 'error') return activeFamilies.size > 0;
    const fam = opFamily(e.op, e.decision);
    // Uncategorised events show only when every family chip is on (default state).
    if (fam === null) return activeFamilies.size === ALL_FAMILIES.length;
    return activeFamilies.has(fam);
  }));

  // Period chips drive BOTH panels via `observatoryStore.setPeriod()`:
  //   - Heatmap window: `refreshPatternDensity()` (debounced 1s).
  //   - Timeline backfill: `loadTimelineEvents()` (debounced 1s).
  //
  // First mount triggers a backfill once per session via the
  // `_periodBackfillTriggered` flag — `observatoryStore._reset()` resets
  // generation counters, the component-local flag prevents a second
  // unconditional fetch on remount.
  let _periodBackfillTriggered = false;
  $effect(() => {
    const _p = observatoryStore.period;  // subscribe — re-fire on period change
    if (_periodBackfillTriggered) {
      void observatoryStore.loadTimelineEvents();
      return;
    }
    _periodBackfillTriggered = true;
    // Only backfill on first mount when both buffers are empty — avoid
    // clobbering an already-warm SSE ring under a refresh storm.
    if (clustersStore.activityEvents.length < 20 && observatoryStore.historicalEvents.length === 0) {
      void observatoryStore.loadTimelineEvents();
    }
  });
</script>

<section class="timeline" data-test="lifecycle-timeline" aria-label="Domain lifecycle timeline">
  <nav class="filter-bar" aria-label="Activity filters">
    {#each ['hot','warm','cold'] as p (p)}
      <button
        type="button"
        class="chip"
        class:chip--on={activePaths.has(p as ActivityPath)}
        onclick={() => togglePath(p as ActivityPath)}
        title="{p} path events"
      >{p}</button>
    {/each}
    <span class="filter-sep" aria-hidden="true">·</span>
    <button
      type="button"
      class="chip"
      class:chip--on={activeFamilies.has('domain')}
      onclick={() => toggleFamily('domain')}
      title="Domain lifecycle (discover, reevaluate, dissolve)"
      aria-label="Domain lifecycle"
    >domain</button>
    <button
      type="button"
      class="chip"
      class:chip--on={activeFamilies.has('cluster')}
      onclick={() => toggleFamily('cluster')}
      title="Cluster lifecycle (split, merge, retire)"
      aria-label="Cluster lifecycle"
    >cluster</button>
    <button
      type="button"
      class="chip"
      class:chip--on={activeFamilies.has('pattern')}
      onclick={() => toggleFamily('pattern')}
      title="Pattern lifecycle (promote, demote, retire)"
      aria-label="Pattern lifecycle"
    >pattern</button>
    <button
      type="button"
      class="chip"
      class:chip--on={activeFamilies.has('readiness')}
      onclick={() => toggleFamily('readiness')}
      title="Readiness signals (stability, emergence)"
      aria-label="Readiness"
    >readiness</button>
    <button
      type="button"
      class="chip"
      class:chip--on={activeFamilies.has('operator_action')}
      onclick={() => toggleFamily('operator_action')}
      title="Operator actions (rebuild, reset, manual promote)"
      aria-label="Operator actions"
    >operator</button>
    <span class="filter-sep" aria-hidden="true">·</span>
    <button
      type="button"
      class="chip"
      class:chip--on={errorsOnly}
      onclick={() => errorsOnly = !errorsOnly}
      title="Show only error/failed/rejected events"
      aria-label="Errors only"
    >errors</button>
    <span class="filter-spacer" aria-hidden="true"></span>
    {#each PERIODS as p (p)}
      <button
        type="button"
        class="chip"
        data-test="period-chip"
        data-period={p}
        aria-pressed={observatoryStore.period === p}
        class:chip--on={observatoryStore.period === p}
        onclick={() => observatoryStore.setPeriod(p)}
        title="Window: last {p}"
      >{p}</button>
    {/each}
  </nav>

  {#if visibleEvents.length === 0}
    <p class="empty-copy">No recent activity — the taxonomy is quiet.</p>
  {:else}
    <ul class="timeline-list">
      {#each visibleEvents as evt (eventKey(evt))}
        {@const summary = keyMetric(evt)}
        <!-- svelte-ignore a11y_click_events_have_key_events -->
        <!-- svelte-ignore a11y_no_noninteractive_element_interactions -->
        <li
          class="timeline-row"
          data-path={evt.path}
          style="height: 20px;"
          onclick={() => toggleExpand(eventKey(evt))}
        >
          <span class="ts" style="font-family: var(--font-mono, monospace); font-size: 10px; width: 60px;">{evt.ts.slice(11, 16)}</span>
          <span class="path-badge" style="background-color: {pathColor(evt.path as ActivityPath)};">{evt.path}</span>
          <span class="op">{evt.op}</span>
          <span class="decision">{evt.decision}</span>
          {#if summary}
            <span class="metric" data-test="row-metric">{summary}</span>
          {/if}
        </li>
        {#if expandedId === eventKey(evt)}
          <li class="context-payload">{JSON.stringify(evt.context, null, 2)}</li>
        {/if}
      {/each}
    </ul>
  {/if}
</section>

<style>
  .timeline { padding: 0; display: flex; flex-direction: column; min-height: 0; }
  /*
   * Brand spec: Ultra-compact density (24px header). The filter-bar must
   * never wrap — chips with horizontally-scrollable overflow keep the row
   * at a fixed 24px even when many chips are present, preserving the
   * IDE-wide layout standard. `flex-wrap: nowrap` + per-chip `flex-shrink: 0`
   * + `overflow-x: auto` is the canonical pattern.
   */
  .filter-bar {
    display: flex;
    flex-wrap: nowrap;
    align-items: center;
    gap: 4px;
    padding: 2px 6px;
    height: 24px;
    border-bottom: 1px solid var(--color-border-subtle);
    flex-shrink: 0;
    overflow-x: auto;
    overflow-y: hidden;
    scrollbar-width: none;
  }
  .filter-bar::-webkit-scrollbar { display: none; }
  .filter-sep {
    color: var(--color-text-dim);
    font-size: 10px;
    flex-shrink: 0;
  }
  .filter-spacer { flex: 1; min-width: 0; }
  /*
   * Brand spec (chip pattern): 18px height, 16px line-height, 0 6px padding,
   * 10px Geist Mono uppercase. `white-space: nowrap` + `flex-shrink: 0`
   * prevent multi-line cascade when many chips compete for horizontal space.
   */
  .chip {
    height: 18px;
    line-height: 16px;
    padding: 0 6px;
    font-size: 10px;
    font-family: var(--font-mono);
    text-transform: uppercase;
    white-space: nowrap;
    flex-shrink: 0;
    background: transparent;
    border: 1px solid var(--color-border-subtle);
    color: var(--color-text-dim);
    cursor: pointer;
    transition: color var(--duration-hover) var(--ease-spring), border-color var(--duration-hover) var(--ease-spring);
  }
  .chip:hover { color: var(--color-text-primary); }
  .chip:focus-visible {
    outline: 1px solid rgba(0, 229, 255, 0.3);
    outline-offset: 2px;
  }
  .chip--on { border-color: var(--color-neon-cyan); color: var(--color-neon-cyan); }

  .empty-copy { padding: 6px; color: var(--color-text-dim); font-size: 11px; margin: 0; }

  .timeline-list { list-style: none; padding: 0; margin: 0; overflow-y: auto; }
  .timeline-row {
    display: flex;
    align-items: center;
    gap: 6px;
    padding: 0 6px;
    border-top: 1px solid var(--color-border-subtle);
    font-size: 11px;
    cursor: pointer;
  }
  .ts {
    width: 60px;
    font-family: var(--font-mono);
    font-size: 10px;
    color: var(--color-text-dim);
    flex-shrink: 0;
  }
  .path-badge {
    padding: 0 4px;
    font-size: 9px;
    font-family: var(--font-mono);
    color: var(--color-text-primary);
    flex-shrink: 0;
    text-transform: uppercase;
  }
  .op {
    font-family: var(--font-mono);
    font-size: 10px;
    color: var(--color-text-primary);
    flex-shrink: 0;
  }
  .decision {
    font-size: 11px;
    color: var(--color-text-secondary);
    flex-shrink: 0;
  }
  /*
   * One-line summary surfaced from `activity-summary.ts::keyMetric()`.
   * Geist Mono 10px text-dim — visually subordinated to the op/decision
   * pair so the eye still reads "lifecycle event" first, summary second.
   * Truncates with ellipsis when long.
   */
  .metric {
    font-family: var(--font-mono);
    font-size: 10px;
    color: var(--color-text-dim);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    min-width: 0;
    flex: 1 1 auto;
  }

  .context-payload {
    padding: 4px 72px;
    font-family: var(--font-mono);
    font-size: 10px;
    color: var(--color-text-secondary);
    background: var(--color-bg-card);
    white-space: pre;
    overflow: auto;
    list-style: none;
  }

  @media (prefers-reduced-motion: reduce) {
    .chip { transition-duration: 0.01ms !important; }
  }
</style>
