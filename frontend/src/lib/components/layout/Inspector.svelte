<script lang="ts">
  import { forgeStore } from '$lib/stores/forge.svelte';
  import { refinementStore } from '$lib/stores/refinement.svelte';
  import { clustersStore } from '$lib/stores/clusters.svelte';
  import { editorStore } from '$lib/stores/editor.svelte';
  import { taxonomyColor, scoreColor, qHealthColor, stateColor } from '$lib/utils/colors';

  /** Known domains for the domain picker (legacy compat). */
  const KNOWN_DOMAINS = ['backend', 'frontend', 'database', 'security', 'devops', 'fullstack', 'general'];

  /** Deduplicate array by `id` field (prevents Svelte keyed each errors). */
  function dedupe<T extends { id: string }>(items: T[]): T[] {
    const seen = new Set<string>();
    return items.filter(item => {
      if (seen.has(item.id)) return false;
      seen.add(item.id);
      return true;
    });
  }
  import { getOptimization } from '$lib/api/client';
  import { updateCluster } from '$lib/api/clusters';
  import { addToast } from '$lib/stores/toast.svelte';
  import ScoreCard from '$lib/components/shared/ScoreCard.svelte';
  import ScoreSparkline from '$lib/components/refinement/ScoreSparkline.svelte';
  import { PHASE_LABELS } from '$lib/utils/dimensions';
  import { formatScore, truncateText, isPassthroughResult } from '$lib/utils/formatting';

  // Tab-aware result: use per-tab cached data when available, fall back to global forge state
  const activeResult = $derived(editorStore.activeResult ?? forgeStore.result);
  // True when viewing a per-tab cached result (not the current forge session)
  const viewingCachedTab = $derived(editorStore.activeResult !== null);

  const isPassthrough = $derived(forgeStore.status === 'passthrough');
  const isHeuristicScored = $derived(activeResult?.scoring_mode === 'heuristic');
  const isPassthroughResult_ = $derived(isPassthroughResult(activeResult));
  // Family detail is shown only when selected AND forge is not actively running
  const forgeActive = $derived(
    forgeStore.status === 'analyzing' ||
    forgeStore.status === 'optimizing' ||
    forgeStore.status === 'scoring'
  );
  const showClusterDetail = $derived(
    clustersStore.selectedClusterId !== null && !forgeActive
  );

  async function openOptimization(traceId: string, optimizationId: string): Promise<void> {
    try {
      const opt = await getOptimization(traceId);
      forgeStore.loadFromRecord(opt); // caches result via editorStore.cacheResult internally
      editorStore.openResult(opt.id); // open tab — data already cached by loadFromRecord
    } catch {
      // Fallback: open tab without data — ForgeArtifact will handle gracefully
      editorStore.openResult(optimizationId);
    }
  }

  function dismissFamily(): void {
    clustersStore.selectCluster(null);
  }

  // Rename state
  let renaming = $state(false);
  let renameValue = $state('');
  let renameSaving = $state(false);

  function startRename(): void {
    if (!clustersStore.clusterDetail) return;
    renameValue = clustersStore.clusterDetail.label;
    renaming = true;
  }

  function cancelRename(): void {
    renaming = false;
    renameValue = '';
  }

  async function submitRename(): Promise<void> {
    const id = clustersStore.selectedClusterId;
    const trimmed = renameValue.trim();
    if (!id || !trimmed || renameSaving) return;
    renameSaving = true;
    try {
      await updateCluster(id, { intent_label: trimmed });
      // Refresh the detail to reflect the new name
      clustersStore.selectCluster(id);
      clustersStore.invalidateClusters();
      renaming = false;
    } catch {
      // keep rename input open on error
    }
    renameSaving = false;
  }

  // Domain picker state
  let domainPickerOpen = $state(false);
  let domainSaving = $state(false);

  function toggleDomainPicker(): void {
    domainPickerOpen = !domainPickerOpen;
  }

  async function selectDomain(newDomain: string): Promise<void> {
    const id = clustersStore.selectedClusterId;
    if (!id || domainSaving) return;
    domainSaving = true;
    try {
      await updateCluster(id, { domain: newDomain });
      clustersStore.selectCluster(id);
      clustersStore.invalidateClusters();
      domainPickerOpen = false;
    } catch {
      // keep picker open on error
    }
    domainSaving = false;
  }

  let promoteSaving = $state(false);

  async function promoteCluster(newState: string): Promise<void> {
    const id = clustersStore.selectedClusterId;
    if (!id || promoteSaving) return;
    promoteSaving = true;
    try {
      await updateCluster(id, { state: newState });
      clustersStore.selectCluster(id);  // refresh detail
      clustersStore.invalidateClusters();  // refresh tree
    } catch {
      addToast('deleted', 'State change failed');
    }
    promoteSaving = false;
  }

  // Sync feedback state from real-time events (e.g. MCP or cross-tab submissions)
  $effect(() => {
    const handler = (e: Event) => {
      const detail = (e as CustomEvent).detail;
      if (detail?.optimization_id && detail.optimization_id === activeResult?.id) {
        forgeStore.feedback = detail.rating;
      }
    };
    window.addEventListener('feedback-event', handler);
    return () => window.removeEventListener('feedback-event', handler);
  });
</script>

<aside
  class="panel"
  aria-label="Inspector panel"
  style="background: var(--color-bg-secondary); border-left: 1px solid var(--color-border-subtle);"
>
  <!-- Header -->
  <div class="panel-header">
    <span class="section-heading">Inspector</span>
  </div>

  <!-- Body -->
  <div class="panel-body">

    {#if showClusterDetail}
      <!-- Pattern family detail -->
      <div class="family-detail">
        {#if clustersStore.clusterDetailLoading}
          <div class="phase-state">
            <div class="spinner" aria-label="Loading family" role="status"></div>
            <span class="phase-label">Loading family...</span>
          </div>

        {:else if clustersStore.clusterDetailError}
          <div class="error-state">
            <span class="error-icon" aria-hidden="true">!</span>
            <span class="error-text">{clustersStore.clusterDetailError}</span>
          </div>

        {:else if clustersStore.clusterDetail}
          {@const family = clustersStore.clusterDetail}

          <!-- Family header -->
          <div class="family-header">
            {#if renaming}
              <form
                class="rename-form"
                onsubmit={(e) => { e.preventDefault(); submitRename(); }}
              >
                <input
                  class="rename-input"
                  type="text"
                  bind:value={renameValue}
                  onkeydown={(e) => { if (e.key === 'Escape') cancelRename(); }}
                  aria-label="Family name"
                />
                <button
                  class="rename-save"
                  type="submit"
                  disabled={renameSaving || !renameValue.trim()}
                  title="Save"
                >&#x2713;</button>
                <button
                  class="rename-cancel"
                  type="button"
                  onclick={cancelRename}
                  title="Cancel"
                >×</button>
              </form>
            {:else}
              <button
                class="family-intent"
                onclick={startRename}
                title="Click to rename"
              >{family.label}</button>
            {/if}
            <button
              class="domain-badge"
              style="background: {taxonomyColor(family.domain)};"
              onclick={toggleDomainPicker}
              title="Click to change domain"
              aria-label="Change domain"
            >{family.domain}</button>
            <span
              class="state-badge"
              style="color: {stateColor(family.state)}; border-color: {stateColor(family.state)};"
            >{family.state}</span>
            {#if domainPickerOpen}
              <div class="domain-picker" role="listbox" aria-label="Select domain">
                {#each KNOWN_DOMAINS as d (d)}
                  <button
                    class="domain-option"
                    class:domain-option--active={d === family.domain}
                    style="background: {taxonomyColor(d)};"
                    onclick={() => selectDomain(d)}
                    disabled={domainSaving}
                    role="option"
                    aria-selected={d === family.domain}
                  >{d}</button>
                {/each}
              </div>
            {/if}
            <button
              class="dismiss-btn"
              onclick={dismissFamily}
              title="Close family detail"
              aria-label="Close family detail"
            >×</button>
          </div>

          <!-- Stats row -->
          <div class="meta-section">
            <div class="meta-row">
              <span class="meta-label">Usage</span>
              <span class="meta-value meta-value--cyan">{family.usage_count}</span>
            </div>
            <div class="meta-row">
              <span class="meta-label">Members</span>
              <span class="meta-value">{family.member_count}</span>
            </div>
            <div class="meta-row">
              <span class="meta-label">Avg Score</span>
              <span class="meta-value meta-value--cyan">{formatScore(family.avg_score)}</span>
            </div>
            {#if family.preferred_strategy}
              <div class="meta-row">
                <span class="meta-label">Strategy</span>
                <span class="meta-value meta-value--cyan">{family.preferred_strategy}</span>
              </div>
            {/if}
          </div>

          <!-- State transition actions -->
          {#if family.state === 'active' || family.state === 'mature'}
            <button
              class="action-btn action-btn--primary"
              onclick={() => promoteCluster('template')}
              disabled={promoteSaving}
              title="Promote this cluster to template state"
            >Promote to template</button>
          {/if}
          {#if family.state === 'archived'}
            <button
              class="action-btn"
              onclick={() => promoteCluster('active')}
              disabled={promoteSaving}
              title="Restore this cluster to active state"
            >Unarchive</button>
          {/if}

          <!-- Meta-patterns -->
          {#if family.meta_patterns.length > 0}
            <div class="family-section">
              <div class="section-heading" style="margin-bottom: 4px;">Meta-patterns</div>
              <div class="pattern-list">
                {#each dedupe(family.meta_patterns) as mp (mp.id)}
                  <div class="pattern-item">
                    <span class="pattern-text">{mp.pattern_text}</span>
                    <span class="source-badge">{mp.source_count}</span>
                  </div>
                {/each}
              </div>
            </div>
          {/if}

          <!-- Linked optimizations -->
          {#if family.optimizations.length > 0}
            <div class="family-section">
              <div class="section-heading" style="margin-bottom: 4px;">Linked optimizations</div>
              <div class="opt-list">
                {#each dedupe(family.optimizations).slice(0, 10) as opt (opt.id)}
                  <button
                    class="opt-item"
                    onclick={() => openOptimization(opt.trace_id, opt.id)}
                    title={opt.raw_prompt}
                  >
                    <span class="opt-prompt">{opt.intent_label || truncateText(opt.raw_prompt)}</span>
                    <span class="opt-score" class:opt-score--null={opt.overall_score === null}>
                      {formatScore(opt.overall_score)}
                    </span>
                  </button>
                {/each}
              </div>
            </div>
          {/if}
        {/if}
      </div>

    {:else if !viewingCachedTab && forgeStore.status === 'idle'}
      {#if clustersStore.taxonomyStats}
        {@const stats = clustersStore.taxonomyStats}
        <div class="health-panel">
          <div class="health-title">TAXONOMY HEALTH</div>
          <div class="health-metric">
            <span class="metric-label">Q_system</span>
            <span class="metric-value" style="color: {qHealthColor(stats.q_system)}">{stats.q_system?.toFixed(3) ?? '—'}</span>
          </div>
          <div class="health-metric">
            <span class="metric-label">Coherence</span>
            <span class="metric-value">{stats.q_coherence?.toFixed(3) ?? '—'}</span>
          </div>
          <div class="health-metric">
            <span class="metric-label">Separation</span>
            <span class="metric-value">{stats.q_separation?.toFixed(3) ?? '—'}</span>
          </div>
          <div class="health-counts">
            <span>{stats.nodes?.active ?? 0} active</span>
            <span class="dot-sep">·</span>
            <span>{stats.nodes?.candidate ?? 0} candidate</span>
            <span class="dot-sep">·</span>
            <span>{stats.nodes?.template ?? 0} template</span>
          </div>
        </div>
      {:else}
        <!-- Empty state -->
        <div class="empty-note">
          Enter a prompt and synthesize
        </div>
      {/if}

    {:else if !viewingCachedTab && forgeActive}
      <!-- Active phase -->
      <div class="phase-state">
        <div class="spinner" aria-label="Processing" role="status"></div>
        <span class="phase-label">
          {PHASE_LABELS[forgeStore.status] ?? forgeStore.status}
        </span>
        {#if forgeStore.currentPhase}
          <span class="phase-detail">{forgeStore.currentPhase}</span>
        {/if}
      </div>

    {:else if !viewingCachedTab && isPassthrough}
      <!-- Passthrough — awaiting external LLM result -->
      <div class="passthrough-state">
        <span class="passthrough-icon" aria-hidden="true">&#8644;</span>
        <span class="passthrough-label">Manual passthrough</span>
        <span class="passthrough-detail">
          {#if forgeStore.assembledPrompt}
            Copy the assembled prompt to your LLM, then paste the result back.
          {:else}
            Preparing prompt...
          {/if}
        </span>
        {#if forgeStore.passthroughStrategy}
          <span class="passthrough-strategy">{forgeStore.passthroughStrategy}</span>
        {/if}
      </div>

    {:else if forgeStore.status === 'complete' || viewingCachedTab}
      <!-- Complete — scores + strategy -->
      <div class="complete-state">

        {#if activeResult?.scores || (!viewingCachedTab && forgeStore.scores)}
          <ScoreCard
            scores={(activeResult?.scores ?? (viewingCachedTab ? null : forgeStore.scores))!}
            originalScores={activeResult?.original_scores ?? (viewingCachedTab ? null : forgeStore.originalScores)}
            deltas={activeResult?.score_deltas ?? (viewingCachedTab ? null : forgeStore.scoreDeltas)}
            overallScore={activeResult?.overall_score ?? null}
            heuristicFlags={activeResult?.heuristic_flags ?? []}
          />
        {:else}
          <div class="scoring-disabled">
            <span class="scoring-disabled-label">Scoring</span>
            <span class="scoring-disabled-value">disabled</span>
          </div>
        {/if}

        <!-- Strategy + scoring mode metadata -->
        <div class="meta-section">
          {#if activeResult?.strategy_used}
            <div class="meta-row">
              <span class="meta-label">Strategy</span>
              <span class="meta-value meta-value--cyan">{activeResult.strategy_used}</span>
            </div>
          {/if}
          {#if isHeuristicScored}
            <div class="meta-row">
              <span class="meta-label">Scoring</span>
              <span class="data-value neon-yellow">{isPassthroughResult_ ? 'heuristic (passthrough)' : 'heuristic'}</span>
            </div>
          {/if}
          {#if activeResult?.provider && !isPassthroughResult_}
            <div class="meta-row">
              <span class="meta-label">Provider</span>
              <span class="meta-value">{activeResult.provider}</span>
            </div>
          {/if}
          {#if activeResult?.models_by_phase && activeResult.provider === 'mcp_sampling'}
            {#each [
              { label: 'Analyzer', key: 'analyze' },
              { label: 'Optimizer', key: 'optimize' },
              { label: 'Scorer', key: 'score' },
            ] as { label, key }}
              <div class="meta-row">
                <span class="meta-label">{label}</span>
                <span class="meta-value meta-value--green">
                  {activeResult.models_by_phase[key] || '?'}
                </span>
              </div>
            {/each}
          {/if}
        </div>

        {#if refinementStore.scoreProgression.length >= 2}
          <div class="sparkline-section">
            <div class="section-heading" style="margin-bottom: 4px;">Score Trend</div>
            <ScoreSparkline scores={refinementStore.scoreProgression} />
            <span class="sparkline-label">{refinementStore.turns.length} versions</span>
          </div>
        {/if}

      </div>

    {:else if forgeStore.status === 'error'}
      <div class="error-state">
        <span class="error-icon" aria-hidden="true">!</span>
        <span class="error-text">{forgeStore.error ?? 'Unknown error'}</span>
      </div>
    {/if}

  </div>
</aside>

<style>





  /* Phase / spinner state */
  .phase-state {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 6px;
    padding: 6px 0;
  }

  .spinner {
    width: 20px;
    height: 20px;
    border: 1px solid var(--color-border-subtle);
    border-top-color: var(--tier-accent, var(--color-neon-cyan));
    animation: spin 800ms linear infinite;
    flex-shrink: 0;
  }

  @keyframes spin {
    to { transform: rotate(360deg); }
  }

  .phase-label {
    font-size: 11px;
    color: var(--color-text-secondary);
    font-family: var(--font-sans);
  }

  .phase-detail {
    font-size: 10px;
    color: var(--color-text-dim);
    font-family: var(--font-mono);
  }

  /* Passthrough state */
  .passthrough-state {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 4px;
    padding: 6px 0;
  }

  .passthrough-icon {
    font-size: 16px;
    color: var(--color-neon-yellow);
    font-family: var(--font-mono);
  }

  .passthrough-label {
    font-size: 11px;
    color: var(--color-neon-yellow);
    font-family: var(--font-sans);
  }

  .passthrough-detail {
    font-size: 10px;
    color: var(--color-text-dim);
    font-family: var(--font-sans);
    text-align: center;
    line-height: 1.4;
    padding: 0 6px;
  }

  .passthrough-strategy {
    font-size: 10px;
    color: var(--color-text-dim);
    font-family: var(--font-mono);
  }

  /* Complete state */
  .complete-state {
    display: flex;
    flex-direction: column;
    gap: 6px;
  }

  /* Metadata rows (strategy, scoring mode, provider) */
  .meta-section {
    display: flex;
    flex-direction: column;
    gap: 2px;
  }

  .meta-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 2px 6px;
    background: var(--color-bg-card);
    border: 1px solid var(--color-border-subtle);
  }

  .meta-label {
    font-size: 10px;
    font-family: var(--font-sans);
    color: var(--color-text-dim);
  }

  .meta-value {
    font-size: 10px;
    font-family: var(--font-mono);
    color: var(--color-text-secondary);
  }

  .meta-value--cyan {
    color: var(--tier-accent, var(--color-neon-cyan));
  }

  .meta-value--green {
    color: var(--color-neon-green);
  }

  /* Error state */
  .error-state {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 6px;
    padding: 6px 0;
  }

  .error-icon {
    font-size: 16px;
    font-weight: bold;
    color: var(--color-neon-red);
    font-family: var(--font-mono);
  }

  .error-text {
    font-size: 10px;
    color: var(--color-text-dim);
    font-family: var(--font-sans);
    text-align: center;
    word-break: break-word;
  }

  /* Scoring disabled state */
  .scoring-disabled {
    display: flex;
    align-items: center;
    gap: 6px;
    padding: 6px;
    background: var(--color-bg-card);
    border: 1px solid var(--color-border-subtle);
  }

  .scoring-disabled-label {
    font-size: 10px;
    font-family: var(--font-sans);
    color: var(--color-text-dim);
  }

  .scoring-disabled-value {
    font-size: 10px;
    font-family: var(--font-mono);
    color: var(--color-neon-yellow);
  }

  /* Sparkline section */
  .sparkline-section {
    display: flex;
    flex-direction: column;
    gap: 2px;
  }

  .sparkline-label {
    font-size: 10px;
    font-family: var(--font-mono);
    color: var(--color-text-dim);
  }

  /* Pattern family detail */
  .family-detail {
    display: flex;
    flex-direction: column;
    gap: 6px;
  }

  .family-header {
    display: flex;
    align-items: center;
    gap: 6px;
    flex-wrap: wrap;
  }

  .family-intent {
    font-size: 12px;
    font-family: var(--font-display);
    color: var(--color-text-primary);
    letter-spacing: 0.02em;
    line-height: 1.3;
    background: transparent;
    border: none;
    padding: 0;
    cursor: pointer;
    text-align: left;
    transition: color 200ms cubic-bezier(0.16, 1, 0.3, 1);
  }

  .family-intent:hover {
    color: var(--tier-accent, var(--color-neon-cyan));
  }

  .rename-form {
    display: flex;
    align-items: center;
    gap: 2px;
    flex: 1;
    min-width: 0;
  }

  .rename-input {
    flex: 1;
    min-width: 0;
    height: 20px;
    padding: 0 4px;
    font-size: 11px;
    font-family: var(--font-display);
    color: var(--color-text-primary);
    background: var(--color-bg-input);
    border: 1px solid color-mix(in srgb, var(--tier-accent, var(--color-neon-cyan)) 30%, transparent);
    outline: none;
  }

  .rename-save,
  .rename-cancel {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 18px;
    height: 18px;
    border: none;
    background: transparent;
    font-size: 12px;
    cursor: pointer;
    flex-shrink: 0;
    transition: color 200ms cubic-bezier(0.16, 1, 0.3, 1);
  }

  .rename-save {
    color: var(--color-neon-green);
  }

  .rename-save:hover {
    color: var(--tier-accent, var(--color-neon-cyan));
  }

  .rename-save:disabled {
    opacity: 0.4;
    cursor: not-allowed;
  }

  .rename-cancel {
    color: var(--color-text-dim);
  }

  .rename-cancel:hover {
    color: var(--color-text-primary);
  }

  .domain-badge {
    display: inline-block;
    font-size: 9px;
    font-family: var(--font-mono);
    color: var(--color-bg-primary);
    padding: 1px 5px;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    flex-shrink: 0;
    border: none;
    cursor: pointer;
    transition: opacity 200ms cubic-bezier(0.16, 1, 0.3, 1);
  }

  .domain-badge:hover {
    opacity: 0.8;
  }

  .domain-picker {
    display: flex;
    flex-wrap: wrap;
    gap: 2px;
    width: 100%;
  }

  .domain-option {
    font-size: 8px;
    font-family: var(--font-mono);
    color: var(--color-bg-primary);
    padding: 1px 4px;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    border: 1px solid transparent;
    cursor: pointer;
    transition: opacity 200ms cubic-bezier(0.16, 1, 0.3, 1);
  }

  .domain-option:hover {
    opacity: 0.8;
  }

  .domain-option--active {
    border-color: var(--color-text-primary);
  }

  .domain-option:disabled {
    opacity: 0.4;
    cursor: not-allowed;
  }

  .dismiss-btn {
    margin-left: auto;
    display: flex;
    align-items: center;
    justify-content: center;
    width: 16px;
    height: 16px;
    border: none;
    background: transparent;
    color: var(--color-text-dim);
    font-size: 12px;
    cursor: pointer;
    flex-shrink: 0;
    transition: color 200ms cubic-bezier(0.16, 1, 0.3, 1);
  }

  .dismiss-btn:hover {
    color: var(--color-text-primary);
  }

  /* Family sub-sections */
  .family-section {
    display: flex;
    flex-direction: column;
  }

  /* Meta-patterns list */
  .pattern-list {
    display: flex;
    flex-direction: column;
    gap: 2px;
  }

  .pattern-item {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 6px;
    padding: 3px 6px;
    background: var(--color-bg-card);
    border: 1px solid var(--color-border-subtle);
  }

  .pattern-text {
    font-size: 10px;
    font-family: var(--font-sans);
    color: var(--color-text-secondary);
    line-height: 1.4;
    flex: 1;
    min-width: 0;
  }

  .source-badge {
    font-size: 9px;
    font-family: var(--font-mono);
    color: var(--tier-accent, var(--color-neon-cyan));
    background: var(--color-bg-secondary);
    border: 1px solid var(--color-border-subtle);
    padding: 0 4px;
    flex-shrink: 0;
    line-height: 1.6;
  }

  /* Linked optimizations list */
  .opt-list {
    display: flex;
    flex-direction: column;
    gap: 2px;
  }

  .opt-item {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 6px;
    padding: 3px 6px;
    background: var(--color-bg-card);
    border: 1px solid var(--color-border-subtle);
    cursor: pointer;
    text-align: left;
    width: 100%;
    font: inherit;
    color: inherit;
    transition: border-color 200ms cubic-bezier(0.16, 1, 0.3, 1),
                background 200ms cubic-bezier(0.16, 1, 0.3, 1);
  }

  .opt-item:hover {
    border-color: var(--color-border-accent);
    background: var(--color-bg-hover);
  }

  .opt-item:active {
    transform: none;
  }

  .opt-prompt {
    font-size: 10px;
    font-family: var(--font-sans);
    color: var(--color-text-secondary);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    flex: 1;
    min-width: 0;
  }

  .opt-score {
    font-size: 10px;
    font-family: var(--font-mono);
    color: var(--tier-accent, var(--color-neon-cyan));
    flex-shrink: 0;
  }

  .opt-score--null {
    color: var(--color-text-dim);
  }

  /* Taxonomy health panel */
  .health-panel {
    display: flex;
    flex-direction: column;
    gap: 4px;
    padding: 6px 0;
  }

  .health-title {
    font-size: 9px;
    font-family: var(--font-mono);
    color: var(--color-text-dim);
    letter-spacing: 0.08em;
    padding: 0 6px;
  }

  .health-metric {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 2px 6px;
    background: var(--color-bg-card);
    border: 1px solid var(--color-border-subtle);
  }

  .metric-label {
    font-size: 10px;
    font-family: var(--font-sans);
    color: var(--color-text-dim);
  }

  .metric-value {
    font-size: 10px;
    font-family: var(--font-mono);
    color: var(--color-text-secondary);
  }

  .health-counts {
    display: flex;
    align-items: center;
    gap: 6px;
    padding: 2px 6px;
    font-size: 10px;
    font-family: var(--font-mono);
    color: var(--color-text-dim);
  }

  .dot-sep {
    color: var(--color-border-subtle);
  }

  /* State badge */
  .state-badge {
    font-size: 9px;
    font-family: var(--font-mono);
    border: 1px solid;
    padding: 1px 4px;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    flex-shrink: 0;
  }

  /* Action buttons */
  .action-btn {
    height: 20px;
    width: 100%;
    padding: 0 8px;
    font-size: 10px;
    font-family: var(--font-sans);
    font-weight: 500;
    color: var(--color-text-secondary);
    background: var(--color-bg-card);
    border: 1px solid var(--color-border-subtle);
    cursor: pointer;
    transition: border-color 200ms cubic-bezier(0.16, 1, 0.3, 1),
                background 200ms cubic-bezier(0.16, 1, 0.3, 1),
                color 200ms cubic-bezier(0.16, 1, 0.3, 1);
  }

  .action-btn:hover {
    border-color: var(--color-border-accent);
    background: var(--color-bg-hover);
    color: var(--color-text-primary);
  }

  .action-btn--primary {
    color: var(--tier-accent, var(--color-neon-cyan));
    border-color: color-mix(in srgb, var(--tier-accent, var(--color-neon-cyan)) 30%, transparent);
  }

  .action-btn--primary:hover {
    border-color: var(--tier-accent, var(--color-neon-cyan));
    background: color-mix(in srgb, var(--tier-accent, var(--color-neon-cyan)) 8%, transparent);
  }
</style>
