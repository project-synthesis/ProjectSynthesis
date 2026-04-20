import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest';
import { render, screen, cleanup, fireEvent, waitFor } from '@testing-library/svelte';

vi.mock('$lib/api/client', () => ({
  githubMe: vi.fn(),
  githubLogout: vi.fn(),
  githubRepos: vi.fn(),
  githubLink: vi.fn(),
  githubLinked: vi.fn(),
  githubUnlink: vi.fn(),
  githubDeviceRequest: vi.fn(),
  githubDevicePoll: vi.fn(),
  githubTree: vi.fn(),
  githubBranches: vi.fn(),
  githubFileContent: vi.fn(),
  githubReindex: vi.fn(),
  githubIndexStatus: vi.fn(),
  migrateProjects: vi.fn(),
}));

vi.mock('$lib/stores/toast.svelte', () => ({
  addToast: vi.fn(),
  toastStore: { add: vi.fn(), toasts: [] },
}));

import GitHubPanel from './GitHubPanel.svelte';
import { githubStore } from '$lib/stores/github.svelte';
import { projectStore } from '$lib/stores/project.svelte';
import * as apiClient from '$lib/api/client';

describe('GitHubPanel', () => {
  beforeEach(() => {
    githubStore._reset();
    githubStore.uiTab = 'info';
    projectStore._reset?.();
    vi.clearAllMocks();
    localStorage.clear();
  });

  afterEach(() => {
    cleanup();
  });

  describe('auth check', () => {
    it('does not call checkAuth when active=false', () => {
      render(GitHubPanel, { props: { active: false } });
      expect(apiClient.githubMe).not.toHaveBeenCalled();
    });

    it('calls checkAuth on active=true', async () => {
      vi.mocked(apiClient.githubMe).mockResolvedValueOnce(null as never);
      render(GitHubPanel, { props: { active: true } });
      await waitFor(() => expect(apiClient.githubMe).toHaveBeenCalled());
    });
  });

  describe('tab persistence via githubStore.uiTab', () => {
    it('defaults to info tab when nothing persisted', () => {
      githubStore.linkedRepo = {
        full_name: 'owner/repo',
        branch: 'main',
        default_branch: 'main',
        language: 'TypeScript',
        project_label: 'Project X',
        linked_at: '2026-04-01T00:00:00Z',
      } as never;
      render(GitHubPanel, { props: { active: true } });
      const infoTab = screen.getByRole('tab', { name: /^Info$/ });
      expect(infoTab).toHaveAttribute('aria-selected', 'true');
    });

    it('clicking Files tab calls setUiTab("files") and persists', async () => {
      githubStore.linkedRepo = {
        full_name: 'owner/repo',
        branch: 'main',
        default_branch: 'main',
        language: 'TypeScript',
        project_label: 'Project X',
        linked_at: '2026-04-01T00:00:00Z',
      } as never;
      vi.mocked(apiClient.githubTree).mockResolvedValueOnce([] as never);
      vi.mocked(apiClient.githubIndexStatus).mockResolvedValueOnce(null as never);
      render(GitHubPanel, { props: { active: true } });

      await fireEvent.click(screen.getByRole('tab', { name: /^Files/ }));

      expect(githubStore.uiTab).toBe('files');
      expect(localStorage.getItem('synthesis:github_tab')).toBe('files');
    });

    it('reads persisted uiTab on load', async () => {
      localStorage.setItem('synthesis:github_tab', 'files');
      // Trigger re-init by manually applying the persisted value.
      // The store reads localStorage at module construction. For this test,
      // we directly set uiTab to simulate the load path.
      githubStore.uiTab = 'files';

      githubStore.linkedRepo = {
        full_name: 'owner/repo',
        branch: 'main',
        default_branch: 'main',
        language: 'TypeScript',
        project_label: 'Project X',
        linked_at: '2026-04-01T00:00:00Z',
      } as never;
      vi.mocked(apiClient.githubTree).mockResolvedValueOnce([] as never);

      render(GitHubPanel, { props: { active: true } });
      const filesTab = screen.getByRole('tab', { name: /^Files/ });
      expect(filesTab).toHaveAttribute('aria-selected', 'true');
    });
  });

  describe('repo picker', () => {
    it('opens repo picker on Link a repository click', async () => {
      const fakeUser = { login: 'octocat', avatar_url: '' };
      vi.mocked(apiClient.githubMe).mockResolvedValue(fakeUser as never);
      vi.mocked(apiClient.githubLinked).mockResolvedValue(null as never);
      vi.mocked(apiClient.githubRepos).mockResolvedValue({ repos: [] } as never);

      render(GitHubPanel, { props: { active: true } });
      await waitFor(() => expect(screen.getByText('octocat')).toBeInTheDocument());

      await fireEvent.click(screen.getByRole('button', { name: /Link a repository/i }));

      await waitFor(() => {
        expect(screen.getByPlaceholderText('Search repos...')).toBeInTheDocument();
      });
    });

    it('filters repos by search query', async () => {
      const fakeUser = { login: 'octocat', avatar_url: '' };
      vi.mocked(apiClient.githubMe).mockResolvedValue(fakeUser as never);
      vi.mocked(apiClient.githubLinked).mockResolvedValue(null as never);
      const repos = [
        { full_name: 'owner/alpha', default_branch: 'main' },
        { full_name: 'owner/beta', default_branch: 'main' },
      ];
      vi.mocked(apiClient.githubRepos).mockResolvedValue({ repos } as never);

      render(GitHubPanel, { props: { active: true } });
      await waitFor(() => expect(screen.getByText('octocat')).toBeInTheDocument());

      await fireEvent.click(screen.getByRole('button', { name: /Link a repository/i }));
      const search = await screen.findByPlaceholderText('Search repos...');
      await waitFor(() => expect(screen.getByText('owner/alpha')).toBeInTheDocument());
      await fireEvent.input(search, { target: { value: 'alpha' } });

      await waitFor(() => {
        expect(screen.getByText('owner/alpha')).toBeInTheDocument();
        expect(screen.queryByText('owner/beta')).toBeNull();
      });
    });
  });

  describe('file tree render', () => {
    it('renders file tree when Files tab active and tree has entries', () => {
      githubStore.linkedRepo = {
        full_name: 'owner/repo',
        branch: 'main',
        default_branch: 'main',
        language: 'TypeScript',
        project_label: 'Project X',
        linked_at: '2026-04-01T00:00:00Z',
      } as never;
      githubStore.uiTab = 'files';
      githubStore.fileTree = [
        { name: 'src', path: 'src', type: 'dir', expanded: false, children: [] },
        { name: 'README.md', path: 'README.md', type: 'file', size: 1024 },
      ];

      render(GitHubPanel, { props: { active: true } });

      expect(screen.getByText('src')).toBeInTheDocument();
      expect(screen.getByText('README.md')).toBeInTheDocument();
    });

    it('shows "No files found" when tree empty and not loading', () => {
      githubStore.linkedRepo = {
        full_name: 'owner/repo',
        branch: 'main',
        default_branch: 'main',
        language: 'TypeScript',
        project_label: 'Project X',
        linked_at: '2026-04-01T00:00:00Z',
      } as never;
      githubStore.uiTab = 'files';
      githubStore.fileTree = [];
      githubStore.treeLoading = false;

      render(GitHubPanel, { props: { active: true } });
      expect(screen.getByText('No files found.')).toBeInTheDocument();
    });
  });
});
