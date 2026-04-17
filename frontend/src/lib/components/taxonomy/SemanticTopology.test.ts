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
  class RingGeometry extends _GeomBase {}
  class MeshBasicMaterial {
    color = new Color();
    opacity = 1;
    transparent = false;
    dispose() {}
    constructor(params?: { opacity?: number; transparent?: boolean; color?: unknown }) {
      if (params?.opacity != null) this.opacity = params.opacity;
      if (params?.transparent != null) this.transparent = params.transparent;
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
  });

  afterEach(() => {
    _sceneOverride.value = null;
    _useRealBuildSceneData.value = false;
    _animationCallbacks.length = 0;
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
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const rings = sceneChildren.filter((c: any) =>
      c instanceof (THREE as any).Mesh && c.geometry instanceof RingGeometryClass,
    ) as Array<{ material: { opacity: number } }>;
    expect(rings.length).toBe(2);

    // The production ring-build loop at SemanticTopology.svelte:443 iterates
    // `data.nodes` in order, so rings[0] corresponds to d1 (first in the
    // scene override) and rings[1] corresponds to d2. `scene.add` on our
    // mocked scene preserves insertion order via `children.push`.
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
});
