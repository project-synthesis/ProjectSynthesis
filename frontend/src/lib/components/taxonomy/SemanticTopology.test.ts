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
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders a canvas element', () => {
    const { container } = render(SemanticTopology);
    expect(container.querySelector('canvas')).toBeTruthy();
  });

  it('shows loading state initially', () => {
    const { container } = render(SemanticTopology);
    expect(container.querySelector('.topology-container')).toBeTruthy();
  });
});
