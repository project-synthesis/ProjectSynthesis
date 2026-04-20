<script lang="ts">
  /**
   * GitHubPanel — sidebar GitHub tab.
   *
   * Owns Device Flow auth, repo picker, Info/Files sub-tabs, linked-repo
   * instrumentation and the recursive file-tree render. Info/Files
   * selection is persisted through `githubStore.uiTab` (store owns the
   * localStorage bridge — see `stores/github.svelte.ts`).
   *
   * Extracted from Navigator.svelte. Depends on `githubStore` + `projectStore`.
   */
  import { githubStore, type TreeNode } from '$lib/stores/github.svelte';
  import { projectStore } from '$lib/stores/project.svelte';
  import { addToast } from '$lib/stores/toast.svelte';
  import { formatRelativeTime } from '$lib/utils/formatting';
  import { tooltip } from '$lib/actions/tooltip';
  import { GITHUB_TOOLTIPS } from '$lib/utils/ui-tooltips';

  interface Props {
    active: boolean;
  }

  let { active }: Props = $props();

  let repoPickerOpen = $state(false);
  let repoSearch = $state('');
  let selectedProjectId = $state<string | null>(null);
  let linkingRepo = $state<string | null>(null);

  const projects = $derived(projectStore.projects);

  const filteredRepos = $derived(
    githubStore.repos
      .filter((r) => !repoSearch || r.full_name.toLowerCase().includes(repoSearch.toLowerCase()))
      .slice(0, 20),
  );

  // Lazy auth check when panel becomes active
  let githubChecked = false;
  $effect(() => {
    if (active && !githubChecked) {
      githubChecked = true;
      githubStore.checkAuth().catch(() => {});
    }
  });

  async function openRepoPicker(): Promise<void> {
    repoPickerOpen = true;
    repoSearch = '';
    selectedProjectId = null;
    linkingRepo = null;
    githubStore.loadRepos();
    await projectStore.refresh();
  }

  async function confirmLinkRepo(fullName: string): Promise<void> {
    const nonLegacy = projects.filter((p) => p.label !== 'Legacy');
    if (nonLegacy.length > 0 && !linkingRepo) {
      linkingRepo = fullName;
      return;
    }
    await githubStore.linkRepo(fullName, selectedProjectId || undefined);
    repoPickerOpen = false;
    linkingRepo = null;
    selectedProjectId = null;
    if (githubStore.linkedRepo) {
      addToast('created', `Linked ${githubStore.linkedRepo.full_name}`);
    }
  }
</script>

{#snippet treeNode(node: TreeNode, depth: number)}
  {#if node.type === 'dir'}
    <button
      class="tree-item tree-item--dir"
      style="margin-left: {depth * 12}px; padding-left: 6px"
      onclick={() => githubStore.toggleTreeNode(node.path)}
    >
      <span class="tree-arrow">{node.expanded ? '▾' : '▸'}</span>
      <span class="tree-name">{node.name}</span>
    </button>
    {#if node.expanded && node.children}
      {#each node.children as child}
        {@render treeNode(child, depth + 1)}
      {/each}
    {/if}
  {:else}
    <button
      class="tree-item tree-item--file"
      class:tree-item--active={githubStore.selectedFile === node.path}
      style="margin-left: {depth * 12}px; padding-left: 6px"
      onclick={() => githubStore.loadFileContent(node.path)}
    >
      <span class="tree-name">{node.name}</span>
      {#if node.size}
        <span class="tree-size">{node.size > 1024 ? `${(node.size / 1024).toFixed(0)}K` : `${node.size}B`}</span>
      {/if}
    </button>
  {/if}
{/snippet}

<div class="panel">
  <header class="panel-header">
    <span class="section-heading">GitHub</span>
    {#if githubStore.connectionState === 'ready'}
      <span class="connection-badge" style="color: var(--color-text-dim)">ready</span>
    {:else if githubStore.connectionState === 'indexing'}
      <span
        class="connection-badge connection-badge--pulse"
        style="color: var(--color-neon-cyan)"
        use:tooltip={githubStore.phaseLabel || 'Indexing…'}
      >indexing</span>
    {:else if githubStore.connectionState === 'error'}
      <span
        class="connection-badge"
        style="color: var(--color-neon-red)"
        use:tooltip={githubStore.indexErrorText ?? 'Indexing failed'}
      >error</span>
    {:else if githubStore.connectionState === 'linked'}
      <span class="connection-badge" style="color: var(--color-neon-cyan)">linked</span>
    {:else if githubStore.connectionState === 'expired'}
      <span class="connection-badge" style="color: var(--color-neon-red)">expired</span>
    {:else if githubStore.connectionState === 'authenticated'}
      <span class="connection-badge" style="color: var(--color-neon-yellow)">no repo</span>
    {/if}
  </header>
  <div class="panel-body">
    {#if githubStore.linkedRepo}
      <div class="github-tabs" role="tablist">
        <button
          class="github-tab"
          class:github-tab--active={githubStore.uiTab === 'info'}
          onclick={() => { githubStore.setUiTab('info'); }}
          role="tab"
          aria-selected={githubStore.uiTab === 'info'}
        >Info</button>
        <button
          class="github-tab"
          class:github-tab--active={githubStore.uiTab === 'files'}
          onclick={() => { githubStore.setUiTab('files'); if (githubStore.fileTree.length === 0) githubStore.loadFileTree(); githubStore.loadIndexStatus(); }}
          role="tab"
          aria-selected={githubStore.uiTab === 'files'}
        >Files
          {#if githubStore.indexStatus?.status === 'building'}
            <span class="index-badge index-badge--building">...</span>
          {:else if githubStore.indexStatus?.file_count}
            <span class="index-badge" use:tooltip={GITHUB_TOOLTIPS.indexed_file_count}>{githubStore.indexStatus.file_count}</span>
          {/if}
        </button>
      </div>

      {#if githubStore.uiTab === 'info'}
        {#if githubStore.connectionState === 'expired'}
          <div class="auth-expired-banner">
            <span class="error-note" style="margin: 0;">GitHub session expired</span>
            <button
              class="action-btn action-btn--primary"
              onclick={() => githubStore.reconnect()}
            >Reconnect</button>
          </div>
        {/if}
        <div class="card-terminal">
          <div class="data-row" use:tooltip={githubStore.linkedRepo.full_name}>
            <span class="data-label">Repo</span>
            <span class="data-value data-value--truncate font-mono">{githubStore.linkedRepo.full_name.split('/')[1]}</span>
          </div>
          <div class="data-row">
            <span class="data-label">Branch</span>
            <span class="data-value font-mono">
              {githubStore.linkedRepo.branch ?? githubStore.linkedRepo.default_branch}
            </span>
          </div>
          {#if githubStore.linkedRepo.language}
            <div class="data-row">
              <span class="data-label">Lang</span>
              <span class="data-value">{githubStore.linkedRepo.language}</span>
            </div>
          {/if}
          <div class="data-row" use:tooltip={githubStore.linkedRepo.project_label ?? 'Auto-created on repo link'}>
            <span class="data-label">Project</span>
            <span class="data-value data-value--truncate font-mono">{githubStore.linkedRepo.project_label ? (githubStore.linkedRepo.project_label.includes('/') ? githubStore.linkedRepo.project_label.split('/').pop() : githubStore.linkedRepo.project_label) : '(pending)'}</span>
          </div>
          {#if githubStore.indexStatus}
            <div class="data-row" use:tooltip={GITHUB_TOOLTIPS.indexed_file_count}>
              <span class="data-label">Index</span>
              <span class="data-value" class:data-value--cyan={githubStore.indexStatus.status === 'ready'}>
                {githubStore.indexStatus.status} ({githubStore.indexStatus.file_count} files)
              </span>
            </div>
            {#if githubStore.indexStatus.synthesis_status}
              <div class="data-row"
                use:tooltip={githubStore.indexStatus.synthesis_error ?? 'Haiku architectural synthesis'}
              >
                <span class="data-label">Synthesis</span>
                <span class="data-value"
                  class:data-value--cyan={githubStore.indexStatus.synthesis_status === 'ready'}
                  class:data-value--amber={githubStore.indexStatus.synthesis_status === 'running' || githubStore.indexStatus.synthesis_status === 'pending'}
                  class:data-value--red={githubStore.indexStatus.synthesis_status === 'error'}
                >
                  {githubStore.indexStatus.synthesis_status}
                </span>
              </div>
            {/if}
            {#if githubStore.indexStatus.index_phase && githubStore.indexStatus.index_phase !== 'ready' && githubStore.indexStatus.index_phase !== 'pending'}
              <div class="data-row"
                use:tooltip={githubStore.indexErrorText ?? githubStore.phaseLabel}
              >
                <span class="data-label">Phase</span>
                <span class="data-value"
                  class:data-value--amber={githubStore.indexStatus.index_phase !== 'error'}
                  class:data-value--red={githubStore.indexStatus.index_phase === 'error'}
                >
                  {githubStore.phaseLabel}{#if (githubStore.indexStatus.files_total ?? 0) > 0 && githubStore.indexStatus.index_phase === 'embedding'}
                    {' '}({githubStore.indexStatus.files_seen ?? 0}/{githubStore.indexStatus.files_total ?? 0})
                  {/if}
                </span>
              </div>
            {/if}
            {#if githubStore.connectionState === 'error' && githubStore.indexErrorText}
              <div class="data-row data-row--error"
                use:tooltip={githubStore.indexErrorText}
              >
                <span class="data-label">Error</span>
                <span class="data-value data-value--red data-value--truncate">
                  {githubStore.indexErrorText}
                </span>
              </div>
            {/if}
          {/if}
          {#if githubStore.linkedRepo.linked_at}
            <div class="data-row">
              <span class="data-label">Linked</span>
              <span class="data-value">{formatRelativeTime(githubStore.linkedRepo.linked_at)}</span>
            </div>
          {/if}
        </div>
        <div class="github-actions">
          <button class="action-btn" onclick={() => githubStore.unlinkRepo()}>UNLINK</button>
          <button class="action-btn" onclick={() => githubStore.reindex()}>REINDEX</button>
        </div>

      {:else}
        {#if githubStore.selectedFile}
          <div class="file-viewer">
            <div class="file-viewer-header">
              <span class="file-viewer-path font-mono">{githubStore.selectedFile}</span>
              <button class="file-viewer-close" aria-label="Close file viewer" onclick={() => githubStore.closeFile()}>x</button>
            </div>
            {#if githubStore.fileLoading}
              <p class="empty-note">Loading...</p>
            {:else if githubStore.fileContent !== null}
              <pre class="file-viewer-content"><code>{githubStore.fileContent}</code></pre>
            {:else}
              <p class="empty-note">Failed to load file.</p>
            {/if}
          </div>
        {:else}
          {#if githubStore.treeLoading}
            <p class="empty-note">Loading file tree...</p>
          {:else if githubStore.fileTree.length === 0}
            <p class="empty-note">No files found.</p>
          {:else}
            <div class="file-tree">
              {#each githubStore.fileTree as node}
                {@render treeNode(node, 0)}
              {/each}
            </div>
          {/if}
        {/if}
      {/if}
    {:else if githubStore.user}
      <div class="github-user-card">
        {#if githubStore.user.avatar_url}
          <img class="github-avatar" src={githubStore.user.avatar_url} alt={githubStore.user.login} width="32" height="32" />
        {/if}
        <span class="github-username font-mono">{githubStore.user.login}</span>
      </div>

      {#if !repoPickerOpen}
        <button
          class="action-btn action-btn--primary"
          onclick={openRepoPicker}
        >
          Link a repository
        </button>
      {:else}
        <input
          class="search-input"
          type="text"
          placeholder="Search repos..."
          bind:value={repoSearch}
        />

        {#if linkingRepo}
          <div class="repo-picker-project">
            <p class="picker-heading">Link <span class="font-mono">{linkingRepo}</span> to:</p>
            <label class="radio-row">
              <input type="radio" name="project" value="" bind:group={selectedProjectId} checked />
              <span>New project</span>
            </label>
            {#each projects.filter((p) => p.label !== 'Legacy') as proj}
              <label class="radio-row">
                <input type="radio" name="project" value={proj.id} bind:group={selectedProjectId} />
                <span class="font-mono">{proj.label}</span>
                <span class="repo-meta"
                  >({proj.prompt_count ?? proj.member_count} prompts
                  {#if (proj.cluster_count ?? 0) > 0}
                    · {proj.cluster_count} clusters
                  {/if})</span
                >
              </label>
            {/each}
            <div class="picker-actions">
              <button class="action-btn action-btn--primary" onclick={() => confirmLinkRepo(linkingRepo!)}>
                Link
              </button>
              <button class="action-btn" onclick={() => { linkingRepo = null; selectedProjectId = null; }}>
                Back
              </button>
            </div>
          </div>
        {:else if githubStore.loading}
          <p class="empty-note">Loading repositories...</p>
        {:else if filteredRepos.length === 0}
          <p class="empty-note">{repoSearch ? 'No matching repos' : 'No repos found'}</p>
        {:else}
          <div class="repo-list">
            {#each filteredRepos as repo}
              <button
                class="repo-item"
                onclick={() => confirmLinkRepo(repo.full_name)}
              >
                <div class="repo-item-header">
                  <span class="repo-name font-mono">{repo.full_name}</span>
                  <span class="repo-item-badges">
                    {#if repo.private}
                      <span class="repo-badge repo-badge--private">priv</span>
                    {/if}
                    {#if repo.language}
                      <span class="repo-meta">{repo.language}</span>
                    {/if}
                  </span>
                </div>
                {#if repo.description}
                  <span class="repo-desc">{repo.description.length > 60 ? repo.description.slice(0, 60) + '...' : repo.description}</span>
                {/if}
                <span class="repo-item-meta">
                  {#if repo.stargazers_count > 0}
                    <span class="repo-stars font-mono">{repo.stargazers_count}</span>
                  {/if}
                  <span class="repo-updated">{formatRelativeTime(repo.updated_at)}</span>
                </span>
              </button>
            {/each}
          </div>
        {/if}

        <button class="action-btn" onclick={() => { repoPickerOpen = false; linkingRepo = null; }}>
          Cancel
        </button>
      {/if}
    {:else}
      {#if githubStore.userCode}
        <div class="device-flow">
          <p class="device-heading">Your authorization code:</p>
          <div class="device-code">
            <span class="device-code-text">{githubStore.userCode}</span>
          </div>
          <p class="device-instructions">
            Copy this code and enter it on GitHub to authorize access to your repositories.
          </p>
          <button
            class="action-btn action-btn--primary"
            onclick={() => {
              navigator.clipboard.writeText(githubStore.userCode ?? '');
              window.open(
                githubStore.verificationUri ?? 'https://github.com/login/device',
                '_blank',
              );
            }}
          >
            Copy code &amp; open GitHub
          </button>
          {#if githubStore.polling}
            <p class="device-status">Waiting for authorization...</p>
          {/if}
          {#if githubStore.error}
            <p class="error-note">{githubStore.error}</p>
          {/if}
          <button class="action-btn" onclick={() => githubStore.cancelLogin()}>
            Cancel
          </button>
        </div>
      {:else}
        <p class="empty-note">Connect GitHub to link repositories for context-aware optimization.</p>
        {#if githubStore.error}
          <p class="error-note">{githubStore.error}</p>
        {/if}
        <button
          class="action-btn action-btn--primary"
          onclick={() => githubStore.login()}
        >
          Connect GitHub
        </button>
      {/if}
    {/if}
  </div>
</div>

<style>
  .auth-expired-banner {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 6px;
    padding: 4px 6px;
    margin-bottom: 6px;
    border: 1px solid var(--color-neon-red);
    background: color-mix(in srgb, var(--color-neon-red) 4%, transparent);
  }
  .connection-badge {
    font-family: var(--font-mono);
    font-size: 9px;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    margin-left: auto;
  }
  .connection-badge--pulse {
    animation: badge-pulse 1.6s ease-in-out infinite;
  }
  @keyframes badge-pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.55; }
  }
  .data-row--error {
    border-top: 1px solid color-mix(in srgb, var(--color-neon-red) 25%, transparent);
    margin-top: 4px;
    padding-top: 4px;
  }

  .search-input {
    width: 100%;
    height: 18px;
    padding: 0 4px;
    margin-bottom: 6px;
    background: var(--color-bg-input);
    border: 1px solid var(--color-border-subtle);
    color: var(--color-text-primary);
    font-size: 10px;
    font-family: var(--font-sans);
    outline: none;
    transition: border-color var(--duration-hover) var(--ease-spring);
  }
  .search-input:focus {
    border-color: color-mix(in srgb, var(--tier-accent, var(--color-neon-cyan)) 30%, transparent);
  }
  .search-input::placeholder {
    color: var(--color-text-dim);
  }
  .repo-list {
    display: flex;
    flex-direction: column;
    gap: 1px;
    max-height: 240px;
    overflow-y: auto;
    margin-bottom: 6px;
  }
  .repo-item {
    display: flex;
    flex-direction: column;
    gap: 1px;
    padding: 4px 6px;
    background: transparent;
    border: none;
    border-left: 1px solid transparent;
    color: var(--color-text-primary);
    cursor: pointer;
    text-align: left;
    font-size: 10px;
    transition: color var(--duration-hover) var(--ease-spring),
                background var(--duration-hover) var(--ease-spring),
                border-color var(--duration-hover) var(--ease-spring);
  }
  .repo-item:hover {
    background: var(--color-bg-hover);
    border-left-color: var(--tier-accent, var(--color-neon-cyan));
  }
  .repo-item-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 4px;
  }
  .repo-item-badges {
    display: flex;
    gap: 4px;
    align-items: center;
    flex-shrink: 0;
  }
  .repo-badge {
    font-size: 8px;
    font-family: var(--font-mono);
    text-transform: uppercase;
    letter-spacing: 0.04em;
    padding: 0 3px;
    border: 1px solid var(--color-border-subtle);
    color: var(--color-text-dim);
  }
  .repo-badge--private {
    color: var(--color-neon-yellow);
    border-color: color-mix(in srgb, var(--color-neon-yellow) 30%, transparent);
  }
  .repo-desc {
    font-size: 9px;
    font-family: var(--font-sans);
    color: var(--color-text-dim);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .repo-item-meta {
    display: flex;
    gap: 6px;
    font-size: 9px;
    color: var(--color-text-dim);
  }
  .repo-stars::before {
    content: '\2605 ';
  }
  .repo-updated {
    font-family: var(--font-mono);
  }
  .repo-name {
    flex: 1;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .repo-meta {
    font-size: 9px;
    color: var(--color-text-dim);
    margin-left: 6px;
    flex-shrink: 0;
  }
  .github-user-card {
    display: flex;
    align-items: center;
    gap: 6px;
    padding: 6px;
  }
  .github-avatar {
    border: 1px solid var(--color-border-subtle);
    flex-shrink: 0;
  }
  .github-username {
    font-size: 10px;
    color: var(--color-text-primary);
  }
  .repo-picker-project {
    margin-bottom: 6px;
  }
  .picker-heading {
    font-size: 10px;
    color: var(--color-text-primary);
    margin-bottom: 4px;
  }
  .radio-row {
    display: flex;
    align-items: center;
    gap: 6px;
    padding: 3px 0;
    font-size: 10px;
    color: var(--color-text-primary);
    cursor: pointer;
    transition: background var(--duration-hover) var(--ease-spring);
  }
  .radio-row:hover {
    background: var(--color-bg-hover);
  }
  .radio-row input[type="radio"] {
    accent-color: var(--tier-accent, var(--color-neon-cyan));
  }
  .picker-actions {
    display: flex;
    gap: 6px;
    margin-top: 6px;
  }
  .github-actions {
    display: flex;
    gap: 6px;
    padding: 6px;
  }
  .github-actions .action-btn {
    flex: 1;
  }
  .data-value--truncate {
    text-align: right;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    max-width: 140px;
  }

  .device-flow {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 6px;
    padding: 6px 0;
  }
  .device-heading {
    font-size: 10px;
    color: var(--color-text-dim);
    margin: 0;
  }
  .device-code {
    display: flex;
    align-items: center;
    justify-content: center;
  }
  .device-code-text {
    font-family: var(--font-mono);
    font-size: 14px;
    font-weight: 700;
    letter-spacing: 3px;
    color: var(--tier-accent, var(--color-neon-cyan));
    padding: 4px 6px;
    border: 1px solid var(--color-border-subtle);
  }
  .device-instructions {
    font-size: 10px;
    color: var(--color-text-dim);
    text-align: center;
    margin: 0;
    line-height: 1.4;
  }
  .device-status {
    font-size: 10px;
    color: var(--color-text-dim);
    margin: 0;
  }
  .error-note {
    font-size: 10px;
    color: var(--color-neon-red);
    margin: 0 0 4px;
  }

  .github-tabs {
    display: flex;
    align-items: stretch;
    height: 24px;
    border-bottom: 1px solid var(--color-border-subtle);
    flex-shrink: 0;
  }
  .github-tab {
    flex: 1 1 0%;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    height: 100%;
    padding: 0;
    border: none;
    border-bottom: 1px solid transparent;
    background: transparent;
    color: var(--color-text-dim);
    font-size: 10px;
    font-weight: 700;
    font-family: var(--font-mono);
    cursor: pointer;
    text-transform: uppercase;
    letter-spacing: 0.03em;
    gap: 4px;
    transition: color var(--duration-hover) var(--ease-spring),
                border-color var(--duration-hover) var(--ease-spring),
                background var(--duration-hover) var(--ease-spring);
  }
  .github-tab:hover {
    color: var(--color-text-primary);
    background: color-mix(in srgb, var(--color-bg-hover) 50%, transparent);
  }
  .github-tab--active {
    color: var(--tier-accent, var(--color-neon-cyan));
    border-bottom-color: var(--tier-accent, var(--color-neon-cyan));
  }
  .github-tab:focus-visible {
    outline-offset: -1px;
  }
  .index-badge {
    font-size: 9px;
    padding: 0 3px;
    border: 1px solid var(--color-border-subtle);
    color: var(--color-text-dim);
  }
  .index-badge--building {
    color: var(--color-neon-yellow);
    border-color: var(--color-neon-yellow);
  }
  .data-value--cyan { color: var(--tier-accent, var(--color-neon-cyan)); }
  .data-value--amber { color: var(--color-neon-yellow); }
  .data-value--red { color: var(--color-neon-red); }

  .file-tree {
    flex: 1;
    overflow-y: auto;
    min-height: 0;
  }
  .tree-item {
    display: flex;
    align-items: center;
    gap: 4px;
    height: 20px;
    padding: 0 6px;
    font-size: 10px;
    font-family: var(--font-mono);
    color: var(--color-text-primary);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    width: 100%;
    text-align: left;
    transition: color var(--duration-hover) var(--ease-spring), background var(--duration-hover) var(--ease-spring);
  }
  .tree-item--dir {
    background: transparent;
    border: none;
    cursor: pointer;
    color: var(--tier-accent, var(--color-neon-cyan));
  }
  .tree-item--dir:hover { background: var(--color-bg-hover); }
  .tree-item--file {
    color: var(--color-text-dim);
    background: transparent;
    border: none;
    cursor: pointer;
  }
  .tree-item--file:hover { background: var(--color-bg-hover); }
  .tree-item--active { background: var(--color-bg-hover); color: var(--color-text-primary); }
  .tree-arrow { font-size: 8px; width: 8px; flex-shrink: 0; }
  .tree-name { overflow: hidden; text-overflow: ellipsis; }
  .tree-size {
    font-size: 9px;
    color: var(--color-text-dim);
    margin-left: auto;
    flex-shrink: 0;
  }

  .file-viewer { display: flex; flex-direction: column; height: 100%; }
  .file-viewer-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 4px 6px;
    border-bottom: 1px solid var(--color-border-subtle);
  }
  .file-viewer-path {
    font-size: 10px;
    color: var(--tier-accent, var(--color-neon-cyan));
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .file-viewer-close {
    background: transparent;
    border: none;
    color: var(--color-text-dim);
    cursor: pointer;
    font-size: 10px;
    padding: 0 4px;
    transition: color var(--duration-hover) var(--ease-spring), background var(--duration-hover) var(--ease-spring);
  }
  .file-viewer-close:hover { color: var(--color-text-primary); background: var(--color-bg-hover); }
  .file-viewer-content {
    overflow: auto;
    flex: 1;
    min-height: 0;
    font-size: 10px;
    line-height: 1.5;
    padding: 6px;
    margin: 0;
    color: var(--color-text-dim);
    white-space: pre;
    tab-size: 2;
  }
</style>
