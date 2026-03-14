<script lang="ts">
  import { editor, type EditorTab } from '$lib/stores/editor.svelte';
  import { forge } from '$lib/stores/forge.svelte';
  import { github } from '$lib/stores/github.svelte';
  import { context } from '$lib/stores/context.svelte';
  import { startOptimization, patchAuthMe, trackOnboardingEvent, type SSEEvent } from '$lib/api/client';
  import StrategyBadge from '$lib/components/shared/StrategyBadge.svelte';
  import { toast } from '$lib/stores/toast.svelte';
  import { user } from '$lib/stores/user.svelte';
  import { history } from '$lib/stores/history.svelte';
  import { checkAndCelebrateMilestones } from '$lib/utils/milestones';
  import { getStrategyInfo } from '$lib/utils/strategyReference';
  import { getCaretCoordinates } from '$lib/utils/caretCoords';
  import { slide } from 'svelte/transition';

  let { tab }: { tab: EditorTab } = $props();

  let strategy = $state('auto');

  // Sync strategy from tab when it has a pre-selected strategy (e.g. opened from StrategyExplainer)
  $effect(() => {
    const tabStrategy = tab.strategy;
    if (tabStrategy) {
      strategy = tabStrategy;
    }
  });

  // Strategy info for hover title
  let selectedStrategyInfo = $derived(getStrategyInfo(strategy));
  let forgeSparking = $state(false);

  // @ context popup state
  let showAtPopup = $state(false);
  let atQuery = $state('');
  let textareaRef: HTMLTextAreaElement | undefined = $state();
  let atSelectedIndex = $state(0);
  let containerRef: HTMLDivElement | undefined = $state();
  let popupRef: HTMLDivElement | undefined = $state();
  let popupTop = $state(0);
  let popupLeft = $state(0);
  let popupAbove = $state(false);

  // Context panel expansion state
  let showContextPanel = $state(false);

  // Inline input states (inlined from ContextBar)
  let showInstructionInput = $state(false);
  let showUrlInput = $state(false);
  let instructionText = $state('');
  let urlText = $state('');
  let fileInput: HTMLInputElement | undefined = $state();

  const FILE_CONTENT_CAP = 50_000;

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

  // Clamp selected index when filtered list shrinks (prevents stale highlight)
  $effect(() => {
    if (atSelectedIndex >= filteredSources.length) {
      atSelectedIndex = Math.max(0, filteredSources.length - 1);
    }
  });

  // Sync github.selectedFiles → context chips (from ContextBar)
  $effect(() => {
    const files = github.selectedFiles;
    const toRemove = context.chips.filter(
      c => c.source === 'github' && !files.some(f => f.path === c.filePath)
    );
    for (const chip of toRemove) {
      context.removeChip(chip.id);
    }
    for (const f of files) {
      if (!context.chips.some(c => c.source === 'github' && c.filePath === f.path)) {
        context.addChip('file', f.name, f.content.length, f.content, 'github', f.path);
      }
    }
  });

  // Format byte size for chip display
  function formatSize(bytes: number): string {
    if (bytes < 1024) return `${bytes}b`;
    return `${(bytes / 1024).toFixed(0)}kb`;
  }

  // Context management functions (inlined from ContextBar)
  function handleContextOptionClick(type: string) {
    if (type === 'file') {
      fileInput?.click();
    } else if (type === 'instruction') {
      showUrlInput = false;
      showInstructionInput = true;
      showContextPanel = true;
    } else if (type === 'url') {
      showInstructionInput = false;
      showUrlInput = true;
      showContextPanel = true;
    } else {
      addChip(type);
    }
  }

  function addChip(type: string, label?: string, size?: number) {
    const chipLabel = label || (type === 'repo' && github.selectedRepo
      ? github.selectedRepo
      : `@${type}`);
    context.addChip(type, chipLabel, size);
  }

  async function handleFileSelect(e: Event) {
    const file = (e.target as HTMLInputElement).files?.[0];
    if (!file) return;
    let text = await file.text();
    if (text.length > FILE_CONTENT_CAP) {
      text = text.slice(0, FILE_CONTENT_CAP);
      toast.warning(`"${file.name}" truncated to 50KB for context injection`);
    }
    context.addChip('file', file.name, text.length, text);
    if (fileInput) fileInput.value = '';
  }

  function submitInstruction() {
    const text = instructionText.trim();
    if (!text) return;
    const preview = text.length > 35 ? text.slice(0, 35) + '\u2026' : text;
    context.addChip('instruction', preview, undefined, text);
    instructionText = '';
    showInstructionInput = false;
    if (!showUrlInput) showContextPanel = false;
  }

  function submitUrl() {
    const url = urlText.trim();
    if (!url || !/^https?:\/\//.test(url)) return;
    const label = url.replace(/^https?:\/\//, '').slice(0, 40);
    context.addChip('url', label, undefined, url);
    urlText = '';
    showUrlInput = false;
    if (!showInstructionInput) showContextPanel = false;
  }

  function removeChip(chip: typeof context.chips[0]) {
    if (chip.source === 'github' && chip.filePath && github.selectedRepo) {
      const [owner, repo] = github.selectedRepo.split('/');
      const branch = github.selectedBranch ?? github.currentRepo?.default_branch ?? 'main';
      github.toggleFileSelection(owner, repo, chip.filePath, branch);
    } else {
      context.removeChip(chip.id);
    }
  }

  // Svelte action: focus on mount
  function focusEl(node: HTMLElement) {
    node.focus();
  }

  function handleAtSelect(source: typeof contextSources[0]) {
    if (['file', 'instruction', 'url'].includes(source.type)) {
      handleContextOptionClick(source.type);
    } else {
      const chipLabel = source.type === 'repo' ? undefined : source.label;
      addChip(source.type, chipLabel);
    }
    closeAtPopup();
    // Remove the @query from textarea and restore cursor position
    if (textareaRef) {
      const text = tab.promptText || '';
      const cursorPos = textareaRef.selectionStart;
      const beforeCursor = text.slice(0, cursorPos);
      const atIdx = beforeCursor.lastIndexOf('@');
      if (atIdx >= 0) {
        const newText = text.slice(0, atIdx) + text.slice(cursorPos);
        editor.updateTabPrompt(tab.id, newText);
        // Wait one frame for Svelte's reactive value= binding to flush to DOM
        const insertPos = atIdx;
        requestAnimationFrame(() => {
          if (textareaRef) {
            textareaRef.focus();
            textareaRef.setSelectionRange(insertPos, insertPos);
          }
        });
      } else {
        textareaRef.focus();
      }
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

    // Detect @ trigger — only after whitespace or at start of text (word-boundary guard)
    const cursorPos = target.selectionStart;
    const charBefore = value[cursorPos - 1];
    if (charBefore === '@') {
      const charBeforeThat = cursorPos > 1 ? value[cursorPos - 2] : undefined;
      if (charBeforeThat === undefined || /\s/.test(charBeforeThat)) {
        showAtPopup = true;
        atQuery = '';
        atSelectedIndex = 0;
        updatePopupPosition();
      }
    } else if (showAtPopup) {
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

    const rawTop = textareaRef.offsetTop + coords.top;
    const rawLeft = textareaRef.offsetLeft + coords.left;
    const cr = containerRef.getBoundingClientRect();

    const belowY = rawTop + coords.height + 4;
    if (cr.top + belowY + POPUP_H_EST > window.innerHeight - MARGIN) {
      popupAbove = true;
      popupTop = Math.max(0, rawTop - POPUP_H_EST - 4);
    } else {
      popupAbove = false;
      popupTop = belowY;
    }

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

  // Dismiss popup on outside click (Bug 2)
  $effect(() => {
    if (!showAtPopup) return;
    const onMousedown = (e: MouseEvent) => {
      const target = e.target as Node;
      if (popupRef?.contains(target) || textareaRef?.contains(target)) return;
      closeAtPopup();
    };
    document.addEventListener('mousedown', onMousedown, true);
    return () => document.removeEventListener('mousedown', onMousedown, true);
  });

  // Dismiss popup on textarea blur (Bug 3)
  function handleTextareaBlur() {
    if (!showAtPopup) return;
    // Delay to let popup mousedown (which calls preventDefault) fire first
    setTimeout(() => {
      if (showAtPopup && document.activeElement !== textareaRef) {
        closeAtPopup();
      }
    }, 150);
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

    const chips = context.getChips();
    const repoChip = chips.find(c => c.type === 'repo');
    const repoFullName = (repoChip?.label?.includes('/') ? repoChip.label : null)
      ?? github.selectedRepo
      ?? undefined;
    const repoBranch = github.selectedBranch ?? github.currentRepo?.default_branch ?? undefined;

    const fileChips = context.chips.filter(c => c.type === 'file' && c.content);
    const fileContexts = fileChips.map(c => ({ name: c.label, content: c.content! }));

    const instructionChips = context.chips.filter(c => c.type === 'instruction' && c.content);
    const instructions = instructionChips.map(c => c.content!);

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
        forge.handleSSEEvent(event);

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

  function toggleContextPanel() {
    showContextPanel = !showContextPanel;
    if (!showContextPanel) {
      showInstructionInput = false;
      showUrlInput = false;
    }
  }
</script>

<div class="flex flex-col h-full">
  <!-- Textarea -->
  <div class="flex-1 p-2 relative" bind:this={containerRef}>
    <textarea
      id="prompt-textarea"
      name="prompt-text"
      bind:this={textareaRef}
      class="w-full h-full bg-bg-input text-text-primary text-xs font-sans leading-normal resize-none border border-border-subtle p-1.5 focus:outline-none focus:border-neon-cyan/40 placeholder:text-text-dim/50 transition-colors duration-200"
      placeholder="Enter your prompt…"
      value={tab.promptText || ''}
      oninput={handleInput}
      onkeydown={handleTextareaKeydown}
      onfocusout={handleTextareaBlur}
      spellcheck="false"
    ></textarea>

    <!-- @ Context injection popup -->
    {#if showAtPopup}
      <!-- svelte-ignore a11y_no_static_element_interactions -->
      <div
        bind:this={popupRef}
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

  <!-- Context expansion panel (slides down when adding context) -->
  {#if showContextPanel}
    <div class="px-2 py-1.5 border-t border-border-subtle bg-bg-secondary/30 shrink-0" transition:slide={{ duration: 200 }}>
      <!-- Context source type buttons -->
      <div class="flex items-center gap-2 mb-1.5">
        {#each contextSources as source}
          <button
            class="flex items-center gap-1 px-2 py-0.5 text-[10px] font-mono border transition-colors
              {(source.type === 'instruction' && showInstructionInput) || (source.type === 'url' && showUrlInput)
                ? 'border-neon-cyan/30 text-neon-cyan bg-neon-cyan/5'
                : 'border-border-subtle text-text-dim hover:text-text-secondary hover:border-border-accent'}"
            onclick={() => handleContextOptionClick(source.type)}
          >
            <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="1.5">
              <path stroke-linecap="round" stroke-linejoin="round" d={source.icon}></path>
            </svg>
            {source.label}
          </button>
        {/each}
      </div>

      <!-- Instruction input -->
      {#if showInstructionInput}
        <div class="flex items-center gap-1" transition:slide={{ duration: 150 }}>
          <input
            bind:value={instructionText}
            use:focusEl
            class="flex-1 bg-bg-input border border-border-accent text-text-primary font-sans
                   text-xs px-2 py-1 focus:outline-none focus:border-neon-cyan/50"
            placeholder="e.g. always use bullet points"
            onkeydown={(e) => {
              if (e.key === 'Enter') submitInstruction();
              if (e.key === 'Escape') { showInstructionInput = false; if (!showUrlInput) showContextPanel = false; }
            }}
          />
          <button
            class="px-2 py-1 text-[10px] font-mono text-neon-cyan border border-neon-cyan/20 hover:bg-neon-cyan/5 transition-colors"
            onclick={submitInstruction}
          >Add</button>
        </div>
      {/if}

      <!-- URL input -->
      {#if showUrlInput}
        <div class="flex items-center gap-1" transition:slide={{ duration: 150 }}>
          <input
            bind:value={urlText}
            use:focusEl
            class="flex-1 bg-bg-input border border-border-accent text-text-primary font-sans
                   text-xs px-2 py-1 focus:outline-none focus:border-neon-cyan/50"
            placeholder="https://..."
            onkeydown={(e) => {
              if (e.key === 'Enter') submitUrl();
              if (e.key === 'Escape') { showUrlInput = false; if (!showInstructionInput) showContextPanel = false; }
            }}
          />
          <button
            class="px-2 py-1 text-[10px] font-mono text-neon-cyan border border-neon-cyan/20 hover:bg-neon-cyan/5 transition-colors"
            onclick={submitUrl}
          >Add</button>
        </div>
      {/if}
    </div>
  {/if}

  <!-- Forge Command Bar — unified context + strategy + action -->
  <div class="flex items-center gap-1.5 px-2 py-1 border-t border-border-subtle bg-bg-secondary/30 shrink-0 min-h-[32px]">
    <!-- Left zone: Context -->
    {#if context.chips.length === 0}
      <span class="font-mono text-[9px] text-text-dim/50 shrink-0">@</span>
    {/if}

    <div class="flex items-center gap-1 flex-1 min-w-0 overflow-x-auto cmd-chips-scroll">
      {#each context.chips as chip (chip.id)}
        <span class="inline-flex items-center gap-0.5 px-1.5 py-0.5 font-mono text-[10px] bg-neon-teal/10 border border-neon-teal/40 text-neon-teal/90 animate-scale-in whitespace-nowrap shrink-0" data-testid="context-chip">
          <span class="text-[9px]">@</span>{chip.label}{#if chip.size}<span class="text-text-dim/60">({formatSize(chip.size)})</span>{/if}
          <button
            class="ml-0.5 text-text-dim hover:text-neon-red transition-colors duration-150"
            onclick={() => removeChip(chip)}
            aria-label="Remove context"
          >&times;</button>
        </span>
      {/each}

      {#if context.chips.length > 1}
        <button
          class="text-[9px] font-mono text-text-dim hover:text-neon-red transition-colors duration-150 px-0.5 whitespace-nowrap shrink-0"
          onclick={() => { github.clearFileSelection(); context.clear(); }}
          aria-label="Clear all context"
        >clear</button>
      {/if}
    </div>

    <button
      class="w-5 h-5 flex items-center justify-center text-text-dim hover:text-neon-cyan transition-colors shrink-0"
      onclick={toggleContextPanel}
      aria-label="Add context"
      aria-haspopup="true"
      aria-expanded={showContextPanel}
    >
      <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2">
        <path stroke-linecap="round" stroke-linejoin="round" d="M12 4v16m8-8H4"></path>
      </svg>
    </button>

    <!-- Divider -->
    <span class="w-px h-4 bg-border-subtle shrink-0"></span>

    <!-- Center zone: Strategy -->
    <div class="flex items-center gap-1 shrink-0" data-tour="strategy">
      <select
        id="strategy-select"
        name="strategy"
        class="bg-bg-input border border-border-subtle px-1.5 py-0.5 text-[11px] text-text-primary focus:outline-none focus:border-neon-cyan/30 cursor-pointer appearance-none"
        style="background-image: url(data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='10' height='10' viewBox='0 0 24 24' fill='none' stroke='%238b8ba8' stroke-width='2'%3E%3Cpath d='M6 9l6 6 6-6'/%3E%3C/svg%3E); background-repeat: no-repeat; background-position: right 6px center; padding-right: 20px;"
        bind:value={strategy}
        title={selectedStrategyInfo?.fullName ?? strategy}
      >
        {#each strategies as s}
          <option value={s} class="bg-bg-card">{s === 'auto' ? 'Auto' : s}</option>
        {/each}
      </select>

      <StrategyBadge strategy={strategy} />
    </div>

    <!-- Divider -->
    <span class="w-px h-4 bg-border-subtle shrink-0"></span>

    <!-- Right zone: Action -->
    <div class="flex items-center gap-1.5 shrink-0">
      {#if forge.isForging}
        <button
          class="px-2 py-0.5 text-[11px] font-medium bg-neon-red/10 text-neon-red border border-neon-red/20 hover:bg-neon-red/20 transition-colors"
          onclick={handleCancel}
        >Cancel</button>
      {/if}

      <button
        class="btn-forge px-2.5 py-0.5 text-[11px] font-semibold transition-colors duration-200
          {forge.isForging
            ? 'opacity-40 cursor-not-allowed'
            : 'hover:-translate-y-px active:translate-y-0'}
          {forgeSparking ? 'forge-sparking' : ''}"
        onclick={handleForge}
        disabled={forge.isForging || !tab.promptText?.trim()}
        data-testid="forge-button"
      >
        {#if forge.isForging}
          <span class="inline-flex items-center gap-1">
            <svg class="w-3 h-3 animate-spin" fill="none" viewBox="0 0 24 24">
              <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
              <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"></path>
            </svg>
            Forging
          </span>
        {:else}
          Synthesize
        {/if}
      </button>
    </div>
  </div>

  <!-- Hidden file picker -->
  <input
    bind:this={fileInput}
    type="file"
    class="hidden"
    accept="text/*,.md,.txt,.py,.ts,.js,.json,.yaml,.yml,.toml"
    onchange={handleFileSelect}
  />
</div>

<style>
  .cmd-chips-scroll::-webkit-scrollbar {
    display: none;
  }
  .cmd-chips-scroll {
    scrollbar-width: none;
    -ms-overflow-style: none;
  }
</style>
