<script lang="ts">
  import { editor } from '$lib/stores/editor.svelte';
  import { github } from '$lib/stores/github.svelte';
  import { forge } from '$lib/stores/forge.svelte';
  import { fetchHistory, fetchRepoTree, fetchFileContent, fetchOptimization, type RepoTreeEntry } from '$lib/api/client';
  import { onMount } from 'svelte';

  interface FileEntry {
    name: string;
    type: 'prompt' | 'forge';
    id?: string;
    promptText?: string;
  }

  let promptFiles = $state<FileEntry[]>([]);

  let forgeFiles = $state<FileEntry[]>([]);

  let repoTree = $state<RepoTreeEntry[]>([]);
  let repoTreeLoading = $state(false);
  let repoTreeError = $state<string | null>(null);

  function openFile(file: FileEntry) {
    if (file.type === 'forge') {
      openForgeFile(file);
      return;
    }
    editor.openTab({
      id: file.id || `file-${file.name}`,
      label: file.name,
      type: 'prompt',
      promptText: file.promptText || '',
      dirty: false
    });
  }

  async function openForgeFile(file: FileEntry) {
    const optId = file.id!.replace('forge-', '');

    function applyRecord(raw_prompt: string) {
      const truncated = raw_prompt.length > 30 ? raw_prompt.slice(0, 30) + '...' : raw_prompt;
      file = { ...file, promptText: raw_prompt, name: truncated };
    }

    const cached = forge.getRecord(optId);
    if (cached) {
      applyRecord(cached.raw_prompt);
      forge.loadFromRecord(cached);
    } else {
      try {
        const record = await fetchOptimization(optId);
        forge.cacheRecord(optId, record);
        applyRecord(record.raw_prompt);
        forge.loadFromRecord(record);
      } catch {
        // Fallback: open with whatever partial data we have from loadRecentForges
      }
    }

    editor.openTab({
      id: file.id!,
      label: file.name,
      type: 'prompt',
      promptText: file.promptText || '',
      dirty: false,
      optimizationId: optId
    });
    editor.setSubTab('pipeline');
  }

  async function loadRecentForges() {
    try {
      const res = await fetchHistory({ limit: 5, sort: 'created_at', order: 'desc' });
      forgeFiles = res.items.map((item) => ({
        name: ((item.raw_prompt as string) || '').slice(0, 30) + '.forge',
        type: 'forge' as const,
        id: `forge-${item.id}`,
        promptText: (item.raw_prompt || '') as string
      }));
    } catch {
      // History not available
    }
  }

  async function loadRepoTree() {
    if (!github.selectedRepo) return;
    const targetRepo = github.selectedRepo;
    repoTree = [];
    repoTreeError = null;
    repoTreeLoading = true;
    try {
      const [owner, repoName] = targetRepo.split('/');
      const branch = github.selectedBranch ?? github.currentRepo?.default_branch ?? 'main';
      const result = await fetchRepoTree(owner, repoName, branch);
      if (github.selectedRepo !== targetRepo) return;
      repoTree = result.tree.slice(0, 50);
    } catch (err) {
      if (github.selectedRepo !== targetRepo) return;
      repoTree = [];
      repoTreeError = (err as Error).message || 'Failed to load files';
    } finally {
      if (github.selectedRepo === targetRepo) {
        repoTreeLoading = false;
      }
    }
  }

  async function openRepoFile(path: string) {
    if (!github.selectedRepo) return;
    try {
      const [owner, repoName] = github.selectedRepo.split('/');
      const branch = github.selectedBranch ?? github.currentRepo?.default_branch ?? 'main';
      const content = await fetchFileContent(owner, repoName, path, branch);
      editor.openTab({
        id: `repo-${path}`,
        label: path.split('/').pop() || path,
        type: 'prompt',
        promptText: content.content || '',
        dirty: false
      });
    } catch {
      // File content not available
    }
  }

  function handleTreeKeydown(e: KeyboardEvent) {
    const items = Array.from(document.querySelectorAll<HTMLElement>('[data-file-item]'));
    if (!items.length) return;
    const currentIdx = items.indexOf(e.target as HTMLElement);
    if (currentIdx === -1) return;

    let newIdx = currentIdx;
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      newIdx = Math.min(currentIdx + 1, items.length - 1);
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      newIdx = Math.max(currentIdx - 1, 0);
    } else if (e.key === 'Home') {
      e.preventDefault();
      newIdx = 0;
    } else if (e.key === 'End') {
      e.preventDefault();
      newIdx = items.length - 1;
    } else {
      return;
    }
    items[newIdx]?.focus();
  }

  onMount(() => {
    loadRecentForges();
  });

  $effect(() => {
    if (github.selectedRepo) {
      loadRepoTree();
    }
  });
</script>

<div class="p-2 space-y-2">
  <!-- Workspace section -->
  <div>
    <div class="flex items-center justify-between px-1 py-1">
      <span class="font-display text-[11px] font-bold uppercase text-text-dim">Workspace</span>
      <button
        class="w-5 h-5 flex items-center justify-center rounded text-text-dim hover:text-text-secondary hover:bg-bg-hover"
        aria-label="New file"
        onclick={() => editor.openTab({ id: `prompt-${Date.now()}`, label: 'New Prompt', type: 'prompt', promptText: '', dirty: false })}
      >
        <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2">
          <path stroke-linecap="round" stroke-linejoin="round" d="M12 4v16m8-8H4"></path>
        </svg>
      </button>
    </div>

    {#if promptFiles.length > 0}
      {#each promptFiles as file, i}
        <button
          class="w-full flex items-center gap-2 px-2 py-1 rounded text-xs text-text-secondary hover:bg-bg-hover hover:text-text-primary transition-colors"
          data-file-item
          onclick={() => openFile(file)}
          onkeydown={handleTreeKeydown}
          style="animation: list-item-in 0.2s cubic-bezier(0.16,1,0.3,1) {i*35}ms both;"
        >
          <svg class="w-3.5 h-3.5 text-neon-cyan/60 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="1.5">
            <path stroke-linecap="round" stroke-linejoin="round" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"></path>
          </svg>
          <span class="truncate">{file.name}</span>
        </button>
      {/each}
    {:else}
      <div class="flex flex-col items-center text-center px-2 py-4">
        <svg class="w-6 h-6 mb-1.5 opacity-30" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="1">
          <path stroke-linecap="round" stroke-linejoin="round" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"></path>
        </svg>
        <p class="text-[10px] text-text-dim">No prompts yet</p>
        <p class="text-[10px] text-text-dim/50 mt-0.5">Click + to create a new prompt.</p>
      </div>
    {/if}
  </div>

  <!-- Recent Syntheses section -->
  {#if forgeFiles.length > 0}
    <div>
      <div class="flex items-center px-1 py-1">
        <span class="font-display text-[11px] font-bold uppercase text-text-dim">Recent Syntheses</span>
      </div>

      {#each forgeFiles as file, i}
        <button
          class="w-full flex items-center gap-2 px-2 py-1 rounded text-xs text-text-secondary hover:bg-bg-hover hover:text-text-primary transition-colors"
          data-file-item
          onclick={() => openFile(file)}
          onkeydown={handleTreeKeydown}
          style="animation: list-item-in 0.2s cubic-bezier(0.16,1,0.3,1) {i*35}ms both;"
        >
          <svg class="w-3.5 h-3.5 text-neon-green/60 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="1.5">
            <path stroke-linecap="round" stroke-linejoin="round" d="M13 10V3L4 14h7v7l9-11h-7z"></path>
          </svg>
          <span class="truncate">{file.name}</span>
        </button>
      {/each}
    </div>
  {/if}

  <!-- Linked Repo section -->
  {#if github.selectedRepo}
    <div>
      <div class="flex items-center justify-between px-1 py-1">
        <span class="font-display text-[11px] font-bold uppercase text-text-dim">Repository</span>
        <button
          class="w-5 h-5 flex items-center justify-center rounded text-text-dim hover:text-text-secondary hover:bg-bg-hover"
          aria-label="Refresh repo tree"
          onclick={loadRepoTree}
        >
          <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="1.5">
            <path stroke-linecap="round" stroke-linejoin="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"></path>
          </svg>
        </button>
      </div>

      {#if repoTreeLoading}
        <div class="flex items-center justify-center py-4">
          <div class="w-3 h-3 border border-neon-cyan/30 border-t-neon-cyan rounded-full animate-spin"></div>
        </div>
      {:else if repoTree.length > 0}
        {#each repoTree as entry}
          <button
            class="w-full flex items-center gap-2 px-2 py-1 rounded text-xs text-text-secondary hover:bg-bg-hover hover:text-text-primary transition-colors"
            data-file-item
            title={entry.path}
            onclick={() => openRepoFile(entry.path)}
            onkeydown={handleTreeKeydown}
          >
            <svg class="w-3.5 h-3.5 text-text-dim/60 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="1.5">
              <path stroke-linecap="round" stroke-linejoin="round" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"></path>
            </svg>
            <span class="truncate">{entry.path.split('/').pop() || entry.path}</span>
          </button>
        {/each}
      {:else}
        {#if repoTreeError}
          <p class="text-[10px] text-neon-red/80 px-2 py-2 truncate" title={repoTreeError}>Failed to load files</p>
        {:else}
          <p class="text-[10px] text-text-dim px-2 py-2">No files loaded</p>
        {/if}
      {/if}
    </div>
  {/if}

  {#if promptFiles.length === 0 && forgeFiles.length === 0 && !github.selectedRepo}
    <div class="flex flex-col items-center justify-center text-center px-2 py-8">
      <svg class="w-8 h-8 mb-2 opacity-30" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="1">
        <path stroke-linecap="round" stroke-linejoin="round" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"></path>
      </svg>
      <p class="text-xs text-text-secondary">No prompts yet</p>
      <p class="text-[10px] text-text-dim/50 mt-0.5">Create a new prompt to get started.</p>
      <button
        class="mt-2 px-3 py-1 text-[10px] rounded btn-outline-cyan"
        onclick={() => editor.openTab({ id: `prompt-${Date.now()}`, label: 'New Prompt', type: 'prompt', promptText: '', dirty: false })}
      >
        + New Prompt
      </button>
    </div>
  {/if}
</div>
