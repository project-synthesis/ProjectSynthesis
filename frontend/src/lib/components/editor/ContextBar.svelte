<script lang="ts">
  import { github } from '$lib/stores/github.svelte';
  import { context } from '$lib/stores/context.svelte';
  import { toast } from '$lib/stores/toast.svelte';

  // Sync github.selectedFiles → context chips.
  // Runs whenever selectedFiles changes (file selected/deselected in the tree browser).
  // Guard with a flag to avoid an infinite loop when this effect calls removeChip/addChip
  // (chip mutations would re-trigger if we watched chips, but we only watch selectedFiles).
  $effect(() => {
    const files = github.selectedFiles;
    // Collect IDs of existing github-sourced chips that are no longer in selectedFiles.
    const toRemove = context.chips.filter(
      c => c.source === 'github' && !files.some(f => f.path === c.filePath)
    );
    for (const chip of toRemove) {
      context.removeChip(chip.id);
    }
    // Add chips for files that don't have one yet.
    for (const f of files) {
      if (!context.chips.some(c => c.source === 'github' && c.filePath === f.path)) {
        context.addChip('file', f.name, f.content.length, f.content, 'github', f.path);
      }
    }
  });

  let showMenu = $state(false);

  // N24: file input binding
  let fileInput: HTMLInputElement;

  // N25: instruction inline input state
  let showInstructionInput = $state(false);
  let instructionText = $state('');

  // N26: URL inline input state
  let showUrlInput = $state(false);
  let urlText = $state('');

  const contextOptions = [
    { type: 'file', label: 'File', icon: 'M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z' },
    { type: 'repo', label: 'Repository', icon: 'M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z' },
    { type: 'url', label: 'URL', icon: 'M13.828 10.172a4 4 0 00-5.656 0l-4 4a4 4 0 105.656 5.656l1.102-1.101m-.758-4.899a4 4 0 005.656 0l4-4a4 4 0 00-5.656-5.656l-1.1 1.1' },
    { type: 'instruction', label: 'Instruction', icon: 'M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z' }
  ];

  // M7: format byte size for display
  function formatSize(bytes: number): string {
    if (bytes < 1024) return `${bytes}b`;
    return `${(bytes / 1024).toFixed(0)}kb`;
  }

  // N43: 50KB cap — generous for context injection; backend uses 1500 chars anyway
  const FILE_CONTENT_CAP = 50_000;

  // N24: read file contents and attach to chip
  async function handleFileSelect(e: Event) {
    const file = (e.target as HTMLInputElement).files?.[0];
    if (!file) return;
    let text = await file.text();
    if (text.length > FILE_CONTENT_CAP) {
      text = text.slice(0, FILE_CONTENT_CAP);
      toast.warning(`"${file.name}" truncated to 50KB for context injection`);
    }
    // N43: pass text.length (actual loaded chars) so the chip accurately reflects
    // how much content was captured — not the original disk size of the file.
    context.addChip('file', file.name, text.length, text);
    fileInput.value = '';
  }

  // N25: submit instruction text as chip with content
  function submitInstruction() {
    const text = instructionText.trim();
    if (!text) return;
    const preview = text.length > 35 ? text.slice(0, 35) + '…' : text;
    context.addChip('instruction', preview, undefined, text);
    instructionText = '';
    showInstructionInput = false;
  }

  // N26: submit URL as chip with stored URL as content
  function submitUrl() {
    const url = urlText.trim();
    if (!url || !/^https?:\/\//.test(url)) return;
    const label = url.replace(/^https?:\/\//, '').slice(0, 40);
    context.addChip('url', label, undefined, url);
    urlText = '';
    showUrlInput = false;
  }

  // Route dropdown option clicks to the correct handler.
  // Exported so PromptEdit's @-popup can delegate to the same flow (N24-N26).
  export function handleContextOptionClick(type: string) {
    if (type === 'file') {
      fileInput?.click();
      showMenu = false;
    } else if (type === 'instruction') {
      showUrlInput = false;         // mutual exclusion
      showInstructionInput = true;
      showMenu = false;
    } else if (type === 'url') {
      showInstructionInput = false; // mutual exclusion
      showUrlInput = true;
      showMenu = false;
    } else {
      addChip(type);
    }
  }

  export function addChip(type: string, label?: string, size?: number) {
    const chipLabel = label || (type === 'repo' && github.selectedRepo
      ? github.selectedRepo
      : `@${type}`);
    context.addChip(type, chipLabel, size);
    showMenu = false;
  }

  export function getChips() {
    return context.getChips();
  }

  export function getContextOptions() {
    return contextOptions;
  }

  // Svelte action: focuses the element when it mounts into the DOM.
  // Used instead of the bare `autofocus` HTML attribute which triggers a11y warnings.
  function focusEl(node: HTMLElement) {
    node.focus();
  }
</script>

<div class="flex items-center gap-1.5 px-4 py-1.5 border-b border-border-subtle bg-bg-secondary/30 shrink-0 min-h-[32px]">
  <span class="font-display text-[10px] font-bold text-text-dim uppercase mr-1">Context</span>

  {#if context.chips.length === 0}
    <span class="text-[10px] text-text-dim/50 italic">Add context with @</span>
  {/if}

  {#each context.chips as chip (chip.id)}
    <span class="inline-flex items-center gap-1 px-2 py-0.5 rounded-full font-mono text-[10px] bg-neon-teal/10 border border-neon-teal/40 text-neon-teal/90 animate-scale-in" data-testid="context-chip">
      <span>@</span>{chip.label}{#if chip.size} <span class="text-text-dim/60">({formatSize(chip.size)})</span>{/if}
      <button
        class="ml-0.5 text-text-dim hover:text-neon-red transition-colors duration-150"
        onclick={() => {
          // When removing a github file chip, also deselect it from the github store
          // so the tree checkbox updates accordingly.
          if (chip.source === 'github' && chip.filePath && github.selectedRepo) {
            const [owner, repo] = github.selectedRepo.split('/');
            const branch = github.selectedBranch ?? github.currentRepo?.default_branch ?? 'main';
            github.toggleFileSelection(owner, repo, chip.filePath, branch);
            // toggleFileSelection will deselect the file, which triggers the $effect above
            // to remove the chip — so we don't call context.removeChip here to avoid double removal.
          } else {
            context.removeChip(chip.id);
          }
        }}
        aria-label="Remove context"
      >
        ×
      </button>
    </span>
  {/each}

  {#if context.chips.length > 1}
    <button
      class="text-[10px] font-mono text-text-dim hover:text-neon-red transition-colors duration-150 px-1"
      onclick={() => {
        github.clearFileSelection();
        context.clear();
      }}
      aria-label="Clear all context"
      title="Clear all context chips"
    >
      clear all
    </button>
  {/if}

  {#if showInstructionInput}
    <div class="flex items-center gap-1 px-1">
      <input
        bind:value={instructionText}
        use:focusEl
        class="bg-bg-input border border-border-accent text-text-primary font-sans
               text-xs px-2 py-0.5 w-52 focus:outline-none focus:border-neon-cyan/50"
        placeholder="e.g. always use bullet points"
        onkeydown={(e) => {
          if (e.key === 'Enter') submitInstruction();
          if (e.key === 'Escape') showInstructionInput = false;
        }}
      />
      <button
        class="text-neon-cyan text-[10px] font-mono px-1 hover:text-neon-cyan/80"
        onclick={submitInstruction}
      >+</button>
    </div>
  {/if}

  {#if showUrlInput}
    <div class="flex items-center gap-1 px-1">
      <input
        bind:value={urlText}
        use:focusEl
        class="bg-bg-input border border-border-accent text-text-primary font-sans
               text-xs px-2 py-0.5 w-52 focus:outline-none focus:border-neon-cyan/50"
        placeholder="https://..."
        onkeydown={(e) => {
          if (e.key === 'Enter') submitUrl();
          if (e.key === 'Escape') showUrlInput = false;
        }}
      />
      <button
        class="text-neon-cyan text-[10px] font-mono px-1 hover:text-neon-cyan/80"
        onclick={submitUrl}
      >+</button>
    </div>
  {/if}

  <div class="relative">
    <button
      class="w-5 h-5 flex items-center justify-center rounded text-text-dim hover:text-neon-cyan hover:bg-bg-hover transition-colors"
      onclick={() => { showMenu = !showMenu; }}
      aria-label="Add context"
      aria-haspopup="listbox"
      aria-expanded={showMenu}
    >
      <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2">
        <path stroke-linecap="round" stroke-linejoin="round" d="M12 4v16m8-8H4"></path>
      </svg>
    </button>

    {#if showMenu}
      <div class="absolute top-full left-0 mt-1 w-36 bg-bg-card border border-border-subtle rounded-lg z-[300] py-1 animate-dropdown-enter">
        {#each contextOptions as opt}
          <button
            class="w-full flex items-center gap-2 px-3 py-1.5 text-xs text-text-secondary hover:bg-bg-hover hover:text-text-primary transition-colors"
            onclick={() => handleContextOptionClick(opt.type)}
          >
            <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="1.5">
              <path stroke-linecap="round" stroke-linejoin="round" d={opt.icon}></path>
            </svg>
            {opt.label}
          </button>
        {/each}
      </div>
    {/if}
  </div>

  <!-- N24: hidden file picker, triggered by handleContextOptionClick('file') -->
  <input
    bind:this={fileInput}
    type="file"
    class="hidden"
    accept="text/*,.md,.txt,.py,.ts,.js,.json,.yaml,.yml,.toml"
    onchange={handleFileSelect}
  />
</div>
