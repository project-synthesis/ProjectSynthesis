<script lang="ts">
  import { github } from '$lib/stores/github.svelte';
  import { connectGitHub, disconnectGitHub, linkRepo, unlinkRepo, fetchLinkedRepo } from '$lib/api/client';
  import GitHubStatus from '$lib/components/github/GitHubStatus.svelte';

  let patInput = $state('');
  let connecting = $state(false);

  async function handleConnect() {
    if (!patInput.trim()) return;
    connecting = true;
    try {
      const res = await connectGitHub(patInput);
      github.setConnected(
        res.username,
        res.repos.map((r: Record<string, unknown>) => ({
          full_name: r.full_name as string,
          description: (r.description || '') as string,
          default_branch: (r.default_branch || 'main') as string,
          private: !!r.private
        }))
      );
      patInput = '';
      // Restore previously linked repo selection (non-blocking)
      fetchLinkedRepo()
        .then((linked) => {
          if (linked && linked.full_name) {
            github.selectRepo(linked.full_name);
          }
        })
        .catch(() => {});
    } catch (err) {
      github.setError((err as Error).message);
    } finally {
      connecting = false;
    }
  }

  async function handleSelectRepo(fullName: string) {
    github.selectRepo(fullName);
    // Persist repo link to backend (non-blocking — local state already updated)
    const repo = github.repos.find(r => r.full_name === fullName);
    linkRepo(fullName, repo?.default_branch).catch(() => {
      // Link failed — local selection still works, just won't persist across refresh
    });
  }

  async function handleDisconnect() {
    try {
      await disconnectGitHub();
      await unlinkRepo().catch(() => {});
    } catch {
      // ignore
    }
    github.disconnect();
  }
</script>

<div class="p-2 space-y-3">
  <GitHubStatus />

  {#if !github.isConnected}
    <div class="space-y-2">
      <label class="text-xs text-text-secondary block" for="github-pat-input">
        Personal Access Token
      </label>
      <input
        id="github-pat-input"
        type="password"
        placeholder="ghp_..."
        class="w-full bg-bg-input border border-border-subtle rounded px-2 py-1.5 text-xs text-text-primary placeholder:text-text-dim focus:outline-none focus:border-neon-cyan/30 font-mono"
        bind:value={patInput}
        onkeydown={(e) => { if (e.key === 'Enter') handleConnect(); }}
      />
      <button
        class="w-full py-1.5 rounded text-xs font-medium transition-all
          {connecting
            ? 'bg-bg-card text-text-dim cursor-wait'
            : 'bg-bg-card border border-border-subtle text-text-primary hover:bg-bg-hover hover:border-neon-cyan/20'}"
        onclick={handleConnect}
        disabled={connecting || !patInput.trim()}
      >
        {connecting ? 'Connecting...' : 'Connect'}
      </button>
    </div>
  {:else}
    <div class="space-y-2">
      <div class="flex items-center justify-between">
        <span class="text-xs text-text-secondary">Repositories</span>
        <button
          class="text-[10px] text-neon-red hover:text-neon-red/80"
          onclick={handleDisconnect}
        >
          Disconnect
        </button>
      </div>

      {#each github.repos as repo}
        <button
          class="w-full text-left px-2 py-1.5 rounded text-xs transition-colors
            {github.selectedRepo === repo.full_name
              ? 'bg-bg-hover border border-border-accent text-text-primary'
              : 'hover:bg-bg-hover text-text-secondary border border-transparent'}"
          onclick={() => handleSelectRepo(repo.full_name)}
        >
          <div class="flex items-center gap-1.5">
            {#if repo.private}
              <svg class="w-3 h-3 text-neon-yellow" fill="currentColor" viewBox="0 0 24 24">
                <path d="M12 1C8.676 1 6 3.676 6 7v2H4v14h16V9h-2V7c0-3.324-2.676-6-6-6zm0 2c2.276 0 4 1.724 4 4v2H8V7c0-2.276 1.724-4 4-4z"></path>
              </svg>
            {:else}
              <svg class="w-3 h-3 text-text-dim" fill="currentColor" viewBox="0 0 24 24">
                <path d="M3 3h18v18H3V3zm2 2v14h14V5H5z"></path>
              </svg>
            {/if}
            <span class="truncate">{repo.full_name}</span>
          </div>
          {#if repo.description}
            <p class="text-[10px] text-text-dim mt-0.5 truncate">{repo.description}</p>
          {/if}
        </button>
      {/each}

      {#if github.repos.length === 0}
        <p class="text-xs text-text-dim text-center py-4">No repositories found</p>
      {/if}
    </div>
  {/if}

  {#if github.error}
    <div class="text-xs text-neon-red bg-neon-red/10 px-2 py-1.5 rounded border border-neon-red/20">
      {github.error}
    </div>
  {/if}
</div>
