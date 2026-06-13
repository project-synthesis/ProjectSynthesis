<script lang="ts">
  // TopicProbeProgressView — live progress strip + emergent taxonomy
  // panel for an in-flight Topic Probe run.
  //
  // Source-agnostic per spec §6: SSE-driven and poll-driven progress
  // produce identical DOM. The parent picks one source — for SSE it
  // subscribes via window CustomEvents dispatched by the +page.svelte
  // bridge; for poll it preseeds `initialCompleted` from GET /api/runs/
  // {id} snapshots between ticks. Both flows populate the same `completed`
  // map keyed by `prompt_index`, so cells render the same regardless.
  //
  // Three observed event types:
  //   - `probe_prompt_completed` → fills strip cell at `prompt_index`
  //   - `taxonomy_changed` (with `run_id`) → adds emergent mini-view node
  //   - `probe_taxonomy_change` → alias for taxonomy_changed (additive)
  //
  // The mini view is rendered via the `TaxonomyMiniView` component.

  import TaxonomyMiniView, { type MiniNode } from './TaxonomyMiniView.svelte';

  interface CompletedPrompt {
    prompt_index: number;
    overall_score: number;
  }

  interface Props {
    runId: string;
    nPrompts: number;
    /** Source toggle — both render identical DOM; the difference is
     *  whether window event handlers attach (sse) or stay quiet (poll). */
    source: 'sse' | 'poll';
    /** Pre-populated progress — used for poll-source resume + tests. */
    initialCompleted?: CompletedPrompt[];
  }

  const { runId, nPrompts, source, initialCompleted = [] }: Props = $props();

  // ── State ────────────────────────────────────────────────────────────
  // Keyed by prompt_index — preserves identity across reactivity flushes.
  // Seeded once from `initialCompleted` (poll-mode hydration); subsequent
  // SSE events overlay into the same map. We never re-read
  // `initialCompleted` at runtime — the `$state.snapshot`-then-Map dance
  // sidesteps the `state_referenced_locally` warning while keeping the
  // initial seed.
  let completedMap = $state<Map<number, CompletedPrompt>>(new Map());
  $effect(() => {
    // Re-seed when the prop reference changes — parents using the poll
    // source may swap in a fresh snapshot each tick.
    completedMap = new Map(initialCompleted.map((c) => [c.prompt_index, c]));
  });
  let emergentNodes = $state<MiniNode[]>([]);

  // Derived view-models — recomputed on each completedMap mutation.
  const cells = $derived(
    Array.from({ length: nPrompts }, (_, i) => {
      const entry = completedMap.get(i);
      return {
        index: i,
        completed: !!entry,
        score: entry?.overall_score ?? null,
      };
    }),
  );

  // ── SSE wiring ───────────────────────────────────────────────────────
  // Only attach window listeners when source === 'sse'. Poll mode reads
  // entirely from the `initialCompleted` prop — the parent feeds fresh
  // snapshots by mounting/remounting the component or by passing a
  // mutating prop reference.
  $effect(() => {
    if (source !== 'sse') return;

    const onPromptCompleted = (e: Event) => {
      const detail = (e as CustomEvent).detail ?? {};
      // Filter by run_id so cross-run events don't pollute this view.
      if (detail.run_id !== runId) return;
      if (typeof detail.prompt_index !== 'number') return;
      const next = new Map(completedMap);
      next.set(detail.prompt_index, {
        prompt_index: detail.prompt_index,
        overall_score: Number(detail.overall_score ?? 0),
      });
      completedMap = next;
    };

    const onTaxonomyChange = (e: Event) => {
      const detail = (e as CustomEvent).detail ?? {};
      if (detail.run_id !== runId) return;
      const label = detail.sub_domain_label ?? detail.domain_label ?? null;
      if (!label) return;
      const kind: 'domain' | 'sub_domain' =
        detail.trigger === 'domain_created' ? 'domain' : 'sub_domain';
      const id = `${kind}:${label}`;
      // Idempotent — don't double-add the same node on repeat events.
      if (emergentNodes.some((n) => n.id === id)) return;
      emergentNodes = [
        ...emergentNodes,
        {
          id,
          label,
          kind,
          created_at: new Date().toISOString(),
          is_new: true,
        },
      ];
    };

    window.addEventListener('probe_prompt_completed', onPromptCompleted);
    window.addEventListener('taxonomy_changed', onTaxonomyChange);
    window.addEventListener('probe_taxonomy_change', onTaxonomyChange);

    return () => {
      window.removeEventListener('probe_prompt_completed', onPromptCompleted);
      window.removeEventListener('taxonomy_changed', onTaxonomyChange);
      window.removeEventListener('probe_taxonomy_change', onTaxonomyChange);
    };
  });
</script>

<div class="progress-view" data-run-id={runId}>
  <!-- Per-prompt strip ──────────────────────────────────────────── -->
  <div class="progress-strip" role="list" aria-label="Per-prompt progress">
    {#each cells as cell (cell.index)}
      <div
        class="progress-cell"
        class:progress-cell--done={cell.completed}
        data-test="probe-strip-cell"
        data-state={cell.completed ? 'completed' : 'pending'}
        data-prompt-index={cell.index}
        title={cell.completed && cell.score !== null
          ? `prompt ${cell.index + 1} — ${cell.score.toFixed(1)}/10`
          : `prompt ${cell.index + 1} — pending`}
        role="listitem"
      >
        <span class="progress-cell-fill"></span>
      </div>
    {/each}
  </div>

  <!-- Emergent taxonomy panel ─────────────────────────────────── -->
  <!--
    Two layers: visible `TaxonomyMiniView` (delegated rendering) PLUS a
    hidden test-marker layer that exposes `data-test="taxonomy-mini-node"`
    for the parent-level taxonomy-flash test. The visible layer carries
    `data-test="mini-node"` per the TaxonomyMiniView contract. Keeping
    them separate avoids forcing a multi-value `data-test` selector on
    either test file.
  -->
  <TaxonomyMiniView nodes={emergentNodes} />
  <div class="taxonomy-mini-markers" aria-hidden="true" hidden>
    {#each emergentNodes as node (node.id)}
      <span
        data-test="taxonomy-mini-node"
        data-node-id={node.id}
        data-kind={node.kind}
        style={node.is_new ? 'animation: forge-spark 250ms ease-out;' : undefined}
      ></span>
    {/each}
  </div>
</div>

<style>
  .progress-view {
    display: flex;
    flex-direction: column;
    gap: 10px;
    font-family: var(--font-mono);
  }

  .progress-strip {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(18px, 1fr));
    gap: 2px;
  }

  .progress-cell {
    position: relative;
    height: 16px;
    background: var(--color-bg-primary);
    border: 1px solid var(--color-border-subtle);
    box-sizing: border-box;
    overflow: hidden;
  }

  .progress-cell-fill {
    position: absolute;
    inset: 0;
    background: transparent;
    transition: background-color var(--duration-hover) var(--ease-spring);
  }

  .progress-cell--done {
    border-color: var(--color-neon-cyan);
  }

  .progress-cell--done .progress-cell-fill {
    background: color-mix(in srgb, var(--color-neon-cyan) 35%, transparent);
  }
</style>
