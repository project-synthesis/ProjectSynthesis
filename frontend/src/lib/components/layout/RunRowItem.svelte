<!-- frontend/src/lib/components/layout/RunRowItem.svelte -->
<script lang="ts">
  import { slide } from 'svelte/transition';
  import type { RunSummary } from '$lib/api/runs';
  import { formatRelativeTime } from '$lib/utils/formatting';
  import RunDetailInline from './RunDetailInline.svelte';
  import RunActionMenu from './RunActionMenu.svelte';

  interface Props {
    run: RunSummary;
    expanded: boolean;
    onClick: () => void;
  }
  let { run, expanded, onClick }: Props = $props();

  const MODE_LABEL: Record<RunSummary['mode'], string> = {
    topic_probe: 'probe',
    seed_agent: 'seed',
    replay_run: 'replay',
  };
  const STATUS_COLOR: Record<RunSummary['status'], string> = {
    running: 'text-neon-cyan',
    completed: 'text-neon-green',
    partial: 'text-neon-yellow',
    failed: 'text-neon-red',
  };

  // v0.4.32 — operator-writable label takes precedence over the system-set
  // ``topic``; falls back to the row id for never-named rows.
  const displayLabel = $derived(run.display_name ?? run.topic ?? run.id);

  // v0.4.32 — kebab menu state + rename state
  let menuOpen = $state(false);
  let renaming = $state(false);
  let renameDraft = $state('');
  let kebabBtn: HTMLButtonElement | undefined = $state();

  // STUB — full impl in GREEN
  function openMenu(): void {
    throw new Error('RED phase — implement in GREEN');
  }
  function closeMenu(): void {
    throw new Error('RED phase — implement in GREEN');
  }
  function startRename(): void {
    throw new Error('RED phase — implement in GREEN');
  }
  async function submitRename(): Promise<void> {
    throw new Error('RED phase — implement in GREEN');
  }
  function cancelRename(): void {
    throw new Error('RED phase — implement in GREEN');
  }
</script>

<div class="run-row" class:expanded role="listitem">
  <button
    class="run-row-header"
    onclick={onClick}
    aria-expanded={expanded}
    aria-label="{MODE_LABEL[run.mode]} {run.topic ?? run.id}"
  >
    <span class="run-mode chip">{MODE_LABEL[run.mode]}</span>
    <span class="run-topic">{run.topic ?? run.id}</span>
    <span class="run-status {STATUS_COLOR[run.status]}">{run.status}</span>
    <span class="run-time text-text-dim">{formatRelativeTime(run.started_at)}</span>
  </button>
  {#if expanded}
    <div transition:slide={{ duration: 200 }} class="run-detail">
      <RunDetailInline {run} />
    </div>
  {/if}
</div>

<style>
  .run-row { border-bottom: 1px solid var(--color-border-subtle); }
  .run-row-header {
    display: grid;
    grid-template-columns: auto 1fr auto auto;
    gap: 6px;
    align-items: center;
    width: 100%;
    padding: 4px 6px;
    background: transparent;
    border: none;
    cursor: pointer;
    color: var(--color-text-primary);
    font-size: 11px;
    text-align: left;
  }
  .run-row-header:hover {
    background: color-mix(in srgb, var(--color-bg-hover) 40%, transparent);
  }
  .run-topic {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .run-status {
    font-family: var(--font-mono);
    font-size: 10px;
  }
  .run-time { font-size: 10px; }
  .run-detail { padding: 4px 6px 6px; }
</style>
