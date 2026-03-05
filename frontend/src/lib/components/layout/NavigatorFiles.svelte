<script lang="ts">
  import { editor } from '$lib/stores/editor.svelte';
  import { github } from '$lib/stores/github.svelte';
  import { fetchHistory, fetchRepoTree, fetchFileContent } from '$lib/api/client';
  import { onMount } from 'svelte';

  interface FileEntry {
    name: string;
    type: 'prompt' | 'forge';
    id?: string;
    promptText?: string;
  }

  let promptFiles = $state<FileEntry[]>([
    { name: 'system-prompt.md', type: 'prompt' },
    { name: 'code-review.md', type: 'prompt' },
    { name: 'data-analysis.md', type: 'prompt' }
  ]);

  let forgeFiles = $state<FileEntry[]>([]);

  interface RepoTreeEntry {
    path: string;
    type: string;
    size?: number;
  }

  let repoTree = $state<RepoTreeEntry[]>([]);
  let repoTreeLoading = $state(false);
  let expandedDirs = $state<Set<string>>(new Set());

  function openFile(file: FileEntry) {
    editor.openTab({
      id: file.id || `file-${file.name}`,
      label: file.name,
      type: 'prompt',
      promptText: file.promptText || '',
      dirty: false
    });
  }

  async function loadRecentForges() {
    try {
      const res = await fetchHistory({ per_page: 5, sort: 'created_at', order: 'desc' });
      forgeFiles = res.items.map((item: Record<string, unknown>) => ({
        name: ((item.raw_prompt as string) || '').slice(0, 30) + '.forge',
        type: 'forge' as const,
        id: `forge-${item.id}`,
        promptText: (item.optimized_prompt || item.raw_prompt || '') as string
      }));
    } catch {
      // History not available
    }
  }

  async function loadRepoTree() {
    if (!github.selectedRepo) return;
    repoTreeLoading = true;
    try {
      const tree = await fetchRepoTree(github.selectedRepo, github.currentRepo?.default_branch || 'main');
      repoTree = (tree as RepoTreeEntry[]).slice(0, 50);
    } catch {
      repoTree = [];
    } finally {
      repoTreeLoading = false;
    }
  }

  async function openRepoFile(path: string) {
    if (!github.selectedRepo) return;
    try {
      const content = await fetchFileContent(
        github.selectedRepo,
        path,
        github.currentRepo?.default_branch || 'main'
      );
      editor.openTab({
        id: `repo-${path}`,
        label: path.split('/').pop() || path,
        type: 'prompt',
        promptText: typeof content === 'string' ? content : (content as Record<string, unknown>).content as string || '',
        dirty: false
      });
    } catch {
      // File content not available
    }
  }

  function toggleDir(dir: string) {
    const next = new Set(expandedDirs);
    if (next.has(dir)) {
      next.delete(dir);
    } else {
      next.add(dir);
    }
    expandedDirs = next;
  }

  onMount(() => {
    loadRecentForges();
    if (github.selectedRepo) {
      loadRepoTree();
    }
  });
</script>

<div class="p-2 space-y-2">
  <!-- Workspace section -->
  <div>
    <div class="flex items-center justify-between px-1 py-1">
      <span class="text-[10px] uppercase tracking-wider text-text-dim font-semibold">Workspace</span>
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

    {#each promptFiles as file}
      <button
        class="w-full flex items-center gap-2 px-2 py-1 rounded text-xs text-text-secondary hover:bg-bg-hover hover:text-text-primary transition-colors"
        onclick={() => openFile(file)}
      >
        <svg class="w-3.5 h-3.5 text-neon-cyan/60 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="1.5">
          <path stroke-linecap="round" stroke-linejoin="round" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"></path>
        </svg>
        <span class="truncate">{file.name}</span>
      </button>
    {/each}
  </div>

  <!-- Recent Forges section -->
  {#if forgeFiles.length > 0}
    <div>
      <div class="flex items-center px-1 py-1">
        <span class="text-[10px] uppercase tracking-wider text-text-dim font-semibold">Recent Forges</span>
      </div>

      {#each forgeFiles as file}
        <button
          class="w-full flex items-center gap-2 px-2 py-1 rounded text-xs text-text-secondary hover:bg-bg-hover hover:text-text-primary transition-colors"
          onclick={() => openFile(file)}
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
        <span class="text-[10px] uppercase tracking-wider text-text-dim font-semibold">Repository</span>
        <button
          class="w-5 h-5 flex items-center justify-center rounded text-text-dim hover:text-text-secondary hover:bg-bg-hover"
          aria-label="Refresh repo tree"
          onclick={loadRepoTree}
        >
          <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2">
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
          {#if entry.type === 'tree'}
            <button
              class="w-full flex items-center gap-2 px-2 py-1 rounded text-xs text-text-secondary hover:bg-bg-hover hover:text-text-primary transition-colors"
              onclick={() => toggleDir(entry.path)}
            >
              <svg class="w-3.5 h-3.5 text-neon-yellow/60 shrink-0 transition-transform {expandedDirs.has(entry.path) ? 'rotate-90' : ''}" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2">
                <path stroke-linecap="round" stroke-linejoin="round" d="M9 5l7 7-7 7"></path>
              </svg>
              <span class="truncate">{entry.path}</span>
            </button>
          {:else}
            <button
              class="w-full flex items-center gap-2 px-2 py-1 rounded text-xs text-text-secondary hover:bg-bg-hover hover:text-text-primary transition-colors"
              onclick={() => openRepoFile(entry.path)}
            >
              <svg class="w-3.5 h-3.5 text-text-dim/60 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="1.5">
                <path stroke-linecap="round" stroke-linejoin="round" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"></path>
              </svg>
              <span class="truncate">{entry.path}</span>
            </button>
          {/if}
        {/each}
      {:else}
        <p class="text-[10px] text-text-dim px-2 py-2">No files loaded</p>
      {/if}
    </div>
  {/if}

  {#if promptFiles.length === 0 && forgeFiles.length === 0 && !github.selectedRepo}
    <p class="text-xs text-text-dim px-2 py-4 text-center">No files in workspace</p>
  {/if}
</div>
