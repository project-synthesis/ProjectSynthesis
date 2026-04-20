// frontend/src/lib/stores/github.svelte.ts
import {
  githubMe, githubLogout, githubRepos, githubLink, githubLinked, githubUnlink,
  githubDeviceRequest, githubDevicePoll, githubTree, githubBranches,
  githubFileContent, githubReindex, githubIndexStatus, migrateProjects,
} from '$lib/api/client';
import type {
  GitHubUser, LinkedRepo, GitHubRepository, RepoTreeEntry, IndexStatus,
} from '$lib/api/client';
import { clustersStore } from '$lib/stores/clusters.svelte';
import { projectStore } from '$lib/stores/project.svelte';
import { toastStore } from '$lib/stores/toast.svelte';

export interface TreeNode {
  name: string;
  path: string;
  type: 'file' | 'dir';
  size?: number;
  children?: TreeNode[];
  expanded?: boolean;
}

class GitHubStore {
  user = $state<GitHubUser | null>(null);
  linkedRepo = $state<LinkedRepo | null>(null);
  repos = $state<GitHubRepository[]>([]);
  loading = $state(false);
  error = $state<string | null>(null);
  authExpired = $state(false);

  // Device flow state
  userCode = $state<string | null>(null);
  verificationUri = $state<string | null>(null);
  polling = $state(false);
  private deviceCode: string | null = null;
  private pollInterval = 5;
  private deviceExpiry = 0;

  // Repo browsing state
  branches = $state<string[]>([]);
  fileTree = $state<TreeNode[]>([]);
  treeLoading = $state(false);
  indexStatus = $state<IndexStatus | null>(null);

  /** Unified connection state — single source of truth for all UI components.
   *
   *  State machine (post C1/C2/C3 — indexing phase is authoritative):
   *    disconnected   → no GitHub user
   *    expired        → session 401
   *    authenticated  → user present, no repo linked
   *    linked         → repo linked, no index_phase yet (fresh link)
   *    indexing       → index_phase in {fetching_tree, embedding, synthesizing}
   *                     OR status/synthesis_status still in progress
   *    error          → index_phase="error" or file/synthesis status="error"
   *    ready          → index_phase="ready" AND status="ready" AND
   *                     synthesis_status in {"ready","skipped",null}
   *
   *  "ready" is now gated on BOTH file indexing AND synthesis completing —
   *  not just file status. This closes the window where the UI claimed ready
   *  while Haiku synthesis was still running (or had silently errored).
   */
  get connectionState(): 'disconnected' | 'expired' | 'authenticated' | 'linked' | 'indexing' | 'error' | 'ready' {
    if (this.authExpired) return 'expired';
    if (!this.user) return 'disconnected';
    if (!this.linkedRepo) return 'authenticated';
    if (!this.indexStatus) return 'linked';

    const s = this.indexStatus;
    const fileStatus = s.status;
    const synthStatus = s.synthesis_status ?? null;
    const phase = s.index_phase ?? null;

    if (phase === 'error' || fileStatus === 'error' || synthStatus === 'error') {
      return 'error';
    }

    const fileInFlight = ['pending', 'building', 'indexing'].includes(fileStatus);
    const synthInFlight = !!synthStatus && ['pending', 'running'].includes(synthStatus);
    const phaseInFlight = !!phase && ['fetching_tree', 'embedding', 'synthesizing'].includes(phase);

    if (fileInFlight || synthInFlight || phaseInFlight) return 'indexing';

    // Truly done: file index ready AND (synthesis ready OR skipped OR absent).
    if (fileStatus === 'ready' && (synthStatus === null || ['ready', 'skipped'].includes(synthStatus))) {
      // Phase must also be ready — belt-and-braces against missed SSE
      // events leaving phase stale at "embedding" after synthesis finished.
      if (phase === 'ready' || phase === null) return 'ready';
    }

    return 'indexing';
  }

  /** Human-readable phase label (for UI display). Falls back on status. */
  get phaseLabel(): string {
    if (!this.indexStatus) return '';
    const phase = this.indexStatus.index_phase ?? null;
    switch (phase) {
      case 'fetching_tree': return 'Fetching repo tree…';
      case 'embedding': return 'Embedding files…';
      case 'synthesizing': return 'Synthesizing context…';
      case 'ready': return 'Ready';
      case 'error': return 'Error';
      case 'pending':
      case null:
      default:
        return this.indexStatus.status === 'ready' ? 'Ready' : 'Preparing…';
    }
  }

  /** Surface error from any layer (file index or synthesis). */
  get indexErrorText(): string | null {
    if (!this.indexStatus) return null;
    return (
      this.indexStatus.error_message
      || this.indexStatus.synthesis_error
      || null
    );
  }

  // File content viewer state
  selectedFile = $state<string | null>(null);
  fileContent = $state<string | null>(null);
  fileLoading = $state(false);

  /**
   * Check if an error is a 401 auth failure and flag the session as expired.
   * Clears user state so the UI immediately shows "session expired" with a
   * reconnect button — even on tabs that had stale data from a previous
   * successful load.  Returns true if the error was a 401 (caller should
   * abort further work).
   */
  private _handleAuthError(err: unknown): boolean {
    const is401 = (
      (err && typeof err === 'object' && 'status' in err && (err as { status: number }).status === 401)
      || (() => {
        const msg = err instanceof Error ? err.message : String(err);
        return msg.includes('401') || msg.toLowerCase().includes('not authenticated') || msg.toLowerCase().includes('expired or revoked');
      })()
    );
    if (!is401) return false;
    this.authExpired = true;
    this.user = null;
    this.error = 'GitHub session expired. Please reconnect.';
    return true;
  }

  async checkAuth() {
    try {
      const user = await githubMe();
      if (user) {
        this.user = user;
        this.authExpired = false;
        await this.loadLinked();
      } else {
        this.user = null;
        this.linkedRepo = null;
        this.authExpired = false;
      }
    } catch {
      // Network error — githubMe uses tryFetch so 401s return null, not throw.
      // Only DNS/CORS failures reach here. Clear user, leave authExpired unchanged.
      this.user = null;
    }
  }

  async login() {
    this.error = null;
    try {
      const data = await githubDeviceRequest();
      this.deviceCode = data.device_code;
      this.userCode = data.user_code;
      this.verificationUri = data.verification_uri;
      this.pollInterval = data.interval || 5;
      this.deviceExpiry = Date.now() + (data.expires_in || 900) * 1000;
      // State is set — UI shows the code and gate modal.
      // User clicks "Continue to GitHub" to proceed.
      // Polling starts immediately (GitHub page may take time).
      this.startPolling();
    } catch (err: unknown) {
      this.error = err instanceof Error ? err.message : 'Failed to start GitHub auth';
    }
  }

  cancelLogin() {
    this.polling = false;
    this.userCode = null;
    this.verificationUri = null;
    this.deviceCode = null;
    this.error = null;
  }

  private async startPolling() {
    this.polling = true;
    while (this.polling && Date.now() < this.deviceExpiry) {
      await new Promise(r => setTimeout(r, this.pollInterval * 1000));
      if (!this.polling) break; // cancelled during wait
      try {
        const result = await githubDevicePoll(this.deviceCode!);
        if (result.status === 'success') {
          this.polling = false;
          this.userCode = null;
          this.verificationUri = null;
          this.deviceCode = null;
          await this.checkAuth();
          return;
        }
        if (result.status === 'slow_down') {
          this.pollInterval += 5;
        }
        if (result.status === 'expired_token') {
          this.polling = false;
          this.userCode = null;
          this.verificationUri = null;
          this.deviceCode = null;
          this.error = 'Authorization expired. Please try again.';
          return;
        }
        // authorization_pending — keep polling
      } catch {
        // Network error, keep polling
      }
    }
    // Timed out
    if (this.polling) {
      this.polling = false;
      this.userCode = null;
      this.verificationUri = null;
      this.deviceCode = null;
      this.error = 'Authorization timed out. Please try again.';
    }
  }

  async logout() {
    try {
      await githubLogout();
      this.user = null;
      this.authExpired = false;
      this.linkedRepo = null;
      this.repos = [];
    } catch (err: unknown) {
      this.error = err instanceof Error ? err.message : 'Operation failed';
    }
  }

  /** Clear stale auth state and start device flow for re-authentication.
   *  Clears linkedRepo so the Navigator template falls to the device flow branch. */
  async reconnect() {
    this.authExpired = false;
    this.linkedRepo = null;
    this.fileTree = [];
    this.branches = [];
    this.indexStatus = null;
    this.error = null;
    await this.login();
  }

  async loadRepos() {
    this.loading = true;
    this.error = null;
    try {
      const response = await githubRepos();
      this.repos = response.repos;
      this.authExpired = false;
    } catch (err: unknown) {
      if (this._handleAuthError(err)) {
        this.error = 'GitHub session expired. Please reconnect.';
      } else {
        this.error = err instanceof Error ? err.message : 'Operation failed';
      }
    } finally {
      this.loading = false;
    }
  }

  async linkRepo(fullName: string, projectId?: string) {
    try {
      const response = await githubLink(fullName, projectId);
      // ADR-005 F1/F5: capture the newly-linked project + migration
      // candidates so the UI can auto-switch scope and offer the sweep
      // toast.  Must happen before loadLinked() so the project list
      // refresh below reflects the new project node immediately.
      projectStore.applyLinkResponse(
        response.project_id ?? null,
        response.migration_candidates ?? null,
      );
      await projectStore.refresh();
      // Re-fetch from GET /repos/linked to get full response (includes project_label)
      await this.loadLinked();
      // ADR-005 F5 — migration toast: offer to sweep recent Legacy prompts
      // into the newly-linked project.  User decides; never automatic.
      this._offerMigrationToast(response.project_id);
      // Start polling index status (background indexing triggered by link)
      this.pollIndexStatus();
    } catch (err: unknown) {
      this.error = err instanceof Error ? err.message : 'Operation failed';
    }
  }

  /** ADR-005 F5 — Move/Keep toast rendered post-link when candidates exist. */
  private _offerMigrationToast(targetProjectId: string | null): void {
    const candidates = projectStore.lastMigrationCandidates;
    if (!candidates || candidates.count <= 0 || !candidates.from_project_id || !targetProjectId) {
      // Nothing to migrate (fresh install, repo re-link, or backend gate miss).
      projectStore.clearMigrationCandidates();
      return;
    }
    const target = projectStore.projects.find((p) => p.id === targetProjectId);
    const targetLabel = target?.label ?? 'this project';
    const count = candidates.count;
    const fromId = candidates.from_project_id;
    const sinceIso = candidates.since;

    toastStore.addWithActions(
      'info',
      `Now working in ${targetLabel}. Move ${count} recent Legacy prompt${count === 1 ? '' : 's'} to this project?`,
      [
        {
          label: 'Move',
          variant: 'primary',
          onClick: async () => {
            try {
              const res = await migrateProjects({
                from_project_id: fromId,
                to_project_id: targetProjectId,
                since: sinceIso,
                repo_full_name_is_null: true,
              });
              toastStore.add('modified', `Moved ${res.migrated} prompt${res.migrated === 1 ? '' : 's'} to ${targetLabel}`);
            } catch (err: unknown) {
              toastStore.add('deleted', err instanceof Error ? err.message : 'Migration failed');
            } finally {
              projectStore.clearMigrationCandidates();
              await projectStore.refresh();
            }
          },
        },
        {
          label: 'Keep in Legacy',
          onClick: () => {
            projectStore.clearMigrationCandidates();
          },
        },
      ],
    );
  }

  async loadLinked() {
    // tryFetch returns null on 404 (no linked repo) — expected
    this.linkedRepo = await githubLinked();
    // Fetch index status whenever a linked repo exists so the Info tab
    // shows "ready (N files)" immediately — not only after visiting Files.
    if (this.linkedRepo) {
      this.loadIndexStatus();
    }
  }

  async unlinkRepo(mode: 'keep' | 'rehome' = 'keep') {
    try {
      // Capture labels before mutating state so the toast text can reference
      // the repo that was just unlinked.
      const prevRepoName = this.linkedRepo?.full_name ?? null;
      const prevProjectId = this.linkedRepo?.project_node_id ?? null;
      const response = await githubUnlink(mode);
      this.linkedRepo = null;
      this.fileTree = [];
      this.branches = [];
      this.indexStatus = null;
      // Refresh project list (member counts shift when rehome moves opts).
      await projectStore.refresh();
      // Clean cluster state: project-tagged clusters may be stale after unlink
      clustersStore.selectCluster(null);
      clustersStore.invalidateClusters();
      // ADR-005 F5 — Stay/Switch toast: user picks whether to stay scoped to
      // the disconnected project or hop back to Legacy.  Skipped for rehome
      // (explicit "send me back to Legacy" intent already made the decision).
      if (mode === 'keep' && prevRepoName && prevProjectId) {
        this._offerStaySwitchToast(prevRepoName, prevProjectId);
      }
      if (response.rehomed_count > 0) {
        toastStore.add(
          'modified',
          `Rehomed ${response.rehomed_count} prompt${response.rehomed_count === 1 ? '' : 's'} to Legacy`,
        );
      }
    } catch (err: unknown) {
      this.error = err instanceof Error ? err.message : 'Operation failed';
    }
  }

  /** ADR-005 F5 — Stay/Switch toast rendered post-unlink. */
  private _offerStaySwitchToast(repoName: string, projectId: string): void {
    const project = projectStore.projects.find((p) => p.id === projectId);
    const projectLabel = project?.label ?? 'the project';
    const legacy = projectStore.projects.find(
      (p) => p.label.trim().toLowerCase() === 'legacy',
    );
    // No Legacy project yet? Fall back to "All projects" — safe neutral.
    const legacyId = legacy?.id ?? null;

    toastStore.addWithActions(
      'info',
      `Disconnected from ${repoName}. Continue viewing ${projectLabel} or switch to Legacy?`,
      [
        {
          label: 'Stay',
          onClick: () => {
            // No-op: current scope already points at the disconnected project.
          },
        },
        {
          label: 'Switch',
          variant: 'primary',
          onClick: () => {
            projectStore.setCurrent(legacyId);
          },
        },
      ],
    );
  }

  // -- Repo browsing --

  async loadBranches() {
    if (!this.linkedRepo || this.authExpired) return;
    const [owner, repo] = this.linkedRepo.full_name.split('/');
    try {
      const data = await githubBranches(owner, repo);
      this.branches = data.branches;
    } catch (err) {
      if (this._handleAuthError(err)) return;
      this.branches = [];
    }
  }

  async loadFileTree() {
    if (!this.linkedRepo || this.authExpired) return;
    const [owner, repo] = this.linkedRepo.full_name.split('/');
    const branch = this.linkedRepo.branch ?? this.linkedRepo.default_branch;
    this.treeLoading = true;
    try {
      const data = await githubTree(owner, repo, branch);
      this.fileTree = this._buildTreeNodes(data.tree);
    } catch (err) {
      if (this._handleAuthError(err)) {
        this.fileTree = [];
      } else {
        this.fileTree = [];
      }
    } finally {
      this.treeLoading = false;
    }
  }

  async loadIndexStatus() {
    // tryFetch returns null on error — graceful
    this.indexStatus = await githubIndexStatus();
  }

  async reindex() {
    if (this.authExpired) return;
    try {
      await githubReindex();
      this.indexStatus = { status: 'building', file_count: 0, indexed_at: null, synthesis_status: 'pending' };
      this.pollIndexStatus();
    } catch (err: unknown) {
      if (this._handleAuthError(err)) return;
      this.error = err instanceof Error ? err.message : 'Reindex failed';
    }
  }

  /** Poll index status until file indexing + synthesis settle, error, or timeout.
   *
   *  Polling remains as a safety net; primary signal is `index_phase_changed`
   *  SSE (subscribed in `+page.svelte`). The two converge on the same state.
   */
  private async pollIndexStatus() {
    let failures = 0;
    for (let i = 0; i < 60; i++) { // max ~120s
      await new Promise(r => setTimeout(r, 2000));
      await this.loadIndexStatus();
      if (!this.indexStatus) {
        failures++;
        if (failures >= 3) break;
        continue;
      }
      failures = 0;
      const state = this.connectionState;
      if (state === 'ready' || state === 'error') break;
    }
  }

  /** Apply a live `index_phase_changed` SSE event to this.indexStatus.
   *
   *  Reactive: updating `.indexStatus` triggers all `$derived` consumers of
   *  `connectionState`, `phaseLabel`, `indexErrorText` to re-render.
   */
  applyPhaseEvent(payload: {
    repo_full_name: string;
    branch: string;
    phase: string;
    status: string;
    files_seen: number;
    files_total: number;
    error?: string;
  }): void {
    // Ignore events for other repos — multi-link safety.
    if (this.linkedRepo && payload.repo_full_name !== this.linkedRepo.full_name) return;

    const prev = this.indexStatus ?? {
      status: payload.status,
      file_count: 0,
      indexed_at: null,
    };
    this.indexStatus = {
      ...prev,
      status: payload.status,
      index_phase: payload.phase,
      files_seen: payload.files_seen,
      files_total: payload.files_total,
      // Error surfaces from whichever layer reported it.
      error_message: payload.phase === 'error' ? payload.error ?? null : prev.error_message ?? null,
      synthesis_error: payload.phase === 'error' && payload.error
        ? payload.error
        : prev.synthesis_error ?? null,
      synthesis_status: (
        payload.phase === 'synthesizing' ? 'running'
        : payload.phase === 'ready' ? 'ready'
        : prev.synthesis_status ?? null
      ),
    };
  }

  async loadFileContent(filePath: string) {
    if (!this.linkedRepo || this.authExpired) return;
    const [owner, repo] = this.linkedRepo.full_name.split('/');
    const branch = this.linkedRepo.branch ?? this.linkedRepo.default_branch;
    this.selectedFile = filePath;
    this.fileLoading = true;
    this.fileContent = null;
    try {
      const data = await githubFileContent(owner, repo, filePath, branch);
      this.fileContent = data.content;
    } catch (err) {
      if (this._handleAuthError(err)) return;
      this.fileContent = null;
      this.error = `Failed to load ${filePath}`;
    } finally {
      this.fileLoading = false;
    }
  }

  closeFile() {
    this.selectedFile = null;
    this.fileContent = null;
  }

  toggleTreeNode(path: string) {
    const toggle = (nodes: TreeNode[]): TreeNode[] =>
      nodes.map(n => {
        if (n.path === path && n.type === 'dir') {
          return { ...n, expanded: !n.expanded };
        }
        if (n.children) {
          return { ...n, children: toggle(n.children) };
        }
        return n;
      });
    this.fileTree = toggle(this.fileTree);
  }

  private _buildTreeNodes(flat: RepoTreeEntry[]): TreeNode[] {
    const root: TreeNode[] = [];
    const dirMap = new Map<string, TreeNode>();

    // Sort: directories first (by depth), then alphabetically
    const sorted = [...flat].sort((a, b) => a.path.localeCompare(b.path));

    for (const entry of sorted) {
      const parts = entry.path.split('/');
      const name = parts[parts.length - 1];
      const parentPath = parts.slice(0, -1).join('/');

      const node: TreeNode = { name, path: entry.path, type: 'file', size: entry.size };

      // Ensure parent directories exist
      let currentPath = '';
      for (let i = 0; i < parts.length - 1; i++) {
        const part = parts[i];
        currentPath = currentPath ? `${currentPath}/${part}` : part;
        if (!dirMap.has(currentPath)) {
          const dirNode: TreeNode = {
            name: part, path: currentPath, type: 'dir',
            children: [], expanded: false,
          };
          dirMap.set(currentPath, dirNode);
          // Attach to parent
          const dirParent = parts.slice(0, i).join('/');
          if (dirParent && dirMap.has(dirParent)) {
            dirMap.get(dirParent)!.children!.push(dirNode);
          } else if (!dirParent) {
            root.push(dirNode);
          }
        }
      }

      // Attach file to parent
      if (parentPath && dirMap.has(parentPath)) {
        dirMap.get(parentPath)!.children!.push(node);
      } else if (!parentPath) {
        root.push(node);
      }
    }

    // Sort: dirs first, then files, alphabetically within each group
    const sortNodes = (nodes: TreeNode[]): TreeNode[] => {
      nodes.sort((a, b) => {
        if (a.type !== b.type) return a.type === 'dir' ? -1 : 1;
        return a.name.localeCompare(b.name);
      });
      for (const n of nodes) {
        if (n.children) sortNodes(n.children);
      }
      return nodes;
    };
    return sortNodes(root);
  }

  /** @internal Test-only: restore initial state */
  _reset() {
    this.user = null;
    this.linkedRepo = null;
    this.repos = [];
    this.loading = false;
    this.error = null;
    this.authExpired = false;
    this.userCode = null;
    this.verificationUri = null;
    this.polling = false;
    this.deviceCode = null;
    this.branches = [];
    this.fileTree = [];
    this.treeLoading = false;
    this.indexStatus = null;
    this.selectedFile = null;
    this.fileContent = null;
    this.fileLoading = false;
  }
}

export const githubStore = new GitHubStore();
