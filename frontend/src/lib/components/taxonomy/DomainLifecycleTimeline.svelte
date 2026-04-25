<script lang="ts">
  /**
   * DomainLifecycleTimeline — Observatory tier-1 surface for taxonomy activity.
   *
   * Renders `clustersStore.activityEvents` as a chronological row list with
   * three filter dimensions: hot/warm/cold execution path, op-family
   * (domain | cluster | pattern | readiness), and an `errors only` toggle.
   * Rows expand to reveal the raw context payload. Period changes on
   * `observatoryStore` trigger a backfill against `/clusters/activity/history`
   * so the panel stays consistent with the global window selector.
   *
   * Op→family mapping is 1-to-1 by intent: `promote` belongs to the pattern
   * family per ADR-005 ("global pattern promotion"); cluster lifecycle uses
   * `split`/`merge`/`retire`. Uncategorised ops surface only when every
   * family chip is on, matching ActivityPanel's "no filter = show all" UX.
   */
  import { clustersStore } from '$lib/stores/clusters.svelte';
  import { observatoryStore } from '$lib/stores/observatory.svelte';
  import { pathColor, type ActivityPath } from '$lib/utils/activity-colors';
  import type { ObservatoryPeriod } from '$lib/api/observatory';

  type OpFamily = 'domain' | 'cluster' | 'pattern' | 'readiness';

  // Period chips live in this filter-bar (NOT in TaxonomyObservatory's shell
  // header) because Readiness is current-state and the asymmetry is owned
  // by the period-aware panels. See TaxonomyObservatory legend (TO3).
  const PERIODS: readonly ObservatoryPeriod[] = ['24h', '7d', '30d'];

  // 1-to-1 op→family lookup. Each backend op key maps to exactly one family
  // so toggling a chip never produces overlap (e.g. `promote` is pattern-only,
  // matching its sole hot-path emitter `global_pattern_promoted`). Ops not
  // present here fall through to the "uncategorised" bucket and only render
  // when every family chip is active (default state).
  const OP_TO_FAMILY: Record<string, OpFamily> = {
    discover: 'domain',
    reevaluate: 'domain',
    dissolve: 'domain',
    split: 'cluster',
    merge: 'cluster',
    retire: 'cluster',
    promote: 'pattern',
    demote: 'pattern',
    re_promote: 'pattern',
    retired: 'pattern',
    global_pattern: 'pattern',
    meta_pattern: 'pattern',
    readiness: 'readiness',
    signal_adjuster: 'readiness',
  };

  const events = $derived(clustersStore.activityEvents);

  let activePaths = $state<Set<ActivityPath>>(new Set(['hot', 'warm', 'cold']));
  let activeFamilies = $state<Set<OpFamily>>(new Set(['domain', 'cluster', 'pattern', 'readiness']));
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

  function isErrorEvent(e: { op: string; decision: string }): boolean {
    return e.op === 'error' || e.decision === 'rejected' || e.decision === 'failed';
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

  const ALL_FAMILIES: readonly OpFamily[] = ['domain', 'cluster', 'pattern', 'readiness'];

  function eventFamily(op: string): OpFamily | null {
    // 1-to-1 lookup; falls back to readiness sub-paths via prefix.
    if (op in OP_TO_FAMILY) return OP_TO_FAMILY[op];
    if (op.startsWith('readiness/')) return 'readiness';
    return null;
  }

  const visibleEvents = $derived(events.filter((e) => {
    if (errorsOnly) return isErrorEvent(e);
    if (!activePaths.has(e.path as ActivityPath)) return false;
    // 'error' op is included whenever any family is active.
    if (e.op === 'error') return activeFamilies.size > 0;
    const fam = eventFamily(e.op);
    // Uncategorised events show only when every family chip is on (default state).
    if (fam === null) return activeFamilies.size === ALL_FAMILIES.length;
    return activeFamilies.has(fam);
  }));

  // Period chips drive the Heatmap window only; Timeline is SSE-live and
  // shows whatever is currently in `clustersStore.activityEvents`. The prior
  // period→`/clusters/activity/history` backfill silently discarded its
  // response (no merge into activityEvents), making the chips a no-op for
  // this panel — see TaxonomyObservatory legend (TO3) and the wiring-fix
  // audit. If a windowed Timeline is re-introduced, route the response
  // through `clustersStore` so the events actually become visible.
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
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
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
