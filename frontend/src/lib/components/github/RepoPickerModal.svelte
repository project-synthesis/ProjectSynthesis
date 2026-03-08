<script lang="ts">
  import { github, type GitHubRepo } from '$lib/stores/github.svelte';
  import { portal } from '$lib/actions/portal';
  import { fetchRepoBranches, type RepoBranch } from '$lib/api/client';
  import RepoBadge from './RepoBadge.svelte';

  let {
    open = false,
    onclose,
    onselectrepo,
  }: {
    open?: boolean;
    onclose?: () => void;
    onselectrepo?: (name: string, branch: string) => void;
  } = $props();

  let search          = $state('');
  let expandedRepo    = $state<string | null>(null);
  let branches        = $state<RepoBranch[]>([]);
  let branchesLoading = $state(false);
  let branchesError   = $state(false);
  let selectedBranch  = $state('');

  // Reset state whenever the modal is opened.
  $effect(() => {
    if (open) {
      search          = '';
      expandedRepo    = null;
      branches        = [];
      branchesLoading = false;
      branchesError   = false;
    }
  });

  // Global Escape closes the modal.
  $effect(() => {
    if (!open) return;
    function onKeydown(e: KeyboardEvent) {
      if (e.key === 'Escape') onclose?.();
    }
    document.addEventListener('keydown', onKeydown);
    return () => document.removeEventListener('keydown', onKeydown);
  });

  // Collapse branch panel when search changes.
  $effect(() => {
    search;
    expandedRepo = null;
  });

  let filtered = $derived(
    github.repos.filter(r =>
      r.full_name.toLowerCase().includes(search.toLowerCase()) ||
      (r.description ?? '').toLowerCase().includes(search.toLowerCase())
    )
  );

  // Label for the confirm button inside the branch panel.
  let confirmLabel = $derived(
    github.selectedRepo === expandedRepo ? 'Update →' : 'Link →'
  );

  async function toggleExpand(repo: GitHubRepo) {
    if (expandedRepo === repo.full_name) { expandedRepo = null; return; }

    // Capture identity before any await so stale completions can self-discard.
    const targetName    = repo.full_name;
    expandedRepo        = targetName;
    // Preserve the currently linked branch when re-opening an already-linked repo.
    selectedBranch      = (github.selectedRepo === repo.full_name && github.selectedBranch)
                            ? github.selectedBranch
                            : repo.default_branch;
    branches            = [];
    branchesLoading     = true;
    branchesError       = false;

    const [owner, repoName] = repo.full_name.split('/');
    try {
      const fetched = await fetchRepoBranches(owner, repoName);
      if (expandedRepo !== targetName) return; // user switched repos while fetching
      branches = fetched;
      // Ensure default branch is always present even if the API omitted it.
      if (branches.length > 0 && !branches.find(b => b.name === repo.default_branch)) {
        branches = [{ name: repo.default_branch, protected: false }, ...branches];
      }
    } catch {
      if (expandedRepo !== targetName) return;
      branchesError = true;
    } finally {
      if (expandedRepo === targetName) branchesLoading = false;
    }
  }

  function confirmLink() {
    if (!expandedRepo) return;
    if (!selectedBranch.trim()) return;
    if (onselectrepo) {
      onselectrepo(expandedRepo, selectedBranch.trim());
    } else {
      github.selectRepo(expandedRepo, selectedBranch.trim());
    }
    expandedRepo = null;
    onclose?.();
  }

  function relativeTime(iso: string | undefined): string {
    if (!iso) return '';
    const diff = Date.now() - new Date(iso).getTime();
    if (diff < 60000) return 'just now';          // < 1 min (handles negatives + 0)
    const mins = Math.floor(diff / 60000);
    if (mins < 60) return `${mins}m ago`;
    const hrs = Math.floor(mins / 60);
    if (hrs < 24) return `${hrs}h ago`;
    const days = Math.floor(hrs / 24);
    if (days < 7) return `${days}d ago`;
    const wks = Math.floor(days / 7);
    if (wks < 5) return `${wks}w ago`;
    return `${Math.floor(days / 30)}mo ago`;
  }
</script>

{#if open}
  <!--
    Portal wrapper: teleports both backdrop + modal to document.body so that
    position:fixed children are always viewport-relative, regardless of any
    CSS transform applied to an ancestor inside the navigator DOM tree.
  -->
  <div use:portal>

    <!-- Backdrop — uses bg-bg-primary design token, not raw black -->
    <div
      class="fixed inset-0 bg-bg-primary/80 z-[200]"
      onclick={() => onclose?.()}
      role="presentation"
    ></div>

    <!-- Dialog -->
    <div
      class="fixed top-[18%] left-1/2 -translate-x-1/2
             w-[480px] max-w-[92vw]
             bg-bg-card border border-border-subtle rounded-xl
             z-[200] overflow-hidden animate-dialog-in"
      role="dialog"
      aria-modal="true"
      aria-labelledby="repo-picker-heading"
    >
      <!-- Header: section heading + search input + close button -->
      <div class="px-4 py-3 border-b border-border-subtle">
        <div class="flex items-center justify-between mb-2.5">
          <h2 id="repo-picker-heading" class="section-heading">
            Select Repository
          </h2>
          <button
            class="w-5 h-5 flex items-center justify-center text-text-dim hover:text-text-primary transition-colors duration-150"
            onclick={() => onclose?.()}
            aria-label="Close"
          >
            <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2">
              <path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>
        <input
          type="text"
          name="repo-search"
          placeholder="Search repositories..."
          autocomplete="off"
          class="w-full bg-bg-input border border-border-subtle rounded-lg
                 px-2.5 py-1.5 text-xs text-text-primary placeholder:text-text-dim
                 focus:outline-none focus:border-neon-cyan/30 transition-colors duration-200"
          bind:value={search}
        />
      </div>

      <!-- Repo list -->
      <div class="max-h-[360px] overflow-y-auto">
        {#each filtered as repo (repo.full_name)}

          <!-- Main row — click to expand / collapse branch selector -->
          <button
            class="w-full text-left px-4 py-2.5 relative transition-colors duration-150
                   hover:bg-bg-hover
                   {github.selectedRepo === repo.full_name ? 'bg-neon-cyan/[0.04]' : ''}"
            onclick={() => toggleExpand(repo)}
          >
            {#if github.selectedRepo === repo.full_name}
              <span class="absolute left-0 top-2.5 bottom-2.5 w-px bg-neon-cyan/50"></span>
            {/if}

            <div class="flex items-center justify-between gap-2">
              <!-- Left: name badge + language chip + size -->
              <div class="flex items-center gap-2 flex-wrap min-w-0">
                <RepoBadge name={repo.full_name} isPrivate={repo.private} />

                {#if repo.language}
                  <span class="text-[10px] font-mono text-text-dim
                               bg-bg-hover border border-border-subtle
                               px-1.5 py-0.5 rounded-md shrink-0">
                    {repo.language}
                  </span>
                {/if}

                {#if repo.size_kb != null}
                  <span class="text-[10px] font-mono text-text-dim/60 shrink-0">
                    {repo.size_kb >= 1024
                      ? `${(repo.size_kb / 1024).toFixed(1)} MB`
                      : `${repo.size_kb} KB`}
                  </span>
                {/if}
              </div>

              <!-- Right: updated_at + checkmark / chevron -->
              <div class="flex items-center gap-1.5 shrink-0">
                {#if repo.updated_at}
                  <span class="text-[10px] text-text-dim/60 font-mono">
                    {relativeTime(repo.updated_at)}
                  </span>
                {/if}

                {#if github.selectedRepo === repo.full_name}
                  {#if github.selectedBranch}
                    <span class="text-[10px] font-mono text-neon-cyan/80
                                 bg-neon-cyan/[0.08] border border-neon-cyan/20
                                 px-1.5 py-0.5 rounded-full">
                      {github.selectedBranch}
                    </span>
                  {/if}
                  <svg class="w-4 h-4 text-neon-green shrink-0"
                       fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2">
                    <path stroke-linecap="round" stroke-linejoin="round" d="M5 13l4 4L19 7" />
                  </svg>

                {:else if expandedRepo === repo.full_name}
                  <svg class="w-3.5 h-3.5 text-neon-cyan/50 shrink-0 transition-transform duration-150"
                       fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2">
                    <path stroke-linecap="round" stroke-linejoin="round" d="M19 9l-7 7-7-7" />
                  </svg>

                {:else}
                  <svg class="w-3.5 h-3.5 text-text-dim/50 shrink-0"
                       fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2">
                    <path stroke-linecap="round" stroke-linejoin="round" d="M9 5l7 7-7 7" />
                  </svg>
                {/if}
              </div>
            </div>

            <!-- Metadata row: license, topics, stars, forks -->
            {#if repo.license_name || (repo.topics?.length ?? 0) > 0 || (repo.stars ?? 0) > 0 || (repo.forks ?? 0) > 0}
              <div class="flex items-center gap-1.5 mt-1 flex-wrap">
                {#if repo.license_name}
                  <span class="text-[9px] font-mono text-text-dim/70 border border-border-subtle px-1 py-px rounded">
                    {repo.license_name}
                  </span>
                {/if}
                {#each (repo.topics ?? []).slice(0, 3) as topic}
                  <span class="text-[9px] font-mono text-text-dim/60 border border-border-subtle px-1 py-px rounded">
                    {topic}
                  </span>
                {/each}
                {#if (repo.stars ?? 0) > 0}
                  <span class="text-[9px] font-mono text-neon-yellow/60">★ {repo.stars}</span>
                {/if}
                {#if (repo.forks ?? 0) > 0}
                  <span class="text-[9px] font-mono text-text-dim/50">{repo.forks} forks</span>
                {/if}
              </div>
            {/if}

            {#if repo.description}
              <p class="text-[10px] text-text-dim mt-0.5 ml-0.5 leading-relaxed line-clamp-2">{repo.description}</p>
            {/if}
          </button>

          <!-- Branch selection panel — inline expansion -->
          {#if expandedRepo === repo.full_name}
            <div class="px-4 pt-2 pb-3 bg-bg-hover/30 border-t border-border-subtle space-y-1.5">
              {#if branchesLoading}
                <p class="text-[10px] text-text-dim italic">Loading branches…</p>
              {:else if branchesError || branches.length === 0}
                <!-- Fallback: text input -->
                <div class="flex items-center gap-2">
                  <span class="text-[10px] font-mono text-text-dim shrink-0">Branch</span>
                  <input
                    name="branch-input" autocomplete="off"
                    class="flex-1 bg-bg-input rounded-lg px-2.5 py-1 text-xs text-text-primary font-mono
                           border border-border-subtle focus:outline-none focus:border-neon-cyan/30 transition-colors duration-200"
                    bind:value={selectedBranch}
                    onkeydown={(e) => { if (e.key === 'Enter') confirmLink(); if (e.key === 'Escape') expandedRepo = null; }}
                  />
                  <button class="px-2.5 py-1 rounded-md text-[10px] font-mono btn-outline-cyan shrink-0" onclick={confirmLink}>
                    {confirmLabel}
                  </button>
                </div>
                {#if branchesError}
                  <p class="text-[9px] text-neon-red/70">Could not load branches — enter manually</p>
                {/if}
              {:else}
                <div class="flex items-center gap-1.5 flex-wrap">
                  <span class="text-[10px] font-mono text-text-dim shrink-0">Branch</span>
                  {#each branches as branch}
                    <button
                      class="text-[10px] font-mono px-2 py-0.5 rounded-full border transition-colors duration-150
                             {selectedBranch === branch.name
                               ? 'bg-neon-cyan/[0.08] border-neon-cyan/30 text-neon-cyan'
                               : 'bg-bg-input border-border-subtle text-text-dim hover:border-neon-cyan/20 hover:text-text-secondary'}"
                      onclick={() => selectedBranch = branch.name}
                    >{branch.name}{#if branch.protected}&nbsp;🔒{/if}</button>
                  {/each}
                  <button class="ml-auto px-2.5 py-1 rounded-md text-[10px] font-mono btn-outline-cyan shrink-0" onclick={confirmLink}>
                    {confirmLabel}
                  </button>
                </div>
              {/if}
            </div>
          {/if}

        {/each}

        {#if filtered.length === 0}
          <p class="text-xs text-text-dim text-center py-8">
            {search ? 'No repositories match your search.' : 'No repositories found.'}
          </p>
        {/if}
      </div>
    </div>

  </div>
{/if}
