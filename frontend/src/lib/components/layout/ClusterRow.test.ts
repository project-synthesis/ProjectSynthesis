import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest';
import { render, screen, cleanup } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';
import ClusterRow from './ClusterRow.svelte';
import { clustersStore } from '$lib/stores/clusters.svelte';

function mkNode(overrides: Record<string, unknown> = {}) {
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
    member_count: 4,
    usage_count: 2,
    avg_score: 7.8,
    color_hex: null,
    umap_x: null,
    umap_y: null,
    umap_z: null,
    preferred_strategy: null,
    created_at: '2026-04-01T00:00:00Z',
    ...overrides,
  } as any;
}

describe('ClusterRow', () => {
  beforeEach(() => clustersStore._reset?.());
  afterEach(() => cleanup());

  it('renders label + member count with "m" suffix for regular clusters', () => {
    render(ClusterRow, {
      props: {
        family: mkNode(),
        nested: false,
        expandedId: null,
        onToggleExpand: vi.fn(),
        onOpenLinkedOpt: vi.fn(),
      },
    });
    expect(screen.getByText('API patterns')).toBeInTheDocument();
    expect(screen.getByText('4m')).toBeInTheDocument();
  });

  it('renders "d" suffix for project-state nodes (child domains)', () => {
    render(ClusterRow, {
      props: {
        family: mkNode({ state: 'project', label: 'Legacy', member_count: 3 }),
        nested: false,
        expandedId: null,
        onToggleExpand: vi.fn(),
        onOpenLinkedOpt: vi.fn(),
      },
    });
    expect(screen.getByText('3d')).toBeInTheDocument();
  });

  it('renders "c" suffix for domain-state nodes (child clusters)', () => {
    render(ClusterRow, {
      props: {
        family: mkNode({ state: 'domain', label: 'backend', member_count: 7 }),
        nested: false,
        expandedId: null,
        onToggleExpand: vi.fn(),
        onOpenLinkedOpt: vi.fn(),
      },
    });
    expect(screen.getByText('7c')).toBeInTheDocument();
  });

  it('calls onToggleExpand when the row button is clicked', async () => {
    const user = userEvent.setup();
    const onToggleExpand = vi.fn();
    const family = mkNode();

    render(ClusterRow, {
      props: {
        family,
        nested: false,
        expandedId: null,
        onToggleExpand,
        onOpenLinkedOpt: vi.fn(),
      },
    });
    await user.click(screen.getByText('API patterns'));
    expect(onToggleExpand).toHaveBeenCalledWith(family);
  });

  it('renders usage_count badge', () => {
    render(ClusterRow, {
      props: {
        family: mkNode({ usage_count: 12 }),
        nested: false,
        expandedId: null,
        onToggleExpand: vi.fn(),
        onOpenLinkedOpt: vi.fn(),
      },
    });
    expect(screen.getByText('12')).toBeInTheDocument();
  });

  it('renders formatted avg_score', () => {
    render(ClusterRow, {
      props: {
        family: mkNode({ avg_score: 8.2 }),
        nested: false,
        expandedId: null,
        onToggleExpand: vi.fn(),
        onOpenLinkedOpt: vi.fn(),
      },
    });
    expect(screen.getByText('8.2')).toBeInTheDocument();
  });

  it('applies nested indent class when nested=true', () => {
    const { container } = render(ClusterRow, {
      props: {
        family: mkNode(),
        nested: true,
        expandedId: null,
        onToggleExpand: vi.fn(),
        onOpenLinkedOpt: vi.fn(),
      },
    });
    expect(container.querySelector('.family-row--subdomain')).not.toBeNull();
  });
});
