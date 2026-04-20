import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/svelte';

// Mock all external dependencies before importing the component
vi.mock('$lib/actions/tooltip', () => ({
  tooltip: () => ({ destroy() {} }),
}));

vi.mock('$lib/utils/ui-tooltips', () => ({
  TOPOLOGY_TOOLTIPS: {
    recluster: 'Recluster tooltip',
    activity: 'Activity tooltip',
    seed: 'Seed tooltip',
    search: 'Search tooltip',
    hint: 'Hint tooltip',
    controls: 'Controls tooltip',
  },
}));

vi.mock('$lib/stores/clusters.svelte', () => ({
  clustersStore: {
    clusterCounts: { active: 5, candidate: 1, archived: 2, mature: 0, template: 0 },
    taxonomyStats: null,
    clusterDetail: null,
    selectedClusterId: null,
    activityEvents: [],
    activityOpen: false,
    stateFilter: 'active',
  },
}));

vi.mock('$lib/stores/routing.svelte', () => ({
  routing: {
    effectiveTier: 'internal',
    provider: 'claude_cli',
    tierColor: 'var(--color-neon-cyan)',
  },
}));

vi.mock('./TopologyInfoPanel.svelte', () => ({
  // Svelte 5 compiled components are functions, not classes
  default: () => {},
}));

vi.mock('./TopologyRenderer', () => ({
  // LODTier type only — no runtime dependency
}));

import TopologyControls from './TopologyControls.svelte';
import { hintsStore } from '$lib/stores/hints.svelte';

function renderControls(overrides: Record<string, unknown> = {}) {
  return render(TopologyControls, {
    props: {
      lodTier: 'mid' as const,
      showActivity: false,
      onSearch: vi.fn(),
      onRecluster: vi.fn().mockResolvedValue(undefined),
      onToggleActivity: vi.fn(),
      onSeed: vi.fn(),
      ...overrides,
    },
  });
}

describe('TopologyControls', () => {
  beforeEach(() => {
    localStorage.clear();
    hintsStore._reset();
    vi.clearAllMocks();
  });

  afterEach(() => { cleanup(); });

  it('renders without crashing', () => {
    const { container } = renderControls();
    expect(container).toBeTruthy();
  });

  it('renders the HUD container', () => {
    const { container } = renderControls();
    expect(container.querySelector('.hud')).toBeTruthy();
  });

  it('shows hint overlay on first visit when not dismissed', async () => {
    const { container } = renderControls();
    // onMount sets hintVisible=true — wait for DOM update
    await vi.waitFor(() => {
      expect(container.querySelector('.hud-hint')).toBeTruthy();
    });
  });

  it('hides hint when previously dismissed via hintsStore', async () => {
    hintsStore.dismiss('pattern_graph');
    const { container } = renderControls();
    // onMount reads hintsStore and keeps hintVisible=false
    await vi.waitFor(() => {
      expect(container.querySelector('.hud-hint')).toBeNull();
    });
  });

  it('controls are initially hidden (auto-hide behavior)', () => {
    const { container } = renderControls();
    const controls = container.querySelector('.hud-controls');
    // Should not have the visible class initially
    expect(controls?.classList.contains('hud-controls--visible')).toBeFalsy();
  });

  it('renders cluster count badges', () => {
    const { container } = renderControls();
    // The component displays active/candidate/archived counts
    expect(container.textContent).toContain('5');
  });
});
