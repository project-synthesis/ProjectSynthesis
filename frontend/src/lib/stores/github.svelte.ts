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

class GitHubStore {
  isConnected = $state(false);
  username = $state('');
  repos = $state<GitHubRepo[]>([]);
  selectedRepo = $state<string | null>(null);
  selectedBranch = $state<string | null>(null);
  files = $state<GitHubFile[]>([]);
  error = $state<string | null>(null);

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
  }

  selectRepo(fullName: string, branch?: string) {
    this.selectedRepo = fullName;
    this.selectedBranch = branch ?? null;
    this.files = [];
  }

  setFiles(files: GitHubFile[]) {
    this.files = files;
  }

  setError(error: string) {
    this.error = error;
  }
}

export const github = new GitHubStore();
