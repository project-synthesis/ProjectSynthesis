import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

// Test-only registry of per-frame callbacks registered via the mocked
// TopologyRenderer. Lets tests simulate a frame tick by invoking all
// registered callbacks in order — the production render loop (see
// `TopologyRenderer.start`) does the same on every `requestAnimationFrame`.
const _animationCallbacks: Array<() => void> = [];
const _tickFrame = () => {
  // Copy first: callbacks may unregister during iteration.
  const snapshot = _animationCallbacks.slice();
  for (const cb of snapshot) cb();
};

// Module-level mutable LOD tier shared across the mocked renderer instance
// and the tests. The production `TopologyRenderer.lodTier` getter returns
// `'far' | 'mid' | 'near'` based on camera distance; in tests there is no
// render loop driving `_checkLod()`, so tests flip this value directly to
// simulate zoom-in/out. Reset in afterEach alongside the other shared state.
const _lodTierOverride: { value: 'far' | 'mid' | 'near' } = { value: 'near' };

// Mock topology modules before any imports that could trigger WebGL
vi.mock('./TopologyRenderer', () => {
  class TopologyRenderer {
    scene = {
      children: [] as unknown[],
      add(obj: unknown) { this.children.push(obj); },
      remove(obj: unknown) {
        const idx = this.children.indexOf(obj);
        if (idx >= 0) this.children.splice(idx, 1);
      },
      traverse(fn: (obj: unknown) => void) {
        // Walk all direct children, recursing into any child with its own
        // `children` array (Group-like). Enough for the production code
        // paths that traverse for edge opacity sweeps and ring dimming.
        const walk = (node: unknown) => {
          fn(node);
          const kids = (node as { children?: unknown[] })?.children;
          if (Array.isArray(kids)) for (const k of kids) walk(k);
        };
        for (const c of this.children) walk(c);
      },
    };
    constructor() {
      // Expose the scene so tests can read root-level meshes (e.g.
      // readiness rings added via `renderer.scene.add(mesh)`). Each test
      // renders a fresh component → a fresh renderer → a fresh scene,
      // and we always capture the most recent one.
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      (globalThis as any).__semTopLastScene = this.scene;
    }
    camera = { position: { distanceTo: () => 80 }, quaternion: { angleTo: () => 0 }, up: { clone: () => ({ negate: () => ({ multiplyScalar: () => ({}) }) }) } };
    // Mirrors the public `lodTier` getter on the real TopologyRenderer. Reads
    // from the module-level override so tests can flip tiers mid-frame.
    get lodTier(): 'far' | 'mid' | 'near' { return _lodTierOverride.value; }
    start = () => {};
    dispose = () => {};
    resize = () => {};
    onLodChange = () => {};
    focusOn = () => {};
    addAnimationCallback = (cb: () => void) => {
      _animationCallbacks.push(cb);
      return () => {
        const idx = _animationCallbacks.indexOf(cb);
        if (idx >= 0) _animationCallbacks.splice(idx, 1);
      };
    };
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
// When `true`, the mock delegates to the real `buildSceneData`. This is used
// by reactivity tests that must exercise the production scene-builder (which
// reads `readinessStore`) to expose missing reactive dependencies.
const _useRealBuildSceneData: { value: boolean } = { value: false };
vi.mock('./TopologyData', async () => {
  const actual = await vi.importActual<typeof import('./TopologyData')>('./TopologyData');
  return {
    buildSceneData: (...args: Parameters<typeof actual.buildSceneData>) => {
      if (_useRealBuildSceneData.value) return actual.buildSceneData(...args);
      return _sceneOverride.value ?? { nodes: [], edges: [] };
    },
    assignLodVisibility: () => {},
    buildNodeMap: () => new Map(),
    computeHierarchicalOpacity: () => 0.4,
  };
});

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

// THREE.js test mocks — modeled: Color (set/copy/clone/lerp on rgb floats),
// RingGeometry (captures inner/outer/segments), MeshBasicMaterial (passes color through).
// Not modeled: world-space transforms, camera math, GPU disposal side effects.
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
  // Semantic Color mock: `set(hex)`, `copy(other)`, and `lerp(to, t)` mutate
  // numeric r/g/b channels so tests can observe interpolated values. Required
  // by the tween-supersede test below (otherwise all color writes are no-ops
  // and the snap-back bug is unobservable). Other tests don't read r/g/b so
  // this remains compatible.
  class Color {
    r = 0; g = 0; b = 0;
    constructor(input?: string | number) {
      if (typeof input === 'string') this.set(input);
      else if (typeof input === 'number') this.setHex(input);
    }
    copy(other: Color) { this.r = other.r; this.g = other.g; this.b = other.b; return this; }
    clone() { const c = new Color(); c.r = this.r; c.g = this.g; c.b = this.b; return c; }
    set(hex: string) {
      if (typeof hex === 'string' && hex.startsWith('#')) {
        const n = parseInt(hex.slice(1), 16);
        this.r = ((n >> 16) & 0xff) / 255;
        this.g = ((n >> 8) & 0xff) / 255;
        this.b = (n & 0xff) / 255;
      }
      return this;
    }
    setHex(n: number) {
      this.r = ((n >> 16) & 0xff) / 255;
      this.g = ((n >> 8) & 0xff) / 255;
      this.b = (n & 0xff) / 255;
      return this;
    }
    lerp(to: Color, t: number) {
      this.r = this.r + (to.r - this.r) * t;
      this.g = this.g + (to.g - this.g) * t;
      this.b = this.b + (to.b - this.b) * t;
      return this;
    }
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
    add(child: unknown) {
      this.children.push(child);
      (child as { parent?: unknown }).parent = this;
    }
    remove(child: unknown) {
      const idx = this.children.indexOf(child);
      if (idx >= 0) this.children.splice(idx, 1);
    }
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
  // RingGeometry captures its constructor args so tests can observe ring
  // geometry dimensions. Production code builds the ring as
  // `new THREE.RingGeometry(radius, radius + thickness, segments)` where
  // `radius = node.size * READINESS_RING_RADIUS_FACTOR`. The size-drift
  // regression test reads `innerRadius` / `outerRadius` after rebuild to
  // assert geometry tracks `node.size`.
  class RingGeometry extends _GeomBase {
    innerRadius: number;
    outerRadius: number;
    segments: number;
    constructor(inner = 0, outer = 0, segments = 0) {
      super();
      this.innerRadius = inner;
      this.outerRadius = outer;
      this.segments = segments;
    }
  }
  class MeshBasicMaterial {
    color = new Color();
    opacity = 1;
    transparent = false;
    dispose() {}
    constructor(params?: { opacity?: number; transparent?: boolean; color?: unknown }) {
      if (params?.opacity != null) this.opacity = params.opacity;
      if (params?.transparent != null) this.transparent = params.transparent;
      // Copy constructor-provided color so `material.color` reflects the
      // initial hex. Without this, `new MeshBasicMaterial({color: new Color('#eab308')})`
      // would silently drop the hex and `material.color` would stay at (0,0,0),
      // which breaks any test that reads initial material color values.
      if (params?.color instanceof Color) {
        this.color.copy(params.color as Color);
      }
    }
  }
  class ShaderMaterial {
    uniforms: Record<string, { value: unknown }> = {};
    isShaderMaterial = true;
    dispose() {}
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
    constructor(geometry?: unknown, material?: unknown) {
      if (geometry !== undefined) this.geometry = geometry;
      if (material !== undefined) this.material = material;
    }
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
  class LineBasicMaterial { opacity = 1; dispose() {} }
  class LineDashedMaterial { opacity = 1; dispose() {} }
  class PointsMaterial { opacity = 1; dispose() {} }
  class LineSegments {
    scale = { setScalar: () => {} };
    userData: Record<string, unknown> = {};
    material: unknown = null;
    geometry: unknown = null;
    computeLineDistances() {}
    constructor(geometry?: unknown, material?: unknown) {
      if (geometry !== undefined) this.geometry = geometry;
      if (material !== undefined) this.material = material;
    }
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
    _useRealBuildSceneData.value = false;
    _animationCallbacks.length = 0;
    _lodTierOverride.value = 'near';
  });

  afterEach(() => {
    _sceneOverride.value = null;
    _useRealBuildSceneData.value = false;
    _animationCallbacks.length = 0;
    _lodTierOverride.value = 'near';
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

  it('re-renders ring markers when readinessStore reports mutate without a taxonomy change', async () => {
    // This test exercises the REAL buildSceneData so the production
    // reactivity chain — `$effect` depends on readinessStore reads — is
    // exercised honestly. The bug: `buildSceneData` is invoked inside
    // `untrack(...)` in SemanticTopology's tree-watch effect, so Svelte
    // never registers `readinessStore.reports` as a dependency. When a
    // report mutates without a taxonomy change, the ring marker stays on
    // the stale tier.
    _useRealBuildSceneData.value = true;

    const buildReport = (
      stabilityTier: 'healthy' | 'guarded' | 'critical',
    ) => ({
      domain_id: 'd1',
      domain_label: 'backend',
      member_count: 10,
      stability: {
        consistency: 0.5,
        dissolution_floor: 0.15,
        hysteresis_creation_threshold: 0.6,
        age_hours: 100,
        min_age_hours: 48,
        member_count: 10,
        member_ceiling: 5,
        sub_domain_count: 0,
        total_opts: 10,
        guards: {
          general_protected: false,
          has_sub_domain_anchor: false,
          age_eligible: true,
          above_member_ceiling: true,
          consistency_above_floor: stabilityTier !== 'critical',
        },
        tier: stabilityTier,
        dissolution_risk: stabilityTier === 'critical' ? 0.9 : 0.2,
        would_dissolve: stabilityTier === 'critical',
      },
      emergence: {
        threshold: 0.6,
        threshold_formula: 'adaptive',
        min_member_count: 8,
        total_opts: 10,
        top_candidate: null,
        gap_to_threshold: null,
        ready: false,
        blocked_reason: 'no_candidates' as const,
        runner_ups: [],
        tier: 'inert' as const,
      },
      computed_at: new Date().toISOString(),
    });

    const { clustersStore } = await import('$lib/stores/clusters.svelte');
    const { readinessStore } = await import('$lib/stores/readiness.svelte');

    // Seed a minimal taxonomy tree with one domain node whose umap coords
    // keep the builder happy. Seed readiness with a 'guarded' report so
    // the first render produces a marker with tier="guarded".
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    clustersStore.taxonomyTree = [
      {
        id: 'd1',
        parent_id: null,
        label: 'backend',
        state: 'domain',
        domain: 'backend',
        task_type: 'general',
        persistence: 1.0,
        coherence: 0.5,
        separation: null,
        stability: null,
        member_count: 10,
        usage_count: 0,
        avg_score: 7,
        color_hex: null,
        umap_x: 0,
        umap_y: 0,
        umap_z: 0,
        preferred_strategy: null,
        output_coherence: null,
        blend_w_raw: null,
        blend_w_optimized: null,
        blend_w_transform: null,
        split_failures: 0,
        meta_pattern_count: 0,
        created_at: null,
      } as any,
    ];
    readinessStore.reports = [buildReport('guarded')];
    readinessStore.loaded = true;

    const { container } = render(SemanticTopology);

    // Nudge the tree-watch $effect — reassigning taxonomyTree is how the
    // other tests in this block trigger the initial scene build.
    await new Promise((r) => setTimeout(r, 50));
    clustersStore.taxonomyTree = [...clustersStore.taxonomyTree];

    await vi.waitFor(() => {
      const markers = container.querySelectorAll('[data-readiness-ring="d1"]');
      expect(markers.length).toBe(1);
      expect(markers[0].getAttribute('data-readiness-tier')).toBe('guarded');
    });

    // Mutate ONLY the readiness store — no taxonomyTree change, no
    // stateFilter change. `composeReadinessTier` now returns 'critical'.
    // If the `$effect` tracked readinessStore.reports properly, it would
    // re-run and the marker would flip to tier="critical". Under the bug,
    // `untrack(...)` swallows the dependency so the scene is never rebuilt
    // and the marker stays on the stale 'guarded' tier.
    readinessStore.reports = [buildReport('critical')];

    await vi.waitFor(
      () => {
        const markers = container.querySelectorAll('[data-readiness-ring="d1"]');
        expect(markers.length).toBe(1);
        expect(markers[0].getAttribute('data-readiness-tier')).toBe('critical');
      },
      { timeout: 500 },
    );
  });

  it('unregisters the billboard callback when a rebuild drops all rings', async () => {
    // Contract: `_readinessRings` map is rebuilt each `rebuildScene`. When
    // a rebuild yields zero rings (e.g. all domain reports disappeared),
    // the per-frame billboard callback must be unsubscribed so stale
    // closures don't linger in `addAnimationCallback`'s internal array.
    // Regression guard — without the unsubscribe, ticking a frame would
    // still invoke the old callback, even after the ring entries are
    // cleared from the map.
    const THREE = await import('three');
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const MeshProto = (THREE as any).Mesh.prototype;
    const lookAtSpy = vi.spyOn(MeshProto, 'lookAt');

    try {
      // First build: one ring. Triggers billboard callback registration.
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
      await new Promise((r) => setTimeout(r, 50));
      clustersStore.taxonomyTree = [...clustersStore.taxonomyTree];

      await vi.waitFor(() => {
        expect(container.querySelectorAll('[data-readiness-ring="d1"]').length).toBe(1);
      });

      // Sanity: callback is registered and fires per frame.
      expect(_animationCallbacks.length).toBeGreaterThan(0);

      // Rebuild with zero rings — domain node's readinessTier is gone.
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
            // readinessTier dropped on purpose
          },
        ],
        edges: [],
      };
      clustersStore.taxonomyTree = [...clustersStore.taxonomyTree];

      await vi.waitFor(() => {
        expect(container.querySelectorAll('[data-readiness-ring]').length).toBe(0);
      });

      // Snapshot post-cleanup, then tick. The billboard callback must NOT
      // fire — only non-ring callbacks (e.g. domain rotation) should run.
      // Since the ring's mesh is disposed, any stale callback would invoke
      // `lookAt` on it — we assert the delta is zero.
      const afterCleanup = lookAtSpy.mock.calls.length;
      for (let i = 0; i < 5; i++) _tickFrame();
      expect(lookAtSpy.mock.calls.length - afterCleanup).toBe(0);
    } finally {
      lookAtSpy.mockRestore();
    }
  });

  it('re-orients ring meshes per animation frame, not just at build', async () => {
    // Bug: SemanticTopology calls `mesh.lookAt(camera.position)` once at ring
    // build time (rebuildScene) and never again. OrbitControls rotation is
    // the dominant interaction — as the user orbits, ring orientation goes
    // stale because the camera position relative to the ring changes but
    // the ring's `lookAt` is never re-invoked.
    //
    // Correct fix (per reviewer): hook per-ring billboarding into the
    // existing per-frame animation loop (same loop that drives
    // `_removeDomainRotation`). Every frame must re-run `lookAt` for each
    // readiness ring so the contour continuously faces the camera.
    //
    // This test spies on Mesh.prototype.lookAt, forces one readiness ring
    // to build, snapshots the post-build call count, ticks N animation
    // frames, and asserts call count grew by at least N (one per frame per
    // ring). Under the bug, the count stays flat after build.
    const THREE = await import('three');
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const MeshProto = (THREE as any).Mesh.prototype;
    const lookAtSpy = vi.spyOn(MeshProto, 'lookAt');

    try {
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
      await new Promise((r) => setTimeout(r, 50));
      clustersStore.taxonomyTree = [...clustersStore.taxonomyTree];

      // Wait for ring build to complete (DOM marker is our build-done signal).
      await vi.waitFor(() => {
        const markers = container.querySelectorAll('[data-readiness-ring="d1"]');
        expect(markers.length).toBe(1);
      });

      // Snapshot the post-build call count. Includes the one build-time
      // lookAt on the ring mesh; may include incidental lookAt calls from
      // other code paths (labels, etc.) — we only care about the delta.
      const postBuildCount = lookAtSpy.mock.calls.length;

      // Tick N animation frames. Each frame should re-orient every ring.
      const N = 5;
      for (let i = 0; i < N; i++) _tickFrame();

      const postTickCount = lookAtSpy.mock.calls.length;
      const delta = postTickCount - postBuildCount;

      // Under the fix: delta >= N (one lookAt per ring per frame).
      // Under the bug: delta === 0 (lookAt is only called at build time).
      expect(delta).toBeGreaterThanOrEqual(N);
    } finally {
      lookAtSpy.mockRestore();
    }
  });

  it('dims readiness ring opacity in lockstep with its parent domain when another domain is highlighted', async () => {
    // Bug: the domain-highlight dim effect (SemanticTopology.svelte ~line
    // 1039) rewrites `mat.opacity` on each domain group's direct children
    // (fill / edges / points) to `baseOpacity * 0.15` when the node does
    // NOT match `clustersStore.highlightedDomain`. Readiness rings are
    // parented to `renderer.scene` at the root (see `scene.add(mesh)` at
    // line ~469), NOT to the domain group — so the current sweep misses
    // them entirely. Result: domain A's dodecahedron dims to 0.15× but
    // its readiness ring stays at its bright built-time opacity.
    //
    // Correct fix (per reviewer): either parent rings under their domain
    // group so they inherit the group sweep, OR extend the highlight
    // effect to iterate `_readinessRings` by node id and apply the same
    // dim factor. Either fix MUST produce the invariant tested here:
    // when domain B is highlighted, domain A's ring is dimmed and
    // domain B's ring keeps its base opacity.
    const THREE = await import('three');
    // Two visible domain nodes, each with a readinessTier → each produces
    // its own ring mesh that the production code `scene.add()`s at scene
    // root. The mock scene.add captures them in `scene.children`.
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
        {
          id: 'd2',
          position: [5, 0, 0] as [number, number, number],
          color: '#ff4895',
          size: 2,
          opacity: 1,
          persistence: 1,
          state: 'domain',
          label: 'frontend',
          visible: true,
          coherence: 0.5,
          avgScore: 8,
          domain: 'frontend',
          memberCount: 12,
          isSubDomain: false,
          readinessTier: 'healthy' as const,
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
      {
        id: 'd2',
        label: 'frontend',
        state: 'domain',
        domain: 'frontend',
        member_count: 12,
        parent_id: null,
      } as any,
    ];

    const { container } = render(SemanticTopology);
    await new Promise((r) => setTimeout(r, 50));
    clustersStore.taxonomyTree = [...clustersStore.taxonomyTree];

    // Wait for scene build — both ring markers present in DOM.
    await vi.waitFor(() => {
      expect(container.querySelectorAll('[data-readiness-ring]').length).toBe(2);
    });

    // Reach the component's renderer scene via the `__semTopLastScene`
    // capture installed by the `TopologyRenderer` mock constructor. Every
    // fresh `render(SemanticTopology)` overwrites this with the newest
    // scene — we rely on it here to read the root-level ring meshes.
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const lastScene = (globalThis as any).__semTopLastScene as
      | { children: unknown[] }
      | undefined;
    expect(lastScene).toBeDefined();
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const RingGeometryClass = (THREE as any).RingGeometry;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const MeshBasicMaterialClass = (THREE as any).MeshBasicMaterial;

    const sceneChildren = lastScene!.children;
    // Readiness rings live inside a tagged `THREE.Group` (isReadinessRingGroup)
    // so the scene-clear traverse in `rebuildScene` can't reach them — mirrors
    // the beam-pool protection pattern. Walk into the group to find rings.
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const ringGroup = sceneChildren.find((c: any) =>
      c?.userData?.isReadinessRingGroup === true,
    ) as { children: unknown[] } | undefined;
    expect(ringGroup).toBeDefined();
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const rings = ringGroup!.children.filter((c: any) =>
      c instanceof (THREE as any).Mesh && c.geometry instanceof RingGeometryClass,
    ) as Array<{ material: { opacity: number } }>;
    expect(rings.length).toBe(2);

    // The production ring-build loop iterates `data.nodes` in order, so
    // rings[0] corresponds to d1 (first in the scene override) and rings[1]
    // corresponds to d2. `group.add` on our mocked group preserves insertion
    // order via `children.push`.
    const ringD1 = rings[0];
    const ringD2 = rings[1];
    expect(ringD1.material).toBeInstanceOf(MeshBasicMaterialClass);
    expect(ringD2.material).toBeInstanceOf(MeshBasicMaterialClass);

    // Built-time opacity: node.opacity (=1) * READINESS_RING_OPACITY_FACTOR (=0.9).
    const BASE = 1 * 0.9;
    expect(ringD1.material.opacity).toBeCloseTo(BASE, 5);
    expect(ringD2.material.opacity).toBeCloseTo(BASE, 5);

    // Sanity: find d1's domain group (first Group in scene.children whose
    // first child is the dodecahedron fill). Its fill material opacity is
    // rewritten by the existing dim effect — if this DOESN'T dim, the
    // test environment isn't running the effect at all and the ring
    // assertion below would be testing nothing. Guarding against a false
    // negative in the assertion.
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const GroupClass = (THREE as any).Group;
    const groups = sceneChildren.filter((c: any) => c instanceof GroupClass) as Array<{
      userData: { isStructural?: boolean };
      children: Array<{ material: { opacity: number } }>;
    }>;
    const d1Group = groups[0];
    const d1Fill = d1Group.children[0];
    const fillBaseOpacity = d1Fill.material.opacity; // = 1 * 0.9 = 0.9

    // Highlight the 'frontend' primary domain (d2's domain) → the dim sweep
    // MUST dim d1's ring (domain 'backend', non-match) to BASE*0.15.
    // `highlightedDomain` is a primary-domain string in production flow
    // (set by ClusterNavigator), not a node id — using the id here would
    // incorrectly dim every ring including the highlighted domain's own.
    clustersStore.highlightedDomain = 'frontend';

    // Wait for the existing dim sweep to dim d1's dodecahedron fill.
    // This proves the effect runs; if the ring material ALSO dims we'd
    // have no bug. The fail below proves the ring is orphaned from the
    // sweep.
    await vi.waitFor(
      () => {
        expect(d1Fill.material.opacity).toBeLessThan(fillBaseOpacity * 0.5);
      },
      { timeout: 500 },
    );

    // The actual bug: d1's readiness ring opacity MUST also drop in
    // lockstep. Currently the dim effect never visits `_readinessRings`,
    // so this opacity stays at its bright built-time value (~0.9).
    expect(ringD1.material.opacity).toBeLessThan(0.3);
    // d2 (highlighted) keeps its base opacity.
    expect(ringD2.material.opacity).toBeCloseTo(BASE, 5);
  });

  it('updates ring tier marker when sceneData tier changes', async () => {
    // Contract: the `{#each sceneData?.nodes.filter(hasReadinessRing) ...}`
    // block in SemanticTopology must reactively re-render when a domain
    // node's `readinessTier` changes between scene rebuilds. If the each
    // block were changed to a non-reactive snapshot (e.g. captured into a
    // plain `let` outside the template), the marker's `data-readiness-tier`
    // attribute would stay pinned to the initial value after a rebuild.
    //
    // This locks in the reactive contract that Task 8 (cubic-bezier tween
    // on tier transition) depends on — the tween needs a reliable "tier
    // changed" signal from the DOM-linked ring entry, which only works if
    // the attribute tracks sceneData on every rebuild.
    const domainNode = (tier: 'guarded' | 'ready' | 'healthy' | 'critical') => ({
      id: 'd1',
      position: [0, 0, 0] as [number, number, number],
      color: '#b44aff',
      size: 2,
      opacity: 1,
      persistence: 1,
      state: 'domain' as const,
      label: 'backend',
      visible: true,
      coherence: 0.5,
      avgScore: 7,
      domain: 'backend',
      memberCount: 30,
      isSubDomain: false,
      readinessTier: tier,
    });

    _sceneOverride.value = { nodes: [domainNode('guarded')], edges: [] };

    const { clustersStore } = await import('$lib/stores/clusters.svelte');
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    clustersStore.taxonomyTree = [
      {
        id: 'd1',
        label: 'backend',
        state: 'domain',
        domain: 'backend',
        member_count: 30,
        parent_id: null,
      } as any,
    ];

    const { container } = render(SemanticTopology);
    await new Promise((r) => setTimeout(r, 50));
    clustersStore.taxonomyTree = [...clustersStore.taxonomyTree];

    await vi.waitFor(() => {
      const marker = container.querySelector('[data-readiness-ring="d1"]');
      expect(marker).toBeTruthy();
      expect(marker?.getAttribute('data-readiness-tier')).toBe('guarded');
    });

    // Swap in a new sceneData result with a different tier. Reassigning
    // `taxonomyTree` nudges the `$effect` that calls `buildSceneData` (our
    // mock returns the updated `_sceneOverride.value`), which updates the
    // `sceneData` $state. The reactive each-block must propagate the new
    // tier to `data-readiness-tier`.
    _sceneOverride.value = { nodes: [domainNode('ready')], edges: [] };
    clustersStore.taxonomyTree = [...clustersStore.taxonomyTree];

    await vi.waitFor(
      () => {
        const marker = container.querySelector('[data-readiness-ring="d1"]');
        expect(marker).toBeTruthy();
        expect(marker?.getAttribute('data-readiness-tier')).toBe('ready');
      },
      { timeout: 500 },
    );
  });

  it('cancels in-flight ring tweens on unmount', async () => {
    // Bug (C1): SemanticTopology's onMount cleanup closure disposes the
    // renderer / beamPool / labels and removes the billboard callback, but
    // NEVER cancels the in-flight `TweenHandle` instances stored on
    // `_readinessRings` entries. A tier transition starts an RAF chain that
    // writes `material.color.copy(...).lerp(...)` on a material whose
    // underlying GL resource is about to be released by `renderer.dispose()`.
    // A mid-tween unmount therefore leaks the RAF loop AND invites a
    // use-after-free on the disposed material (the exact hazard the tween
    // comment at lines 32-34 claims to guard against via cancellation).
    //
    // Contract: on unmount, every active `entry.tween.cancel()` MUST fire.
    // Since `tweenRingColor`'s cancel path is the ONLY call site of
    // `cancelAnimationFrame` in this component, spying on the global is a
    // direct, non-brittle probe — any delta after unmount proves tweens
    // were cancelled.
    const domainNode = (tier: 'guarded' | 'critical') => ({
      id: 'd1',
      position: [0, 0, 0] as [number, number, number],
      color: '#b44aff',
      size: 2,
      opacity: 1,
      persistence: 1,
      state: 'domain' as const,
      label: 'backend',
      visible: true,
      coherence: 0.5,
      avgScore: 7,
      domain: 'backend',
      memberCount: 10,
      isSubDomain: false,
      readinessTier: tier,
    });

    _sceneOverride.value = { nodes: [domainNode('guarded')], edges: [] };

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

    const { container, unmount } = render(SemanticTopology);
    await new Promise((r) => setTimeout(r, 50));
    clustersStore.taxonomyTree = [...clustersStore.taxonomyTree];

    // Wait for the first ring build. Marker presence is the build-done signal.
    await vi.waitFor(() => {
      const marker = container.querySelector('[data-readiness-ring="d1"]');
      expect(marker).toBeTruthy();
      expect(marker?.getAttribute('data-readiness-tier')).toBe('guarded');
    });

    // Install spies AFTER the first build so RAF ids from the initial scene
    // setup don't pollute the capture. Track every RAF id allocated between
    // the tier flip and unmount — these are the candidate tween ids that
    // a correct cleanup must cancel.
    const rafSpy = vi.spyOn(window, 'requestAnimationFrame');
    const cancelSpy = vi.spyOn(window, 'cancelAnimationFrame');

    try {
      // Flip tier → triggers `tweenRingColor(...)` inside `rebuildScene`,
      // which immediately calls `requestAnimationFrame(step)` and stores the
      // returned `TweenHandle` on `_readinessRings.get('d1').tween`.
      _sceneOverride.value = { nodes: [domainNode('critical')], edges: [] };
      clustersStore.taxonomyTree = [...clustersStore.taxonomyTree];

      // Confirm the rebuild landed AND the tween actually started (RAF was
      // requested). If this fails, the test setup never armed a tween and a
      // "cancel didn't fire" assertion below would be meaningless.
      await vi.waitFor(() => {
        const marker = container.querySelector('[data-readiness-ring="d1"]');
        expect(marker?.getAttribute('data-readiness-tier')).toBe('critical');
        expect(rafSpy).toHaveBeenCalled();
      });

      const cancelCountBeforeUnmount = cancelSpy.mock.calls.length;

      // Unmount the component. Under the fix, the onMount cleanup iterates
      // `_readinessRings` and invokes `entry.tween?.cancel()` on each live
      // entry — each cancel() that finds a still-active RAF id calls
      // `cancelAnimationFrame(rafId)`. Under the bug, the cleanup disposes
      // the renderer WITHOUT cancelling tweens; cancelAnimationFrame is
      // never invoked for the tween RAF and the closure leaks.
      unmount();

      const cancelCountAfterUnmount = cancelSpy.mock.calls.length;

      // Under the fix: at least one cancelAnimationFrame call during unmount
      // (the in-flight tween's RAF). Under the bug: delta === 0.
      expect(cancelCountAfterUnmount).toBeGreaterThan(cancelCountBeforeUnmount);
    } finally {
      rafSpy.mockRestore();
      cancelSpy.mockRestore();
    }
  });

  it('preserves rendered color across rapid tier changes (no snap-back)', async () => {
    // Bug (I1): when a tier transition is superseded by a second tier change
    // BEFORE the first tween finishes, the new tween is built from
    // `readinessTierColor(existing.lastTier)` — i.e. the PURE hex of the
    // previous tier, not the material's currently-rendered interpolated
    // color. The next RAF then executes `material.color.copy(from).lerp(to, 0)`
    // which SNAPS the ring to `from` (the previous tier's pure color),
    // visible as a single-frame backward color flash before the new
    // transition begins.
    //
    // Contract: after a mid-flight supersede, tween 2's first step MUST NOT
    // reset the rendered color to the pure color of tier B. The new tween's
    // `from` has to be the material's live color at supersede time (a
    // mid-interpolation value between pure A and pure B), not `pureB`.
    //
    // This test controls `requestAnimationFrame` + `performance.now` so tween
    // 1 can be advanced to the middle of its duration, then drives tween 2's
    // first step and asserts the material is NOT at pure tier B.
    const THREE = await import('three');
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const ColorClass = (THREE as any).Color;
    const { readinessTierColor } = await import('./readiness-tier');

    // Controllable RAF queue — each `requestAnimationFrame(cb)` pushes into
    // `rafQueue` and returns an id; `drainRaf(now)` sets `performance.now()`
    // return value and invokes the queued callbacks in order. Matches the
    // existing `_tickFrame` pattern used elsewhere in this file.
    let currentNow = 1000;
    const rafQueue: Array<{ id: number; cb: (t: number) => void }> = [];
    let nextRafId = 1;
    const rafSpy = vi.spyOn(window, 'requestAnimationFrame').mockImplementation((cb) => {
      const id = nextRafId++;
      rafQueue.push({ id, cb: cb as (t: number) => void });
      return id;
    });
    const cancelSpy = vi.spyOn(window, 'cancelAnimationFrame').mockImplementation((id) => {
      const idx = rafQueue.findIndex((e) => e.id === id);
      if (idx >= 0) rafQueue.splice(idx, 1);
    });
    const perfSpy = vi.spyOn(performance, 'now').mockImplementation(() => currentNow);
    const drainRaf = (now: number) => {
      currentNow = now;
      const batch = rafQueue.splice(0);
      for (const entry of batch) entry.cb(now);
    };

    const domainNode = (tier: 'guarded' | 'critical' | 'ready') => ({
      id: 'd1',
      position: [0, 0, 0] as [number, number, number],
      color: '#b44aff',
      size: 2,
      opacity: 1,
      persistence: 1,
      state: 'domain' as const,
      label: 'backend',
      visible: true,
      coherence: 0.5,
      avgScore: 7,
      domain: 'backend',
      memberCount: 10,
      isSubDomain: false,
      readinessTier: tier,
    });

    _sceneOverride.value = { nodes: [domainNode('guarded')], edges: [] };

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

    try {
      const { container } = render(SemanticTopology);
      await new Promise((r) => setTimeout(r, 50));
      clustersStore.taxonomyTree = [...clustersStore.taxonomyTree];

      // Wait for initial build. Material starts at pure `guarded` color.
      await vi.waitFor(() => {
        const marker = container.querySelector('[data-readiness-ring="d1"]');
        expect(marker?.getAttribute('data-readiness-tier')).toBe('guarded');
      });

      // Reach the ring material via the scene capture.
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const lastScene = (globalThis as any).__semTopLastScene as
        | { children: unknown[] }
        | undefined;
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const ringGroup = lastScene!.children.find((c: any) =>
        c?.userData?.isReadinessRingGroup === true,
      ) as { children: unknown[] } | undefined;
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const ring = ringGroup!.children.find((c: any) =>
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        c instanceof (THREE as any).Mesh &&
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        c.geometry instanceof (THREE as any).RingGeometry,
      ) as { material: { color: { r: number; g: number; b: number } } };
      expect(ring).toBeDefined();

      // Sanity: material starts at the pure guarded color.
      const pureGuarded = new ColorClass(readinessTierColor('guarded'));
      const pureCritical = new ColorClass(readinessTierColor('critical'));
      const pureReady = new ColorClass(readinessTierColor('ready'));
      expect(ring.material.color.r).toBeCloseTo(pureGuarded.r, 5);
      expect(ring.material.color.g).toBeCloseTo(pureGuarded.g, 5);
      expect(ring.material.color.b).toBeCloseTo(pureGuarded.b, 5);

      // --- Tween 1: guarded → critical ---------------------------------
      _sceneOverride.value = { nodes: [domainNode('critical')], edges: [] };
      clustersStore.taxonomyTree = [...clustersStore.taxonomyTree];

      await vi.waitFor(() => {
        const marker = container.querySelector('[data-readiness-ring="d1"]');
        expect(marker?.getAttribute('data-readiness-tier')).toBe('critical');
      });

      // Advance tween 1 to the MIDDLE of its 320ms duration. With
      // `_CUBIC(0.5) = 1 - 0.125 = 0.875`, the material is now ~87.5% of
      // the way from pure guarded to pure critical. It is not pure
      // critical (that would be t=1) and it is not pure guarded (t=0).
      drainRaf(1160); // start=1000 → t=(1160-1000)/320=0.5
      // Enqueue for next step (tween 1 requested another RAF because t<1)
      // is deliberately LEFT in the queue — the supersede below will
      // cancel it and queue tween 2 in its place.

      const midR = ring.material.color.r;
      const midG = ring.material.color.g;
      const midB = ring.material.color.b;
      // Mid color must actually be between the two pure tiers; if it's
      // equal to either, the test setup isn't exercising the tween and
      // the supersede assertion below would be vacuous.
      const distFromCritical = Math.hypot(
        midR - pureCritical.r,
        midG - pureCritical.g,
        midB - pureCritical.b,
      );
      const distFromGuarded = Math.hypot(
        midR - pureGuarded.r,
        midG - pureGuarded.g,
        midB - pureGuarded.b,
      );
      expect(distFromCritical).toBeGreaterThan(0.01);
      expect(distFromGuarded).toBeGreaterThan(0.01);

      // --- Supersede: critical → ready (tween 2) ------------------------
      // This mutates `existing.lastTier` to 'ready' inside rebuildScene.
      // Under the bug, tween 2 is built with `from = pureCritical` (the
      // previous lastTier's pure color), NOT the material's current
      // (midR,midG,midB). Tween 2 will then enqueue its first RAF step.
      _sceneOverride.value = { nodes: [domainNode('ready')], edges: [] };
      clustersStore.taxonomyTree = [...clustersStore.taxonomyTree];

      await vi.waitFor(() => {
        const marker = container.querySelector('[data-readiness-ring="d1"]');
        expect(marker?.getAttribute('data-readiness-tier')).toBe('ready');
      });

      // Drain the queued RAF at the SAME timestamp supersede captured as
      // its `start`, so tween 2's first step sees t=0. At t=0,
      // `material.color.copy(from).lerp(to, 0) === from`. Under the bug,
      // `from = pureCritical` — the material SNAPS backward. Under the
      // fix, `from` is the material's live color at supersede (≈mid),
      // so this step is a no-op visually.
      drainRaf(currentNow); // t=0 for tween 2

      // The critical assertion: material MUST NOT have snapped to pure
      // critical. If it did, `from` was sourced from `lastTier`'s pure
      // color instead of the live material color — the I1 bug.
      const postSnapDistFromCritical = Math.hypot(
        ring.material.color.r - pureCritical.r,
        ring.material.color.g - pureCritical.g,
        ring.material.color.b - pureCritical.b,
      );
      // Under the fix: material equals (midR,midG,midB) — far from pure
      // critical. Under the bug: material equals pureCritical exactly,
      // making `postSnapDistFromCritical ≈ 0`.
      expect(postSnapDistFromCritical).toBeGreaterThan(0.01);

      // Stronger: material should still be approximately the pre-supersede
      // mid color. This locks in the intended semantics — tween 2 must
      // start from the currently-rendered color, not from any pure hex.
      expect(ring.material.color.r).toBeCloseTo(midR, 5);
      expect(ring.material.color.g).toBeCloseTo(midG, 5);
      expect(ring.material.color.b).toBeCloseTo(midB, 5);

      // And neither pure color is the answer here.
      const postSnapDistFromReady = Math.hypot(
        ring.material.color.r - pureReady.r,
        ring.material.color.g - pureReady.g,
        ring.material.color.b - pureReady.b,
      );
      expect(postSnapDistFromReady).toBeGreaterThan(0.01);
    } finally {
      rafSpy.mockRestore();
      cancelSpy.mockRestore();
      perfSpy.mockRestore();
    }
  });

  it('rebuilds ring geometry when domain size changes', async () => {
    // Bug (I2): the Cycle 4 "smarter ring merge" reuse branch in
    // `rebuildScene` (around lines 548-563) keeps the existing ring mesh
    // when a domain's id is unchanged, and updates `lastTier` / position /
    // lookAt — but it never recreates the RingGeometry when `node.size`
    // changes. `node.size` reflects cluster size and evolves when
    // `member_count` grows (clusterPhysics reconciles base scale around
    // line ~750). Pre-refactor (Cycle 3) the ring was disposed and
    // rebuilt every rebuild, so radius always tracked size. The Cycle 4
    // reuse branch broke that invariant.
    //
    // Contract: after a rebuild where `node.size` changes for the same
    // domain id, the ring's geometry inner/outer radius MUST reflect the
    // new `size * READINESS_RING_RADIUS_FACTOR`. Under the fix, the reuse
    // branch tracks `lastSize` on `ReadinessRingEntry` and disposes +
    // recreates the geometry when `lastSize !== node.size`. Under the bug,
    // the mesh keeps its first-build geometry and the ring is visibly
    // drifted (undersized for grown clusters, oversized for shrunk).
    const THREE = await import('three');
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const RingGeometryClass = (THREE as any).RingGeometry;

    const READINESS_RING_RADIUS_FACTOR = 1.25; // must match component constant

    const domainNode = (size: number) => ({
      id: 'd1',
      position: [0, 0, 0] as [number, number, number],
      color: '#b44aff',
      size,
      opacity: 1,
      persistence: 1,
      state: 'domain' as const,
      label: 'backend',
      visible: true,
      coherence: 0.5,
      avgScore: 7,
      domain: 'backend',
      memberCount: 10,
      isSubDomain: false,
      readinessTier: 'guarded' as const,
    });

    // First build at size=1.0 → expected outerRadius ≈ 1.25
    _sceneOverride.value = { nodes: [domainNode(1.0)], edges: [] };

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

    await vi.waitFor(() => {
      expect(container.querySelectorAll('[data-readiness-ring="d1"]').length).toBe(1);
    });

    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const lastScene = (globalThis as any).__semTopLastScene as
      | { children: unknown[] }
      | undefined;
    expect(lastScene).toBeDefined();
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const ringGroup = lastScene!.children.find((c: any) =>
      c?.userData?.isReadinessRingGroup === true,
    ) as { children: unknown[] } | undefined;
    expect(ringGroup).toBeDefined();
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const findRing = () =>
      ringGroup!.children.find((c: any) =>
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        c instanceof (THREE as any).Mesh && c.geometry instanceof RingGeometryClass,
      ) as { geometry: { innerRadius: number; outerRadius: number } } | undefined;

    const ringBefore = findRing();
    expect(ringBefore).toBeDefined();
    // Sanity: initial geometry matches size=1.0 — inner = radius,
    // outer = radius + thickness (per RingGeometry ctor in rebuildScene).
    const READINESS_RING_THICKNESS = 0.05;
    expect(ringBefore!.geometry.innerRadius).toBeCloseTo(
      1.0 * READINESS_RING_RADIUS_FACTOR,
      5,
    );
    expect(ringBefore!.geometry.outerRadius).toBeCloseTo(
      1.0 * READINESS_RING_RADIUS_FACTOR + READINESS_RING_THICKNESS,
      5,
    );

    // Now mutate size to 2.0 and trigger a rebuild. Same domain id, so
    // the reuse branch fires (existing = _readinessRings.get('d1')).
    _sceneOverride.value = { nodes: [domainNode(2.0)], edges: [] };
    clustersStore.taxonomyTree = [...clustersStore.taxonomyTree];

    // Give the reactive rebuild a tick to land — the mesh remains in the
    // ring group (reuse branch doesn't re-parent), but under the fix its
    // `.geometry` is a freshly constructed RingGeometry.
    await new Promise((r) => setTimeout(r, 50));

    const ringAfter = findRing();
    expect(ringAfter).toBeDefined();
    // Under the fix: geometry was disposed + recreated with the new
    // radius (inner = 2.0 * 1.25 = 2.5, outer = 2.5 + 0.05 = 2.55).
    // Under the bug (I2): geometry is the original RingGeometry from
    // the first build, so innerRadius still equals 1.25 (the size=1.0
    // value) instead of 2.5.
    expect(ringAfter!.geometry.innerRadius).toBeCloseTo(
      2.0 * READINESS_RING_RADIUS_FACTOR,
      5,
    );
    expect(ringAfter!.geometry.outerRadius).toBeCloseTo(
      2.0 * READINESS_RING_RADIUS_FACTOR + READINESS_RING_THICKNESS,
      5,
    );
  });

  it('attenuates ring opacity by renderer.lodTier each frame', async () => {
    // Task 9: LOD attenuation. The per-frame callback registered via
    // `renderer.addAnimationCallback()` reads the public `renderer.lodTier`
    // getter and composes opacity from four multiplicands:
    //   opacity = LOD_OPACITY[tier]
    //           * READINESS_RING_OPACITY_FACTOR
    //           * node.opacity
    //           * dimFactor
    // This test exercises the single-domain (no-highlight) case, where
    // `dimFactor = 1.0` and `node.opacity = 1.0`, so the composed opacity
    // collapses to `LOD_OPACITY[tier] * READINESS_RING_OPACITY_FACTOR`:
    //   far  → 0.4 * 0.9 = 0.36
    //   mid  → 0.7 * 0.9 = 0.63
    //   near → 1.0 * 0.9 = 0.9
    // The LOD callback is the FINAL opacity writer per frame — it runs
    // after the dim-sweep `$effect` and supersedes it on every tick.
    // Cycle 5 originally asserted the raw LOD tier values (0.4 / 0.7 / 1.0);
    // Bug Cycle G's GREEN composed the remaining factors into the same
    // callback (so dim stops being clobbered), which updated the expected
    // values here. Expressed via constants (mirroring the dim×LOD test
    // below) so future opacity-factor tweaks propagate.
    //
    // Note on scope: this test asserts ONLY the opacity contract. It does
    // NOT assert anything about `lookAt` call counts — the GREEN agent is
    // free to either fold billboard re-orientation into the same LOD
    // callback or keep them separate. The pre-existing billboard test
    // (`re-orients ring meshes per animation frame, not just at build`)
    // already covers the lookAt invariant.
    const READINESS_LOD_OPACITY = { far: 0.4, mid: 0.7, near: 1.0 } as const;
    const READINESS_RING_OPACITY_FACTOR = 0.9;
    const THREE = await import('three');
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const RingGeometryClass = (THREE as any).RingGeometry;

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
    await new Promise((r) => setTimeout(r, 50));
    clustersStore.taxonomyTree = [...clustersStore.taxonomyTree];

    // Wait for the ring to be built.
    await vi.waitFor(() => {
      expect(container.querySelectorAll('[data-readiness-ring="d1"]').length).toBe(1);
    });

    // Reach into the scene to get the ring material. Same pattern as the
    // dim-in-lockstep test above — rings live inside the isReadinessRingGroup
    // tagged group, not at scene root.
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const lastScene = (globalThis as any).__semTopLastScene as
      | { children: unknown[] }
      | undefined;
    expect(lastScene).toBeDefined();
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const ringGroup = lastScene!.children.find((c: any) =>
      c?.userData?.isReadinessRingGroup === true,
    ) as { children: unknown[] } | undefined;
    expect(ringGroup).toBeDefined();
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const ring = ringGroup!.children.find((c: any) =>
      c instanceof (THREE as any).Mesh && c.geometry instanceof RingGeometryClass,
    ) as { material: { opacity: number } } | undefined;
    expect(ring).toBeDefined();

    // Flip to 'far' and tick. After the LOD callback fires, opacity must
    // be LOD_far * RING_FACTOR = 0.4 * 0.9 = 0.36, regardless of what the
    // dim-sweep $effect wrote earlier.
    _lodTierOverride.value = 'far';
    _tickFrame();
    expect(ring!.material.opacity).toBeCloseTo(
      READINESS_LOD_OPACITY.far * READINESS_RING_OPACITY_FACTOR,
      5,
    );

    // Flip to 'mid' and tick. opacity → 0.7 * 0.9 = 0.63.
    _lodTierOverride.value = 'mid';
    _tickFrame();
    expect(ring!.material.opacity).toBeCloseTo(
      READINESS_LOD_OPACITY.mid * READINESS_RING_OPACITY_FACTOR,
      5,
    );

    // Flip to 'near' and tick. opacity → 1.0 * 0.9 = 0.9 (fully lit, scaled
    // by the base ring factor).
    _lodTierOverride.value = 'near';
    _tickFrame();
    expect(ring!.material.opacity).toBeCloseTo(
      READINESS_LOD_OPACITY.near * READINESS_RING_OPACITY_FACTOR,
      5,
    );
  });

  it('composes dim factor with LOD opacity each frame', async () => {
    // Bug Cycle 6 (RED): the per-frame LOD callback (added in Cycle 5) writes
    // `entry.material.opacity = READINESS_LOD_OPACITY[tier]` for every ring,
    // overwriting the dim-sweep `$effect` (Bug Cycle C) that applied
    // `DOMAIN_DIM_FACTOR` to non-highlighted domains. The dim-sweep only runs
    // on rebuild / highlight-change, so after the very next frame tick the
    // non-highlighted ring is back to full LOD opacity — the dim is lost.
    //
    // Correct contract (RED locks it; GREEN picks the cleanest implementation):
    // the LOD callback must be the FINAL per-frame writer AND must compose
    // with both `node.opacity`, `READINESS_RING_OPACITY_FACTOR`, and
    // `DOMAIN_DIM_FACTOR`. i.e. per frame:
    //   opacity = LOD_OPACITY[tier] * node.opacity * RING_OPACITY_FACTOR * dimFactor
    // where `dimFactor = (highlighted && node.domain !== highlighted) ? 0.15 : 1.0`.
    //
    // This test differs from the existing dim-sweep-in-lockstep test in that
    // it ticks a frame AFTER highlighting — proving that the LOD callback
    // does not clobber the dim. It also differs from the existing LOD test
    // in that it renders TWO domains and checks dim composition, not just
    // bare tier attenuation.
    const THREE = await import('three');
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const RingGeometryClass = (THREE as any).RingGeometry;

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
        {
          id: 'd2',
          position: [5, 0, 0] as [number, number, number],
          color: '#ff4895',
          size: 2,
          opacity: 1,
          persistence: 1,
          state: 'domain',
          label: 'frontend',
          visible: true,
          coherence: 0.5,
          avgScore: 8,
          domain: 'frontend',
          memberCount: 12,
          isSubDomain: false,
          readinessTier: 'healthy' as const,
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
      {
        id: 'd2',
        label: 'frontend',
        state: 'domain',
        domain: 'frontend',
        member_count: 12,
        parent_id: null,
      } as any,
    ];

    const { container } = render(SemanticTopology);
    await new Promise((r) => setTimeout(r, 50));
    clustersStore.taxonomyTree = [...clustersStore.taxonomyTree];

    await vi.waitFor(() => {
      expect(container.querySelectorAll('[data-readiness-ring]').length).toBe(2);
    });

    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const lastScene = (globalThis as any).__semTopLastScene as
      | { children: unknown[] }
      | undefined;
    expect(lastScene).toBeDefined();
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const ringGroup = lastScene!.children.find((c: any) =>
      c?.userData?.isReadinessRingGroup === true,
    ) as { children: unknown[] } | undefined;
    expect(ringGroup).toBeDefined();
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const rings = ringGroup!.children.filter((c: any) =>
      c instanceof (THREE as any).Mesh && c.geometry instanceof RingGeometryClass,
    ) as Array<{ material: { opacity: number } }>;
    expect(rings.length).toBe(2);
    // Ring index follows `data.nodes` iteration order (group.add pushes).
    const ringBackend = rings[0];
    const ringFrontend = rings[1];

    // Highlight 'backend' (d1). d2 ('frontend') is non-highlighted and MUST
    // be dimmed by DOMAIN_DIM_FACTOR (= 0.15) on top of LOD + base factor.
    clustersStore.highlightedDomain = 'backend';

    // Wait for the dim-sweep $effect to apply the initial dim on rebuild /
    // highlight change. This proves the effect ran before we tick a frame.
    const RING_OPACITY_FACTOR = 0.9;
    const DIM = 0.15;
    await vi.waitFor(
      () => {
        // Immediately after the $effect runs (before any frame tick):
        //   d1 (highlighted): 1 * 0.9 = 0.9
        //   d2 (non-highlighted): 1 * 0.9 * 0.15 ≈ 0.135
        expect(ringFrontend.material.opacity).toBeCloseTo(RING_OPACITY_FACTOR * DIM, 5);
      },
      { timeout: 500 },
    );

    // --- THE BUG: tick one frame. Under current HEAD, the LOD callback
    // overwrites opacity to READINESS_LOD_OPACITY['near'] = 1.0 for BOTH
    // rings — the dim is lost. Under the fix, the LOD callback composes
    // with node.opacity, RING_OPACITY_FACTOR, and DOMAIN_DIM_FACTOR.
    _lodTierOverride.value = 'near';
    _tickFrame();

    // Highlighted ring ('backend'): LOD_near (1.0) * node.opacity (1) *
    // RING_OPACITY_FACTOR (0.9) * dimFactor (1.0) = 0.9.
    expect(ringBackend.material.opacity).toBeCloseTo(1.0 * 1 * RING_OPACITY_FACTOR * 1.0, 5);
    // Non-highlighted ring ('frontend'): must stay DIMMED after the frame.
    // LOD_near (1.0) * node.opacity (1) * RING_OPACITY_FACTOR (0.9) * DIM (0.15)
    // = 0.135. Under current HEAD opacity is 1.0 instead — the dim is gone.
    expect(ringFrontend.material.opacity).toBeCloseTo(
      1.0 * 1 * RING_OPACITY_FACTOR * DIM,
      5,
    );

    // --- LOD tier still attenuates under dim composition. Flip to 'far'
    // and tick: LOD_far = 0.4. Highlighted: 0.4 * 0.9 = 0.36. Dimmed:
    // 0.4 * 0.9 * 0.15 = 0.054.
    _lodTierOverride.value = 'far';
    _tickFrame();
    expect(ringBackend.material.opacity).toBeCloseTo(0.4 * RING_OPACITY_FACTOR, 5);
    expect(ringFrontend.material.opacity).toBeCloseTo(
      0.4 * RING_OPACITY_FACTOR * DIM,
      5,
    );
  });

  it('readiness ring respects brand directive — no glow, no shadow, no rounded corners', async () => {
    // Brand-guard contract: the `[data-readiness-ring]` DOM marker is a test
    // sentinel (display:none span), not a visual element. It must never gain
    // glow, drop-shadow, box-shadow, or rounded-corner styling — even if a
    // future maintainer is tempted to decorate it. Industrial cyberpunk:
    // 1px neon contours, zero effects.
    _sceneOverride.value = {
      nodes: [
        {
          id: 'd1',
          position: [0, 0, 0] as [number, number, number],
          color: '#b44aff',
          size: 1,
          opacity: 1,
          persistence: 0.8,
          state: 'domain',
          label: 'backend',
          visible: true,
          coherence: 0.8,
          avgScore: 7,
          domain: 'backend',
          memberCount: 30,
          isSubDomain: false,
          readinessTier: 'critical' as const,
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
        member_count: 30,
        parent_id: null,
      } as any,
    ];

    const { container } = render(SemanticTopology);
    await new Promise((r) => setTimeout(r, 50));
    clustersStore.taxonomyTree = [...clustersStore.taxonomyTree];

    let marker: Element | null = null;
    await vi.waitFor(() => {
      marker = container.querySelector('[data-readiness-ring="d1"]');
      expect(marker).toBeTruthy();
    });

    if (marker) {
      const style = window.getComputedStyle(marker);
      expect(style.filter).not.toContain('blur');
      expect(style.filter).not.toContain('drop-shadow');
      expect(style.boxShadow === '' || style.boxShadow === 'none').toBe(true);
      expect(style.borderRadius === '' || style.borderRadius === '0px').toBe(true);
    }
  });
});
