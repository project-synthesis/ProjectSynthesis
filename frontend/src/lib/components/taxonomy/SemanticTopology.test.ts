import { describe, it, expect, vi, beforeEach } from 'vitest';

// Mock topology modules before any imports that could trigger WebGL
vi.mock('./TopologyRenderer', () => {
  class TopologyRenderer {
    scene = {
      children: [] as unknown[],
      add: () => {},
      remove: () => {},
    };
    start = () => {};
    dispose = () => {};
    resize = () => {};
    onLodChange = () => {};
    focusOn = () => {};
  }
  return { TopologyRenderer };
});

vi.mock('./TopologyInteraction', () => {
  class TopologyInteraction {
    clear = () => {};
    registerNode = () => {};
    dispose = () => {};
  }
  return { TopologyInteraction };
});

vi.mock('./TopologyLabels', () => {
  class TopologyLabels {
    group = { visible: true };
    clear = () => {};
    getOrCreate = () => ({ position: { set: () => {} } });
    setVisible = () => {};
    dispose = () => {};
  }
  return { TopologyLabels };
});

vi.mock('./TopologyWorker', () => ({
  settleForces: (input: { positions: Float32Array; sizes: Float32Array; iterations: number }) => ({
    positions: input.positions,
  }),
}));

vi.mock('./TopologyData', () => ({
  buildSceneData: () => ({ nodes: [], edges: [] }),
  assignLodVisibility: () => {},
}));

vi.mock('./TopologyControls.svelte', () => ({
  default: vi.fn(),
}));

vi.mock('$lib/api/clusters', () => ({
  getClusterTree: vi.fn().mockResolvedValue([]),
  getClusterStats: vi.fn().mockResolvedValue(null),
  triggerRecluster: vi.fn().mockResolvedValue({ status: 'completed', message: 'ok' }),
  matchPattern: vi.fn().mockResolvedValue({ match: null }),
  getClusterDetail: vi.fn().mockResolvedValue(null),
  getClusterTemplates: vi.fn().mockResolvedValue({ total: 0, count: 0, offset: 0, has_more: false, next_offset: null, items: [] }),
}));

vi.mock('three', () => {
  class Vector3 {
    x = 0; y = 0; z = 0;
    constructor(x = 0, y = 0, z = 0) { this.x = x; this.y = y; this.z = z; }
  }
  class IcosahedronGeometry {}
  class MeshBasicMaterial {}
  class Mesh {
    position = { set: () => {} };
    scale = { setScalar: () => {} };
    userData: Record<string, unknown> = {};
  }
  class BufferGeometry { setAttribute = () => {}; }
  class Float32BufferAttribute {}
  class LineBasicMaterial {}
  class LineSegments {}
  return { Vector3, IcosahedronGeometry, MeshBasicMaterial, Mesh, BufferGeometry, Float32BufferAttribute, LineBasicMaterial, LineSegments };
});

import { render } from '@testing-library/svelte';
import SemanticTopology from './SemanticTopology.svelte';

// jsdom doesn't have ResizeObserver — provide a stub
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
Object.defineProperty(globalThis, 'ResizeObserver', {
  value: ResizeObserverStub,
  writable: true,
  configurable: true,
});

describe('SemanticTopology', () => {
  beforeEach(async () => {
    vi.clearAllMocks();
    const { clustersStore } = await import('$lib/stores/clusters.svelte');
    clustersStore._reset();
  });

  it('renders a canvas element', () => {
    const { container } = render(SemanticTopology);
    expect(container.querySelector('canvas')).toBeTruthy();
  });

  it('shows loading state initially', () => {
    const { container } = render(SemanticTopology);
    expect(container.querySelector('.topology-container')).toBeTruthy();
  });

  it('canvas has accessibility attributes', () => {
    const { container } = render(SemanticTopology);
    const canvas = container.querySelector('canvas');
    expect(canvas?.getAttribute('aria-label')).toBe('Taxonomy topology visualization');
    expect(canvas?.getAttribute('tabindex')).toBe('0');
  });

  it('displays error when taxonomy load fails', async () => {
    const clusters = await import('$lib/api/clusters');
    vi.mocked(clusters.getClusterTree).mockRejectedValueOnce(new Error('Connection failed'));
    const { container } = render(SemanticTopology);
    // Wait for the async loadTree() to settle
    await vi.waitFor(() => {
      const errorEl = container.querySelector('.topology-error');
      expect(errorEl).toBeTruthy();
      expect(errorEl?.textContent).toBe('Connection failed');
      expect(errorEl?.getAttribute('role')).toBe('alert');
    });
  });
});
