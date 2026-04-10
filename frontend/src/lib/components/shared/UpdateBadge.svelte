<script lang="ts">
  import { updateStore } from '$lib/stores/update.svelte';
  import { tooltip } from '$lib/actions/tooltip';

  let dialogEl = $state<HTMLDivElement | null>(null);

  function toggleDialog() {
    if (updateStore.updating) return;
    updateStore.dialogOpen = !updateStore.dialogOpen;
  }

  function handleUpdate() {
    updateStore.startUpdate();
  }

  function handleClickOutside(e: MouseEvent) {
    if (dialogEl && !dialogEl.contains(e.target as Node)) {
      updateStore.dialogOpen = false;
    }
  }

  $effect(() => {
    if (updateStore.dialogOpen) {
      document.addEventListener('click', handleClickOutside, true);
      return () => document.removeEventListener('click', handleClickOutside, true);
    }
  });

  const categoryColor: Record<string, string> = {
    Added: '#22c55e',
    Changed: '#eab308',
    Fixed: '#ef4444',
    Removed: '#ef4444',
    Deprecated: '#7a7a9e',
  };
</script>

<div class="update-badge-wrapper" bind:this={dialogEl}>
  {#if updateStore.updating}
    <span class="update-badge updating">&#8635; Restarting...</span>
  {:else}
    <button
      class="update-badge available"
      onclick={toggleDialog}
      use:tooltip={'Update available — click for details'}
    >
      &#8593; v{updateStore.latestVersion}
    </button>
  {/if}

  {#if updateStore.dialogOpen}
    <div class="update-dialog">
      <div class="dialog-header">
        <div>
          <div class="dialog-title">Update Available</div>
          <div class="dialog-subtitle">
            v{updateStore.currentVersion} &rarr; v{updateStore.latestVersion}
          </div>
        </div>
        <span class="dialog-new-badge">NEW</span>
      </div>

      {#if updateStore.changelogEntries && updateStore.changelogEntries.length > 0}
        <div class="dialog-changelog">
          <div class="changelog-label">What's New</div>
          {#each updateStore.changelogEntries as entry}
            <div class="changelog-entry">
              <span style="color: {categoryColor[entry.category] ?? '#7a7a9e'}">{entry.category}</span>
              &mdash; {entry.text}
            </div>
          {/each}
        </div>
      {/if}

      {#if !updateStore.hideDetachedWarning}
        <details class="dialog-warning">
          <summary>This will detach from your current branch</summary>
          <p>
            If you've made local commits or customizations, they won't be lost but
            will no longer be on an active branch. You can recover them later with
            <code>git checkout main &amp;&amp; git merge HEAD@{'{'}1{'}'}</code>.
          </p>
          <p class="warning-who">
            This matters if you've committed changes to strategies, prompts, or code.
            If you only use the app as-is, you can safely dismiss this warning.
          </p>
          <label class="warning-dismiss">
            <input
              type="checkbox"
              checked={updateStore.hideDetachedWarning}
              onchange={(e) => updateStore.dismissWarning((e.target as HTMLInputElement).checked)}
            />
            Don't show this warning again
          </label>
        </details>
      {/if}

      <div class="dialog-actions">
        <button class="btn-update" onclick={handleUpdate}>Update &amp; Restart</button>
        <button class="btn-later" onclick={() => updateStore.dialogOpen = false}>Later</button>
      </div>

      <div class="dialog-footer">
        Your data (database, preferences, embeddings) is preserved.
      </div>
    </div>
  {/if}
</div>

<style>
  .update-badge-wrapper {
    position: relative;
  }
  .update-badge {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    padding: 0 6px;
    font-family: var(--font-mono);
    font-size: 10px;
    letter-spacing: 0.5px;
    text-transform: uppercase;
    border: none;
    background: transparent;
    cursor: pointer;
    line-height: 18px;
  }
  .update-badge.available {
    color: #22c55e;
    border: 1px solid #22c55e;
  }
  .update-badge.updating {
    color: #eab308;
    cursor: default;
    animation: pulse 1.5s ease-in-out infinite;
  }
  @keyframes pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.5; }
  }
  .update-dialog {
    position: absolute;
    bottom: 24px;
    right: 0;
    width: 360px;
    border: 1px solid var(--color-border-subtle, #1a1a2e);
    background: var(--color-bg-secondary, #0d0d14);
    font-family: var(--font-mono);
    z-index: 100;
  }
  .dialog-header {
    padding: 12px 16px;
    border-bottom: 1px solid var(--color-border-subtle, #1a1a2e);
    display: flex;
    align-items: center;
    justify-content: space-between;
  }
  .dialog-title {
    color: var(--color-text, #e0e0e0);
    font-size: 13px;
    font-weight: 600;
  }
  .dialog-subtitle {
    color: var(--color-text-dim, #4a4a6e);
    font-size: 11px;
    margin-top: 2px;
  }
  .dialog-new-badge {
    color: #22c55e;
    font-size: 10px;
    border: 1px solid #22c55e;
    padding: 2px 6px;
    letter-spacing: 0.5px;
  }
  .dialog-changelog {
    padding: 12px 16px;
    border-bottom: 1px solid var(--color-border-subtle, #1a1a2e);
    max-height: 160px;
    overflow-y: auto;
  }
  .changelog-label {
    color: var(--color-text-dim, #7a7a9e);
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 1px;
    margin-bottom: 8px;
  }
  .changelog-entry {
    color: var(--color-text-secondary, #c0c0d0);
    font-size: 11px;
    line-height: 1.6;
    margin-bottom: 4px;
  }
  .dialog-warning {
    padding: 12px 16px;
    border-bottom: 1px solid var(--color-border-subtle, #1a1a2e);
    color: var(--color-text-dim, #7a7a9e);
    font-size: 10px;
    line-height: 1.5;
  }
  .dialog-warning summary {
    cursor: pointer;
    color: #eab308;
    font-size: 11px;
  }
  .dialog-warning p {
    margin: 8px 0 0;
  }
  .dialog-warning code {
    background: rgba(255,255,255,0.05);
    padding: 1px 4px;
  }
  .warning-who {
    color: var(--color-text-dim, #4a4a6e);
    font-style: italic;
  }
  .warning-dismiss {
    display: flex;
    align-items: center;
    gap: 6px;
    margin-top: 8px;
    cursor: pointer;
  }
  .warning-dismiss input {
    accent-color: #22c55e;
  }
  .dialog-actions {
    padding: 12px 16px;
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .btn-update {
    flex: 1;
    padding: 8px 0;
    text-align: center;
    border: 1px solid #22c55e;
    color: #22c55e;
    background: transparent;
    cursor: pointer;
    font-family: var(--font-mono);
    font-size: 11px;
    letter-spacing: 0.5px;
    text-transform: uppercase;
  }
  .btn-update:hover {
    background: rgba(34, 197, 94, 0.1);
  }
  .btn-later {
    padding: 8px 12px;
    text-align: center;
    border: 1px solid var(--color-border-subtle, #2a2a3e);
    color: var(--color-text-dim, #7a7a9e);
    background: transparent;
    cursor: pointer;
    font-family: var(--font-mono);
    font-size: 11px;
  }
  .dialog-footer {
    padding: 8px 16px 12px;
    color: var(--color-text-dim, #4a4a6e);
    font-size: 10px;
    line-height: 1.4;
  }
</style>
