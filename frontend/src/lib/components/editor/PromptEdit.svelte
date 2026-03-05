<script lang="ts">
  import { editor, type EditorTab } from '$lib/stores/editor.svelte';
  import { forge } from '$lib/stores/forge.svelte';
  import { github } from '$lib/stores/github.svelte';
  import { startOptimization, type SSEEvent } from '$lib/api/client';
  import ContextBar from './ContextBar.svelte';
  import CopyButton from '$lib/components/shared/CopyButton.svelte';
  import ModelBadge from '$lib/components/shared/ModelBadge.svelte';
  import StrategyBadge from '$lib/components/shared/StrategyBadge.svelte';
  import { toast } from '$lib/stores/toast.svelte';

  let { tab }: { tab: EditorTab } = $props();

  let strategy = $state('auto');
  let abortController = $state<AbortController | null>(null);
  let forgeSparking = $state(false);

  // @ context popup state
  let showAtPopup = $state(false);
  let atQuery = $state('');
  let contextBarRef: ContextBar | undefined = $state();
  let textareaRef: HTMLTextAreaElement | undefined = $state();
  let atPopupX = $state(0);
  let atPopupY = $state(0);
  let atSelectedIndex = $state(0);

  const contextSources = [
    { type: 'file', label: 'File', category: 'Sources', icon: 'M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z' },
    { type: 'repo', label: 'Repository', category: 'Sources', icon: 'M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z' },
    { type: 'url', label: 'URL', category: 'Sources', icon: 'M13.828 10.172a4 4 0 00-5.656 0l-4 4a4 4 0 105.656 5.656l1.102-1.101m-.758-4.899a4 4 0 005.656 0l4-4a4 4 0 00-5.656-5.656l-1.1 1.1' },
    { type: 'template', label: 'Template', category: 'Templates', icon: 'M4 5a1 1 0 011-1h14a1 1 0 011 1v2a1 1 0 01-1 1H5a1 1 0 01-1-1V5zM4 13a1 1 0 011-1h6a1 1 0 011 1v6a1 1 0 01-1 1H5a1 1 0 01-1-1v-6zM16 13a1 1 0 011-1h2a1 1 0 011 1v6a1 1 0 01-1 1h-2a1 1 0 01-1-1v-6z' },
    { type: 'instruction', label: 'Instruction', category: 'Templates', icon: 'M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z' }
  ];

  const filteredSources = $derived(
    atQuery.length === 0
      ? contextSources
      : contextSources.filter(s =>
          s.label.toLowerCase().includes(atQuery.toLowerCase()) ||
          s.type.toLowerCase().includes(atQuery.toLowerCase()) ||
          s.category.toLowerCase().includes(atQuery.toLowerCase())
        )
  );

  function handleAtSelect(source: typeof contextSources[0]) {
    contextBarRef?.addChip(source.type, source.label);
    closeAtPopup();
    // Remove the @query from textarea
    if (textareaRef) {
      const text = tab.promptText || '';
      const cursorPos = textareaRef.selectionStart;
      // Find the @ that triggered this popup
      const beforeCursor = text.slice(0, cursorPos);
      const atIdx = beforeCursor.lastIndexOf('@');
      if (atIdx >= 0) {
        const newText = text.slice(0, atIdx) + text.slice(cursorPos);
        editor.updateTabPrompt(tab.id, newText);
      }
      textareaRef.focus();
    }
  }

  function closeAtPopup() {
    showAtPopup = false;
    atQuery = '';
    atSelectedIndex = 0;
  }

  const strategies = [
    'auto',
    'CO-STAR', 'RISEN', 'chain-of-thought', 'few-shot-scaffolding',
    'role-task-format', 'structured-output', 'step-by-step',
    'constraint-injection', 'context-enrichment', 'persona-assignment'
  ];

  function handleInput(e: Event) {
    const target = e.target as HTMLTextAreaElement;
    const value = target.value;
    editor.updateTabPrompt(tab.id, value);

    // Detect @ trigger
    const cursorPos = target.selectionStart;
    const charBefore = value[cursorPos - 1];
    if (charBefore === '@') {
      // Position the popup near the textarea cursor
      const rect = target.getBoundingClientRect();
      atPopupX = rect.left + 20;
      atPopupY = rect.top + 40;
      showAtPopup = true;
      atQuery = '';
      atSelectedIndex = 0;
    } else if (showAtPopup) {
      // Update fuzzy query with characters after @
      const beforeCursor = value.slice(0, cursorPos);
      const atIdx = beforeCursor.lastIndexOf('@');
      if (atIdx >= 0) {
        atQuery = beforeCursor.slice(atIdx + 1);
      } else {
        closeAtPopup();
      }
    }
  }

  function handleTextareaKeydown(e: KeyboardEvent) {
    if (!showAtPopup) return;

    if (e.key === 'ArrowDown') {
      e.preventDefault();
      atSelectedIndex = Math.min(atSelectedIndex + 1, filteredSources.length - 1);
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      atSelectedIndex = Math.max(atSelectedIndex - 1, 0);
    } else if (e.key === 'Enter' && filteredSources.length > 0) {
      e.preventDefault();
      handleAtSelect(filteredSources[atSelectedIndex]);
    } else if (e.key === 'Escape') {
      e.preventDefault();
      closeAtPopup();
    }
  }

  async function handleForge() {
    if (!tab.promptText?.trim()) return;
    if (forge.isForging) return;

    // Trigger forge-spark animation before disable
    forgeSparking = true;
    setTimeout(() => { forgeSparking = false; }, 600);

    forge.startForge(tab.promptText);
    editor.setSubTab('pipeline');

    const controller = await startOptimization(
      {
        prompt: tab.promptText,
        strategy: strategy === 'auto' ? undefined : strategy,
        repo_full_name: github.selectedRepo ?? undefined,
        repo_branch: github.currentRepo?.default_branch ?? undefined
      },
      (event: SSEEvent) => {
        const data = event.data as Record<string, unknown>;
        switch (event.event) {
          case 'stage': {
            const stageName = data.stage as string;
            if (data.status === 'started') {
              forge.setStageRunning(stageName);
            } else if (data.status === 'complete') {
              // Always capture duration and token count from stage complete event
              // (it's the authoritative source — dedicated result events don't include it)
              if (forge.stageResults[stageName]) {
                forge.stageResults[stageName] = {
                  ...forge.stageResults[stageName],
                  duration: data.duration_ms as number | undefined,
                  tokenCount: data.token_count as number | undefined
                };
              }
              // Only mark complete if a result event hasn't already done so
              if (forge.stageStatuses[stageName] !== 'done') {
                forge.setStageComplete(stageName, {
                  stage: stageName,
                  data,
                  duration: data.duration_ms as number | undefined,
                  tokenCount: data.token_count as number | undefined
                });
              }
            } else if (data.status === 'skipped') {
              forge.setStageSkipped(stageName);
            }
            break;
          }
          case 'analysis':
            forge.setStageComplete('analyze', { stage: 'analyze', data, duration: data.duration_ms as number | undefined });
            break;
          case 'strategy':
            forge.setStageComplete('strategy', { stage: 'strategy', data, duration: data.duration_ms as number | undefined });
            break;
          case 'step_progress':
            forge.appendStreamingText(data.content as string || '');
            break;
          case 'optimization':
            forge.setStageComplete('optimize', { stage: 'optimize', data, duration: data.duration_ms as number | undefined });
            break;
          case 'validation':
            forge.setStageComplete('validate', { stage: 'validate', data, duration: data.duration_ms as number | undefined });
            if (data.overall_score != null) {
              forge.overallScore = data.overall_score as number;
            } else {
              const scores = data.scores as Record<string, number> | undefined;
              if (scores?.overall_score != null) {
                forge.overallScore = scores.overall_score;
              }
            }
            break;
          case 'complete':
            if (data.optimization_id) {
              forge.optimizationId = data.optimization_id as string;
            }
            forge.finishForge(forge.overallScore ?? undefined, data.total_duration_ms as number | undefined, data.total_tokens as number | undefined);
            toast.success('Forge complete — prompt optimized!');
            break;
          case 'error':
            forge.setStageFailed(data.stage as string || 'pipeline', data.error as string);
            break;
          case 'rate_limit_warning':
            // Non-fatal warning — show toast but do NOT stop the pipeline
            toast.warning(data.message as string || 'Rate limit warning — retrying');
            break;
          default:
            break;
        }
      },
      (err: Error) => {
        forge.error = err.message;
        forge.isForging = false;
      },
      () => {
        if (forge.isForging) {
          forge.finishForge();
        }
      }
    );

    abortController = controller;
  }

  function handleCancel() {
    if (abortController) {
      abortController.abort();
      abortController = null;
    }
    forge.isForging = false;
  }
</script>

<div class="flex flex-col h-full">
  <!-- Textarea -->
  <div class="flex-1 p-4 relative">
    <textarea
      bind:this={textareaRef}
      class="w-full h-full bg-bg-input text-text-primary text-sm font-sans leading-relaxed resize-none border border-border-subtle rounded-lg p-3 focus:outline-none focus:border-neon-cyan placeholder:text-text-dim/50 transition-colors duration-300"
      placeholder="Enter your prompt here... Describe what you want the AI to do, and PromptForge will optimize it for better results."
      value={tab.promptText || ''}
      oninput={handleInput}
      onkeydown={handleTextareaKeydown}
      spellcheck="false"
    ></textarea>

    <!-- @ Context injection popup -->
    {#if showAtPopup}
      <!-- svelte-ignore a11y_no_static_element_interactions -->
      <div
        class="absolute left-4 top-10 w-64 bg-bg-card border border-border-subtle rounded-lg z-50 animate-dropdown-enter"
        data-testid="at-context-popup"
        onmousedown={(e) => e.preventDefault()}
      >
        <div class="px-3 py-2 border-b border-border-subtle">
          <div class="flex items-center gap-1.5 text-xs text-text-dim">
            <span class="text-neon-cyan font-mono">@</span>
            <input
              class="flex-1 bg-transparent text-text-primary text-xs focus:outline-none"
              placeholder="Search context sources..."
              value={atQuery}
              readonly
            />
          </div>
        </div>
        <div class="py-1 max-h-48 overflow-y-auto">
          {#each filteredSources as source, i}
            <button
              class="w-full flex items-center gap-2 px-3 py-1.5 text-xs transition-colors
                {i === atSelectedIndex ? 'bg-bg-hover text-text-primary' : 'text-text-secondary hover:bg-bg-hover hover:text-text-primary'}"
              onclick={() => handleAtSelect(source)}
            >
              <svg class="w-3.5 h-3.5 text-neon-cyan/70" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="1.5">
                <path stroke-linecap="round" stroke-linejoin="round" d={source.icon}></path>
              </svg>
              <span>{source.label}</span>
              <span class="ml-auto text-[10px] text-text-dim">{source.category}</span>
            </button>
          {/each}
          {#if filteredSources.length === 0}
            <div class="px-3 py-2 text-xs text-text-dim italic">No matching sources</div>
          {/if}
        </div>
      </div>
    {/if}
  </div>

  <!-- Word count -->
  <div class="flex items-center justify-end px-4 py-0.5 text-[10px] text-text-dim shrink-0">
    <span data-testid="word-count">{(tab.promptText || '').split(/\s+/).filter(Boolean).length} words</span>
    <span class="mx-1.5">·</span>
    <span>{(tab.promptText || '').length} chars</span>
  </div>

  <!-- Context bar (below textarea per spec) -->
  <ContextBar bind:this={contextBarRef} />

  <!-- Action row -->
  <div class="flex items-center justify-between px-4 py-2 border-t border-border-subtle bg-bg-secondary/30 shrink-0">
    <div class="flex items-center gap-2">
      <!-- Strategy selector -->
      <select
        class="bg-bg-input border border-border-subtle rounded px-2 py-1 text-xs text-text-primary focus:outline-none focus:border-neon-cyan/30 cursor-pointer"
        bind:value={strategy}
      >
        {#each strategies as s}
          <option value={s} class="bg-bg-card">{s === 'auto' ? 'Auto Strategy' : s}</option>
        {/each}
      </select>

      <StrategyBadge strategy={strategy} />

      {#if tab.promptText}
        <CopyButton text={tab.promptText} />
      {/if}
    </div>

    <div class="flex items-center gap-2">
      {#if forge.isForging}
        <button
          class="px-3 py-1.5 rounded-lg text-xs font-medium bg-neon-red/10 text-neon-red border border-neon-red/20 hover:bg-neon-red/20 transition-all"
          onclick={handleCancel}
        >
          Cancel
        </button>
      {/if}

      <button
        class="btn-forge px-4 py-1.5 rounded-lg text-xs font-semibold transition-all duration-200
          {forge.isForging
            ? 'opacity-40 cursor-not-allowed'
            : 'hover:-translate-y-px active:translate-y-0'}
          {forgeSparking ? 'forge-sparking' : ''}"
        onclick={handleForge}
        disabled={forge.isForging || !tab.promptText?.trim()}
        data-testid="forge-button"
      >
        {#if forge.isForging}
          <span class="inline-flex items-center gap-1.5">
            <svg class="w-3 h-3 animate-spin" fill="none" viewBox="0 0 24 24">
              <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
              <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"></path>
            </svg>
            Forging...
          </span>
        {:else}
          Forge
        {/if}
      </button>
    </div>
  </div>
</div>
