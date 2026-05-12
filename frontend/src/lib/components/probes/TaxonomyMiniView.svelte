<script lang="ts">
  // TaxonomyMiniView — slim taxonomy panel rendering emergent nodes
  // (domains + sub-domains) that appeared during a Topic Probe run.
  //
  // Two invariants per spec §6:
  //   1. New nodes carry `animation: forge-spark 250ms ease-out` so the
  //      reveal feels like the brand Validate-stage signature flash.
  //   2. Nodes created within the last 30s carry a `NEW` chip badge —
  //      after the cool-off window the chip disappears so the tree
  //      stops shouting once the run has settled.
  //
  // Pure render — the parent (TopicProbeProgressView) owns the SSE
  // wiring and passes the typed `nodes` prop down. Recomputing the
  // "is recent" classification on every render keeps the chip in sync
  // without a per-node timer.

  export interface MiniNode {
    id: string;
    label: string;
    kind: 'domain' | 'sub_domain';
    /** ISO timestamp — used to decide whether to flash the NEW chip. */
    created_at: string;
    /** True iff freshly emergent in the current run — drives the
     *  forge-spark entrance animation. */
    is_new?: boolean;
  }

  interface Props {
    nodes: MiniNode[];
  }

  const { nodes }: Props = $props();

  const NEW_CHIP_WINDOW_MS = 30_000;

  function isRecent(createdAt: string): boolean {
    const ts = Date.parse(createdAt);
    if (!Number.isFinite(ts)) return false;
    return Date.now() - ts < NEW_CHIP_WINDOW_MS;
  }
</script>

<div class="mini-view" aria-label="Emergent taxonomy">
  {#if nodes.length === 0}
    <span class="mini-empty">No new nodes yet.</span>
  {:else}
    <ul class="mini-list">
      {#each nodes as node (node.id)}
        {@const recent = isRecent(node.created_at)}
        <li
          class="mini-node"
          class:mini-node--new={node.is_new}
          class:mini-node--sub={node.kind === 'sub_domain'}
          data-test="mini-node"
          data-node-id={node.id}
          data-kind={node.kind}
          style={node.is_new ? 'animation: forge-spark 250ms ease-out;' : undefined}
        >
          <span class="mini-dot" aria-hidden="true"></span>
          <span class="mini-label">{node.label}</span>
          {#if recent}
            <span class="mini-chip" data-test="new-chip">NEW</span>
          {/if}
        </li>
      {/each}
    </ul>
  {/if}
</div>

<style>
  .mini-view {
    display: flex;
    flex-direction: column;
    gap: 4px;
    min-width: 180px;
    padding: 6px;
    border: 1px solid var(--color-border-subtle);
    background: var(--color-bg-primary);
    font-family: var(--font-mono);
  }

  .mini-empty {
    font-size: 10px;
    color: var(--color-text-dim);
    padding: 4px;
  }

  .mini-list {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: 2px;
  }

  .mini-node {
    display: flex;
    align-items: center;
    gap: 6px;
    padding: 3px 6px;
    font-size: 10px;
    color: var(--color-text-secondary);
    border: 1px solid transparent;
    height: 20px;
    box-sizing: border-box;
  }

  .mini-node--new {
    color: var(--color-text-primary);
  }

  .mini-node--sub {
    margin-left: 10px;
  }

  .mini-dot {
    width: 4px;
    height: 4px;
    background: var(--color-neon-cyan);
    flex-shrink: 0;
  }

  .mini-node--sub .mini-dot {
    background: var(--color-neon-purple);
  }

  .mini-label {
    flex: 1;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    letter-spacing: 0.02em;
  }

  .mini-chip {
    font-size: 8px;
    font-weight: 700;
    letter-spacing: 0.08em;
    color: var(--color-neon-yellow);
    border: 1px solid var(--color-neon-yellow);
    padding: 0 4px;
    height: 12px;
    line-height: 12px;
    box-sizing: border-box;
    text-transform: uppercase;
  }
</style>
