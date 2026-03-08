<script lang="ts">
  import { github } from '$lib/stores/github.svelte';
  import { workbench } from '$lib/stores/workbench.svelte';
  import { disconnectGitHub, linkRepo, unlinkRepo, getGitHubLoginUrl } from '$lib/api/client';
  import { toast } from '$lib/stores/toast.svelte';
  import type { RepoInfo } from '$lib/api/client';
  import GitHubStatus from '$lib/components/github/GitHubStatus.svelte';
  import RepoPickerModal from '$lib/components/github/RepoPickerModal.svelte';

  let showRepoPicker = $state(false);

  function handleSelectRepo(fullName: string, branch?: string) {
    const repo = github.repos.find(r => r.full_name === fullName);
    const resolvedBranch = branch ?? repo?.default_branch;
    github.selectRepo(fullName, resolvedBranch);
    // Persist repo link to backend (non-blocking — local state already updated)
    linkRepo(fullName, resolvedBranch).catch(() => {
      // Link failed — local selection still works, just won't persist across refresh
    });
  }

  async function handleDisconnect() {
    try {
      await disconnectGitHub();
      await unlinkRepo().catch(() => {}); // best-effort — local selection already cleared
      toast.success('GitHub disconnected');
    } catch {
      toast.error('Disconnect failed — connection cleared locally');
    } finally {
      github.disconnect(); // always clear local state, even on API failure
    }
  }
</script>

<div class="p-2 space-y-3">
  <GitHubStatus />

  {#if !github.isConnected}
    <div class="space-y-2">
      {#if workbench.githubOAuthEnabled}
        <button
          class="w-full py-1.5 text-xs font-medium transition-all
            bg-bg-card border border-border-subtle text-text-primary
            hover:bg-bg-hover hover:border-neon-cyan/20
            flex items-center justify-center gap-2"
          onclick={() => { window.location.href = getGitHubLoginUrl(); }}
        >
          <svg class="w-3.5 h-3.5 shrink-0 text-text-dim" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
            <path d="M12 0C5.37 0 0 5.37 0 12c0 5.31 3.435 9.795 8.205 11.385.6.105.825-.255.825-.57 0-.285-.015-1.23-.015-2.235-3.015.555-3.795-.735-4.035-1.41-.135-.345-.72-1.41-1.23-1.695-.42-.225-1.02-.78-.015-.795.945-.015 1.62.87 1.845 1.23 1.08 1.815 2.805 1.305 3.495.99.105-.78.42-1.305.765-1.605-2.67-.3-5.46-1.335-5.46-5.925 0-1.305.465-2.385 1.23-3.225-.12-.3-.54-1.53.12-3.18 0 0 1.005-.315 3.3 1.23.96-.27 1.98-.405 3-.405s2.04.135 3 .405c2.295-1.56 3.3-1.23 3.3-1.23.66 1.65.24 2.88.12 3.18.765.84 1.23 1.905 1.23 3.225 0 4.605-2.805 5.625-5.475 5.925.435.375.81 1.095.81 2.22 0 1.605-.015 2.895-.015 3.3 0 .315.225.69.825.57A12.02 12.02 0 0 0 24 12c0-6.63-5.37-12-12-12z"/>
          </svg>
          Connect via GitHub
        </button>
      {:else}
        <!-- Compact setup hint -->
        <div class="border border-border-subtle px-2.5 py-2.5 space-y-2">
          <div class="flex items-center gap-1.5">
            <span class="font-mono text-[7.5px] uppercase tracking-[0.12em] text-neon-red border border-neon-red/30 px-1 py-[2px] leading-none shrink-0">
              SETUP
            </span>
            <span class="font-mono text-[8.5px] text-text-dim truncate">GitHub App required</span>
          </div>
          <div class="font-mono text-[8.5px] text-text-dim leading-relaxed space-y-0.5">
            <div>
              Set <span class="text-neon-green">GITHUB_APP_CLIENT_ID</span>
            </div>
            <div>
              &amp; <span class="text-neon-green">GITHUB_APP_CLIENT_SECRET</span>
            </div>
            <div class="text-text-dim/60 pt-0.5">
              in <span class="text-text-secondary">.env</span>
              → <span class="text-text-secondary">./init.sh restart</span>
            </div>
          </div>
          <a
            href="https://github.com/settings/apps/new"
            target="_blank"
            rel="noopener noreferrer"
            class="font-mono text-[8px] text-neon-cyan/60 hover:text-neon-cyan
              transition-colors duration-150 flex items-center gap-1"
          >
            <svg class="w-2.5 h-2.5 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2">
              <path stroke-linecap="round" stroke-linejoin="round" d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14"/>
            </svg>
            Create GitHub App
          </a>
        </div>
      {/if}
    </div>
  {:else}
    <div class="space-y-2">
      <div class="flex items-center justify-between">
        <span class="font-display text-[11px] font-bold uppercase text-text-dim">Repositories</span>
        <div class="flex items-center gap-2">
          <button
            class="text-[10px] text-neon-cyan hover:text-neon-cyan/80"
            onclick={() => { showRepoPicker = true; }}
          >
            Browse…
          </button>
          <button
            class="text-[10px] text-neon-red hover:text-neon-red/80"
            onclick={handleDisconnect}
          >
            Disconnect
          </button>
        </div>
      </div>

      {#each github.repos as repo}
        <button
          class="w-full text-left px-2 py-1.5 rounded text-xs transition-colors relative
            {github.selectedRepo === repo.full_name
              ? 'bg-neon-cyan/5 border border-neon-cyan/25 text-text-primary pl-3'
              : 'hover:bg-bg-hover text-text-secondary border border-transparent'}"
          onclick={() => handleSelectRepo(repo.full_name)}
        >
          {#if github.selectedRepo === repo.full_name}
            <span class="absolute left-0 top-1.5 bottom-1.5 w-[1px] bg-neon-cyan/50"></span>
          {/if}
          <div class="flex items-center gap-1.5 min-w-0">
            {#if repo.private}
              <svg class="w-3 h-3 text-neon-yellow shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="1.5">
                <path stroke-linecap="round" stroke-linejoin="round" d="M16.5 10.5V6.75a4.5 4.5 0 10-9 0v3.75m-.75 11.25h10.5a2.25 2.25 0 002.25-2.25v-6.75a2.25 2.25 0 00-2.25-2.25H6.75a2.25 2.25 0 00-2.25 2.25v6.75a2.25 2.25 0 002.25 2.25z"></path>
              </svg>
            {:else}
              <svg class="w-3 h-3 text-text-dim shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="1.5">
                <path stroke-linecap="round" stroke-linejoin="round" d="M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z"></path>
              </svg>
            {/if}
            <span class="truncate">{repo.full_name}</span>
            {#if github.selectedRepo === repo.full_name && github.selectedBranch}
              <span class="text-[10px] font-mono text-neon-cyan/70 shrink-0">@ {github.selectedBranch}</span>
            {/if}
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

<RepoPickerModal open={showRepoPicker} onclose={() => { showRepoPicker = false; }} onselectrepo={handleSelectRepo} />
