<script lang="ts">
  import { forgeStore } from '$lib/stores/forge.svelte';
  import { refinementStore } from '$lib/stores/refinement.svelte';
  import { clustersStore } from '$lib/stores/clusters.svelte';
  import { templatesStore } from '$lib/stores/templates.svelte';
  import { editorStore } from '$lib/stores/editor.svelte';
  import { taxonomyColor, scoreColor, qHealthColor, stateColor, DIMENSION_COLORS } from '$lib/utils/colors';
  import { TAXONOMY_TOOLTIPS, CLUSTER_TOOLTIPS, STAT_TOOLTIPS } from '$lib/utils/metric-tooltips';
  import { INSPECTOR_TOOLTIPS } from '$lib/utils/ui-tooltips';
  import { assessTaxonomyHealth } from '$lib/utils/taxonomy-health';
  import { tooltip } from '$lib/actions/tooltip';

  /** Deduplicate array by `id` field (prevents Svelte keyed each errors). */
  function dedupe<T extends { id: string }>(items: T[]): T[] {
    const seen = new Set<string>();
    return items.filter(item => {
      if (seen.has(item.id)) return false;
      seen.add(item.id);
      return true;
    });
  }
  import { updateCluster } from '$lib/api/clusters';
  import { listProjects } from '$lib/api/client';
  import { addToast } from '$lib/stores/toast.svelte';
  import MarkdownRenderer from '$lib/components/shared/MarkdownRenderer.svelte';
  import ScoreCard from '$lib/components/shared/ScoreCard.svelte';
  import ScoreSparkline from '$lib/components/shared/ScoreSparkline.svelte';
  import { PHASE_LABELS, DIMENSION_LABELS } from '$lib/utils/dimensions';
  import { formatScore, isPassthroughResult, trendInfo, formatRelativeTime } from '$lib/utils/formatting';

  // Tab-aware result: use per-tab cached data when available, fall back to global forge state
  const activeResult = $derived(editorStore.activeResult ?? forgeStore.result);
  // True when viewing a per-tab cached result (not the current forge session)
  const viewingCachedTab = $derived(editorStore.activeResult !== null);

  const isPassthrough = $derived(forgeStore.status === 'passthrough');
  const isHeuristicScored = $derived(
    activeResult?.scoring_mode === 'heuristic' || activeResult?.scoring_mode === 'hybrid_passthrough',
  );
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
      addToast('deleted', 'Rename failed');
    }
    renameSaving = false;
  }

  let projectLabels = $state<Record<string, string>>({});
  let projectsLoaded = false;
  $effect(() => {
    if (projectsLoaded) return;
    projectsLoaded = true;
    listProjects().then(ps => {
      projectLabels = Object.fromEntries(ps.map(p => [p.id, p.label]));
    }).catch(() => {});
  });

  let _templatesLoaded = $state(false);
  $effect(() => {
    if (_templatesLoaded) return;
    _templatesLoaded = true;
    templatesStore.load(null);
  });

  let showDimensions = $state(false);

  let promoteSaving = $state(false);

  async function promoteCluster(newState: 'active'): Promise<void> {
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

  // F6: Tab-aware feedback — read from per-tab cache when viewing a cached tab
  const feedback = $derived(editorStore.activeFeedback ?? forgeStore.feedback);

  // Sync feedback state from real-time events (e.g. MCP or cross-tab submissions)
  $effect(() => {
    const handler = (e: Event) => {
      const detail = (e as CustomEvent).detail;
      if (detail?.optimization_id && detail.optimization_id === activeResult?.id) {
        forgeStore.feedback = detail.rating;
        editorStore.cacheFeedback(detail.optimization_id, detail.rating);
      }
    };
    window.addEventListener('feedback-event', handler);
    return () => window.removeEventListener('feedback-event', handler);
  });

  // Sub-domain detection: check if selected domain's parent is also a domain
  const selectedIsSubDomain = $derived.by(() => {
    const detail = clustersStore.clusterDetail;
    if (!detail || detail.state !== 'domain') return false;
    const tree = clustersStore.taxonomyTree;
    return tree.some(n => n.id === detail.parent_id && n.state === 'domain');
  });

  const selectedParentDomainLabel = $derived.by(() => {
    if (!selectedIsSubDomain) return null;
    const detail = clustersStore.clusterDetail;
    if (!detail) return null;
    const tree = clustersStore.taxonomyTree;
    const parent = tree.find(n => n.id === detail.parent_id);
    return parent?.label ?? null;
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

          <!-- Sub-domain breadcrumb: shown when selected domain's parent is also a domain -->
          {#if selectedIsSubDomain && selectedParentDomainLabel}
            <div class="subdomain-breadcrumb">
              <button class="breadcrumb-parent" onclick={() => {
                const tree = clustersStore.taxonomyTree;
                const detail = clustersStore.clusterDetail;
                if (detail?.parent_id) {
                  const parent = tree.find(n => n.id === detail.parent_id);
                  if (parent) clustersStore.selectCluster(parent.id);
                }
              }}>
                {selectedParentDomainLabel}
              </button>
              <span class="breadcrumb-separator">›</span>
              <span class="breadcrumb-current">{clustersStore.clusterDetail?.label ?? ''}</span>
            </div>
          {/if}

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
                  use:tooltip={INSPECTOR_TOOLTIPS.save}
                aria-label="Save"
                >&#x2713;</button>
                <button
                  class="rename-cancel"
                  type="button"
                  onclick={cancelRename}
                  use:tooltip={INSPECTOR_TOOLTIPS.cancel}
                  aria-label="Cancel"
                >×</button>
              </form>
            {:else}
              <button
                class="family-intent"
                onclick={startRename}
                use:tooltip={family.label}
                aria-label="Click to rename: {family.label}"
              >{family.label}</button>
            {/if}
            {#if selectedIsSubDomain && selectedParentDomainLabel}
              <span
                class="domain-badge"
                style="background: {taxonomyColor(selectedParentDomainLabel)};"
              >{selectedParentDomainLabel}</span>
              <span
                class="state-badge"
                style="color: {taxonomyColor(family.domain)}; border-color: {taxonomyColor(family.domain)};"
              >Sub-domain</span>
            {:else}
              <span
                class="domain-badge"
                style="background: {taxonomyColor(family.domain)};"
              >{family.domain}</span>
              <span
                class="state-badge"
                style="color: {stateColor(family.state)}; border-color: {stateColor(family.state)};"
              >{family.state}</span>
            {/if}
            <button
              class="dismiss-btn"
              onclick={dismissFamily}
              use:tooltip={INSPECTOR_TOOLTIPS.close_detail}
              aria-label="Close family detail"
            >×</button>
          </div>

          <!-- Stats row -->
          <div class="meta-section">
            <div class="meta-row" use:tooltip={CLUSTER_TOOLTIPS.usage_count}>
              <span class="meta-label">Usage</span>
              <span class="meta-value meta-value--cyan">{family.usage_count}</span>
            </div>
            <div class="meta-row" use:tooltip={CLUSTER_TOOLTIPS.member_count}>
              <span class="meta-label">Members</span>
              <span class="meta-value">{family.member_count}</span>
            </div>
            {#if family.project_ids && family.project_ids.length > 0}
              <div class="meta-row">
                <span class="meta-label">{family.project_ids.length === 1 ? 'Project' : 'Projects'}</span>
                <span class="meta-value meta-value--cyan data-value--truncate"
                  use:tooltip={family.project_ids.map((id: string) => projectLabels[id] ?? id.slice(0, 8)).join(', ')}
                >
                  {family.project_ids.map((id: string) => {
                    const label = projectLabels[id];
                    if (!label) return id.slice(0, 8);
                    return label.includes('/') ? label.split('/').pop() : label;
                  }).join(', ')}
                </span>
              </div>
            {/if}
            <div class="meta-row" use:tooltip={CLUSTER_TOOLTIPS.avg_score}>
              <span class="meta-label">Avg Score</span>
              <span class="meta-value meta-value--cyan">{formatScore(family.avg_score)}</span>
            </div>
            {#if family.preferred_strategy}
              <div class="meta-row" use:tooltip={CLUSTER_TOOLTIPS.preferred_strategy}>
                <span class="meta-label">Strategy</span>
                <span class="meta-value meta-value--cyan">{family.preferred_strategy}</span>
              </div>
            {/if}
          </div>

          <!-- State transition actions -->
          {#if family.state === 'archived' && family.member_count > 0}
            <button
              class="action-btn"
              onclick={() => promoteCluster('active')}
              disabled={promoteSaving}
              use:tooltip={INSPECTOR_TOOLTIPS.unarchive}
            >Unarchive</button>
          {/if}

          <!-- Meta-patterns (context-aware by node state) -->
          {#if family.meta_patterns.length > 0}
            <div class="family-section">
              <div class="section-heading" style="margin-bottom: 4px;">
                {#if family.state === 'domain'}
                  Top Patterns ({family.member_count} {family.member_count === 1 ? 'cluster' : 'clusters'})
                {:else if family.state === 'archived'}
                  Meta-patterns (archived)
                {:else}
                  Meta-patterns
                {/if}
              </div>
              <div class="pattern-list">
                {#each dedupe(family.meta_patterns) as mp (mp.id)}
                  <div class="pattern-item">
                    <span class="pattern-text">{mp.pattern_text}</span>
                    <span class="source-badge" use:tooltip={CLUSTER_TOOLTIPS.source_count}>{mp.source_count}</span>
                  </div>
                {/each}
              </div>
            </div>
          {:else if family.state === 'domain' && family.member_count > 0}
            <div class="family-section">
              <p class="empty-note">Patterns emerge as optimizations accumulate</p>
            </div>
          {:else if family.state === 'domain' && family.member_count === 0}
            <div class="family-section">
              <p class="empty-note">No clusters in this domain yet</p>
            </div>
          {:else if family.state === 'candidate'}
            <div class="family-section">
              <p class="empty-note">Patterns extracted after promotion to active</p>
            </div>
          {:else if family.state === 'archived'}
            <div class="family-section">
              <p class="empty-note">No meta-patterns were extracted</p>
            </div>
          {/if}

          <!-- Templates forked from this cluster — shows reparent annotation when
               the cluster's current domain drifted away from the template's frozen
               domain_label (templates preserve their origin domain even across reparents). -->
          {@const clusterTemplates = (templatesStore.templates ?? []).filter((t) => t.source_cluster_id === family.id && !t.retired_at)}
          {#if clusterTemplates.length > 0}
            <div class="family-section">
              <div class="section-heading" style="margin-bottom: 4px;">
                Templates ({clusterTemplates.length})
              </div>
              <div class="template-list">
                {#each clusterTemplates as tpl (tpl.id)}
                  <div class="template-row-compact">
                    <span class="template-label-compact">{tpl.label}</span>
                    <span class="template-origin-compact">
                      {tpl.domain_label}
                      {#if tpl.domain_label !== family.domain}
                        <em class="template-reparented">(reparented)</em>
                      {/if}
                    </span>
                  </div>
                {/each}
              </div>
            </div>
          {/if}

          <!-- Linked optimizations moved to ClusterNavigator as sub-items -->
        {/if}
      </div>

    {:else if !viewingCachedTab && forgeStore.status === 'idle'}
      {#if clustersStore.taxonomyStats}
        {@const stats = clustersStore.taxonomyStats}
        {@const health = assessTaxonomyHealth(stats)}
        <div class="health-panel">
          <div class="health-title">TAXONOMY HEALTH</div>
          <div class="health-metric" use:tooltip={TAXONOMY_TOOLTIPS.q_system}>
            <span class="metric-label">Q_health</span>
            <span class="metric-value" style="color: {qHealthColor(stats.q_health ?? stats.q_system)}">{(stats.q_health ?? stats.q_system)?.toFixed(3) ?? '—'}</span>
          </div>
          <div class="health-metric" use:tooltip={TAXONOMY_TOOLTIPS.coherence}>
            <span class="metric-label">Coherence</span>
            <span class="metric-value">{(stats.q_health_coherence_w ?? stats.q_coherence)?.toFixed(3) ?? '—'}</span>
          </div>
          <div class="health-metric" use:tooltip={TAXONOMY_TOOLTIPS.separation}>
            <span class="metric-label">Separation</span>
            <span class="metric-value">{(stats.q_health_separation_w ?? stats.q_separation)?.toFixed(3) ?? '—'}</span>
          </div>
          {#if stats.q_sparkline && stats.q_sparkline.length >= 2}
            <div class="health-sparkline">
              <ScoreSparkline scores={stats.q_sparkline} width={100} height={18} minRange={0.2} />
              {#if health}
                <span class="health-headline" style="color: {health.color}">{health.headline}</span>
              {/if}
            </div>
          {/if}
          {#if health}
            <div class="health-detail">{health.detail}</div>
          {/if}
          <div class="health-counts">
            <span use:tooltip={TAXONOMY_TOOLTIPS.active}>{clustersStore.clusterCounts.active} active</span>
            <span class="dot-sep">·</span>
            <span use:tooltip={TAXONOMY_TOOLTIPS.candidate}>{clustersStore.clusterCounts.candidate} candidate</span>
          </div>
        </div>
      {:else}
        <!-- Empty state -->
        <div class="empty-note">
          Enter a prompt and synthesize
        </div>
      {/if}

    {:else if !viewingCachedTab && forgeActive}
      <!-- Pipeline phase indicator -->
      {@const phases = ['analyzing', 'optimizing', 'scoring']}
      {@const currentIdx = phases.indexOf(forgeStore.status)}
      <div class="phase-state">
        <div class="phase-steps">
          {#each phases as phase, i (phase)}
            {@const isDone = i < currentIdx}
            {@const isActive = i === currentIdx}
            <div class="phase-step" class:phase-step--done={isDone} class:phase-step--active={isActive}>
              <span class="phase-dot" class:phase-dot--done={isDone} class:phase-dot--active={isActive}>
                {#if isDone}&#10003;{:else}{i + 1}{/if}
              </span>
              <span class="phase-step-label">{PHASE_LABELS[phase]}</span>
            </div>
            {#if i < phases.length - 1}
              <span class="phase-connector" class:phase-connector--done={isDone}></span>
            {/if}
          {/each}
        </div>
        {#if forgeStore.currentPhase && forgeStore.phaseModels[forgeStore.currentPhase]}
          <div class="phase-model">
            {forgeStore.phaseModels[forgeStore.currentPhase]}
          </div>
        {/if}
        <button class="phase-cancel" onclick={() => forgeStore.cancel()}>cancel</button>
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
          {#if activeResult?.domain}
            <div class="meta-row">
              <span class="meta-label">Domain</span>
              <span class="meta-value" style={activeResult.domain === 'general' ? 'opacity: 0.5' : ''}>{activeResult.domain}</span>
            </div>
          {/if}
          {#if activeResult?.routing_tier}
            <div class="meta-row">
              <span class="meta-label">Tier</span>
              <span class="meta-value"
                class:meta-value--green={activeResult.routing_tier === 'sampling'}
                class:neon-yellow={activeResult.routing_tier === 'passthrough'}
                class:meta-value--cyan={activeResult.routing_tier === 'internal'}>
                {activeResult.routing_tier}
              </span>
            </div>
          {/if}
          {#if activeResult?.provider}
            <div class="meta-row">
              <span class="meta-label">Provider</span>
              <span class="meta-value" class:neon-yellow={isPassthroughResult_}>
                {isPassthroughResult_ ? 'passthrough' : activeResult.provider}
              </span>
            </div>
          {/if}
          {#if activeResult?.repo_full_name}
            <div class="meta-row">
              <span class="meta-label">Repo</span>
              <span class="meta-value font-mono">{activeResult.repo_full_name}</span>
            </div>
          {/if}
          {#if activeResult?.scoring_mode && activeResult.scoring_mode !== 'skipped' && activeResult.scores}
            <div class="meta-row">
              <span class="meta-label">Scoring</span>
              <span class="meta-value" class:neon-yellow={isHeuristicScored}>
                {activeResult.scoring_mode === 'hybrid_passthrough' ? 'hybrid (external + heuristic)'
                  : activeResult.scoring_mode === 'heuristic' ? 'heuristic only'
                  : activeResult.scoring_mode}
              </span>
            </div>
          {/if}
          {#if activeResult?.model_used}
            <div class="meta-row">
              <span class="meta-label">Model</span>
              <span class="meta-value" class:neon-yellow={isPassthroughResult_} class:meta-value--green={activeResult.routing_tier === 'sampling'}>
                {activeResult.model_used}
              </span>
            </div>
          {/if}
          {#if activeResult?.models_by_phase && !isPassthroughResult_}
            {#each [
              { label: 'Analyzer', key: 'analyze' },
              { label: 'Optimizer', key: 'optimize' },
              { label: 'Scorer', key: 'score' },
            ] as { label, key }}
              {#if activeResult.models_by_phase[key]}
                <div class="meta-row">
                  <span class="meta-label">{label}</span>
                  <span class="meta-value" class:meta-value--green={activeResult.routing_tier === 'sampling'} class:meta-value--cyan={activeResult.routing_tier !== 'sampling'}>
                    {activeResult.models_by_phase[key]}
                  </span>
                </div>
              {/if}
            {/each}
          {/if}
          {#if activeResult?.duration_ms}
            <div class="meta-row" use:tooltip={STAT_TOOLTIPS.duration(activeResult.duration_ms)}>
              <span class="meta-label">Duration</span>
              <span class="meta-value">{(activeResult.duration_ms / 1000).toFixed(1)}s</span>
            </div>
          {/if}
        </div>

        {#if activeResult?.suggestions && activeResult.suggestions.length > 0}
          <div class="suggestions-section">
            <div class="section-heading">Suggestions</div>
            {#each activeResult.suggestions as sug}
              <div class="suggestion-item">
                <span class="suggestion-source">{sug.source}</span>
                <span class="suggestion-text">{sug.text}</span>
              </div>
            {/each}
          </div>
        {/if}

        {#if activeResult?.changes_summary}
          <div class="changes-section">
            <div class="section-heading">Changes</div>
            <div class="changes-text">
              <MarkdownRenderer content={activeResult.changes_summary} />
            </div>
          </div>
        {/if}

        {#if refinementStore.scoreProgression.length >= 2}
          <div class="sparkline-section">
            <div class="section-heading" style="margin-bottom: 4px; display: flex; align-items: center; gap: 6px;">
              Score Trend
              <button
                class="dim-toggle"
                onclick={() => showDimensions = !showDimensions}
                use:tooltip={showDimensions ? INSPECTOR_TOOLTIPS.score_toggle_avg : INSPECTOR_TOOLTIPS.score_toggle_dim}
              >{showDimensions ? 'AVG' : 'DIM'}</button>
            </div>
            {#if showDimensions}
              {@const dimData = refinementStore.dimensionProgressions}
              {@const allValues = Object.values(dimData).flat()}
              {@const globalMin = allValues.length > 0 ? Math.min(...allValues) : 0}
              {@const globalMax = allValues.length > 0 ? Math.max(...allValues) : 1}
              {@const globalRange = globalMax - globalMin || 1}
              <!-- Matches ScoreSparkline defaults: 120x24, padding=2 -->
              <svg
                width={120}
                height={24}
                viewBox="0 0 120 24"
                class="sparkline"
                aria-label="Per-dimension score progression"
                role="img"
              >
                {#each Object.entries(dimData) as [dim, values]}
                  {#if values.length >= 2}
                    {@const step = 116 / (values.length - 1)}
                    {@const pts = values.map((v, i) => `${2 + i * step},${22 - ((v - globalMin) / globalRange) * 20}`).join(' ')}
                    <polyline
                      points={pts}
                      fill="none"
                      stroke={DIMENSION_COLORS[dim] ?? 'var(--color-text-dim)'}
                      stroke-width="1"
                      stroke-linejoin="round"
                      stroke-linecap="round"
                      opacity="0.8"
                    >
                      <title>{DIMENSION_LABELS[dim] ?? dim}</title>
                    </polyline>
                  {/if}
                {/each}
              </svg>
            {:else}
              <ScoreSparkline
                scores={refinementStore.scoreProgression}
                baseline={refinementStore.scoreProgression.length > 0 ? refinementStore.scoreProgression[0] : null}
              />
            {/if}
            <span class="sparkline-label">{refinementStore.turns.length} versions</span>
          </div>
        {/if}

        <!-- F11: Show individual dimension scores for selected refinement version -->
        {#if refinementStore.selectedVersion?.scores}
          {@const versionScores = refinementStore.selectedVersion.scores as unknown as import('$lib/api/client').DimensionScores}
          <div class="version-scores-section">
            <div class="section-heading" style="margin-bottom: 4px;">
              v{refinementStore.selectedVersion.version} Scores
            </div>
            <ScoreCard
              scores={versionScores}
              originalScores={activeResult?.original_scores ?? activeResult?.scores}
            />
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

  .phase-steps {
    display: flex;
    align-items: center;
    gap: 0;
    padding: 8px 0;
  }

  .phase-step {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 3px;
  }

  .phase-dot {
    width: 18px;
    height: 18px;
    border-radius: 50%;
    border: 1px solid var(--color-border-subtle);
    display: flex;
    align-items: center;
    justify-content: center;
    font-family: var(--font-mono);
    font-size: 9px;
    font-weight: 600;
    color: var(--color-text-dim);
    transition: all 200ms cubic-bezier(0.16, 1, 0.3, 1);
  }

  .phase-dot--active {
    border-color: var(--tier-accent, var(--color-neon-cyan));
    color: var(--tier-accent, var(--color-neon-cyan));
    animation: phase-active 1.5s ease-in-out infinite;
  }

  .phase-dot--done {
    border-color: var(--color-neon-green);
    color: var(--color-neon-green);
    font-size: 10px;
  }

  @keyframes phase-active {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.5; }
  }

  .phase-step-label {
    font-family: var(--font-mono);
    font-size: 9px;
    color: var(--color-text-dim);
    text-transform: uppercase;
    letter-spacing: 0.05em;
  }

  .phase-step--active .phase-step-label {
    color: var(--tier-accent, var(--color-neon-cyan));
  }

  .phase-step--done .phase-step-label {
    color: var(--color-neon-green);
  }

  .phase-connector {
    width: 16px;
    height: 1px;
    background: var(--color-border-subtle);
    margin: 0 2px;
    margin-bottom: 16px;
  }

  .phase-connector--done {
    background: var(--color-neon-green);
  }

  .phase-model {
    font-family: var(--font-mono);
    font-size: 9px;
    color: var(--color-text-dim);
    text-align: center;
    margin-top: 2px;
  }

  .phase-cancel {
    font-family: var(--font-mono);
    font-size: 10px;
    color: var(--color-text-dim);
    background: none;
    border: none;
    padding: 2px 0;
    cursor: pointer;
    margin-top: 4px;
    opacity: 0.6;
    transition: opacity 200ms cubic-bezier(0.16, 1, 0.3, 1), color 200ms cubic-bezier(0.16, 1, 0.3, 1);
  }

  .phase-cancel:hover {
    opacity: 1;
    color: var(--tier-accent, var(--color-neon-cyan));
  }

  .phase-label {
    font-size: 11px;
    color: var(--color-text-secondary);
    font-family: var(--font-sans);
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

  .meta-value.neon-yellow {
    color: var(--color-neon-yellow);
  }

  /* Changes summary */
  .changes-section {
    margin-top: 6px;
  }

  .changes-text {
    padding: 3px 6px;
    background: var(--color-bg-card);
    border: 1px solid var(--color-border-subtle);
    max-height: 150px;
    overflow-y: auto;
  }

  .changes-text :global(.md-render) {
    font-size: 9px;
  }

  /* Suggestions */
  .suggestions-section {
    display: flex;
    flex-direction: column;
    gap: 2px;
    margin-top: 6px;
  }

  .suggestion-item {
    display: flex;
    gap: 6px;
    padding: 3px 6px;
    background: var(--color-bg-card);
    border: 1px solid var(--color-border-subtle);
  }

  .suggestion-source {
    font-size: 9px;
    font-family: var(--font-mono);
    color: var(--tier-accent, var(--color-neon-cyan));
    text-transform: uppercase;
    flex-shrink: 0;
    min-width: 52px;
  }

  .suggestion-text {
    font-size: 9px;
    font-family: var(--font-sans);
    color: var(--color-text-secondary);
    line-height: 1.3;
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

  .dim-toggle {
    font-family: var(--font-mono);
    font-size: 9px;
    color: var(--color-text-dim);
    background: var(--color-bg-card);
    border: 1px solid var(--color-border-subtle);
    padding: 0 4px;
    cursor: pointer;
    line-height: 14px;
    transition: color 200ms, border-color 200ms;
  }

  .dim-toggle:hover {
    color: var(--color-text-primary);
    border-color: var(--color-neon-cyan);
  }

  .health-sparkline {
    display: flex;
    align-items: center;
    gap: 6px;
    margin-top: 2px;
  }

  .health-headline {
    font-family: var(--font-mono);
    font-size: 9px;
    white-space: nowrap;
    font-weight: 600;
  }

  .health-detail {
    font-family: var(--font-sans);
    font-size: 10px;
    color: var(--color-text-secondary);
    line-height: 1.4;
    margin-top: 2px;
  }

  /* .health-trend removed — unused selector */

  /* Pattern family detail */
  .family-detail {
    display: flex;
    flex-direction: column;
    gap: 6px;
  }

  .family-header {
    display: flex;
    align-items: center;
    gap: 4px;
    /* No flex-wrap — name truncates to keep badges + dismiss on one line */
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
    /* Truncate to make room for domain badge + state badge + dismiss btn */
    flex: 1;
    min-width: 0;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
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
  }

  .dismiss-btn {
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

  /* Sub-domain breadcrumb */
  .subdomain-breadcrumb {
    display: flex;
    align-items: center;
    gap: 4px;
    padding: 2px 0;
    font-size: 9px;
  }

  .breadcrumb-parent {
    color: var(--color-text-dim);
    background: none;
    border: none;
    cursor: pointer;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    font-size: 9px;
    padding: 0;
  }

  .breadcrumb-parent:hover {
    color: var(--tier-accent, var(--color-neon-cyan));
  }

  .breadcrumb-separator {
    color: var(--color-text-dim);
  }

  .breadcrumb-current {
    color: var(--color-text-secondary);
  }

  .template-list {
    display: flex;
    flex-direction: column;
    gap: 2px;
  }
  .template-row-compact {
    display: flex;
    align-items: baseline;
    gap: 8px;
    padding: 3px 4px;
    font-size: 10px;
    border-left: 1px solid var(--color-border-subtle);
  }
  .template-label-compact {
    flex: 1;
    min-width: 0;
    color: var(--color-text-primary);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .template-origin-compact {
    font-size: 9px;
    font-family: var(--font-mono);
    color: var(--color-text-dim);
    flex-shrink: 0;
  }
  .template-reparented {
    font-style: italic;
    color: var(--color-neon-amber, var(--color-text-muted));
    margin-left: 4px;
  }
</style>
