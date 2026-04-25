<script lang="ts">
  import { updateStore } from '$lib/stores/update.svelte';
  import { tooltip } from '$lib/actions/tooltip';

  let dialogEl = $state<HTMLDivElement | null>(null);
  let badgeEl = $state<HTMLButtonElement | null>(null);
  let dialogStyle = $state('');

  function toggleDialog(e: MouseEvent) {
    if (updateStore.updating || updateStore.updateComplete) return;
    e.stopPropagation();
    updateStore.dialogOpen = !updateStore.dialogOpen;
    if (updateStore.dialogOpen && badgeEl) {
      const rect = badgeEl.getBoundingClientRect();
      dialogStyle = `position:fixed;bottom:${window.innerHeight - rect.top + 4}px;right:${window.innerWidth - rect.right}px;`;
      // Fetch the preflight readiness probe so the user sees dirty
      // files / divergence / in-flight ops BEFORE clicking apply.
      updateStore.loadPreflight();
    }
  }

  function handleUpdate(e: MouseEvent) {
    e.stopPropagation();
    updateStore.startUpdate(false);
  }

  function handleForceUpdate(e: MouseEvent) {
    e.stopPropagation();
    updateStore.startUpdate(true);
  }

  function handleRetry(e: MouseEvent) {
    e.stopPropagation();
    updateStore.retryHealthCheck();
  }

  /** Map a step name to its display state for the timeline. */
  function stepDisplayStatus(step: string):
    | 'pending' | 'running' | 'done' | 'warning' | 'failed' {
    const event = [...updateStore.stepHistory].reverse().find((e) => e.step === step);
    if (!event) return 'pending';
    return event.status;
  }

  function stepDetail(step: string): string {
    const event = [...updateStore.stepHistory].reverse().find((e) => e.step === step);
    return event?.detail ?? '';
  }

  const STEP_LABELS: Record<string, string> = {
    preflight: 'Pre-flight',
    drain: 'Drain in-flight',
    fetch_tags: 'Fetch tags',
    stash: 'Stash user edits',
    checkout: 'Checkout tag',
    deps: 'Install deps',
    migrate: 'Run migrations',
    pop_stash: 'Restore user edits',
    restart: 'Restart services',
    validate: 'Validate health',
  };

  function handleClickOutside(e: MouseEvent) {
    const target = e.target as Node;
    if (dialogEl && !dialogEl.contains(target) && badgeEl && !badgeEl.contains(target)) {
      updateStore.dialogOpen = false;
    }
  }

  $effect(() => {
    if (updateStore.dialogOpen) {
      // Defer listener to next tick so the opening click doesn't
      // immediately trigger close via the capture-phase handler.
      const timer = setTimeout(() => {
        document.addEventListener('click', handleClickOutside);
      }, 0);
      return () => {
        clearTimeout(timer);
        document.removeEventListener('click', handleClickOutside);
      };
    }
  });

  const categoryColor: Record<string, string> = {
    Added: 'var(--color-neon-green)',
    Changed: 'var(--color-neon-yellow)',
    Fixed: 'var(--color-neon-red)',
    Removed: 'var(--color-neon-red)',
    Deprecated: 'var(--color-text-dim)',
  };
</script>

<div class="update-badge-wrapper">
  {#if updateStore.updating}
    <button
      class="update-badge updating"
      bind:this={badgeEl}
      onclick={toggleDialog}
      use:tooltip={`Updating — step: ${updateStore.updateStep ?? 'starting…'}`}
    >&#8635; {updateStore.updateStep ? STEP_LABELS[updateStore.updateStep] ?? updateStore.updateStep : 'Restarting…'}</button>
  {:else if updateStore.pollTimeout}
    <button
      class="update-badge timeout"
      onclick={handleRetry}
      use:tooltip={'Health check timed out — click to retry'}
    >&#8635; Retry</button>
  {:else if updateStore.updateComplete && updateStore.stashPopConflicts.length > 0}
    <button
      class="update-badge warning"
      bind:this={badgeEl}
      onclick={(e) => { e.stopPropagation(); updateStore.dialogOpen = !updateStore.dialogOpen; }}
      use:tooltip={'Update applied with stash-pop conflicts — click for details'}
    >&#9888; {updateStore.stashPopConflicts.length} conflict{updateStore.stashPopConflicts.length === 1 ? '' : 's'}</button>
  {:else}
    <button
      class="update-badge available"
      bind:this={badgeEl}
      onclick={toggleDialog}
      use:tooltip={'Update available — click for details'}
    >
      <span class="badge-dot"></span>
      &#8593; v{updateStore.latestVersion}
    </button>
  {/if}

  {#if updateStore.updating}
    <div class="progress-timeline" bind:this={dialogEl} style={dialogStyle}>
      <div class="progress-header">Update in progress</div>
      {#each updateStore.stepOrder as step}
        {@const status = stepDisplayStatus(step)}
        <div class="progress-row" data-status={status}>
          <span class="progress-icon">
            {#if status === 'pending'}∙
            {:else if status === 'running'}↻
            {:else if status === 'done'}✓
            {:else if status === 'warning'}!
            {:else}×{/if}
          </span>
          <span class="progress-label">{STEP_LABELS[step] ?? step}</span>
          {#if stepDetail(step)}
            <span class="progress-detail">{stepDetail(step)}</span>
          {/if}
        </div>
      {/each}
    </div>
  {/if}

  {#if updateStore.dialogOpen && updateStore.updateComplete}
    <div class="update-dialog completion" bind:this={dialogEl} style={dialogStyle}>
      <div class="dialog-header">
        <div>
          <div class="dialog-title">
            {updateStore.updateSuccess ? 'Update applied' : 'Update completed with warnings'}
          </div>
          <div class="dialog-subtitle">
            v{updateStore.currentVersion} &rarr; v{updateStore.latestVersion}
          </div>
        </div>
        <span class="dialog-new-badge" style="color: {updateStore.updateSuccess ? 'var(--color-neon-green)' : 'var(--color-neon-yellow)'}; border-color: currentColor;">
          {updateStore.updateSuccess ? 'OK' : 'WARN'}
        </span>
      </div>

      {#if updateStore.validationChecks.length > 0}
        <div class="dialog-changelog">
          <div class="changelog-label">Validation checks</div>
          {#each updateStore.validationChecks as chk}
            <div class="changelog-entry">
              <span style="color: {chk.passed ? 'var(--color-neon-green)' : 'var(--color-neon-red)'}">
                {chk.passed ? '✓' : '×'}
              </span>
              {chk.name}{chk.detail ? ' — ' + chk.detail : ''}
            </div>
          {/each}
        </div>
      {/if}

      {#if updateStore.stashPopConflicts.length > 0}
        <div class="dialog-changelog conflict-section">
          <div class="changelog-label" style="color: var(--color-neon-red);">Stash-pop conflicts</div>
          <div class="changelog-entry">
            Your prompt edits couldn't be cleanly restored. Resolve via
            <code>git status</code> — the listed files are in an unmerged state:
          </div>
          {#each updateStore.stashPopConflicts as path}
            <div class="changelog-entry">
              <span style="color: var(--color-neon-red);">!</span> {path}
            </div>
          {/each}
        </div>
      {/if}

      <div class="dialog-actions">
        <button class="btn-update" onclick={() => { updateStore.updateComplete = false; updateStore.dialogOpen = false; }}>
          Acknowledge
        </button>
      </div>
    </div>
  {:else if updateStore.dialogOpen}
    <div class="update-dialog" bind:this={dialogEl} style={dialogStyle}>
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
              <span style="color: {categoryColor[entry.category] ?? 'var(--color-text-dim)'}">{entry.category}</span>
              &mdash; {entry.text}
            </div>
          {/each}
        </div>
      {/if}

      {#if updateStore.preflightLoading}
        <div class="dialog-preflight loading">Checking readiness…</div>
      {:else if updateStore.preflight}
        {@const pf = updateStore.preflight}
        <div class="dialog-preflight" class:has-blocks={pf.blocking_issues.length > 0}>
          <div class="preflight-label">Pre-flight</div>

          {#if pf.blocking_issues.length > 0}
            {#each pf.blocking_issues as issue}
              <div class="preflight-row blocking">
                <span class="preflight-icon">×</span> {issue}
              </div>
            {/each}
          {/if}

          {#if pf.warnings.length > 0}
            {#each pf.warnings as warn}
              <div class="preflight-row warn">
                <span class="preflight-icon">!</span> {warn}
              </div>
            {/each}
          {/if}

          {#if pf.dirty_files.length > 0}
            <details class="preflight-details">
              <summary>
                {pf.dirty_files.length} dirty file{pf.dirty_files.length === 1 ? '' : 's'}
                {#if pf.will_auto_stash}<span class="auto-stash-tag">auto-stash</span>{/if}
              </summary>
              {#each pf.dirty_files as df}
                <div class="preflight-row file">
                  <span class="dirty-source dirty-source-{df.source}">{df.source}</span>
                  <span class="dirty-path">{df.path}</span>
                  <span class="dirty-status">{df.status}</span>
                </div>
              {/each}
            </details>
          {/if}

          {#if pf.user_customizations.length > 0 && pf.dirty_files.length === 0}
            <div class="preflight-row note">
              <span class="preflight-icon">i</span>
              {pf.user_customizations.length} prompt customization{pf.user_customizations.length === 1 ? '' : 's'} on file (clean working tree).
            </div>
          {/if}

          {#if pf.in_flight_optimizations > 0}
            <div class="preflight-row note">
              <span class="preflight-icon">»</span>
              {pf.in_flight_optimizations} optimization{pf.in_flight_optimizations === 1 ? '' : 's'} running — drain wait up to 60s.
            </div>
          {/if}
        </div>
      {:else if updateStore.preflightError}
        <div class="dialog-preflight loading">Pre-flight error: {updateStore.preflightError}</div>
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
        {#if updateStore.preflight && !updateStore.preflight.can_apply}
          <button class="btn-update" disabled>Blocked — fix issues first</button>
        {:else if updateStore.preflight && updateStore.preflight.warnings.length > 0}
          <button
            class="btn-update btn-update-warning"
            onclick={handleForceUpdate}
            use:tooltip={'Apply with warnings (commits-ahead / in-flight)'}
          >Update &amp; Restart (force)</button>
        {:else}
          <button class="btn-update" onclick={handleUpdate}>Update &amp; Restart</button>
        {/if}
        <button class="btn-later" onclick={() => updateStore.dialogOpen = false}>Later</button>
      </div>

      <div class="dialog-footer">
        {#if updateStore.preflight && updateStore.preflight.will_auto_stash}
          Edited prompts are auto-stashed during checkout and restored after.
        {:else}
          Your data (database, preferences, embeddings) is preserved.
        {/if}
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
    color: var(--color-neon-green);
    border: 1px solid var(--color-neon-green);
    border-radius: 0;
    position: relative;
  }
  .badge-dot {
    position: absolute;
    top: -3px;
    right: -3px;
    width: 7px;
    height: 7px;
    background: var(--color-neon-green);
    border: 1px solid var(--color-bg-primary);
    animation: pulse-dot 2s ease-in-out infinite;
  }
  @keyframes pulse-dot {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.4; }
  }
  .update-badge.updating {
    color: var(--color-neon-yellow);
    cursor: default;
    animation: pulse 1.5s ease-in-out infinite;
  }
  @keyframes pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.5; }
  }
  .update-dialog {
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
    color: var(--color-neon-green);
    font-size: 10px;
    border: 1px solid var(--color-neon-green);
    padding: 2px 6px;
    letter-spacing: 0.5px;
    border-radius: 0;
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
    color: var(--color-neon-yellow);
    font-size: 11px;
  }
  .dialog-warning p {
    margin: 8px 0 0;
  }
  .dialog-warning code {
    background: color-mix(in srgb, var(--color-text-primary) 5%, transparent);
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
    accent-color: var(--color-neon-green);
    border-radius: 0;
    -webkit-appearance: none;
    appearance: none;
    width: 12px;
    height: 12px;
    border: 1px solid var(--color-border-subtle);
    background: transparent;
    cursor: pointer;
    position: relative;
  }
  .warning-dismiss input:checked {
    border-color: var(--color-neon-green);
    background: var(--color-neon-green);
  }
  .warning-dismiss input:checked::after {
    content: '';
    position: absolute;
    top: 1px;
    left: 3px;
    width: 4px;
    height: 7px;
    border: solid var(--color-bg-primary);
    border-width: 0 1.5px 1.5px 0;
    transform: rotate(45deg);
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
    border: 1px solid var(--color-neon-green);
    color: var(--color-neon-green);
    background: transparent;
    cursor: pointer;
    font-family: var(--font-mono);
    font-size: 11px;
    letter-spacing: 0.5px;
    text-transform: uppercase;
    border-radius: 0;
  }
  .btn-update:hover {
    background: color-mix(in srgb, var(--color-neon-green) 10%, transparent);
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
    border-radius: 0;
  }
  .dialog-footer {
    padding: 8px 16px 12px;
    color: var(--color-text-dim, #4a4a6e);
    font-size: 10px;
    line-height: 1.4;
  }

  /* --- Pre-flight panel (v0.4.6 hardening) ----------------------- */
  .dialog-preflight {
    padding: 10px 16px;
    border-bottom: 1px solid var(--color-border-subtle, #1a1a2e);
    font-size: 10px;
    line-height: 1.5;
    color: var(--color-text-secondary, #c0c0d0);
  }
  .dialog-preflight.loading {
    color: var(--color-text-dim);
    font-style: italic;
  }
  .dialog-preflight.has-blocks {
    border-left: 1px solid var(--color-neon-red);
  }
  .preflight-label {
    color: var(--color-text-dim);
    font-size: 9px;
    text-transform: uppercase;
    letter-spacing: 1px;
    margin-bottom: 6px;
  }
  .preflight-row {
    display: flex;
    gap: 6px;
    align-items: baseline;
    padding: 2px 0;
  }
  .preflight-row.blocking {
    color: var(--color-neon-red);
  }
  .preflight-row.warn {
    color: var(--color-neon-yellow);
  }
  .preflight-row.note {
    color: var(--color-text-dim);
  }
  .preflight-icon {
    font-family: var(--font-mono);
    font-weight: 700;
    flex-shrink: 0;
    width: 12px;
  }
  .preflight-details {
    margin-top: 4px;
  }
  .preflight-details summary {
    cursor: pointer;
    color: var(--color-text-dim);
    font-size: 10px;
  }
  .auto-stash-tag {
    margin-left: 6px;
    padding: 0 4px;
    border: 1px solid color-mix(in srgb, var(--color-neon-cyan) 50%, transparent);
    color: var(--color-neon-cyan);
    font-size: 8px;
    text-transform: uppercase;
    letter-spacing: 0.5px;
  }
  .preflight-row.file {
    font-size: 10px;
    padding: 1px 0;
  }
  .dirty-source {
    font-family: var(--font-mono);
    font-size: 8px;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    padding: 0 3px;
    border: 1px solid;
    flex-shrink: 0;
  }
  .dirty-source-user_api {
    color: var(--color-neon-cyan);
    border-color: color-mix(in srgb, var(--color-neon-cyan) 50%, transparent);
  }
  .dirty-source-manual_edit {
    color: var(--color-neon-yellow);
    border-color: color-mix(in srgb, var(--color-neon-yellow) 50%, transparent);
  }
  .dirty-source-untracked {
    color: var(--color-text-dim);
    border-color: var(--color-border-subtle);
  }
  .dirty-path {
    font-family: var(--font-mono);
    color: var(--color-text-secondary);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    min-width: 0;
    flex: 1;
  }
  .dirty-status {
    font-family: var(--font-mono);
    color: var(--color-text-dim);
    font-size: 9px;
  }

  .conflict-section {
    border-left: 1px solid var(--color-neon-red);
  }

  /* --- Step progress timeline ------------------------------------ */
  .progress-timeline {
    width: 320px;
    border: 1px solid var(--color-border-subtle, #1a1a2e);
    background: var(--color-bg-secondary, #0d0d14);
    font-family: var(--font-mono);
    z-index: 100;
    padding: 8px 12px;
  }
  .progress-header {
    color: var(--color-text-dim);
    font-size: 9px;
    text-transform: uppercase;
    letter-spacing: 1px;
    padding-bottom: 4px;
    margin-bottom: 4px;
    border-bottom: 1px solid var(--color-border-subtle);
  }
  .progress-row {
    display: flex;
    gap: 6px;
    align-items: baseline;
    padding: 1px 0;
    font-size: 10px;
    color: var(--color-text-dim);
  }
  .progress-row[data-status="running"] {
    color: var(--color-neon-cyan);
  }
  .progress-row[data-status="done"] {
    color: var(--color-text-secondary);
  }
  .progress-row[data-status="warning"] {
    color: var(--color-neon-yellow);
  }
  .progress-row[data-status="failed"] {
    color: var(--color-neon-red);
  }
  .progress-icon {
    width: 10px;
    flex-shrink: 0;
    text-align: center;
    font-weight: 700;
  }
  .progress-label {
    flex: 1;
  }
  .progress-detail {
    color: var(--color-text-dim);
    font-size: 9px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    min-width: 0;
    max-width: 50%;
  }

  .update-badge.timeout {
    color: var(--color-neon-red);
    border: 1px solid var(--color-neon-red);
  }
  .update-badge.warning {
    color: var(--color-neon-yellow);
    border: 1px solid var(--color-neon-yellow);
  }
  .btn-update[disabled] {
    opacity: 0.4;
    cursor: not-allowed;
  }
  .btn-update.btn-update-warning {
    border-color: var(--color-neon-yellow);
    color: var(--color-neon-yellow);
  }
  .btn-update.btn-update-warning:hover {
    background: color-mix(in srgb, var(--color-neon-yellow) 10%, transparent);
  }
</style>
