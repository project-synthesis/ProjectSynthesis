<script lang="ts">
  import { tick, onDestroy, untrack } from 'svelte';
  import { forge } from '$lib/stores/forge.svelte';
  import { editor } from '$lib/stores/editor.svelte';
  import { history } from '$lib/stores/history.svelte';
  import { patchOptimization } from '$lib/api/client';
  import { STRATEGY_HEX } from '$lib/utils/strategy';
  import CopyButton from '$lib/components/shared/CopyButton.svelte';
  import ScoreCircle from '$lib/components/shared/ScoreCircle.svelte';
  import ScoreBar from '$lib/components/shared/ScoreBar.svelte';
  import DiffView from '$lib/components/shared/DiffView.svelte';
  import StrategyBadge from '$lib/components/shared/StrategyBadge.svelte';
  import TraceView from '$lib/components/pipeline/TraceView.svelte';
  import FeedbackInline from '$lib/components/editor/FeedbackInline.svelte';
  import FeedbackTier2 from '$lib/components/editor/FeedbackTier2.svelte';
  import ResultAssessment from '$lib/components/editor/ResultAssessment.svelte';
  import RefinementInput from '$lib/components/editor/RefinementInput.svelte';
  import BranchIndicator from '$lib/components/pipeline/BranchIndicator.svelte';
  import { feedback } from '$lib/stores/feedback.svelte';
  import { refinement } from '$lib/stores/refinement.svelte';
  import { toast } from '$lib/stores/toast.svelte';
  import { getScoreColor } from '$lib/utils/colors';

  type ArtifactSubTab = 'optimized' | 'diff' | 'scores' | 'trace';
  let activeSubTab = $state<ArtifactSubTab>('optimized');
  let titleInputEl = $state<HTMLInputElement | undefined>();

  const subTabs: { id: ArtifactSubTab; label: string }[] = [
    { id: 'optimized', label: 'Optimized' },
    { id: 'diff', label: 'Diff' },
    { id: 'scores', label: 'Scores' },
    { id: 'trace', label: 'Trace' }
  ];

  let editingTitle = $state(false);
  let titleInput = $state('');
  let displayTitle = $state('Forge Artifact');
  let completedAt = $state<Date | null>(null);

  $effect(() => {
    if (forge.optimizationId) displayTitle = 'Forge Artifact';
  });

  // ── Tier 2 / Result Assessment state ────────────────────────────────────────
  let showTier2 = $state(false);
  let issueSuggestions = $state<Array<{ issue_id: string; reason: string; confidence: number }>>([]);
  let resultAssessment = $state<any>(null);
  let feedbackInlineRef = $state<any>(null);

  $effect(() => {
    const optId = forge.optimizationId;
    if (optId && !forge.isForging) {
      feedback.loadFeedback(optId);
      feedback.loadAdaptationPulse();
      refinement.loadBranches(optId);
    }
  });

  // Load result assessment from SSE adaptation data when forge completes
  $effect(() => {
    if (forge.overallScore != null && !forge.isForging && forge.stageResults['validate']) {
      // Build a basic assessment from available data if no SSE assessment arrived
      const validateData = forge.stageResults['validate']?.data as Record<string, unknown> | undefined;
      const adaptationData = forge.stageResults['adaptation']?.data as Record<string, unknown> | undefined;

      if (adaptationData?.result_assessment) {
        resultAssessment = adaptationData.result_assessment;
      } else if (validateData) {
        // Build minimal assessment from validation scores
        const scores = (validateData.scores || {}) as Record<string, number>;
        const overall = forge.overallScore ?? 0;
        const verdict = overall >= 7.5 ? 'strong' : overall >= 6.0 ? 'solid' : overall >= 4.5 ? 'mixed' : 'weak';
        resultAssessment = {
          verdict,
          confidence: 'medium',
          headline: `Score: ${overall.toFixed(1)}/10`,
          dimension_insights: Object.entries(scores)
            .filter(([k]) => k !== 'overall_score')
            .map(([dim, score]) => ({
              dimension: dim,
              score,
              weight: 0.2,
              label: dim.replace(/_score$/, '').replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase()),
              assessment: `${score.toFixed(1)}/10`,
              is_weak: score < 5,
              is_strong: score >= 8,
              delta_from_previous: null,
              framework_avg: null,
              user_priority: 'normal',
            })),
          trade_offs: [],
          retry_journey: {
            total_attempts: 1,
            best_attempt: 1,
            score_trajectory: [overall],
            gate_sequence: [],
            momentum_trend: 'stable',
            summary: 'Single attempt.',
          },
          framework_fit: null,
          improvement_signals: [],
          next_actions: [],
        };
      }

      // Load issue suggestions from adaptation data
      // Backend emits {suggestions: [...]} wrapper; extract the inner array
      if (adaptationData?.issue_suggestions) {
        const issueData = adaptationData.issue_suggestions as any;
        issueSuggestions = issueData?.suggestions ?? issueData ?? [];
      }
    }
  });

  // Handle SSE events for adaptation_injected and adaptation_impact
  let _lastImpactTs = $state(0);
  $effect(() => {
    const adaptData = forge.stageResults['adaptation']?.data as Record<string, unknown> | undefined;
    if (!adaptData?.adaptation_impact) return;

    const impact = adaptData.adaptation_impact as Record<string, unknown>;
    const ts = (impact._ts as number) || Date.now();
    if (ts <= _lastImpactTs) return;
    _lastImpactTs = ts;

    if (impact.has_meaningful_change) {
      const improvements = impact.improvements as Array<{ dim: string; prev: number; curr: number }> | undefined;
      if (improvements?.length && feedbackInlineRef) {
        const top = improvements[0];
        const label = top.dim.replace(/_score$/, '').replace(/\b\w/g, (c: string) => c.toUpperCase());
        const delta = (top.curr - top.prev).toFixed(1);
        feedbackInlineRef.flashImpactDelta(label, `+${delta}`);
      }
      // Toast for meaningful adaptation impact
      toast.info(
        `Adaptation impact: ${(impact.estimated_impact as string) || 'weights updated'}`,
        4000,
      );
    }
  });

  // Surface feedback store errors as persistent toasts
  $effect(() => {
    if (feedback.error) {
      toast.error(`Feedback failed: ${feedback.error}`);
    }
  });

  $effect(() => {
    if (titleInputEl) titleInputEl.focus();
  });

  $effect(() => {
    if (forge.overallScore != null && !forge.isForging) {
      completedAt = new Date();
    } else if (forge.isForging) {
      completedAt = null;
    }
  });

  // ── Tags editing ─────────────────────────────────────────────────────────────
  const MAX_TAGS = 10;
  const TAGS_DEBOUNCE_MS = 500;
  let pendingTags = $state<string[]>([]);
  let addingTag = $state(false);
  let newTagValue = $state('');
  let newTagInputEl = $state<HTMLInputElement | undefined>();
  let tagsDebounceTimer: ReturnType<typeof setTimeout> | null = null;
  let prevTagsSnapshot: string[] = [];

  onDestroy(() => {
    if (tagsDebounceTimer) clearTimeout(tagsDebounceTimer);
  });

  // Sync pendingTags from forge.tags when optimizationId changes
  $effect(() => {
    void forge.optimizationId;  // explicit dependency
    pendingTags = untrack(() => forge.tags) ?? [];
    addingTag = false;
    newTagValue = '';
  });

  $effect(() => {
    if (newTagInputEl && addingTag) newTagInputEl.focus();
  });

  function debouncedPatchTags(tags: string[]) {
    if (tagsDebounceTimer) clearTimeout(tagsDebounceTimer);
    const snapshot = [...prevTagsSnapshot];
    const id = forge.optimizationId;
    tagsDebounceTimer = setTimeout(async () => {
      if (!id) return;
      try {
        await patchOptimization(id, { tags });
        // also update history store if this entry exists there
        history.updateEntryTags(id, tags);
        forge.invalidateRecord(id);
      } catch {
        // revert
        pendingTags = snapshot;
        forge.tags = snapshot;
        history.updateEntryTags(id, snapshot);
        toast.error('Failed to save tags');
      }
    }, TAGS_DEBOUNCE_MS);
  }

  function addTag() {
    const val = newTagValue.trim();
    if (!val || pendingTags.includes(val) || pendingTags.length >= MAX_TAGS) {
      addingTag = false;
      newTagValue = '';
      return;
    }
    prevTagsSnapshot = [...pendingTags];
    pendingTags = [...pendingTags, val];
    forge.tags = [...pendingTags];
    newTagValue = '';
    addingTag = false;
    debouncedPatchTags(pendingTags);
  }

  function removeTag(tag: string) {
    prevTagsSnapshot = [...pendingTags];
    pendingTags = pendingTags.filter(t => t !== tag);
    forge.tags = [...pendingTags];
    debouncedPatchTags(pendingTags);
  }

  async function handleReforge() {
    editor.setSubTab('edit');
    await tick();
    const btn = document.querySelector<HTMLButtonElement>('[data-testid="forge-button"]');
    if (!btn) {
      toast.error('Unable to start re-run — editor not ready');
      return;
    }
    if (btn.disabled) {
      toast.error('Enter a prompt before re-running');
      return;
    }
    btn.click();
  }

  async function saveTitle() {
    // Guard against blur firing after Escape already cancelled the edit
    if (!editingTitle || !forge.optimizationId || !titleInput.trim()) {
      editingTitle = false;
      return;
    }
    try {
      await patchOptimization(forge.optimizationId, { title: titleInput.trim() });
      displayTitle = titleInput.trim();
      // Keep history list and record cache in sync
      history.updateEntryTitle(forge.optimizationId!, titleInput.trim());
      forge.invalidateRecord(forge.optimizationId!);
      toast.success('Title saved');
    } catch {
      toast.error('Failed to save title');
    }
    editingTitle = false;
  }

  let validationData = $derived(
    forge.stageResults['validate']?.data as Record<string, unknown> || {}
  );
  let scores = $derived(
    (validationData.scores || {}) as Record<string, number>
  );

  // Retry UI state — derived from STRATEGY_HEX so new frameworks appear automatically
  const retryStrategies = [
    { value: 'auto', label: 'Auto (keep current)' },
    ...Object.keys(STRATEGY_HEX).map(k => ({ value: k, label: k.replace(/-/g, ' ') }))
  ];
  let showRetryMenu = $state(false);
  let selectedRetryStrategy = $state('auto');

  async function handleRetry() {
    if (!forge.optimizationId) return;
    showRetryMenu = false;
    await forge.retryForge(
      forge.optimizationId,
      selectedRetryStrategy === 'auto' ? undefined : selectedRetryStrategy
    );
  }

  function handleDocClick() {
    if (showRetryMenu) showRetryMenu = false;
  }
</script>

<div
  class="flex flex-col h-full animate-fade-in"
  role="presentation"
  onclick={handleDocClick}
  onkeydown={(e) => { if (e.key === 'Escape') handleDocClick(); }}
>
  <!-- Header -->
  <div class="flex items-center justify-between px-4 py-2 border-b border-border-subtle shrink-0 gap-2">
    <div class="flex items-center gap-2 min-w-0">
      {#if editingTitle && forge.optimizationId}
        <input
          name="artifact-title"
          class="text-sm font-semibold text-text-primary bg-transparent border-b
                 border-neon-cyan/50 focus:outline-none max-w-[200px]"
          bind:this={titleInputEl}
          bind:value={titleInput}
          onblur={saveTitle}
          onkeydown={(e) => {
            if (e.key === 'Enter') saveTitle();
            if (e.key === 'Escape') { editingTitle = false; titleInput = displayTitle; }
          }}
        />
      {:else}
        <div class="group flex items-center gap-1.5 min-w-0">
          <h2
            class="text-sm font-semibold text-text-primary shrink-0
                   {forge.optimizationId ? 'cursor-pointer hover:text-neon-cyan/80 transition-colors' : ''}"
            ondblclick={() => {
              if (forge.optimizationId) { titleInput = displayTitle; editingTitle = true; }
            }}
            title={forge.optimizationId ? 'Double-click to rename' : ''}
          >{displayTitle}</h2>
          {#if forge.optimizationId}
            <svg class="w-3 h-3 text-text-dim opacity-0 group-hover:opacity-40 transition-opacity shrink-0"
                 fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="1.5">
              <path stroke-linecap="round" stroke-linejoin="round" d="M16.862 4.487l1.687-1.688a1.875 1.875 0 112.652 2.652L10.582 16.07a4.5 4.5 0 01-1.897 1.13L6 18l.8-2.685a4.5 4.5 0 011.13-1.897l8.932-8.931zm0 0L19.5 7.125"></path>
            </svg>
          {/if}
        </div>
      {/if}
      {#if forge.stageResults?.strategy?.data?.primary_framework}
        <StrategyBadge strategy={forge.stageResults.strategy.data.primary_framework as string} />
      {/if}
      {#if completedAt}
        <span class="text-[10px] text-text-dim font-mono shrink-0">
          {completedAt.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
        </span>
      {/if}
    </div>
    <div class="flex items-center gap-2 shrink-0">
      {#if forge.isForging}
        <svg class="w-3.5 h-3.5 animate-spin text-neon-cyan/60" fill="none" viewBox="0 0 24 24">
          <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
          <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"></path>
        </svg>
      {:else if forge.optimizationId}
        <!-- Retry button with inline dropdown -->
        <div class="relative">
          <button
            class="text-[10px] px-2 py-1 border border-border-subtle text-text-secondary hover:border-neon-cyan/30 hover:text-neon-cyan transition-colors font-mono"
            onclick={() => { showRetryMenu = !showRetryMenu; }}
            title="Retry with a different strategy"
          >
            ↺ Retry
          </button>
          {#if showRetryMenu}
            <div
              role="menu"
              tabindex="-1"
              class="absolute right-0 top-full mt-1 w-52 bg-bg-card border border-neon-cyan/30 z-[200] font-mono"
              onclick={(e) => e.stopPropagation()}
              onkeydown={(e) => e.stopPropagation()}
            >
              <div class="px-3 py-1.5 border-b border-border-subtle text-[10px] text-neon-cyan/70 uppercase tracking-wider">
                Retry with strategy
              </div>
              <div class="px-3 py-2">
                <select
                  name="retry-strategy"
                  class="w-full bg-bg-input border border-border-subtle px-2 py-1 text-[11px] text-text-primary focus:outline-none focus:border-neon-cyan/40 appearance-none cursor-pointer"
                  bind:value={selectedRetryStrategy}
                >
                  {#each retryStrategies as s}
                    <option value={s.value} class="bg-bg-card">{s.label}</option>
                  {/each}
                </select>
              </div>
              <div class="px-3 pb-2 flex items-center gap-2">
                <button
                  class="flex-1 py-1 text-[11px] border border-neon-cyan/40 text-neon-cyan hover:bg-neon-cyan/10 transition-colors font-mono"
                  onclick={handleRetry}
                >
                  Retry
                </button>
                <button
                  class="text-[11px] text-text-dim hover:text-text-secondary transition-colors px-1"
                  onclick={() => { showRetryMenu = false; }}
                >
                  Cancel
                </button>
              </div>
            </div>
          {/if}
        </div>
      {/if}
      {#if forge.streamingText && !forge.isForging}
        <button
          class="text-[10px] px-2 py-1 border border-border-subtle text-text-secondary hover:border-neon-cyan/30 hover:text-text-primary transition-colors"
          onclick={handleReforge}
          title="Re-synthesize this prompt"
        >
          Re-run
        </button>
      {/if}
      {#if !forge.isForging && forge.optimizationId && refinement.branchCount > 1}
        <BranchIndicator optimizationId={forge.optimizationId} />
      {/if}
      {#if forge.overallScore != null}
        <ScoreCircle score={forge.overallScore} size={28} />
      {/if}
    </div>
  </div>

  <!-- Tags row -->
  {#if forge.optimizationId}
    <!-- svelte-ignore a11y_no_static_element_interactions -->
    <div class="flex items-center flex-wrap gap-1 px-4 py-1.5 border-b border-border-subtle bg-bg-secondary/30 min-h-[30px]">
      {#each pendingTags as tag}
        <span class="inline-flex items-center gap-0.5 px-1.5 py-0.5 text-[10px] font-mono border border-neon-cyan/30 text-neon-cyan/80 bg-neon-cyan/5">
          {tag}
          <button
            class="ml-0.5 text-neon-cyan/50 hover:text-neon-red transition-colors leading-none"
            onclick={() => removeTag(tag)}
            aria-label="Remove tag {tag}"
            title="Remove tag"
          >×</button>
        </span>
      {/each}
      {#if addingTag}
        <input
          type="text"
          bind:this={newTagInputEl}
          bind:value={newTagValue}
          placeholder="tag name"
          class="bg-transparent border border-neon-cyan/50 px-1 py-0.5 text-[10px] font-mono text-text-primary focus:outline-none w-20"
          onclick={(e: MouseEvent) => e.stopPropagation()}
          onblur={addTag}
          onkeydown={(e: KeyboardEvent) => {
            e.stopPropagation();
            if (e.key === 'Enter') { e.preventDefault(); addTag(); }
            if (e.key === 'Escape') { e.preventDefault(); addingTag = false; newTagValue = ''; }
          }}
        />
      {:else if pendingTags.length < MAX_TAGS}
        <button
          class="text-[10px] font-mono text-text-dim/50 hover:text-neon-cyan/70 transition-colors px-1 border border-transparent hover:border-neon-cyan/20"
          onclick={(e: MouseEvent) => { e.stopPropagation(); addingTag = true; }}
          title="Add tag"
        >{pendingTags.length === 0 ? '+ Add tag' : '+'}</button>
      {/if}
    </div>
  {/if}

  <!-- Sub-tab bar -->
  <div class="flex items-center h-8 border-b border-border-subtle bg-bg-secondary/50 px-2 gap-1 shrink-0">
    {#each subTabs as st}
      <button
        class="px-3 py-1 text-xs transition-colors
          {activeSubTab === st.id
            ? 'text-neon-cyan border-b border-neon-cyan bg-bg-primary'
            : 'text-text-dim hover:text-text-secondary'}"
        onclick={() => { activeSubTab = st.id; }}
      >
        {st.label}
      </button>
    {/each}
  </div>

  <!-- Sub-tab content -->
  <div class="flex-1 overflow-y-auto p-4" style="overscroll-behavior: contain;">
    {#if activeSubTab === 'optimized'}
      {#if forge.streamingText}
        <div class="bg-bg-card border border-border-subtle rounded-lg p-4">
          <div class="flex items-center justify-between mb-2">
            <span class="text-xs text-text-secondary font-medium">Optimized Prompt</span>
            <CopyButton text={forge.streamingText} />
          </div>
          <p class="text-[13px] text-text-primary font-sans whitespace-pre-wrap leading-relaxed">{forge.streamingText}</p>
        </div>
      {:else}
        <div class="text-center py-12">
          <p class="text-sm text-text-dim">No artifact generated yet. Synthesize a prompt first.</p>
        </div>
      {/if}

    {:else if activeSubTab === 'diff'}
      {#if forge.rawPrompt && forge.streamingText}
        <DiffView original={forge.rawPrompt} modified={forge.streamingText} />
      {:else}
        <div class="text-center py-12">
          <p class="text-sm text-text-dim">Run a synthesis to see the diff comparison.</p>
        </div>
      {/if}

    {:else if activeSubTab === 'scores'}
      {#if Object.keys(scores).length > 0}
        <div class="space-y-3">
          {#each Object.entries(scores).filter(([k]) => k !== 'overall_score') as [key, val]}
            {@const scoreVal = typeof val === 'number' ? val : 0}
            <div class="space-y-1">
              <div class="flex justify-between text-xs">
                <span class="text-text-secondary capitalize">{key.replace(/_/g, ' ')}</span>
                <span class="font-mono text-text-primary">{scoreVal}/10</span>
              </div>
              <div class="relative h-1.5 bg-bg-primary overflow-hidden"
                   style="--bar-accent: {getScoreColor(scoreVal)}33;">
                <ScoreBar score={scoreVal} max={10} />
                <div class="bar-glass absolute inset-0 pointer-events-none"></div>
              </div>
            </div>
          {/each}
        </div>
      {:else}
        <div class="text-center py-12">
          <p class="text-sm text-text-dim">Scores will appear after validation completes.</p>
        </div>
      {/if}

    {:else if activeSubTab === 'trace'}
      <TraceView />
    {/if}
  </div>

  <!-- Result Assessment (between content and feedback strip) -->
  {#if forge.optimizationId && !forge.isForging && resultAssessment}
    <div class="px-4 pb-2">
      <ResultAssessment assessment={resultAssessment} />
    </div>
  {/if}

  {#if forge.optimizationId && !forge.isForging && forge.streamingText}
    <FeedbackInline
      bind:this={feedbackInlineRef}
      optimizationId={forge.optimizationId}
      onexpandTier2={() => { showTier2 = true; }}
      onopenTier3={async () => {
        await feedback.loadAdaptationSummary();
        feedback.showAdaptationPanel = true;
      }}
    />
  {/if}

  {#if forge.optimizationId && showTier2}
    <FeedbackTier2
      optimizationId={forge.optimizationId}
      {issueSuggestions}
      onclose={() => { showTier2 = false; }}
    />
  {/if}

  {#if forge.optimizationId && refinement.refinementOpen}
    <RefinementInput optimizationId={forge.optimizationId} />
  {/if}
</div>
