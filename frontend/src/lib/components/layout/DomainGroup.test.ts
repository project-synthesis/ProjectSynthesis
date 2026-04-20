import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest';
import { render, screen, cleanup } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';
import DomainGroup from './DomainGroup.svelte';
import { clustersStore } from '$lib/stores/clusters.svelte';

function mkCluster(overrides: Record<string, unknown> = {}) {
  return {
    id: 'c-1',
    parent_id: null,
    label: 'API patterns',
    state: 'active',
    domain: 'backend',
    task_type: 'coding',
    persistence: null,
    coherence: null,
    separation: null,
    stability: null,
    member_count: 3,
    usage_count: 1,
    avg_score: 7.5,
    color_hex: null,
    umap_x: null,
    umap_y: null,
    umap_z: null,
    preferred_strategy: null,
    created_at: '2026-04-01T00:00:00Z',
    ...overrides,
  } as any;
}

describe('DomainGroup', () => {
  beforeEach(() => clustersStore._reset?.());
  afterEach(() => cleanup());

  it('renders the domain label and total count', () => {
    render(DomainGroup, {
      props: {
        domain: 'backend',
        group: {
          directClusters: [mkCluster({ usage_count: 9 })],
          subDomains: [],
          totalCount: 42,
        },
        expandedId: null,
        onToggleExpand: vi.fn(),
        onOpenLinkedOpt: vi.fn(),
      },
    });
    expect(screen.getByText('backend')).toBeInTheDocument();
    expect(screen.getByText('42')).toBeInTheDocument();
  });

  it('strips "project:" prefix for project-scope domains', () => {
    render(DomainGroup, {
      props: {
        domain: 'project:Legacy',
        group: { directClusters: [], subDomains: [], totalCount: 0 },
        expandedId: null,
        onToggleExpand: vi.fn(),
        onOpenLinkedOpt: vi.fn(),
      },
    });
    expect(screen.getByText('Legacy')).toBeInTheDocument();
    expect(screen.queryByText('project:Legacy')).not.toBeInTheDocument();
  });

  it('renders direct ClusterRows inside the group', () => {
    render(DomainGroup, {
      props: {
        domain: 'backend',
        group: {
          directClusters: [
            mkCluster({ id: 'c-1', label: 'Auth patterns' }),
            mkCluster({ id: 'c-2', label: 'Routing patterns' }),
          ],
          subDomains: [],
          totalCount: 2,
        },
        expandedId: null,
        onToggleExpand: vi.fn(),
        onOpenLinkedOpt: vi.fn(),
      },
    });
    expect(screen.getByText('Auth patterns')).toBeInTheDocument();
    expect(screen.getByText('Routing patterns')).toBeInTheDocument();
  });

  it('renders sub-domain group headers with their cluster counts', () => {
    render(DomainGroup, {
      props: {
        domain: 'backend',
        group: {
          directClusters: [],
          subDomains: [
            {
              id: 'sd-auth',
              label: 'auth',
              displayLabel: 'auth',
              parentLabel: 'backend',
              clusters: [mkCluster({ id: 'c-1', label: 'Nested cluster' })],
            },
          ],
          totalCount: 1,
        },
        expandedId: null,
        onToggleExpand: vi.fn(),
        onOpenLinkedOpt: vi.fn(),
      },
    });
    expect(screen.getByText('auth')).toBeInTheDocument();
    expect(screen.getByText('Nested cluster')).toBeInTheDocument();
  });

  it('clicking the domain highlight button toggles highlightedDomain', async () => {
    const user = userEvent.setup();
    const spy = vi.spyOn(clustersStore, 'toggleHighlightDomain');

    render(DomainGroup, {
      props: {
        domain: 'backend',
        group: { directClusters: [mkCluster()], subDomains: [], totalCount: 1 },
        expandedId: null,
        onToggleExpand: vi.fn(),
        onOpenLinkedOpt: vi.fn(),
      },
    });
    await user.click(screen.getByText('backend'));
    expect(spy).toHaveBeenCalledWith('backend');
  });
});
