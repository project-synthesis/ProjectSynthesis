import { describe, it, expect, vi, beforeEach } from 'vitest';

// Mock topology modules before any imports that could trigger WebGL
vi.mock('./TopologyRenderer', () => {
  class TopologyRenderer {
    scene = {
      children: [] as unknown[],
      add: () => {},
      remove: () => {},
      traverse: () => {},
    };
    camera = { position: { distanceTo: () => 80 }, quaternion: { angleTo: () => 0 }, up: { clone: () => ({ negate: () => ({ multiplyScalar: () => ({}) }) }) } };
    start = () => {};
    dispose = () => {};
    resize = () => {};
    onLodChange = () => {};
    focusOn = () => {};
    addAnimationCallback = () => () => {};
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
    setVisibleFor = () => {};
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
  buildNodeMap: () => new Map(),
}));

vi.mock('./BeamPool', () => {
  class BeamPool {
    group = { name: 'beam-pool', children: [] as unknown[], add() {}, remove() {} };
    acquire() { return null; }
    update() {}
    terminateAll() {}
    dispose() {}
  }
  return { BeamPool };
});

vi.mock('./ClusterPhysics', () => {
  class ClusterPhysics {
    onBeamImpact() {}
    setBaseScale() {}
    update() {}
    clear() {}
    isActive() { return false; }
  }
  return { ClusterPhysics };
});

vi.mock('./BeamShader', () => ({
  createRippleUniforms: () => ({
    uColor: { value: { r: 1, g: 1, b: 1, copy: () => {} } },
    uOpacity: { value: 1 },
    uRipple: { value: 0 },
  }),
  RIPPLE_VERTEX_SHADER: '',
  RIPPLE_FRAGMENT_SHADER: '',
}));

vi.mock('./TopologyControls.svelte', () => ({
  default: vi.fn(),
}));

vi.mock('$lib/api/clusters', () => ({
  getClusterTree: vi.fn().mockResolvedValue([]),
  getClusterStats: vi.fn().mockResolvedValue(null),
  getClusterSimilarityEdges: vi.fn().mockResolvedValue([]),
  getClusterInjectionEdges: vi.fn().mockResolvedValue([]),
  triggerRecluster: vi.fn().mockResolvedValue({ status: 'completed', message: 'ok' }),
  matchPattern: vi.fn().mockResolvedValue({ match: null }),
  getClusterDetail: vi.fn().mockResolvedValue(null),
  getClusterTemplates: vi.fn().mockResolvedValue({ total: 0, count: 0, offset: 0, has_more: false, next_offset: null, items: [] }),
}));

vi.mock('three', () => {
  class Vector3 {
    x = 0; y = 0; z = 0;
    constructor(x = 0, y = 0, z = 0) { this.x = x; this.y = y; this.z = z; }
    copy() { return this; }
    clone() { return new Vector3(this.x, this.y, this.z); }
    set(x: number, y: number, z: number) { this.x = x; this.y = y; this.z = z; return this; }
    subVectors() { return this; }
    addVectors() { return this; }
    multiplyScalar() { return this; }
    normalize() { return this; }
    crossVectors() { return this; }
    negate() { return this; }
    add() { return this; }
    distanceTo() { return 10; }
    unproject() { return this; }
    getWorldPosition(target: Vector3) { return target; }
  }
  class Color {
    r = 0; g = 0; b = 0;
    constructor() {}
    copy() { return this; }
    setHex() { return this; }
    multiplyScalar() { return this; }
  }
  class Quaternion {
    copy() { return this; }
    angleTo() { return 0; }
  }
  class Group {
    children: unknown[] = [];
    name = '';
    add() {}
    remove() {}
  }
  class IcosahedronGeometry {}
  class DodecahedronGeometry {}
  class EdgesGeometry {}
  class MeshBasicMaterial { color = new Color(); opacity = 1; transparent = false; }
  class ShaderMaterial {
    uniforms: Record<string, { value: unknown }> = {};
    isShaderMaterial = true;
  }
  class Mesh {
    position = new Vector3();
    scale = { setScalar: () => {} };
    userData: Record<string, unknown> = {};
    material: unknown = null;
    parent: unknown = null;
    visible = true;
    frustumCulled = true;
  }
  const _emptyArray = new Float32Array(0);
  class BufferAttribute {
    array: ArrayLike<number> = _emptyArray;
    needsUpdate = false;
    constructor(arr?: ArrayLike<number>) { if (arr) this.array = arr; }
  }
  class BufferGeometry {
    setAttribute() {}
    setIndex() {}
    getAttribute() { return new BufferAttribute(); }
    getIndex() { return new BufferAttribute(new Uint16Array(0)); }
    computeBoundingSphere() {}
    dispose() {}
  }
  class Float32BufferAttribute {}
  class LineBasicMaterial {}
  class LineDashedMaterial {}
  class PointsMaterial {}
  class LineSegments {}
  class Points {}
  class Sprite {}
  class QuadraticBezierCurve3 {
    v0 = new Vector3(); v1 = new Vector3(); v2 = new Vector3();
    getPoint(_t: number, target?: Vector3) { return target ?? new Vector3(); }
  }
  const AdditiveBlending = 1;
  const DoubleSide = 2;
  return {
    Vector3, Color, Quaternion, Group, IcosahedronGeometry, DodecahedronGeometry,
    EdgesGeometry, MeshBasicMaterial, ShaderMaterial, Mesh, BufferAttribute,
    BufferGeometry, Float32BufferAttribute, LineBasicMaterial, LineDashedMaterial,
    PointsMaterial, LineSegments, Points, Sprite, QuadraticBezierCurve3,
    AdditiveBlending, DoubleSide,
  };
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
    // Taxonomy loading is handled by +layout.svelte — simulate a failed load
    // by setting the store's error state directly.
    const { clustersStore } = await import('$lib/stores/clusters.svelte');
    clustersStore.taxonomyError = 'Connection failed';
    const { container } = render(SemanticTopology);
    await vi.waitFor(() => {
      const errorEl = container.querySelector('.topology-error');
      expect(errorEl).toBeTruthy();
      expect(errorEl?.textContent).toBe('Connection failed');
      expect(errorEl?.getAttribute('role')).toBe('alert');
    });
  });
});
