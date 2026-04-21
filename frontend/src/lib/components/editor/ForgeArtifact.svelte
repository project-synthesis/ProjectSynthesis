<script lang="ts">
  import { forgeStore } from '$lib/stores/forge.svelte';
  import { editorStore } from '$lib/stores/editor.svelte';
  import { refinementStore } from '$lib/stores/refinement.svelte';
  import MarkdownRenderer from '$lib/components/shared/MarkdownRenderer.svelte';
  import { copyToClipboard, formatCompactChars } from '$lib/utils/formatting';
  import { slide } from 'svelte/transition';
  import { navSlide } from '$lib/utils/transitions';
  import { tooltip } from '$lib/actions/tooltip';
  import { ARTIFACT_TOOLTIPS } from '$lib/utils/ui-tooltips';

  let copied = $state(false);
  let showOriginal = $state(false);
  let renderMarkdown = $state(true);
  let changesCollapsed = $state(true);
  let enrichmentCollapsed = $state(true);
  let contextCollapsed = $state(true);

  // Use per-tab cached result if available, fall back to global forgeStore.result
  const result = $derived(editorStore.activeResult ?? forgeStore.result);

  // F6: Tab-aware feedback — read from per-tab cache when viewing a cached tab
  const feedback = $derived(editorStore.activeFeedback ?? forgeStore.feedback);

  // Retrieval diagnostics from context_sources.enrichment_meta.curated_retrieval
  const curatedRetrieval = $derived.by(() => {
    const meta = result?.context_sources?.enrichment_meta as Record<string, unknown> | undefined;
    return meta?.curated_retrieval as Record<string, unknown> | undefined;
  });
  const hasContextDiagnostics = $derived(
    curatedRetrieval != null && (curatedRetrieval.files_included as number) > 0
  );

  // Enrichment telemetry — pipeline observability for the enrichment process
  const enrichmentMeta = $derived.by(() => {
    return result?.context_sources?.enrichment_meta as Record<string, unknown> | undefined;
  });
  const contextSources = $derived.by(() => {
    if (!result?.context_sources) return null;
    const { enrichment_meta: _, ...sources } = result.context_sources;
    return sources as Record<string, boolean>;
  });
  const hasEnrichmentData = $derived(contextSources != null && Object.keys(contextSources).length > 0);
  const activeLayerCount = $derived(
    contextSources ? Object.values(contextSources).filter(Boolean).length : 0
  );
  const totalLayerCount = $derived(
    contextSources ? Object.keys(contextSources).length : 0
  );

  // Pipeline execution order for layer display (not alphabetical)
  const LAYER_ORDER: { key: string; label: string }[] = [
    { key: 'heuristic_analysis',    label: 'Heuristic Analysis' },
    { key: 'codebase_context',      label: 'Codebase Context' },
    { key: 'strategy_intelligence', label: 'Strategy Intelligence' },
    { key: 'applied_patterns',      label: 'Applied Patterns' },
    { key: 'cluster_injection',     label: 'Pattern Injection' },
    { key: 'few_shot_examples',     label: 'Few-Shot Examples' },
    // Deprecated keys — shown only if present in legacy data
    { key: 'workspace_guidance',    label: 'Workspace Guidance' },
    { key: 'adaptation',            label: 'Adaptation State' },
    { key: 'performance_signals',   label: 'Performance Signals' },
  ];

  // I-9: per-layer skip reason resolution. Backend populates
  // `enrichment_meta.profile_skipped_layers` (list of layer keys) and
  // `patterns_deferred_to_pipeline` (when internal/sampling tier defers
  // applied_patterns to the pipeline's auto_inject_patterns() call).
  // The copy below is what the Inspector shows next to gray layer dots
  // so users understand *why* a layer is inactive ("by design, not bug").
  function skipReasonFor(layerKey: string): string | null {
    if (!enrichmentMeta) return null;
    const skipped = enrichmentMeta.profile_skipped_layers as string[] | undefined;
    if (!skipped?.includes(layerKey)) return null;
    if (
      layerKey === 'applied_patterns'
      && enrichmentMeta.patterns_deferred_to_pipeline === true
    ) {
      return 'deferred to pipeline';
    }
    const profile = enrichmentMeta.enrichment_profile as string | undefined;
    if (profile) {
      return `skipped — ${profile.replace('_', ' ')} profile`;
    }
    return 'skipped';
  }

  // The displayed prompt: original, selected refinement version, or latest optimized
  const displayPrompt = $derived.by(() => {
    if (showOriginal) return result?.raw_prompt || forgeStore.prompt || '';
    // If a refinement version is selected, show that version's prompt
    const selected = refinementStore.selectedVersion;
    if (selected && selected.prompt) return selected.prompt;
    let text = result?.optimized_prompt || '';
    // Strip LLM preamble + meta-headers + code fences (defensive — backend also strips)
    // 1. Remove preamble like "Here is the optimized prompt:\n\n"
    text = text.replace(/^(?:here\s+is|below\s+is)[^`\n]*(?:prompt|version)[^`\n]*:?\s*\n+/i, '');
    // 2. Remove meta-headers like "# Optimized Prompt"
    text = text.replace(/^#{1,3}\s+(?:optimized|improved|rewritten|enhanced)\s+(?:prompt|version)\s*:?\s*\n*/i, '');
    // 3. Unwrap markdown code fences wrapping the entire prompt
    text = text.replace(/^```(?:markdown|md)?\s*\n([\s\S]*?)```\s*$/i, '$1');
    // 4. Strip trailing closing fence + orphaned # from truncated LLM output
    text = text.replace(/\n```\s*$/, '');
    text = text.replace(/\n#{1,3}\s*$/, '');
    // 5. Strip leaked ## Changes / ## Applied Patterns sections (last-resort defense —
    //    backend sanitize_optimization_result() should catch these, but this guards
    //    against stale DB records or edge cases in passthrough mode)
    text = text.replace(/\n(?:---\s*\n)?#{1,4}\s+(?:(?:Summary\s+of\s+)?Changes?(?:\s+(?:Made|Summary))?|What\s+Changed(?:\s+and\s+Why)?|Applied\s+Patterns)\s*\n[\s\S]*$/i, '');
    text = text.replace(/\n\*{2}(?:(?:Summary\s+of\s+)?Changes?(?:\s+(?:Made|Summary))?|What\s+Changed(?:\s+and\s+Why)?)\*{2}\s*\n[\s\S]*$/i, '');
    text = text.replace(/\n(?:Changes|What\s+changed)\s*:\s*\n[\s\S]*$/i, '');
    return text.trim();
  });

  const displayLabel = $derived.by(() => {
    if (showOriginal) return 'ORIGINAL PROMPT';
    const selected = refinementStore.selectedVersion;
    if (selected) return `OPTIMIZED PROMPT — v${selected.version}`;
    return 'OPTIMIZED PROMPT';
  });

  async function handleCopy() {
    if (!result?.optimized_prompt) return;
    await copyToClipboard(displayPrompt);
    copied = true;
    setTimeout(() => { copied = false; }, 2000);
  }

  function viewDiff() {
    if (!result?.id) return;
    editorStore.openDiff(result.id);
  }
</script>

<div class="forge-artifact">
  {#if !result}
    <div class="empty-result">
      <span class="empty-label">No result yet — click SYNTHESIZE to optimize your prompt</span>
    </div>
  {:else}
    <!-- Header bar -->
    <div class="artifact-header">
      <span class="section-title">{displayLabel}</span>
      <div class="header-actions">
        <button
          class="action-btn"
          class:action-btn--active={showOriginal}
          onclick={() => showOriginal = !showOriginal}
          use:tooltip={showOriginal ? ARTIFACT_TOOLTIPS.show_optimized : ARTIFACT_TOOLTIPS.show_original}
        >
          {showOriginal ? 'OPTIMIZED' : 'ORIGINAL'}
        </button>
        <button
          class="action-btn"
          class:action-btn--active={renderMarkdown}
          onclick={() => renderMarkdown = !renderMarkdown}
          use:tooltip={renderMarkdown ? ARTIFACT_TOOLTIPS.show_raw : ARTIFACT_TOOLTIPS.render_markdown}
        >
          {renderMarkdown ? 'RAW' : 'RENDER'}
        </button>
        <button
          class="action-btn"
          onclick={viewDiff}
          use:tooltip={ARTIFACT_TOOLTIPS.view_diff}
        >
          DIFF
        </button>
        <span class="header-divider"></span>
        <button
          class="feedback-btn"
          class:feedback-btn--active={feedback === 'thumbs_up'}
          onclick={() => forgeStore.submitFeedback('thumbs_up')}
          aria-label="Thumbs up"
          use:tooltip={ARTIFACT_TOOLTIPS.good_result}
        >
          <span class="feedback-icon">&#9650;</span>
        </button>
        <button
          class="feedback-btn"
          class:feedback-btn--active={feedback === 'thumbs_down'}
          onclick={() => forgeStore.submitFeedback('thumbs_down')}
          aria-label="Thumbs down"
          use:tooltip={ARTIFACT_TOOLTIPS.poor_result}
        >
          <span class="feedback-icon">&#9660;</span>
        </button>
        <span class="header-divider"></span>
        <button
          class="action-btn action-btn--primary"
          onclick={handleCopy}
          use:tooltip={ARTIFACT_TOOLTIPS.copy}
        >
          {copied ? 'COPIED' : 'COPY'}
        </button>
      </div>
    </div>

    {#if result?.repo_full_name}
      <div class="artifact-repo-context font-mono">{result.repo_full_name}</div>
    {/if}

    <!-- Prompt display (original / optimized / selected version) -->
    <div class="prompt-output-wrap">
      {#if renderMarkdown}
        <div class="prompt-output-md">
          <MarkdownRenderer content={displayPrompt} />
        </div>
      {:else}
        <pre class="prompt-output">{displayPrompt}</pre>
      {/if}
    </div>

    <!-- Changes summary — collapsible, default collapsed for space optimization -->
    {#if result.changes_summary}
      <div class="changes-section">
        <button class="changes-toggle" onclick={() => changesCollapsed = !changesCollapsed} aria-expanded={!changesCollapsed}>
          <span class="toggle-indicator">{changesCollapsed ? '▸' : '▾'}</span>
          <span class="section-title">CHANGES</span>
        </button>
        {#if !changesCollapsed}
          <div class="changes-body" transition:slide={navSlide}>
            <MarkdownRenderer content={result.changes_summary} class="changes-md" />
          </div>
        {/if}
      </div>
    {/if}

    <!-- Enrichment telemetry — pipeline layer observability -->
    {#if hasEnrichmentData && contextSources}
      <div class="changes-section">
        <button class="changes-toggle" onclick={() => enrichmentCollapsed = !enrichmentCollapsed} aria-expanded={!enrichmentCollapsed}>
          <span class="toggle-indicator">{enrichmentCollapsed ? '▸' : '▾'}</span>
          <span class="section-title">ENRICHMENT</span>
          <span class="header-metrics"><span class="header-metric"><span class="header-metric-value">{activeLayerCount}/{totalLayerCount}</span> layers</span></span>
        </button>
        {#if !enrichmentCollapsed}
          <div class="enrichment-body" transition:slide={navSlide}>
            <!-- Classification: task type + domain (from heuristic analysis) -->
            {#if result?.task_type || result?.domain || enrichmentMeta?.enrichment_profile}
              <div class="enrichment-classification">
                {#if enrichmentMeta?.enrichment_profile}
                  <span class="enrichment-tag">
                    <span class="stat-label">profile</span>
                    <span class="stat-value">{(enrichmentMeta.enrichment_profile as string).replace('_', ' ')}</span>
                  </span>
                {/if}
                {#if result?.task_type}
                  <span class="enrichment-tag">
                    <span class="stat-label">task</span>
                    <span class="stat-value">{result.task_type}</span>
                  </span>
                {/if}
                {#if result?.domain}
                  <span class="enrichment-tag">
                    <span class="stat-label">domain</span>
                    <span class="stat-value">{result.domain}</span>
                  </span>
                {/if}
                {#if result?.strategy_used}
                  <span class="enrichment-tag">
                    <span class="stat-label">strategy</span>
                    <span class="stat-value stat-ok">{result.strategy_used}</span>
                  </span>
                {/if}
                {#if enrichmentMeta?.heuristic_disambiguation}
                  {@const dis = enrichmentMeta.heuristic_disambiguation as Record<string, unknown>}
                  {#if dis.original_task_type && dis.corrected_to}
                    <span class="enrichment-tag">
                      <span class="stat-label">corrected</span>
                      <span class="stat-value stat-ok">{dis.original_task_type} &rarr; {dis.corrected_to}</span>
                    </span>
                  {/if}
                {/if}
                {#if enrichmentMeta?.llm_classification_fallback}
                  <span class="enrichment-tag">
                    <span class="stat-label">llm fallback</span>
                    <span class="stat-value stat-ok">haiku</span>
                  </span>
                {/if}
              </div>
            {/if}

            <!-- Layer list: pipeline execution order, single column -->
            <div class="enrichment-layers">
              {#each LAYER_ORDER as layer (layer.key)}
                {#if contextSources[layer.key] !== undefined}
                  {@const active = contextSources[layer.key]}
                  {@const skipReason = active ? null : skipReasonFor(layer.key)}
                  <div class="enrichment-row" class:enrichment-row--active={active}>
                    <span class="enrichment-dot"></span>
                    <span class="enrichment-label">{layer.label}</span>
                    {#if skipReason}
                      <span class="enrichment-skip-reason">{skipReason}</span>
                    {/if}
                  </div>
                {/if}
              {/each}
            </div>

            <!-- Applied pattern texts — shows which patterns the LLM received -->
            {#if enrichmentMeta?.applied_pattern_texts}
              {@const patternTexts = enrichmentMeta.applied_pattern_texts as { text: string; source: string; cluster_label?: string; similarity?: number; source_count?: number }[]}
              {#if patternTexts.length > 0}
                <div class="enrichment-patterns">
                  <span class="enrichment-patterns-heading">Injected Patterns ({patternTexts.length})</span>
                  <ul class="enrichment-pattern-list">
                    {#each patternTexts as p}
                      <li>
                        <span class="ep-text">{p.text}</span>
                        {#if p.cluster_label}
                          <span class="ep-source">{p.cluster_label}</span>
                        {:else if p.source === 'explicit'}
                          <span class="ep-source">selected</span>
                        {/if}
                      </li>
                    {/each}
                  </ul>
                </div>
              {/if}
            {/if}

            <!-- Retrieval diagnostics row -->
            {#if curatedRetrieval || enrichmentMeta?.explore_synthesis || enrichmentMeta?.combined_context_chars != null}
              <div class="enrichment-diagnostics">
                {#if curatedRetrieval}
                  {@const status = curatedRetrieval.status as string}
                  {@const filesIncluded = (curatedRetrieval.files_included ?? 0) as number}
                  {@const reason = curatedRetrieval.reason as string | undefined}
                  <span class="context-stat">
                    <span class="stat-label">curated</span>
                    <span class="stat-value" class:stat-skip={status === 'skipped_task_type'} class:stat-ok={filesIncluded > 0}>
                      {#if status === 'skipped_task_type'}
                        skipped
                      {:else if status === 'empty' || filesIncluded === 0}
                        0 files
                      {:else}
                        {filesIncluded} files
                      {/if}
                    </span>
                  </span>
                  {#if status === 'skipped_task_type' && reason}
                    <span class="context-stat">
                      <span class="stat-label">skip</span>
                      <span class="stat-value stat-skip">{reason}</span>
                    </span>
                  {/if}
                {/if}
                {#if enrichmentMeta?.explore_synthesis}
                  {@const synth = enrichmentMeta.explore_synthesis as {present: boolean; char_count: number}}
                  <span class="context-stat">
                    <span class="stat-label">synthesis</span>
                    <span class="stat-value" class:stat-ok={synth.present} class:stat-skip={!synth.present}>
                      {synth.present ? `${(synth.char_count / 1000).toFixed(1)}K` : 'absent'}
                    </span>
                  </span>
                {/if}
                {#if enrichmentMeta?.workspace_as_fallback}
                  <span class="context-stat">
                    <span class="stat-label">workspace</span>
                    <span class="stat-value stat-ok">fallback</span>
                  </span>
                {/if}
                {#if enrichmentMeta?.combined_context_chars != null}
                  <span class="context-stat">
                    <span class="stat-label">budget</span>
                    <span class="stat-value" class:stat-warn={enrichmentMeta.was_truncated}>
                      {((enrichmentMeta.combined_context_chars as number) / 1000).toFixed(1)}K{#if enrichmentMeta.was_truncated} truncated{/if}
                    </span>
                  </span>
                {/if}
                {#if enrichmentMeta?.strategy_intelligence_fallback}
                  <span class="context-stat">
                    <span class="stat-label">strategy</span>
                    <span class="stat-value stat-ok">fallback (cross-domain)</span>
                  </span>
                {/if}
              </div>
            {/if}

            <!-- Strategy intelligence detail — shows what rankings were injected -->
            {#if enrichmentMeta?.strategy_intelligence_detail}
              <div class="enrichment-detail">
                <div class="enrichment-detail-heading">STRATEGY RANKINGS</div>
                <pre class="enrichment-detail-text">{enrichmentMeta.strategy_intelligence_detail}</pre>
              </div>
            {/if}

            <!-- Domain signal scores — shows which domains matched during heuristic classification -->
            {#if enrichmentMeta?.domain_signals}
              {@const signals = enrichmentMeta.domain_signals as Record<string, number>}
              {@const entries = Object.entries(signals).sort((a, b) => (b[1] as number) - (a[1] as number))}
              {#if entries.length > 0}
                <div class="enrichment-diagnostics">
                  <span class="stat-label">domain signals</span>
                  {#each entries as [domain, score]}
                    <span class="context-stat">
                      <span class="stat-value">{domain}</span>
                      <span class="stat-value" style="opacity: 0.6;">{(score as number).toFixed(1)}</span>
                    </span>
                  {/each}
                </div>
              {/if}
            {/if}

            <!-- Divergence alerts — tech stack conflicts between prompt and codebase -->
            {#if enrichmentMeta?.divergences}
              {@const divs = enrichmentMeta.divergences as Array<{prompt_tech: string; codebase_tech: string; category: string; severity: string}>}
              {@const divSource = (enrichmentMeta.divergence_source ?? 'unknown') as string}
              {#if divs.length > 0}
                <div class="enrichment-detail">
                  <div class="enrichment-detail-heading">DIVERGENCES</div>
                  {#each divs as d}
                    <div class="context-stat" style="margin-bottom: 2px;">
                      <span class="stat-label">{d.category}</span>
                      <span class="stat-value" class:stat-warn={d.severity === 'conflict'} class:stat-ok={d.severity === 'migration'}>
                        {d.prompt_tech} &ne; {d.codebase_tech}
                      </span>
                      <span class="stat-value" style="font-size: 8px; opacity: 0.6;">{d.severity}</span>
                    </div>
                  {/each}
                  <div class="context-stat" style="margin-top: 2px;">
                    <span class="stat-label">source</span>
                    <span class="stat-value">{divSource === 'synthesis_fallback' ? 'synthesis (profile skipped codebase)' : divSource}</span>
                  </div>
                </div>
              {/if}
            {/if}
          </div>
        {/if}
      </div>
    {/if}

    <!-- Context diagnostics — shows retrieval quality for codebase-aware optimizations -->
    {#if hasContextDiagnostics && curatedRetrieval}
      {@const files = (curatedRetrieval.files ?? []) as Array<{path: string; score: number; content_chars?: number; source?: string}>}
      {@const nearMisses = (curatedRetrieval.near_misses ?? []) as Array<{path: string; score: number}>}
      {@const totalIndexed = (curatedRetrieval.total_files_indexed ?? 0) as number}
      {@const budgetUsed = (curatedRetrieval.budget_used_chars ?? 0) as number}
      {@const budgetMax = (curatedRetrieval.budget_max_chars ?? 0) as number}
      {@const budgetPct = budgetMax > 0 ? (budgetUsed / budgetMax * 100) : 0}
      {@const stopReason = (curatedRetrieval.stop_reason ?? null) as string | null}
      {@const diversityExcluded = (curatedRetrieval.diversity_excluded ?? 0) as number}
      {@const hasDiagnostics = budgetMax > 0}
      <div class="changes-section">
        <button class="changes-toggle" onclick={() => contextCollapsed = !contextCollapsed} aria-expanded={!contextCollapsed}>
          <span class="toggle-indicator">{contextCollapsed ? '▸' : '▾'}</span>
          <span class="section-title">CONTEXT</span>
          <span class="header-metrics">
            <span class="header-metric"><span class="header-metric-value">{files.length}/{totalIndexed}</span> files</span>
            {#if hasDiagnostics}
              <span class="header-metric"><span class="header-metric-value">{budgetPct.toFixed(0)}%</span> budget</span>
            {/if}
          </span>
        </button>
        {#if !contextCollapsed}
          <div class="context-body" transition:slide={navSlide}>
            {#if hasDiagnostics}
              <div class="context-stats">
                <span class="context-stat">
                  <span class="stat-label">budget</span>
                  <span class="stat-value">{(budgetUsed / 1000).toFixed(1)}K / {(budgetMax / 1000).toFixed(0)}K</span>
                </span>
                {#if stopReason}
                  <span class="context-stat">
                    <span class="stat-label">stop</span>
                    <span class="stat-value" class:stat-warn={stopReason === 'budget'}>
                      {stopReason === 'budget' ? 'budget exhausted' : stopReason === 'relevance_exhausted' ? 'all relevant included' : stopReason}
                    </span>
                  </span>
                {/if}
                {#if diversityExcluded > 0}
                  <span class="context-stat">
                    <span class="stat-label">diversity</span>
                    <span class="stat-value">{diversityExcluded} displaced</span>
                  </span>
                {/if}
              </div>
            {/if}

            <div class="context-file-list">
              <div class="context-list-heading">SELECTED</div>
              {#each files as file, i}
                <div class="context-file-row">
                  <span class="context-file-rank">{i + 1}</span>
                  <span class="context-file-score" style="color: {file.score >= 0.5 ? 'var(--color-neon-green)' : file.score >= 0.35 ? 'var(--color-neon-cyan)' : 'var(--color-text-dim)'};">{file.score.toFixed(3)}</span>
                  <span class="context-file-path">{file.path}</span>
                  {#if file.source && file.source !== 'full'}
                    <span class="context-file-source" style="color: {file.source === 'outline' ? 'var(--color-text-dim)' : 'var(--color-neon-teal)'};">{file.source === 'import-graph' ? 'import' : file.source}</span>
                  {/if}
                  {#if file.content_chars != null && file.content_chars > 0}
                    <span class="context-file-chars">{formatCompactChars(file.content_chars)}</span>
                  {/if}
                </div>
              {/each}
            </div>

            {#if nearMisses.length > 0}
              <div class="context-file-list">
                <div class="context-list-heading">NEAR MISSES</div>
                {#each nearMisses as miss}
                  <div class="context-file-row context-file-row--miss">
                    <span class="context-file-rank">&mdash;</span>
                    <span class="context-file-score" style="color: {miss.score >= 0.5 ? 'var(--color-neon-green)' : miss.score >= 0.35 ? 'var(--color-neon-cyan)' : 'var(--color-text-dim)'};">{miss.score.toFixed(3)}</span>
                    <span class="context-file-path">{miss.path}</span>
                  </div>
                {/each}
              </div>
            {/if}
          </div>
        {/if}
      </div>
    {/if}

  {/if}
</div>

<style>
  .forge-artifact {
    display: flex;
    flex-direction: column;
    height: 100%;
    overflow-y: auto;
    overflow-x: hidden;
    min-width: 0;
    max-width: 100%;
  }

  .empty-result {
    display: flex;
    align-items: center;
    justify-content: center;
    height: 100%;
  }

  .empty-label {
    font-size: 11px;
    font-family: var(--font-mono);
    color: var(--color-text-dim);
  }

  .artifact-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    height: 24px;
    padding: 0 4px;
    background: var(--color-bg-secondary);
    border-bottom: 1px solid var(--color-border-subtle);
    flex-shrink: 0;
    gap: 4px;
  }

  .section-title {
    font-size: 10px;
    font-family: var(--font-display);
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    color: var(--tier-accent, var(--color-text-dim));
  }

  .header-actions {
    display: flex;
    align-items: center;
    gap: 4px;
  }

  .artifact-repo-context {
    font-size: 10px;
    color: var(--color-text-dim);
    padding: 0 6px 4px;
  }


  .prompt-output-wrap {
    flex: 1;
    overflow: auto;
    overflow-x: hidden;
    background: var(--color-bg-input);
    border-bottom: 1px solid var(--color-border-subtle);
    min-height: 0;
    min-width: 0;
    width: 100%;
  }

  .prompt-output {
    margin: 0;
    padding: 6px;
    font-family: var(--font-sans);
    font-size: 12px;
    line-height: 1.6;
    color: var(--color-text-primary);
    white-space: pre-wrap;
    word-break: break-word;
  }

  .prompt-output-md {
    padding: 6px;
    overflow-wrap: anywhere;
    word-break: break-word;
  }

  .changes-section {
    flex-shrink: 0;
    border-bottom: 1px solid var(--color-border-subtle);
    display: flex;
    flex-direction: column;
  }

  .changes-toggle {
    display: flex;
    align-items: center;
    gap: 4px;
    height: 22px;
    padding: 0 6px;
    background: var(--color-bg-secondary);
    border: none;
    color: inherit;
    font: inherit;
    cursor: pointer;
    flex-shrink: 0;
    width: 100%;
    transition: background var(--duration-hover) var(--ease-spring);
  }

  .changes-toggle:hover {
    background: var(--color-bg-hover);
  }

  .changes-toggle:focus-visible {
    outline: 1px solid color-mix(in srgb, var(--tier-accent, var(--color-neon-cyan)) 30%, transparent);
    outline-offset: -1px;
  }

  .changes-body {
    overflow: auto;
    max-height: 178px;
    padding: 4px 6px;
  }

  .changes-body :global(.changes-md) {
    font-size: 11px;
    color: var(--color-text-secondary);
  }

  .changes-body :global(.changes-md table) {
    width: 100%;
    font-size: 11px;
    border-collapse: collapse;
  }

  .changes-body :global(.changes-md th),
  .changes-body :global(.changes-md td) {
    padding: 3px 6px;
    border: 1px solid var(--color-border-subtle);
    text-align: left;
    vertical-align: top;
  }

  .changes-body :global(.changes-md th) {
    background: var(--color-bg-tertiary);
    color: var(--color-text-primary);
    font-weight: 600;
    white-space: nowrap;
  }

  .header-divider {
    width: 1px;
    height: 12px;
    background: var(--color-border-subtle);
    flex-shrink: 0;
  }

  .feedback-btn {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 18px;
    height: 16px;
    background: transparent;
    border: 1px solid transparent;
    color: var(--color-text-dim);
    cursor: pointer;
    transition: color var(--duration-hover) var(--ease-spring),
                border-color var(--duration-hover) var(--ease-spring);
  }

  .feedback-btn:hover {
    color: var(--color-text-primary);
    border-color: var(--color-border-subtle);
  }

  .feedback-btn--active {
    color: var(--tier-accent, var(--color-neon-cyan));
    border-color: var(--tier-accent, var(--color-neon-cyan));
  }

  .feedback-btn--active:hover {
    color: var(--tier-accent, var(--color-neon-cyan));
    border-color: var(--tier-accent, var(--color-neon-cyan));
  }

  .feedback-icon {
    font-size: 8px;
    line-height: 1;
  }

  /* ---- Section header metrics (shared by ENRICHMENT + CONTEXT) ---- */
  .header-metrics {
    display: flex;
    align-items: center;
    gap: 3px;
    margin-left: auto;
  }

  .header-metric {
    font-family: var(--font-mono);
    font-size: 9px;
    color: var(--color-text-dim);
    letter-spacing: 0.02em;
  }

  .header-metric + .header-metric::before {
    content: '\00b7';
    margin-right: 3px;
    color: color-mix(in srgb, var(--color-text-dim) 40%, transparent);
  }

  .header-metric-value {
    color: var(--color-text-secondary);
  }

  .context-body {
    padding: 6px;
    border-top: 1px solid var(--color-border-subtle);
    display: flex;
    flex-direction: column;
    gap: 6px;
  }

  .context-stats {
    display: flex;
    gap: 6px;
    flex-wrap: wrap;
  }

  .context-stat {
    display: flex;
    gap: 4px;
    font-family: var(--font-mono);
    font-size: 9px;
  }

  .stat-label {
    color: var(--color-text-dim);
    text-transform: uppercase;
    letter-spacing: 0.04em;
  }

  .stat-value {
    color: var(--color-text-secondary);
  }

  .stat-warn {
    color: var(--color-neon-yellow);
  }

  .stat-skip {
    color: var(--color-text-dim);
    font-style: italic;
  }

  .stat-ok {
    color: var(--color-neon-cyan);
  }

  /* ---- Enrichment telemetry ---- */

  .enrichment-body {
    padding: 4px 6px 6px;
    display: flex;
    flex-direction: column;
    gap: 4px;
  }

  .enrichment-classification {
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
    padding-bottom: 3px;
    border-bottom: 1px solid var(--color-border-subtle);
  }

  .enrichment-tag {
    display: flex;
    gap: 4px;
    font-family: var(--font-mono);
    font-size: 9px;
  }

  .enrichment-layers {
    display: flex;
    flex-direction: column;
    gap: 1px;
  }

  .enrichment-row {
    display: flex;
    align-items: center;
    gap: 6px;
    height: 18px;
  }

  .enrichment-dot {
    width: 5px;
    height: 5px;
    flex-shrink: 0;
    background: var(--color-bg-hover);
    border: 1px solid var(--color-border-subtle);
  }

  .enrichment-row--active .enrichment-dot {
    background: var(--color-neon-green);
    border-color: var(--color-neon-green);
  }

  .enrichment-label {
    font-family: var(--font-mono);
    font-size: 9px;
    color: var(--color-text-dim);
    text-transform: uppercase;
    letter-spacing: 0.04em;
  }

  .enrichment-row--active .enrichment-label {
    color: var(--color-text-secondary);
  }

  .enrichment-skip-reason {
    margin-left: auto;
    font-family: var(--font-mono);
    font-size: 9px;
    color: var(--color-text-dim);
    opacity: 0.7;
    text-transform: lowercase;
    letter-spacing: 0.02em;
  }

  .enrichment-patterns {
    padding-top: 4px;
    border-top: 1px solid var(--color-border-subtle);
  }

  .enrichment-patterns-heading {
    font-size: 9px;
    font-weight: 700;
    color: var(--color-text-muted);
    letter-spacing: 0.05em;
  }

  .enrichment-pattern-list {
    list-style: none;
    padding: 0;
    margin: 3px 0 0;
  }

  .enrichment-pattern-list li {
    display: flex;
    align-items: baseline;
    gap: 4px;
    padding: 1px 0;
  }

  .ep-text {
    font-size: 9px;
    color: var(--color-text-secondary);
    flex: 1;
    min-width: 0;
  }

  .ep-source {
    font-size: 8px;
    color: var(--color-text-muted);
    font-style: italic;
    flex-shrink: 0;
  }

  .enrichment-diagnostics {
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
    padding-top: 3px;
    border-top: 1px solid var(--color-border-subtle);
  }

  .enrichment-detail {
    padding-top: 3px;
    border-top: 1px solid var(--color-border-subtle);
  }

  .enrichment-detail-heading {
    font-family: var(--font-display);
    font-size: 9px;
    font-weight: 700;
    color: var(--color-text-dim);
    text-transform: uppercase;
    letter-spacing: 0.1em;
    margin-bottom: 2px;
  }

  .enrichment-detail-text {
    font-family: var(--font-mono);
    font-size: 9px;
    color: var(--color-text-secondary);
    white-space: pre-wrap;
    margin: 0;
    line-height: 1.5;
  }

  .context-file-list {
    display: flex;
    flex-direction: column;
    gap: 1px;
  }

  .context-list-heading {
    font-family: var(--font-display);
    font-size: 10px;
    font-weight: 700;
    color: var(--color-text-dim);
    text-transform: uppercase;
    letter-spacing: 0.1em;
    padding: 2px 0;
  }

  .context-file-row {
    display: flex;
    align-items: center;
    gap: 6px;
    font-family: var(--font-mono);
    font-size: 9px;
    padding: 1px 0;
  }

  .context-file-row--miss {
    opacity: 0.5;
  }

  .context-file-rank {
    width: 16px;
    text-align: right;
    color: var(--color-text-dim);
    flex-shrink: 0;
  }

  .context-file-score {
    width: 36px;
    text-align: right;
    flex-shrink: 0;
  }

  .context-file-path {
    color: var(--color-text-secondary);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    min-width: 0;
  }

  .context-file-source {
    font-family: var(--font-mono);
    font-size: 8px;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    flex-shrink: 0;
    opacity: 0.7;
  }

  .context-file-chars {
    margin-left: auto;
    color: var(--color-text-dim);
    flex-shrink: 0;
  }
</style>
