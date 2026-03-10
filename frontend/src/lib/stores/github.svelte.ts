import { fetchRepoTree, fetchFileContent } from '$lib/api/client';

export interface GitHubRepo {
  full_name: string;
  description: string;
  default_branch: string;
  private: boolean;
  language?: string;
  size_kb?: number;
  stars?: number;
  forks?: number;
  open_issues?: number;
  updated_at?: string;
  pushed_at?: string;
  license_name?: string;
  topics?: string[];
}

export interface GitHubFile {
  path: string;
  sha: string;
  content?: string;
}

export interface TreeNode {
  name: string;
  path: string;
  type: 'blob' | 'tree';
  children?: TreeNode[];
}

export interface SelectedFile {
  name: string;
  path: string;
  content: string;
}

class GitHubStore {
  isConnected = $state(false);
  username = $state('');
  repos = $state<GitHubRepo[]>([]);
  selectedRepo = $state<string | null>(null);
  selectedBranch = $state<string | null>(null);
  files = $state<GitHubFile[]>([]);
  error = $state<string | null>(null);

  // Command palette trigger — set true to open the repo picker modal from outside
  showRepoPicker = $state(false);

  // File tree state
  fileTree = $state<TreeNode[] | null>(null);
  treeLoading = $state(false);
  treeError = $state<string | null>(null);
  selectedFiles = $state<SelectedFile[]>([]);
  fileError = $state<string | null>(null);

  get currentRepo(): GitHubRepo | undefined {
    return this.repos.find(r => r.full_name === this.selectedRepo);
  }

  setConnected(username: string, repos: GitHubRepo[]) {
    this.isConnected = true;
    this.username = username;
    this.repos = repos;
    this.error = null;
  }

  disconnect() {
    this.isConnected = false;
    this.username = '';
    this.repos = [];
    this.selectedRepo = null;
    this.selectedBranch = null;
    this.files = [];
    this.error = null;
    this.fileTree = null;
    this.treeLoading = false;
    this.treeError = null;
    this.selectedFiles = [];
  }

  selectRepo(fullName: string, branch?: string) {
    this.selectedRepo = fullName;
    this.selectedBranch = branch ?? null;
    this.files = [];
    this.fileTree = null;
    this.treeError = null;
    this.selectedFiles = [];
  }

  setFiles(files: GitHubFile[]) {
    this.files = files;
  }

  setError(error: string) {
    this.error = error;
  }

  async loadFileTree(owner: string, repo: string, branch: string): Promise<void> {
    this.treeLoading = true;
    this.treeError = null;
    try {
      const response = await fetchRepoTree(owner, repo, branch);
      // Build nested tree from flat path list
      const roots: TreeNode[] = [];
      const dirMap = new Map<string, TreeNode>();

      for (const entry of response.tree) {
        if (entry.type === 'commit') continue; // skip submodules
        if (entry.type === 'tree') continue;   // skip explicit dir entries; dirs are built from file paths
        const parts = entry.path.split('/');
        const isBlob = entry.type === 'blob' || entry.type == null;

        let current = roots;
        let currentPath = '';

        for (let i = 0; i < parts.length; i++) {
          const part = parts[i];
          currentPath = currentPath ? `${currentPath}/${part}` : part;
          const isLast = i === parts.length - 1;

          if (isLast) {
            const node: TreeNode = {
              name: part,
              path: entry.path,
              type: isBlob ? 'blob' : 'tree',
            };
            current.push(node);
          } else {
            // Intermediate directory segment
            let dir = dirMap.get(currentPath);
            if (!dir) {
              dir = { name: part, path: currentPath, type: 'tree', children: [] };
              dirMap.set(currentPath, dir);
              current.push(dir);
            }
            current = dir.children!;
          }
        }
      }

      // Sort recursively: directories first, then files, both alphabetically
      function sortNodes(nodes: TreeNode[], depth = 0): TreeNode[] {
        if (depth > 50) return nodes;
        nodes.sort((a, b) => {
          if (a.type !== b.type) return a.type === 'tree' ? -1 : 1;
          return a.name.localeCompare(b.name);
        });
        for (const node of nodes) {
          if (node.children) sortNodes(node.children, depth + 1);
        }
        return nodes;
      }

      this.fileTree = sortNodes(roots);
    } catch (err) {
      this.treeError = (err as Error).message;
    } finally {
      this.treeLoading = false;
    }
  }

  async toggleFileSelection(owner: string, repo: string, filePath: string, branch: string): Promise<void> {
    const existingIndex = this.selectedFiles.findIndex(f => f.path === filePath);
    if (existingIndex >= 0) {
      // Deselect
      this.selectedFiles = this.selectedFiles.filter(f => f.path !== filePath);
      return;
    }
    if (this.selectedFiles.length >= 5) {
      // Silently ignore — UI disables the checkbox
      return;
    }
    try {
      this.fileError = null;
      const response = await fetchFileContent(owner, repo, filePath, branch);
      const fileName = filePath.split('/').pop() ?? filePath;
      this.selectedFiles = [
        ...this.selectedFiles,
        { name: fileName, path: filePath, content: response.content }
      ];
    } catch (err) {
      this.fileError = (err as Error).message;
    }
  }

  clearFileSelection() {
    this.selectedFiles = [];
  }

  clearFileTree() {
    this.fileTree = null;
    this.treeLoading = false;
    this.treeError = null;
    this.selectedFiles = [];
  }
}

export const github = new GitHubStore();
