<!-- frontend/src/lib/components/layout/RunRowItem.svelte -->
<script lang="ts">
  import { slide } from 'svelte/transition';
  import { patchRun, type RunSummary } from '$lib/api/runs';
  import { formatRelativeTime } from '$lib/utils/formatting';
  import RunDetailInline from './RunDetailInline.svelte';
  import RunActionMenu from './RunActionMenu.svelte';

  interface Props {
    run: RunSummary;
    expanded: boolean;
    onClick: () => void;
    selectMode?: boolean;
    selected?: boolean;
    onToggleSelect?: () => void;
    onDeleteConfirm?: (id: string) => void;
    onRenamed?: (updated: RunSummary) => void;
  }
  let { run, expanded, onClick, selectMode = false, selected = false, onToggleSelect, onDeleteConfirm, onRenamed }: Props = $props();

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
  let renameError = $state<string | null>(null);
  let kebabBtn: HTMLButtonElement | undefined = $state();

  // Svelte action: focus the element when mounted (replacement for the
  // discouraged `autofocus` attribute). Guarded so screen readers and
  // keyboard users keep predictable focus order.
  function focusOnMount(node: HTMLInputElement) {
    node.focus();
    return {};
  }

  function openMenu(): void {
    menuOpen = true;
  }
  function closeMenu(): void {
    menuOpen = false;
    kebabBtn?.focus();
  }
  function startRename(): void {
    renameDraft = displayLabel === run.id ? '' : displayLabel;
    renaming = true;
    menuOpen = false;
  }
  async function submitRename(): Promise<void> {
    try {
      const updated = await patchRun(run.id, { display_name: renameDraft });
      // Optimistic — but actually pessimistic since we await. Update the local prop is read-only;
      // RunsPanel's runs[] will be refreshed on next list query OR via callback.
      // For now: just exit rename mode; the next list-refresh will show new display_name.
      onRenamed?.(updated);
      renaming = false;
    } catch (err) {
      // Stay in rename mode; show inline error
      renameError = err instanceof Error ? err.message : 'Rename failed';
    }
  }
  function cancelRename(): void {
    renaming = false;
    renameError = null;
  }
</script>

<div class="run-row" class:expanded class:selected role="listitem">
  <div class="run-row-content">
    {#if selectMode}
      <input
        type="checkbox"
        class="row-checkbox"
        aria-label="Select row {displayLabel}"
        checked={selected}
        onchange={onToggleSelect}
        onclick={(e) => e.stopPropagation()}
      />
    {/if}
    <button
      class="run-row-header"
      onclick={onClick}
      aria-expanded={expanded}
      aria-label="{MODE_LABEL[run.mode]} {displayLabel}"
    >
      <span class="run-mode chip">{MODE_LABEL[run.mode]}</span>
      {#if renaming}
        <input
          class="input-field rename-input"
          bind:value={renameDraft}
          maxlength="200"
          use:focusOnMount
          onkeydown={(e) => {
            if (e.key === 'Enter') { e.preventDefault(); submitRename(); }
            if (e.key === 'Escape') { e.preventDefault(); cancelRename(); }
          }}
          onblur={() => { if (renaming) submitRename(); }}
          onclick={(e) => e.stopPropagation()}
        />
      {:else}
        <span class="run-topic">{displayLabel}</span>
      {/if}
      <span class="run-status {STATUS_COLOR[run.status]}">{run.status}</span>
      <span class="run-time text-text-dim">{formatRelativeTime(run.started_at)}</span>
    </button>
    <button
      bind:this={kebabBtn}
      class="btn-icon kebab-btn"
      aria-label="Open actions menu"
      aria-haspopup="menu"
      aria-expanded={menuOpen}
      onclick={openMenu}
    >
      ⋯
    </button>
    {#if menuOpen}
      <RunActionMenu
        onRename={startRename}
        onDelete={() => { closeMenu(); onDeleteConfirm?.(run.id); }}
        onClose={closeMenu}
      />
    {/if}
  </div>
  {#if renameError}
    <p class="text-[10px] text-neon-red" role="alert">{renameError}</p>
  {/if}
  {#if expanded}
    <div transition:slide={{ duration: 200 }} class="run-detail">
      <RunDetailInline {run} />
    </div>
  {/if}
</div>

<style>
  .run-row { border-bottom: 1px solid var(--color-border-subtle); }
  .run-row.selected {
    background: color-mix(in srgb, var(--color-neon-cyan) 8%, transparent);
    border-left: 1px solid var(--color-neon-cyan);
  }
  .run-row-content {
    display: flex;
    align-items: center;
    gap: 6px;
  }
  .row-checkbox {
    width: 16px;
    height: 16px;
    border-radius: 0;
    cursor: pointer;
  }
  .kebab-btn {
    flex-shrink: 0;
  }
  .rename-input {
    flex: 1;
    min-width: 0;
  }
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
