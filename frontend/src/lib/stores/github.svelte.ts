// frontend/src/lib/stores/github.svelte.ts
import {
  githubMe, githubLogout, githubRepos, githubLink, githubLinked, githubUnlink,
  githubDeviceRequest, githubDevicePoll, githubTree, githubBranches,
  githubFileContent, githubReindex, githubIndexStatus,
} from '$lib/api/client';
import type {
  GitHubUser, LinkedRepo, GitHubRepository, RepoTreeEntry, IndexStatus,
} from '$lib/api/client';

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

  // File content viewer state
  selectedFile = $state<string | null>(null);
  fileContent = $state<string | null>(null);
  fileLoading = $state(false);

  async checkAuth() {
    try {
      const user = await githubMe();
      if (user) {
        this.user = user;
        this.authExpired = false;
        await this.loadLinked();
      } else {
        this.user = null;
      }
    } catch {
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
      this.linkedRepo = null;
      this.repos = [];
    } catch (err: unknown) {
      this.error = err instanceof Error ? err.message : 'Operation failed';
    }
  }

  async loadRepos() {
    this.loading = true;
    this.error = null;
    try {
      const response = await githubRepos();
      this.repos = response.repos;
      this.authExpired = false;
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Operation failed';
      // Detect expired/revoked token (backend returns 401)
      if (msg.includes('401') || msg.toLowerCase().includes('expired') || msg.toLowerCase().includes('revoked')) {
        this.authExpired = true;
        this.error = 'GitHub session expired. Please reconnect.';
      } else {
        this.error = msg;
      }
    } finally {
      this.loading = false;
    }
  }

  async linkRepo(fullName: string, projectId?: string) {
    try {
      await githubLink(fullName, projectId);
      // Re-fetch from GET /repos/linked to get full response (includes project_label)
      await this.loadLinked();
      // Start polling index status (background indexing triggered by link)
      this.pollIndexStatus();
    } catch (err: unknown) {
      this.error = err instanceof Error ? err.message : 'Operation failed';
    }
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

  async unlinkRepo() {
    try {
      await githubUnlink();
      this.linkedRepo = null;
      this.fileTree = [];
      this.branches = [];
      this.indexStatus = null;
    } catch (err: unknown) {
      this.error = err instanceof Error ? err.message : 'Operation failed';
    }
  }

  // -- Repo browsing --

  async loadBranches() {
    if (!this.linkedRepo) return;
    const [owner, repo] = this.linkedRepo.full_name.split('/');
    try {
      const data = await githubBranches(owner, repo);
      this.branches = data.branches;
    } catch {
      this.branches = [];
    }
  }

  async loadFileTree() {
    if (!this.linkedRepo) return;
    const [owner, repo] = this.linkedRepo.full_name.split('/');
    const branch = this.linkedRepo.branch ?? this.linkedRepo.default_branch;
    this.treeLoading = true;
    try {
      const data = await githubTree(owner, repo, branch);
      this.fileTree = this._buildTreeNodes(data.tree);
    } catch {
      this.fileTree = [];
    } finally {
      this.treeLoading = false;
    }
  }

  async loadIndexStatus() {
    // tryFetch returns null on error — graceful
    this.indexStatus = await githubIndexStatus();
  }

  async reindex() {
    try {
      await githubReindex();
      this.indexStatus = { status: 'building', file_count: 0, indexed_at: null };
      this.pollIndexStatus();
    } catch (err: unknown) {
      this.error = err instanceof Error ? err.message : 'Reindex failed';
    }
  }

  /** Poll index status until it leaves "building" state or fails. */
  private async pollIndexStatus() {
    let failures = 0;
    for (let i = 0; i < 30; i++) { // max ~60s
      await new Promise(r => setTimeout(r, 2000));
      await this.loadIndexStatus();
      if (!this.indexStatus) {
        failures++;
        if (failures >= 3) break; // stop after 3 consecutive failures
        continue;
      }
      failures = 0;
      if (this.indexStatus.status !== 'building') break;
    }
  }

  async loadFileContent(filePath: string) {
    if (!this.linkedRepo) return;
    const [owner, repo] = this.linkedRepo.full_name.split('/');
    const branch = this.linkedRepo.branch ?? this.linkedRepo.default_branch;
    this.selectedFile = filePath;
    this.fileLoading = true;
    this.fileContent = null;
    try {
      const data = await githubFileContent(owner, repo, filePath, branch);
      this.fileContent = data.content;
    } catch {
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
