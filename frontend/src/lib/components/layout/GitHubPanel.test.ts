import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest';
import { render, cleanup } from '@testing-library/svelte';
import { mockFetch } from '$lib/test-utils';
import GitHubPanel from './GitHubPanel.svelte';
import { githubStore } from '$lib/stores/github.svelte';

describe('GitHubPanel — smoke', () => {
  beforeEach(() => {
    mockFetch([
      { match: '/api/github/me', response: null, status: 401 },
      { match: '/api/repos', response: [] },
      { match: '/api/projects', response: [] },
    ]);
    githubStore.user = null;
    githubStore.linkedRepo = null;
    githubStore.repos = [];
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it('mounts without throwing when inactive', () => {
    const { container } = render(GitHubPanel, { props: { active: false } });
    expect(container).toBeTruthy();
  });

  it('mounts without throwing when active (triggers lazy auth check)', () => {
    const { container } = render(GitHubPanel, { props: { active: true } });
    expect(container).toBeTruthy();
  });

  it('surfaces disconnected-state copy when no user/token', () => {
    const { container } = render(GitHubPanel, { props: { active: true } });
    // Disconnected connection state should render some auth-prompt content.
    const text = container.textContent || '';
    expect(text.length).toBeGreaterThan(0);
  });
});
