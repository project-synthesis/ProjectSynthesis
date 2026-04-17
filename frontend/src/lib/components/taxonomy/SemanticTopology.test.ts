import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

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

// Shared mutable scene override — tests can assign to _sceneOverride to
// force specific buildSceneData output. Reset in beforeEach.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const _sceneOverride: { value: any | null } = { value: null };
vi.mock('./TopologyData', () => ({
  buildSceneData: () => _sceneOverride.value ?? { nodes: [], edges: [] },
  assignLodVisibility: () => {},
  buildNodeMap: () => new Map(),
  computeHierarchicalOpacity: () => 0.4,
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
    position = new Vector3();
    rotation = { y: 0 };
    userData: Record<string, unknown> = {};
    scale = { setScalar: () => {} };
    add() {}
    remove() {}
  }
  class _GeomBase {
    getAttribute() {
      return { count: 0, getX: () => 0, getY: () => 0, getZ: () => 0 };
    }
    dispose() {}
  }
  class IcosahedronGeometry extends _GeomBase {}
  class DodecahedronGeometry extends _GeomBase {}
  class EdgesGeometry extends _GeomBase {}
  class RingGeometry extends _GeomBase {}
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
    geometry: unknown = null;
    lookAt() {}
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
  class LineSegments {
    scale = { setScalar: () => {} };
    userData: Record<string, unknown> = {};
    material: unknown = null;
    geometry: unknown = null;
    computeLineDistances() {}
  }
  class Points {
    scale = { setScalar: () => {} };
    userData: Record<string, unknown> = {};
    material: unknown = null;
  }
  class Sprite {}
  class QuadraticBezierCurve3 {
    v0 = new Vector3(); v1 = new Vector3(); v2 = new Vector3();
    getPoint(_t: number, target?: Vector3) { return target ?? new Vector3(); }
  }
  const AdditiveBlending = 1;
  const DoubleSide = 2;
  return {
    Vector3, Color, Quaternion, Group, IcosahedronGeometry, DodecahedronGeometry,
    EdgesGeometry, RingGeometry, MeshBasicMaterial, ShaderMaterial, Mesh, BufferAttribute,
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

describe('SemanticTopology — readiness ring overlay', () => {
  beforeEach(async () => {
    vi.clearAllMocks();
    const { clustersStore } = await import('$lib/stores/clusters.svelte');
    clustersStore._reset();
    const { readinessStore } = await import('$lib/stores/readiness.svelte');
    readinessStore.reports = [];
    readinessStore.loaded = false;
    _sceneOverride.value = null;
  });

  afterEach(() => {
    _sceneOverride.value = null;
  });

  it('renders an invisible data-readiness-ring marker per domain node with a tier', async () => {
    _sceneOverride.value = {
      nodes: [
        {
          id: 'd1',
          position: [0, 0, 0] as [number, number, number],
          color: '#b44aff',
          size: 2,
          opacity: 1,
          persistence: 1,
          state: 'domain',
          label: 'backend',
          visible: true,
          coherence: 0.5,
          avgScore: 7,
          domain: 'backend',
          memberCount: 10,
          isSubDomain: false,
          readinessTier: 'guarded' as const,
        },
      ],
      edges: [],
    };

    const { clustersStore } = await import('$lib/stores/clusters.svelte');
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    clustersStore.taxonomyTree = [
      {
        id: 'd1',
        label: 'backend',
        state: 'domain',
        domain: 'backend',
        member_count: 10,
        parent_id: null,
      } as any,
    ];

    const { container } = render(SemanticTopology);

    // Trigger the reactive effect by reassigning after onMount registers the renderer
    await new Promise((r) => setTimeout(r, 50));
    clustersStore.taxonomyTree = [...clustersStore.taxonomyTree];

    await vi.waitFor(() => {
      const markers = container.querySelectorAll('[data-readiness-ring="d1"]');
      expect(markers.length).toBe(1);
      expect(markers[0].getAttribute('data-readiness-tier')).toBe('guarded');
    });
  });

  it('does not render a marker on a domain node without a resolved readinessTier', async () => {
    // Domain node is present but `readinessTier` is undefined (no report or
    // report not yet loaded). The `hasReadinessRing` predicate should gate
    // the marker out — no `[data-readiness-ring]` span should appear.
    _sceneOverride.value = {
      nodes: [
        {
          id: 'd1',
          position: [0, 0, 0] as [number, number, number],
          color: '#b44aff',
          size: 2,
          opacity: 1,
          persistence: 1,
          state: 'domain',
          label: 'backend',
          visible: true,
          coherence: 0.5,
          avgScore: 7,
          domain: 'backend',
          memberCount: 10,
          isSubDomain: false,
          // readinessTier intentionally omitted
        },
      ],
      edges: [],
    };

    const { clustersStore } = await import('$lib/stores/clusters.svelte');
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    clustersStore.taxonomyTree = [
      {
        id: 'd1',
        label: 'backend',
        state: 'domain',
        domain: 'backend',
        member_count: 10,
        parent_id: null,
      } as any,
    ];

    const { container } = render(SemanticTopology);

    await new Promise((r) => setTimeout(r, 50));
    clustersStore.taxonomyTree = [...clustersStore.taxonomyTree];

    // Give the reactive effect a tick to run
    await new Promise((r) => setTimeout(r, 50));
    const markers = container.querySelectorAll('[data-readiness-ring]');
    expect(markers.length).toBe(0);
  });

  it('does not render a marker on non-domain nodes', async () => {
    _sceneOverride.value = {
      nodes: [
        {
          id: 'c1',
          position: [0, 0, 0] as [number, number, number],
          color: '#b44aff',
          size: 1,
          opacity: 1,
          persistence: 1,
          state: 'active',
          label: 'cluster',
          visible: true,
          coherence: 0.5,
          avgScore: 6,
          domain: 'backend',
          memberCount: 5,
          isSubDomain: false,
        },
      ],
      edges: [],
    };

    const { clustersStore } = await import('$lib/stores/clusters.svelte');
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    clustersStore.taxonomyTree = [
      {
        id: 'c1',
        label: 'cluster',
        state: 'active',
        domain: 'backend',
        member_count: 5,
        parent_id: null,
      } as any,
    ];

    const { container } = render(SemanticTopology);

    await new Promise((r) => setTimeout(r, 50));
    const markers = container.querySelectorAll('[data-readiness-ring]');
    expect(markers.length).toBe(0);
  });
});
