<script lang="ts">
  import { editor, type EditorTab } from '$lib/stores/editor.svelte';
  import { forge } from '$lib/stores/forge.svelte';
  import { github } from '$lib/stores/github.svelte';
  import { context } from '$lib/stores/context.svelte';
  import { startOptimization, type SSEEvent } from '$lib/api/client';
  import ContextBar from './ContextBar.svelte';
  import CopyButton from '$lib/components/shared/CopyButton.svelte';
  import StrategyBadge from '$lib/components/shared/StrategyBadge.svelte';
  import { toast } from '$lib/stores/toast.svelte';
  import { user } from '$lib/stores/user.svelte';
  import { history } from '$lib/stores/history.svelte';
  import { patchAuthMe, trackOnboardingEvent } from '$lib/api/client';
  import { checkAndCelebrateMilestones } from '$lib/utils/milestones';
  import { getStrategyInfo } from '$lib/utils/strategyReference';
  import Tip from '$lib/components/shared/Tip.svelte';
  import { getCaretCoordinates } from '$lib/utils/caretCoords';

  let { tab }: { tab: EditorTab } = $props();

  let strategy = $state('auto');

  // Sync strategy from tab when it has a pre-selected strategy (e.g. opened from StrategyExplainer)
  $effect(() => {
    const tabStrategy = tab.strategy;
    if (tabStrategy) {
      strategy = tabStrategy;
    }
  });

  // Strategy info for inline display
  let selectedStrategyInfo = $derived(getStrategyInfo(strategy));
  let forgeSparking = $state(false);

  // @ context popup state
  let showAtPopup = $state(false);
  let atQuery = $state('');
  let contextBarRef: ContextBar | undefined = $state();
  let textareaRef: HTMLTextAreaElement | undefined = $state();
  let atSelectedIndex = $state(0);
  let containerRef: HTMLDivElement | undefined = $state();
  let popupTop = $state(0);
  let popupLeft = $state(0);
  let popupAbove = $state(false);

  const contextSources = [
    { type: 'file', label: 'File', category: 'Sources', icon: 'M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z' },
    { type: 'repo', label: 'Repository', category: 'Sources', icon: 'M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z' },
    { type: 'url', label: 'URL', category: 'Sources', icon: 'M13.828 10.172a4 4 0 00-5.656 0l-4 4a4 4 0 105.656 5.656l1.102-1.101m-.758-4.899a4 4 0 005.656 0l4-4a4 4 0 00-5.656-5.656l-1.1 1.1' },
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
    // file/instruction/url open their own UX flows in ContextBar (file picker,
    // inline text/URL inputs). Delegate so they behave identically to the
    // ContextBar dropdown clicks and produce chips with actual content.
    if (['file', 'instruction', 'url'].includes(source.type)) {
      contextBarRef?.handleContextOptionClick(source.type);
    } else {
      // For 'repo' type, omit label so ContextBar.addChip resolves github.selectedRepo itself
      const chipLabel = source.type === 'repo' ? undefined : source.label;
      contextBarRef?.addChip(source.type, chipLabel);
    }
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
      showAtPopup = true;
      atQuery = '';
      atSelectedIndex = 0;
      updatePopupPosition();
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

  function updatePopupPosition() {
    if (!textareaRef || !containerRef) return;
    const POPUP_W = 256, POPUP_H_EST = 220, MARGIN = 8;
    const coords = getCaretCoordinates(textareaRef, textareaRef.selectionStart);

    // Textarea-relative → container-relative
    const rawTop = textareaRef.offsetTop + coords.top;
    const rawLeft = textareaRef.offsetLeft + coords.left;

    // Container viewport rect for edge detection
    const cr = containerRef.getBoundingClientRect();

    // Vertical: below caret by default, flip above if overflows viewport
    const belowY = rawTop + coords.height + 4;
    if (cr.top + belowY + POPUP_H_EST > window.innerHeight - MARGIN) {
      popupAbove = true;
      popupTop = Math.max(0, rawTop - POPUP_H_EST - 4);
    } else {
      popupAbove = false;
      popupTop = belowY;
    }

    // Horizontal: start at caret, shift left if overflows right edge
    const viewportRight = cr.left + rawLeft + POPUP_W;
    if (viewportRight > window.innerWidth - MARGIN) {
      popupLeft = Math.max(0, rawLeft - (viewportRight - window.innerWidth + MARGIN));
    } else {
      popupLeft = rawLeft;
    }
  }

  // Recalculate popup position when textarea is scrolled while popup is open
  $effect(() => {
    if (showAtPopup && textareaRef) {
      const ta = textareaRef;
      const onScroll = () => updatePopupPosition();
      ta.addEventListener('scroll', onScroll);
      return () => ta.removeEventListener('scroll', onScroll);
    }
  });

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
    if (!tab.promptText?.trim()) {
      toast.error('Please enter a prompt before synthesizing');
      return;
    }
    if (forge.isForging) return;

    // Trigger forge-spark animation before disable
    forgeSparking = true;
    setTimeout(() => { forgeSparking = false; }, 600);

    forge.startForge(tab.promptText);
    editor.setSubTab('pipeline');

    const chips = contextBarRef?.getChips() ?? [];
    const repoChip = chips.find(c => c.type === 'repo');
    const repoFullName = (repoChip?.label?.includes('/') ? repoChip.label : null)
      ?? github.selectedRepo
      ?? undefined;
    const repoBranch = github.selectedBranch ?? github.currentRepo?.default_branch ?? undefined;

    // N24: collect file chips with content
    const fileChips = context.chips.filter(c => c.type === 'file' && c.content);
    const fileContexts = fileChips.map(c => ({ name: c.label, content: c.content! }));

    // N25: collect instruction chips with content
    const instructionChips = context.chips.filter(c => c.type === 'instruction' && c.content);
    const instructions = instructionChips.map(c => c.content!);

    // N26: collect url chips with content (URL string)
    const urlChips = context.chips.filter(c => c.type === 'url' && c.content);
    const urlContexts = urlChips.map(c => c.content!);

    const controller = await startOptimization(
      {
        prompt: tab.promptText,
        strategy: strategy === 'auto' ? undefined : strategy,
        repo_full_name: repoFullName,
        repo_branch: repoBranch,
        file_contexts: fileContexts.length > 0 ? fileContexts : undefined,
        instructions: instructions.length > 0 ? instructions : undefined,
        url_contexts: urlContexts.length > 0 ? urlContexts : undefined,
      },
      (event: SSEEvent) => {
        // Delegate all event dispatching to the store (single source of truth)
        forge.handleSSEEvent(event);

        // Post-processing for 'complete' event only (tab linkage, caching, milestones)
        if (event.event === 'complete' && typeof event.data === 'object' && event.data !== null) {
          const data = event.data as Record<string, unknown>;
          if (data.optimization_id) {
            const optId = data.optimization_id as string;
            const storeTab = editor.openTabs.find(t => t.id === tab.id);
            if (storeTab) storeTab.optimizationId = optId;
            const record = forge.buildRecordFromState(
              optId,
              (data.total_duration_ms as number) ?? undefined,
              (data.total_tokens as number) ?? undefined
            );
            forge.cacheRecord(optId, record);
          }

          // Milestone + celebration system
          const usedChips = [
            ...fileChips.map(c => c.label),
            ...instructionChips.map(() => 'instruction'),
            ...urlChips.map(() => 'URL'),
          ];
          const isFirstForge = history.totalCount === 0;
          if (isFirstForge) {
            patchAuthMe({ onboarding_completed: true }).catch(() => {});
            user.onboardingCompleted = true;
            trackOnboardingEvent('first_forge', { score: forge.overallScore }).catch(() => {});
          }

          const achieved = checkAndCelebrateMilestones({
            forgeCount: history.totalCount + 1,
            score: forge.overallScore,
            usedContext: usedChips.length > 0,
            strategy: strategy,
            repoLinked: !!github.selectedRepo,
          });

          if (achieved.length === 0) {
            if (usedChips.length > 0) {
              toast.success(`Forge complete — ${usedChips.length} context item${usedChips.length !== 1 ? 's' : ''} applied`);
            } else {
              toast.success('Forge complete — prompt optimized!');
            }
          }
        }
      },
      (err: Error) => {
        forge.error = err.message;
        forge.isForging = false;
        forge.currentStage = null;
      },
      () => {
        if (forge.isForging) {
          forge.finishForge();
        }
      }
    );

    forge.setAbortController(controller);
  }

  function handleCancel() {
    forge.cancel();
  }
</script>

<div class="flex flex-col h-full">
  <!-- Textarea -->
  <div class="flex-1 p-4 relative" bind:this={containerRef}>
    <textarea
      id="prompt-textarea"
      name="prompt-text"
      bind:this={textareaRef}
      class="w-full h-full bg-bg-input text-text-primary text-sm font-sans leading-relaxed resize-none border border-border-subtle rounded-lg p-3 focus:outline-none focus:border-neon-cyan/60 placeholder:text-text-dim/50 transition-colors duration-300"
      placeholder="Enter your prompt…"
      value={tab.promptText || ''}
      oninput={handleInput}
      onkeydown={handleTextareaKeydown}
      spellcheck="false"
    ></textarea>

    <!-- @ Context injection popup -->
    {#if showAtPopup}
      <!-- svelte-ignore a11y_no_static_element_interactions -->
      <div
        class="absolute w-64 bg-bg-card border border-border-subtle z-[300]"
        class:animate-dropdown-enter={!popupAbove}
        class:animate-dropdown-enter-up={popupAbove}
        style:top="{popupTop}px"
        style:left="{popupLeft}px"
        data-testid="at-context-popup"
        onmousedown={(e) => e.preventDefault()}
      >
        <div class="px-3 py-2 border-b border-border-subtle">
          <div class="flex items-center gap-1.5 text-xs text-text-dim">
            <span class="text-neon-cyan font-mono">@</span>
            <input
              name="at-query"
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
        <div class="px-3 py-1.5 border-t border-border-subtle flex items-center justify-between">
          <span class="text-[9px] text-text-dim/50 font-mono">↑↓ navigate · Enter select · Esc close</span>
        </div>
      </div>
    {/if}
  </div>

  <!-- Context bar (below textarea per spec) -->
  <ContextBar bind:this={contextBarRef} />

  <!-- Action row -->
  <div class="flex flex-col border-t border-border-subtle bg-bg-secondary/30 shrink-0">
    <div class="flex items-center justify-between px-4 py-2">
    <div class="flex items-center gap-2" data-tour="strategy">
      <!-- Strategy selector -->
      <select
        id="strategy-select"
        name="strategy"
        class="bg-bg-input border border-border-subtle rounded px-2 py-1 text-xs text-text-primary focus:outline-none focus:border-neon-cyan/30 cursor-pointer appearance-none"
        style="background-image: url(data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='10' height='10' viewBox='0 0 24 24' fill='none' stroke='%238b8ba8' stroke-width='2'%3E%3Cpath d='M6 9l6 6 6-6'/%3E%3C/svg%3E); background-repeat: no-repeat; background-position: right 8px center; padding-right: 24px;"
        bind:value={strategy}
      >
        {#each strategies as s}
          <option value={s} class="bg-bg-card">{s === 'auto' ? 'Auto Strategy' : s}</option>
        {/each}
      </select>

      <StrategyBadge strategy={strategy} />

      {#if selectedStrategyInfo && strategy !== 'auto'}
        <span class="font-mono text-[8px] text-text-dim/50 max-w-[200px] truncate hidden sm:inline" title={selectedStrategyInfo.fullName}>
          {selectedStrategyInfo.fullName}
        </span>
      {/if}

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
            Synthesizing...
          </span>
        {:else}
          Synthesize
        {/if}
      </button>
    </div>
  </div>
  <!-- Contextual tips -->
  <div class="px-4 pb-1">
    <Tip id="strategy-select" text="Strategy determines which prompt framework is applied" />
    <Tip id="forge-shortcut" text="Ctrl+Enter is the shortcut to start synthesis" />
  </div>
  </div>
</div>
