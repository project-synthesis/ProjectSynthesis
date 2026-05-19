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

// Hoisted spies — exposed at module scope so the optimization-beam-wiring
// tests below can assert on them. `vi.hoisted` runs before vi.mock factories.
const { beamAcquireSpy, beamDisposeSpy, beamTerminateAllSpy, onBeamImpactSpy } = vi.hoisted(() => ({
  beamAcquireSpy: vi.fn().mockReturnValue(null),
  beamDisposeSpy: vi.fn(),
  beamTerminateAllSpy: vi.fn(),
  onBeamImpactSpy: vi.fn(),
}));

vi.mock('./BeamPool', () => {
  class BeamPool {
    group = { name: 'beam-pool', children: [] as unknown[], add() {}, remove() {} };
    acquire = beamAcquireSpy;
    update = vi.fn();
    terminateAll = beamTerminateAllSpy;
    dispose = beamDisposeSpy;
  }
  return { BeamPool };
});

vi.mock('./ClusterPhysics', () => {
  class ClusterPhysics {
    onBeamImpact = onBeamImpactSpy;
    setBaseScale = vi.fn();
    update = vi.fn();
    clear = vi.fn();
    isActive = vi.fn().mockReturnValue(false);
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
  // Sub-project D — ClusterBuilder extracts wireframe edges via
  // WireframeGeometry on its IcosahedronGeometry(1, 1) wireBase. The
  // mock just needs to be a disposable that LineSegments accepts.
  class WireframeGeometry extends _GeomBase {
    constructor(_source?: unknown) { super(); }
  }
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
  // MeshStandardMaterial is the canonical lit material per brand reference
  // (Pattern Graph 3D scope) — accepts `emissive` + `emissiveIntensity` +
  // `roughness` + `metalness`. Cluster fills + domain anchors use it.
  class MeshStandardMaterial {
    color = new Color();
    emissive = new Color();
    emissiveIntensity = 1;
    roughness = 0.5;
    metalness = 0.5;
    opacity = 1;
    transparent = false;
    dispose() {}
    constructor(params?: {
      color?: unknown;
      emissive?: unknown;
      emissiveIntensity?: number;
      roughness?: number;
      metalness?: number;
      opacity?: number;
      transparent?: boolean;
    }) {
      if (params?.opacity != null) this.opacity = params.opacity;
      if (params?.transparent != null) this.transparent = params.transparent;
      if (params?.emissiveIntensity != null) this.emissiveIntensity = params.emissiveIntensity;
      if (params?.roughness != null) this.roughness = params.roughness;
      if (params?.metalness != null) this.metalness = params.metalness;
      if (params?.color instanceof Color) {
        this.color.copy(params.color as Color);
      }
      if (params?.emissive instanceof Color) {
        this.emissive.copy(params.emissive as Color);
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
    castShadow = false;
    receiveShadow = false;
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
    // Neural Dust (canon F10) rotates this every frame via _removeDustAnim;
    // headless tests need the rotation field to exist so the callback
    // doesn't throw `Cannot read properties of undefined (reading 'y')`.
    rotation = { x: 0, y: 0, z: 0 };
    scale = { setScalar: () => {} };
    userData: Record<string, unknown> = {};
    material: unknown = null;
  }
  class Sprite {}
  class QuadraticBezierCurve3 {
    v0 = new Vector3(); v1 = new Vector3(); v2 = new Vector3();
    getPoint(_t: number, target?: Vector3) { return target ?? new Vector3(); }
  }
  // CanvasTexture used by canon F2 — radial-gradient glow texture cached
  // on globalThis.__semTopGlowTexture. Disposable.
  class CanvasTexture {
    constructor(public source: HTMLCanvasElement) {}
    dispose = () => {};
  }
  const AdditiveBlending = 1;
  const DoubleSide = 2;
  const FrontSide = 0;
  return {
    Vector3, Color, Quaternion, Group, IcosahedronGeometry, DodecahedronGeometry,
    EdgesGeometry, WireframeGeometry, RingGeometry, MeshBasicMaterial, MeshStandardMaterial, ShaderMaterial,
    Mesh, BufferAttribute, BufferGeometry, Float32BufferAttribute, LineBasicMaterial,
    LineDashedMaterial, PointsMaterial, LineSegments, Points, Sprite, QuadraticBezierCurve3,
    CanvasTexture, AdditiveBlending, DoubleSide, FrontSide,
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
    const groups = sceneChildren.filter((c: any) =>
      c instanceof GroupClass && c.userData?.isStructural === true,
    ) as Array<{
      userData: { isStructural?: boolean };
      position: { x: number };
      children: Array<{ material: { opacity: number } }>;
    }>;
    // d1 is at position [0,0,0]; d2 at [5,0,0]. Pick d1 by its x-coordinate
    // rather than relying on scene.children insertion order — domain groups
    // carry userData.isStructural = true (SemanticTopology.svelte:1041) but
    // their relative order in scene.children is implementation-detail and
    // no longer guaranteed after the userData.persistent refactor.
    const d1Group = groups.find((g) => g.position.x === 0) ?? groups[0];
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

  it.skip('cancels in-flight ring tweens on unmount [deferred — Sub-project D scoped tier-tween out of RingBuilder]', async () => {
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

  it.skip('preserves rendered color across rapid tier changes (no snap-back) [deferred — Sub-project D scoped tier-tween out of RingBuilder]', async () => {
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

  it.skip('rebuilds ring geometry when domain size changes [deferred — Sub-project D scoped size-drift rebuild out of RingBuilder]', async () => {
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
    // `coordinator.register('camera', ...)` reads the public `renderer.lodTier`
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

// ---------------------------------------------------------------------------
// Template ring pool — growable mesh pool for templated clusters (Task 19)
// ---------------------------------------------------------------------------
// Helper: build a minimal SceneNode-shaped cluster with `template_count` set.
// Fields must satisfy the SceneNode shape enough for rebuildScene not to crash.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const _makeClusterNode = (id: string, templateCount: number, color = '#36b5ff'): any => ({
  id,
  position: [Math.random() * 20 - 10, Math.random() * 20 - 10, 0] as [number, number, number],
  color,
  size: 1,
  opacity: 1,
  persistence: 1,
  state: 'active',
  label: id,
  visible: true,
  coherence: 0.6,
  avgScore: 7,
  domain: 'backend',
  memberCount: 5,
  isSubDomain: false,
  template_count: templateCount,
});

// Helper: mount the component and wait for templateRings to appear.
// Returns the scene reference captured by the TopologyRenderer mock.
async function _mountWithClusters(
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  nodes: any[],
): Promise<{
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  lastScene: any;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  templateRingPool: any;
}> {
  _sceneOverride.value = { nodes, edges: [] };
  const { clustersStore } = await import('$lib/stores/clusters.svelte');
  clustersStore.taxonomyTree = nodes.map(
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (n: any) =>
      ({
        id: n.id,
        label: n.label ?? n.id,
        state: n.state ?? 'active',
        domain: n.domain ?? 'backend',
        member_count: n.memberCount ?? 5,
        parent_id: null,
      }) as any,
  );

  render(SemanticTopology);
  await new Promise((r) => setTimeout(r, 50));
  // Nudge the $effect
  clustersStore.taxonomyTree = [...clustersStore.taxonomyTree];
  await new Promise((r) => setTimeout(r, 50));

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const lastScene = (globalThis as any).__semTopLastScene;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const templateRingPool = (globalThis as any).__semTopTemplateRingPool;
  return { lastScene, templateRingPool };
}

// Collect all template ring meshes from the scene (tagged with userData.kind === 'template_ring').
// eslint-disable-next-line @typescript-eslint/no-explicit-any
function _collectTemplateRings(lastScene: any): any[] {
  if (!lastScene) return [];
  const templateRings: unknown[] = [];
  // Walk depth-1 children plus any group children.
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const walk = (node: any) => {
    if (node?.userData?.kind === 'template_ring') templateRings.push(node);
    if (Array.isArray(node?.children)) {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      for (const child of node.children) walk(child);
    }
  };
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  for (const child of lastScene.children as any[]) walk(child);
  return templateRings as any[];
}

describe('SemanticTopology — template ring pool (Task 19)', () => {
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
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (globalThis as any).__semTopTemplateRingPool = undefined;
  });

  afterEach(() => {
    _sceneOverride.value = null;
    _useRealBuildSceneData.value = false;
    _animationCallbacks.length = 0;
    _lodTierOverride.value = 'near';
  });

  it('renders a template ring for clusters with template_count > 0 and not for template_count === 0', async () => {
    const nodes = [
      _makeClusterNode('c1', 2, '#b44aff'), // has templates → gets template ring
      _makeClusterNode('c2', 0, '#ff4895'), // no templates → no template ring
    ];
    const { lastScene } = await _mountWithClusters(nodes);

    // Template ring pool global must be exposed in test mode
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    expect((globalThis as any).__semTopTemplateRingPool).toBeDefined();

    const templateRings = _collectTemplateRings(lastScene);
    // Only c1 gets a template ring
    expect(templateRings.length).toBe(1);
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const h = templateRings[0] as any;
    expect(h.userData.clusterId).toBe('c1');
    expect(h.visible).toBe(true);
  });

  it('grows pool beyond initial 50-mesh capacity on demand', async () => {
    // 75 templated clusters → pool must grow from initial 50 to 100 (one 50-chunk)
    const nodes = Array.from({ length: 75 }, (_, i) =>
      _makeClusterNode(`c${i}`, 1),
    );
    const { templateRingPool } = await _mountWithClusters(nodes);
    expect(templateRingPool).toBeDefined();
    // Pool retains high-water mark — must be ≥75, should be exactly 100 (50 + one 50-chunk)
    expect(templateRingPool.length).toBeGreaterThanOrEqual(75);
    expect(templateRingPool.length).toBeLessThanOrEqual(100);
  });

  it('warns when template ring pool exceeds 500-mesh cap', async () => {
    const warnSpy = vi.spyOn(console, 'warn');
    try {
      const nodes = Array.from({ length: 510 }, (_, i) =>
        _makeClusterNode(`c${i}`, 1),
      );
      await _mountWithClusters(nodes);
      expect(warnSpy).toHaveBeenCalled();
      // Warning message should mention the pool cap
      const warned = warnSpy.mock.calls.some((args) =>
        String(args[0]).includes('template ring pool'),
      );
      expect(warned).toBe(true);
    } finally {
      warnSpy.mockRestore();
    }
  });

  it('retains pool high-water mark after cluster count shrinks', async () => {
    // First render: 100 templated clusters → pool grows to ≥100
    const bigNodes = Array.from({ length: 100 }, (_, i) =>
      _makeClusterNode(`c${i}`, 1),
    );
    const { templateRingPool: poolAfterBig } = await _mountWithClusters(bigNodes);
    const highWater = poolAfterBig.length;
    expect(highWater).toBeGreaterThanOrEqual(100);

    // Re-render the same component instance with only 20 templated clusters.
    // We trigger a second scene rebuild by mutating the store.
    const { clustersStore } = await import('$lib/stores/clusters.svelte');
    _sceneOverride.value = {
      nodes: Array.from({ length: 20 }, (_, i) => _makeClusterNode(`c${i}`, 1)),
      edges: [],
    };
    clustersStore.taxonomyTree = [...clustersStore.taxonomyTree];
    await new Promise((r) => setTimeout(r, 50));

    // Pool must not shrink — high-water mark is retained.
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const poolAfterShrink = (globalThis as any).__semTopTemplateRingPool;
    expect(poolAfterShrink).toBeDefined();
    expect(poolAfterShrink.length).toBeGreaterThanOrEqual(highWater);
  });

  it('template ring color matches the cluster node color (same live color)', async () => {
    const clusterColor = '#b44aff';
    const nodes = [_makeClusterNode('c1', 1, clusterColor)];
    const { lastScene } = await _mountWithClusters(nodes);

    const templateRings = _collectTemplateRings(lastScene);
    expect(templateRings.length).toBe(1);
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const templateRing = templateRings[0] as any;
    const THREE = await import('three');
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const MeshBasicMaterialClass = (THREE as any).MeshBasicMaterial;
    expect(templateRing.material).toBeInstanceOf(MeshBasicMaterialClass);

    // The color on the template ring material must match `parseInt(clusterColor.replace('#',''),16)`.
    const expectedHex = parseInt(clusterColor.replace('#', ''), 16);
    const expectedR = ((expectedHex >> 16) & 0xff) / 255;
    const expectedG = ((expectedHex >> 8) & 0xff) / 255;
    const expectedB = (expectedHex & 0xff) / 255;

    expect(templateRing.material.color.r).toBeCloseTo(expectedR, 4);
    expect(templateRing.material.color.g).toBeCloseTo(expectedG, 4);
    expect(templateRing.material.color.b).toBeCloseTo(expectedB, 4);
  });

  it('template ring and node color are set in the same rebuildScene pass', async () => {
    // Contract: _syncTemplateRings runs inside rebuildScene (not in a separate $effect
    // tick), so after a rebuild both the cluster mesh and the template ring mesh must
    // already reflect the node's color — no extra tick required.
    const clusterColor = '#ff4895';
    const nodes = [_makeClusterNode('c1', 1, clusterColor)];
    const { lastScene } = await _mountWithClusters(nodes);

    const templateRings = _collectTemplateRings(lastScene);
    expect(templateRings.length).toBe(1);
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const templateRing = templateRings[0] as any;

    // Node mesh is stored in nodeMeshes.  The Three.js mock Mesh captures
    // the fill material as the first child of the group.  Walk the scene
    // to find a Group whose fill.userData does NOT have 'kind: template ring',
    // confirming both are present after a single build (no extra tick).
    const THREE = await import('three');
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const GroupClass = (THREE as any).Group;
    const groups = (lastScene?.children ?? []).filter(
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      (c: any) => c instanceof GroupClass,
    );
    // At least one non-template-ring group should exist (the cluster node group)
    expect(groups.length).toBeGreaterThan(0);

    // Both the group (node) and the template ring are in scene after one build pass.
    expect(templateRing.visible).toBe(true);
    // Template ring color matches what was provided — same source as node color
    const expectedHex = parseInt(clusterColor.replace('#', ''), 16);
    const expectedR = ((expectedHex >> 16) & 0xff) / 255;
    expect(templateRing.material.color.r).toBeCloseTo(expectedR, 4);
  });
});

// ─────────────────────────────────────────────────────────────────────────
// Optimization beam wiring (Data-as-Matter spec)
//
// Spec: docs/specs/2026-04-05-data-as-matter-design.md "Trigger Mapping"
// (Status: Shipped). When prompts are optimized live, plasma beams stream
// from the camera-attached emitter into target cluster nodes. The wiring
// chain is:
//   1. Backend SSE event (`optimization_created`, `seed_batch_progress`)
//   2. routes/app/+page.svelte dispatches a window CustomEvent
//      ('optimization-event' / 'seed-batch-progress')
//   3. SemanticTopology effect snapshots `_prevNodeSizes` (only when
//      `detail.status === 'completed'` for optimization-event; always
//      for seed-batch-progress) and sets `_seedBatchActive` for seed
//   4. Next `rebuildScene` (triggered by taxonomy_changed SSE updating
//      clustersStore.taxonomyTree) compares new sizes vs snapshot;
//      fires `beamPool.acquire` + `clusterPhysics.onBeamImpact` for any
//      domain node that grew
//
// These tests cover items 3 + 4 — the snapshot + rebuild-driven beam
// firing path. Items 1 + 2 (SSE → window event) are covered by
// routes/app/page.tier-trigger.test.ts and the SSE replay tests.
//
// One known divergence from the strict spec: the implementation filters
// to `node.state === 'domain'` only (noise reduction for batch seeds —
// firing 50 cluster beams in a 50-prompt batch would be visually
// overwhelming). The spec wording was "assigned cluster" but in practice
// firing at the domain is the design call. Documented inline at the
// rebuildScene block.
// ─────────────────────────────────────────────────────────────────────────

describe('SemanticTopology — optimization beam wiring (Data-as-Matter)', () => {
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
  });

  /** Advance the formation animation past completion. The entrance/auto-focus/
   *  growth-detection blocks all live inside the per-frame formation callback
   *  that fires when `formProgress >= formDuration (90 frames)`. Without ticking,
   *  none of the beam logic executes. */
  async function _tickPastFormation() {
    // formDuration = 90 frames; tick a few extra to land in the t >= 1.0 branch.
    for (let i = 0; i < 95; i++) _tickFrame();
    // Allow setTimeout-staggered entrance beams + microtask flushes to settle.
    await new Promise((r) => setTimeout(r, 350));
  }

  /** Read SemanticTopology.svelte source for source-grep wiring assertions
   *  via Vite's `import.meta.glob ?raw`. Same mechanism used by
   *  brand-compliance.test.ts + cleanup-contract.test.ts. */
  const _semTopSourceGlob = import.meta.glob<string>(
    ['./SemanticTopology.svelte'],
    { query: '?raw', import: 'default', eager: true },
  );
  function _semTopSrc(): string {
    const src = _semTopSourceGlob['./SemanticTopology.svelte'];
    if (typeof src !== 'string') {
      throw new Error('Data-as-Matter wiring tests: SemanticTopology.svelte not in glob map');
    }
    return src;
  }

  function _envelopePoolSrc(): string {
    const mod = import.meta.glob<string>(['./EnvelopePool.ts'], {
      query: '?raw',
      import: 'default',
      eager: true,
    });
    return mod['./EnvelopePool.ts'] ?? '';
  }

  /** Build a minimal domain scene with one domain node at the given size. */
  function _makeDomainScene(domainId: string, size: number) {
    return {
      nodes: [
        {
          id: domainId,
          position: [0, 0, 0] as [number, number, number],
          color: '#b44aff',
          size,
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
        },
      ],
      edges: [],
    };
  }

  function _makeDomainTreeNode(id: string, memberCount: number) {
    return {
      id,
      label: 'backend',
      state: 'domain',
      domain: 'backend',
      member_count: memberCount,
      parent_id: null,
    };
  }

  it('source: entrance burst block exists in source (Navigate trigger per spec)', () => {
    const src = _semTopSrc();
    // Per Data-as-Matter spec § Trigger Mapping: "Navigate (view enter)" fires
    // one beam per existing cluster, staggered 30ms apart, sustain 200ms each.
    // Implementation uses a stagger of i * 150ms and longer sustain (production
    // tuned via UX testing). The `_hasPlayedEntrance` one-shot guard prevents
    // re-firing on subsequent rebuilds. Source assertions instead of full
    // integration test because the formation-animation tick + setTimeout
    // staggers + Svelte component lifecycle make timing-fragile assertions
    // unreliable in jsdom.
    expect(src).toMatch(/_hasPlayedEntrance/);
    expect(src).toMatch(/_hasPlayedEntrance\s*=\s*true/);
    // Filter to domain nodes (entrance burst targets domain anchors only).
    expect(src).toMatch(/state\s*===\s*['"]domain['"]/);
    // Post-Sub-project-C: the entrance burst routes through
    // `impactCoordinator.fire(...)` with `trigger: 'entrance'` from inside
    // a staggered setTimeout block. The preset table owns the tactile
    // feedback (kineticDisplacement) per TRIGGER_PRESETS.entrance.
    expect(src).toMatch(/setTimeout\([\s\S]*?impactCoordinator[?!]?\.fire/);
    expect(src).toMatch(/impactCoordinator[?!]?\.fire\(\s*\{[\s\S]*?trigger\s*:\s*['"]entrance['"]/);
  });

  // Source-grep gates for the rest of the SSE → beam wiring chain.
  //
  // Why source-grep instead of integration tests for the optimization-event
  // and seed-batch-progress paths: the growth-detection logic runs inside
  // the formation animation completion handler. A second rebuild kicks off
  // a second formation, and the test environment's component lifecycle
  // doesn't always re-honor the `_hasPlayedEntrance` guard cleanly across
  // rebuilds — making integration tests of the second-rebuild path
  // timing-fragile. Source-grep gates assert the wiring exists and would
  // catch a regression that removes the listener / snapshot / growth-
  // detection code without depending on test-environment lifecycle quirks.
  // The first-rebuild entrance burst IS integration-tested above (passes).

  it('source: window.addEventListener is wired for optimization-event', () => {
    const src = _semTopSrc();
    expect(src).toMatch(/window\.addEventListener\(['"]optimization-event['"]/);
    expect(src).toMatch(/window\.removeEventListener\(['"]optimization-event['"]/);
  });

  it('source: window.addEventListener is wired for seed-batch-progress', () => {
    const src = _semTopSrc();
    expect(src).toMatch(/window\.addEventListener\(['"]seed-batch-progress['"]/);
    expect(src).toMatch(/window\.removeEventListener\(['"]seed-batch-progress['"]/);
  });

  it('source: optimization-event handler gates on detail.status === "completed"', () => {
    const src = _semTopSrc();
    // The snapshot step must filter to actual completions to avoid
    // false-positive beams on failed optimizations.
    expect(src).toMatch(/detail\?\.status\s*!==\s*['"]completed['"]/);
  });

  it('source: optimization-event immediately fires live beam to assigned domain', () => {
    const src = _semTopSrc();
    // Instead of waiting for a post-rebuild growth check, single optimize
    // fires a beam immediately on the event to provide real-time synthesis feel.
    // Post-Sub-project-C the immediate beam routes through
    // `impactCoordinator.fire(...)` with `trigger: 'optimization'`.
    expect(src).toMatch(/targetDomain\s*=\s*detail\.domain\s*\?\s*parsePrimaryDomain\(detail\.domain\)\s*:\s*['"]general['"]/);
    expect(src).toMatch(/targetNode\s*=\s*sceneData\?\.nodes\.find/);
    expect(src).toMatch(/impactCoordinator[?!]?\.fire\(\s*\{[\s\S]*?trigger\s*:\s*['"]optimization['"]/);
  });

  it('source: snapshot writes _prevNodeSizes from current scene node sizes during seed batch', () => {
    const src = _semTopSrc();
    // onSeedProgress should snapshot all current sizes into _prevNodeSizes 
    // for later growth comparison.
    expect(src).toMatch(/_prevNodeSizes\.clear\(\)/);
    expect(src).toMatch(/_prevNodeSizes\.set\([^,]+,\s*[a-zA-Z_$][\w.$]*\.size\)/);
  });

  it('source: growth-detection block fires beamPool.acquire + onBeamImpact when prev size is exceeded', () => {
    const src = _semTopSrc();
    // The block must compare prev vs current and fire beam only when grown.
    expect(src).toMatch(/_prevNodeSizes\.get\([^)]+\)/);
    expect(src).toMatch(/node\.size\s*>\s*prevSize/);
    // After firing the beams it must clear the snapshot to avoid replaying.
    expect(src).toMatch(/_prevNodeSizes\.clear\(\)/);
  });

  it('source: seed-batch path uses bigger radius + longer sustain than single-optimize', () => {
    // Post-Sub-project-C: the radius multiplier + base sustain knobs live
    // in `TRIGGER_PRESETS['post-growth']` inside ImpactCoordinator.ts (the
    // single source of trigger semantics). SemanticTopology fires the
    // trigger; the preset table owns the values. Assert both the
    // SemanticTopology fire-site uses the 'post-growth' trigger AND the
    // preset table has the expected magic numbers (thicknessMultiplier:
    // 2.0 + sustainMs: 3500).
    const src = _semTopSrc();
    expect(src).toMatch(/impactCoordinator[?!]?\.fire\(\s*\{[\s\S]*?trigger\s*:\s*['"]post-growth['"]/);
    const mod = import.meta.glob<string>(['./ImpactCoordinator.ts'], {
      query: '?raw',
      import: 'default',
      eager: true,
    });
    const impactSrc = mod['./ImpactCoordinator.ts'];
    expect(typeof impactSrc).toBe('string');
    // Preset entry shape: thickness 2.0 + sustain 3500 (with sizeFactorSustainBonus).
    expect(impactSrc).toMatch(/['"]post-growth['"]\s*:\s*\{[\s\S]*?thicknessMultiplier\s*:\s*2\.0/);
    expect(impactSrc).toMatch(/['"]post-growth['"]\s*:\s*\{[\s\S]*?sustainMs\s*:\s*3500/);
  });

  it('source: growth-detection filters to domain-only nodes (documented design call)', () => {
    const src = _semTopSrc();
    // Per Data-as-Matter spec § Trigger Mapping: the strict reading was
    // "fire at the assigned cluster". Implementation filters to domain-
    // only as a noise reduction (firing 50 cluster beams during a 50-
    // prompt batch would be visually overwhelming). The filter is
    // intentional — assert it is documented in source so it is not
    // accidentally relaxed.
    expect(src).toMatch(/if\s*\(node\.state\s*!==\s*['"]domain['"]\)\s*continue/);
  });

  it('source: handleLodChange uses lightweight visibility update — no unconditional rebuildScene', () => {
    const src = _semTopSrc();
    // Click-zoom bug fix: handleLodChange must NOT call rebuildScene on
    // every LOD tier change. The previous implementation triggered a
    // full rebuild mid-focus-animation (when the camera crossed an LOD
    // boundary), producing the visible "reset and zoom again" artifact
    // the user reported.
    //
    // Verifies the gated rebuild pattern: rebuildScene only fires when
    // `needsFullRebuild` is true (a newly-visible node has no mesh).
    const handleLodChangeMatch = src.match(
      /function\s+handleLodChange\s*\([^)]*\)\s*:\s*void\s*\{[\s\S]*?\n\s\s\}/,
    );
    expect(handleLodChangeMatch).not.toBeNull();
    if (!handleLodChangeMatch) return;
    const body = handleLodChangeMatch[0];
    // Must declare the gate flag.
    expect(body).toMatch(/needsFullRebuild\s*=\s*false/);
    // Must do per-mesh visibility toggling.
    expect(body).toMatch(/nodeMeshes\.get\([^)]+\)/);
    expect(body).toMatch(/\.parent\s*as\s*THREE\.Group\)\.visible\s*=\s*node\.visible/);
    // Must gate the rebuildScene call on the flag (prevents unconditional rebuild).
    expect(body).toMatch(/if\s*\(\s*needsFullRebuild\s*\)\s*\{\s*\n\s*rebuildScene\(sceneData\)/);
  });

  // Cycle 3: SemanticTopology wiring + causal-ordering bug fix.
  //
  // The existing click chain at SemanticTopology.svelte:1876-1898 fires
  // `clusterPhysics?.onBeamImpact(node.id, ...)` synchronously with the
  // beam acquire. The beam takes `FIRING_MS` (~700ms) to travel, so the cluster ripples
  // BEFORE the beam visually arrives. Cycle 3 migrates all impact reactions
  // (ripple, plasma envelope, emissive flash) into a single `onImpact`
  // callback passed to `beamPool.acquire` — it fires at the firing→sustain
  // edge, synchronizing every reaction to actual beam arrival.
  //
  // Source-grep regression tests pin the wiring shape so future refactors
  // can't accidentally re-introduce the synchronous (anti-causal) call.

  it('source: envelopePool is declared at module scope and imported from EnvelopePool', () => {
    const src = _semTopSrc();
    expect(src).toMatch(/import\s*\{[^}]*EnvelopePool[^}]*\}\s*from\s*['"]\.\/EnvelopePool['"]/);
    expect(src).toMatch(/let\s+envelopePool\s*:\s*EnvelopePool\s*\|\s*null\s*=\s*null/);
  });

  // DELETED (Sub-project B / spec §5.2.2 M3): `_removeEnvelopeUpdate` symbol
  // absorbed into `coordinator.dispose()`. Replacement coverage: cleanup-contract
  // test #6 asserts `coordinator?.dispose()` appears in cleanup return.

  it('source: envelopePool constructed in onMount + group added to scene', () => {
    const src = _semTopSrc();
    expect(src).toMatch(/envelopePool\s*=\s*new\s+EnvelopePool\(\)/);
    expect(src).toMatch(/renderer[!?]?\.scene\.add\(envelopePool[!?]?\.group\)/);
  });

  it("source: envelopePool.update wired via coordinator.register('impact', ...)", () => {
    const src = _semTopSrc();
    // Post-Sub-project B (spec §5.2.2 M3): envelope per-frame work is now
    // registered through the AnimationCoordinator in the `impact` phase.
    // Anchor on coordinator.register('impact', ...) call referencing
    // `envelopePool.update`.
    expect(src).toMatch(
      /coordinator\.register\(\s*['"]impact['"][\s\S]{0,300}envelopePool[?!]?\.update/,
    );
  });

  it('source: EnvelopePool.group survives rebuildScene via userData.persistent flag (orphan-group regression, new mechanism)', () => {
    // Operator-reported symptom history: "only two clusters get the envelope"
    // (commit 025b0048 fix). Root cause was rebuildScene's indiscriminate
    // while-loop wiping persistent groups along with ephemerals.
    //
    // The cleanupScene helper now reads userData.persistent to preserve
    // pool groups across the rebuild cycle. EnvelopePool sets the flag
    // in its constructor. This test pins both surfaces.
    //
    // Spec: docs/superpowers/specs/2026-05-16-lifecycle-hardening-design.md §5.3
    const envSrc = _envelopePoolSrc(); // Add this helper alongside _semTopSrc
    expect(envSrc).toMatch(/this\.group\.userData\.persistent\s*=\s*true/);

    const src = _semTopSrc();
    const rebuildIdx = src.indexOf('function rebuildScene(');
    expect(rebuildIdx).toBeGreaterThan(0);
    const handleLodIdx = src.indexOf('function handleLodChange(', rebuildIdx);
    const slice = src.slice(rebuildIdx, handleLodIdx > rebuildIdx ? handleLodIdx : rebuildIdx + 80000);

    // rebuildScene uses the helper; no manual save/restore
    expect(slice).toMatch(/cleanupScene\(/);
    expect(slice).not.toMatch(/scene\.remove\(\s*envelopePool\.group\s*\)/);
    expect(slice).not.toMatch(/^\s*renderer\.scene\.add\(\s*envelopePool\.group\s*\)/m);
  });

  it('source: click $effect fires impactCoordinator.fire with click trigger (causal-ordering fix)', () => {
    const src = _semTopSrc();
    // Post-Sub-project-C: the click selection $effect routes through
    // `impactCoordinator.fire({ trigger: 'click', ... })` (canon F7 + F18
    // + F19 causal-ordering). The coordinator's onImpact callback fires
    // every F19 reaction synchronously with beam arrival; SemanticTopology
    // no longer touches beamPool.acquire directly. Equivalent end-to-end
    // coverage: ImpactCoordinator.test.ts §5.1 #6 (click preset semantics).
    expect(src).toMatch(
      /impactCoordinator[?!]?\.fire\(\s*\{[\s\S]*?trigger\s*:\s*['"]click['"]/,
    );
  });

  it('source: entrance materialization beams route through impactCoordinator.fire with trigger: "entrance" (spec §5.3 M3 Row 1 — anchor moved from _triggerBeamImpact helper to coordinator fire site)', () => {
    const src = _semTopSrc();
    // Regression guard: pre-Sub-project-C the entrance burst either had an
    // empty onImpact callback (only clicked/optimized clusters got the
    // plasma envelope + emissive glow) OR fired through the
    // `_triggerBeamImpact` helper. Post-Sub-project-C the entrance burst
    // routes through `impactCoordinator.fire(...)` with `trigger: 'entrance'`
    // — the preset table owns the kineticDisplacement=false + sustainMs +
    // sizeFactor knobs (TRIGGER_PRESETS.entrance in ImpactCoordinator.ts).
    // Equivalent runtime coverage: ImpactCoordinator.test.ts §5.1 #3
    // (entrance preset semantics).
    const entranceStart = src.indexOf('_hasPlayedEntrance = true');
    expect(entranceStart).toBeGreaterThan(0);
    // Slice ~2000 chars from that anchor — covers the forEach setTimeout block.
    const entranceSlice = src.slice(entranceStart, entranceStart + 2000);
    // The fire(...) call with trigger: 'entrance' must appear inside the
    // entrance setTimeout body. Use a [\s\S] regex so JSDoc/whitespace
    // between `fire(` and the `trigger:` field doesn't trip the match.
    expect(entranceSlice).toMatch(
      /impactCoordinator[?!]?\.fire\(\s*\{[\s\S]*?trigger\s*:\s*['"]entrance['"]/,
    );
  });

  it('source: clusterPhysics.onBeamImpact is NOT called synchronously inside the click $effect', () => {
    const src = _semTopSrc();
    // Locate the click selection $effect — anchored on the comment
    // "Sync external family selection".
    const effectStart = src.indexOf('Sync external family selection');
    expect(effectStart).toBeGreaterThan(0);
    const effectBody = src.slice(effectStart, effectStart + 4000);

    // Post-Sub-project-C: the effect routes through impactCoordinator.fire
    // (the coordinator's onImpact callback owns the F19 kinetic-shake
    // call). The effect body itself must NEVER call clusterPhysics
    // synchronously — that would precede beam arrival by ~700ms.
    const fireIdx = effectBody.indexOf('impactCoordinator');
    expect(fireIdx).toBeGreaterThan(0);
    const preFire = effectBody.slice(0, fireIdx);
    expect(preFire).not.toMatch(/clusterPhysics[?!]?\.onBeamImpact/);
  });

  // DELETED (Sub-project C / spec §5.3 M3 Row 2): `source: onImpact callback
  // body fires envelopePool.acquire + flashEmissive (passive inspection, no
  // kinetic shake)`. The `_triggerBeamImpact` helper is gone; the onImpact
  // chain end-to-end coverage moves to ImpactCoordinator.test.ts §5.1 #3
  // (entrance preset — onImpact does NOT fire clusterPhysics.onBeamImpact),
  // #8 (onImpact F7 causal-order — physics → envelope → flash → engulfed),
  // and #13 (envelope acquired with raw freshNode.size, no floor).

  it('source: envelope shape literal — "domain" for state==="domain", else "cluster" — lives in ImpactCoordinator.fire onImpact body (spec §5.3 M3 Row 3 — anchor moved from _triggerBeamImpact to coordinator)', () => {
    // Shape selection now lives inside ImpactCoordinator.ts's `fire(...)`
    // body (the DRY single-source helper post-Sub-project-C), not in the
    // deleted `_triggerBeamImpact` helper. The ternary assigns to `shape`
    // before the onImpact callback closes over it.
    const mod = import.meta.glob<string>(['./ImpactCoordinator.ts'], {
      query: '?raw',
      import: 'default',
      eager: true,
    });
    const src = mod['./ImpactCoordinator.ts'];
    expect(typeof src).toBe('string');
    expect(src).toMatch(
      /freshNode\.state\s*===\s*['"]domain['"]\s*\?\s*['"]domain['"]\s*:\s*['"]cluster['"]/,
    );
  });

  // Sub-project E (spec §5.5 M3) — DELETED 4 source-grep tests anchored on
  // `_flashStates`, `flashEmissive`, `_tickFlashStates` 3-phase lifecycle,
  // and the `GLOW_TOTAL_MS` cleanup branch. The state map + per-frame tick
  // + attack/hold/decay state machine migrated to `FlashController.ts`.
  // Replacement coverage: FlashController.test.ts #2 (state map shape),
  // #4-#8 (3-phase tick + cleanup branch), #9 + #11 (flash() + baseline
  // capture). Cross-component wiring covered by SC.integration.test.ts INT-4
  // (flash + idle pulse don't compete) + cleanup-contract.test.ts §Sub-project-E
  // tests #2 + #14 (FC export + breathing handler reads SC.isFlashActive).

  it("source: FlashController._tick wired via animationCoordinator.register('impact', ...) in FC constructor (spec §5.5 M3 Row — anchor moved from SemanticTopology.svelte._tickFlashStates to FC.ts)", () => {
    // Sub-project E: flash per-frame tick migrated from
    // `_tickFlashStates` in SemanticTopology.svelte to `FlashController._tick`
    // in `FlashController.ts`. The register-site moved INSIDE FC's
    // constructor, where it calls `deps.animationCoordinator.register('impact', ...)`.
    // The dep wrapper preserves the canon impact-phase ordering invariant
    // (beam → envelope → flash) when SC constructs FC.
    const mod = import.meta.glob<string>(['./FlashController.ts'], {
      query: '?raw',
      import: 'default',
      eager: true,
    });
    const src = mod['./FlashController.ts'];
    expect(typeof src).toBe('string');
    expect(src as string).toMatch(
      /deps\.animationCoordinator\.register\(\s*['"]impact['"][\s\S]{0,300}this\._tick/,
    );
  });

  it('source: cleanup return invokes selectionController?.dispose() (spec §5.5 M3 Row — anchor migrated from coordinator?.dispose()→_flashStates.clear() ordering to SC.dispose() presence + position)', () => {
    // Sub-project E: `_flashStates.clear()` deleted from cleanup body —
    // `FlashController.dispose()` (invoked transitively via
    // `selectionController?.dispose()`) owns the state-map clear + baseline
    // restore per F19. The cancel-before-clear ordering invariant becomes
    // SC.dispose() PRESENCE (which internally cancels FC's register, restores
    // baselines, and clears the map) BEFORE pool disposes. Equivalent
    // runtime coverage: FlashController.test.ts #13 (dispose restores
    // baselines) + SC.integration.test.ts INT-5 (dispose chain order).
    const src = _semTopSrc();
    const closeIdx = src.indexOf('</script>');
    const returnPattern = /return\s*\(\s*\)\s*=>\s*\{/g;
    let lastReturnIdx = -1;
    let m: RegExpExecArray | null;
    while ((m = returnPattern.exec(src)) !== null) {
      if (m.index < closeIdx) lastReturnIdx = m.index + m[0].length;
    }
    expect(lastReturnIdx).toBeGreaterThan(0);
    let depth = 1;
    let i = lastReturnIdx;
    while (i < src.length && depth > 0) {
      if (src[i] === '{') depth++;
      else if (src[i] === '}') depth--;
      i++;
    }
    const cleanupBody = src.slice(lastReturnIdx, i - 1);

    // SC dispose is present in cleanup body.
    expect(cleanupBody).toMatch(/selectionController\?\.dispose\(\)/);
    // Anti-regression: the deleted state-map clear must NOT reappear here —
    // FlashController.dispose() owns that work post-migration.
    expect(cleanupBody).not.toMatch(/_flashStates\.clear\(\)/);
  });

  // Sub-project E (spec §5.5 M3) — DELETED `source: cleanup return restores
  // baselines on every active flash before clearing the map`. The restore
  // loop migrated to `FlashController.dispose()` which iterates its private
  // `_states` map and writes `mat.emissiveIntensity = state.baselineEmissive`
  // before clearing. Replacement coverage: FlashController.test.ts #13
  // (dispose restores baselines + clears map) + SC.integration.test.ts INT-5
  // (SC.dispose chains FC.dispose under the new state machine API).

  it('source: every impactCoordinator.fire site routes through coordinator (canon F7 universal causal-ordering invariant)', () => {
    const src = _semTopSrc();
    // Canon F7 invariant — every impact reaction fires from inside the
    // coordinator's onImpact callback, never synchronously with
    // beamPool.acquire. Post-Sub-project-C, SemanticTopology has ZERO
    // direct beamPool.acquire calls (cleanup-contract test #3); every
    // beam trigger routes through `impactCoordinator.fire({...})`. Four
    // call sites today (entrance, post-growth, optimization, click) —
    // each must include a `trigger:` field that the coordinator's preset
    // table dispatches on. Without this universal indirection, a future
    // refactor could regress one site silently and re-introduce
    // anti-causal ordering at that site only — invisible to the
    // click-effect-specific regression tests above.
    const firePattern = /impactCoordinator[?!]?\.fire\(\s*\{([\s\S]*?)\}\s*\)/g;
    const matches = Array.from(src.matchAll(firePattern));
    expect(matches.length).toBeGreaterThanOrEqual(4);
    for (const match of matches) {
      const configBody = match[1];
      expect(configBody, `impactCoordinator.fire site missing trigger field — preset dispatch risk\n${match[0]}`).toMatch(/trigger\s*:/);
    }
  });

  it('source: cleanup return calls coordinator?.dispose() + envelopePool?.dispose() + nulls envelopePool', () => {
    const src = _semTopSrc();
    // Locate the onMount cleanup return body.
    const closeIdx = src.indexOf('</script>');
    const returnPattern = /return\s*\(\s*\)\s*=>\s*\{/g;
    let lastReturnIdx = -1;
    let m: RegExpExecArray | null;
    while ((m = returnPattern.exec(src)) !== null) {
      if (m.index < closeIdx) lastReturnIdx = m.index + m[0].length;
    }
    expect(lastReturnIdx).toBeGreaterThan(0);
    let depth = 1;
    let i = lastReturnIdx;
    while (i < src.length && depth > 0) {
      if (src[i] === '{') depth++;
      else if (src[i] === '}') depth--;
      i++;
    }
    const cleanupBody = src.slice(lastReturnIdx, i - 1);

    // Post-Sub-project B (spec §5.2.2 M3): `_removeEnvelopeUpdate` symbol is
    // absorbed into `coordinator.dispose()`; the pool-dispose-then-null pattern
    // survives.
    expect(cleanupBody).toMatch(/coordinator\?\.dispose\(\)/);
    expect(cleanupBody).toMatch(/envelopePool\?\.dispose\(\)/);
    // Pool reference nulled after dispose so subsequent disposals are no-ops.
    expect(cleanupBody).toMatch(/envelopePool\s*=\s*null/);
  });

  it('source: galaxy formation animation is gated on cache miss — preserves view across stateFilter mutations', () => {
    const src = _semTopSrc();
    // Click-zoom bug (intermittent variant): the $effect watching
    // taxonomyTree + stateFilter + readinessStore.reports re-fires when
    // ANY of those mutate. The previous implementation unconditionally
    // re-collapsed every node to random near-origin then ran a 90-frame
    // galaxy formation animation. When this fires mid-click-focus
    // (because _loadClusterDetail mutates stateFilter on cross-filter
    // clicks, or because a taxonomy_changed/domain_readiness_changed
    // SSE arrives within the ~600ms focus animation window), the user
    // sees: zoom in → all clusters reset to origin (visible as "LOD
    // reset") → 90-frame formation animation while camera completes
    // its zoom → "zoom in again". The fingerprint cache is the natural
    // discriminator: cache hit = same node-set = subsequent rebuild =
    // skip formation; cache miss = first time seeing this node-set =
    // formation makes sense.
    //
    // Cache check happens at `const cached = topologyCache.get(...)`
    // and the formation block must be inside `if (!cached)`.
    expect(src).toMatch(
      /const\s+cached\s*=\s*topologyCache\.get\(fingerprint\)/,
    );
    // The collapse-to-origin block must be inside an `if (!cached)`
    // branch — not unconditional.
    expect(src).toMatch(
      /if\s*\(\s*!\s*cached\s*\)\s*\{[\s\S]{0,4000}Start all nodes collapsed at origin/,
    );
    // Post-Sub-project B (spec §5.2.2 M3): the formation animation registration
    // (now `_removeFormationAnim = coordinator.register('ambient', ...)`) must
    // be inside the same `!cached` branch. Formation runs in the `ambient`
    // phase per spec §3.5.
    expect(src).toMatch(
      /if\s*\(\s*!\s*cached\s*\)\s*\{[\s\S]{0,8000}removeFormation\s*=\s*coordinator\.register\(\s*['"]ambient['"][\s\S]{0,300}/,
    );
    // The else branch must set positions directly to settled values
    // (no animation, no collapse).
    expect(src).toMatch(
      /\}\s*else\s*\{[\s\S]{0,2000}n\.position\s*=\s*\[\s*\n?\s*settledPositions\[i\s*\*\s*3\]/,
    );
  });
  it('source: breathing loop does NOT override emissiveIntensity while selectionController?.isFlashActive(nodeId) — FlashController._tick owns full attack→hold→decay ramp (Engulfment-Bug regression) (spec §5.5 M3 Row — anchors migrated _flashStates.has → SC.isFlashActive, impactCoordinator.isEngulfed → SC.isEngulfed, _tickFlashStates → flashController._tick)', () => {
    // Live-observed: MATURE/database clusters showed the full engulfment burst
    // on selection, but ACTIVE clusters (low member count, baseline ~0.5-0.6)
    // appeared dim during the 120ms attack phase and only briefly bloomed
    // during hold. Root cause: the breathing animation loop ran a blend-OUT
    // formula `mat.emissiveIntensity = baseEmissive + idlePulse * blendOut`
    // during `glowElapsed < GLOW_ATTACK_MS && isSelected` that DECAYED toward
    // baseEmissive at frame 120ms — overwriting the flash tick's cubic ramp
    // from baseline → peak. For low-baseline clusters that meant the attack
    // phase never crossed the 0.85 bloom threshold and the engulfment was
    // effectively invisible.
    //
    // Post-Sub-project-E: the flash-state predicate migrated from local
    // `_flashStates.has(nodeId)` to `selectionController?.isFlashActive(nodeId)`
    // (SC owns FC, exposes the predicate as a facade). The engulfed-set
    // predicate similarly migrated from `impactCoordinator?.isEngulfed(nodeId)`
    // to `selectionController?.isEngulfed(nodeId)` (engulfed state lives on
    // SC's internal state machine). The contract preserved: when SC says
    // a flash is active, the breathing loop is a no-op so FC's _tick owns
    // the per-frame emissive write.
    const src = _semTopSrc();

    // Locate the breathing-loop section.
    const breathingMatch = src.match(
      /T3\.4 — Idle Ambient Energy Pulse[\s\S]*?\(canon\)[\s\S]*?const isSelected = clustersStore\.selectedClusterId === nodeId/,
    );
    expect(breathingMatch).not.toBeNull();

    // Post-Sub-project-E: the flash-active branch now anchors on
    // `selectionController?.isFlashActive(nodeId)` and chains via
    // `else if (selectionController?.isEngulfed(...)`. The branch body
    // must remain a no-op for `mat.emissiveIntensity`.
    const flashBranchMatch = src.match(
      /if \(selectionController(?:\?\.|\!\.|\.)isFlashActive\(nodeId\)\) \{([\s\S]*?)\} else if \(selectionController/,
    );
    expect(flashBranchMatch).not.toBeNull();
    const flashBranchBody = flashBranchMatch![1];
    expect(flashBranchBody).not.toMatch(/mat\.emissiveIntensity\s*=/);
    expect(flashBranchBody).not.toMatch(/blendOut/);

    // Defense-in-depth: the comment must mention that flashController._tick
    // (or FC._tick) owns the ramp, so future editors don't accidentally
    // re-add an override.
    expect(flashBranchBody).toMatch(/flashController\._tick|FlashController\._tick|FC\._tick/);
  });


  it('source: selection idle pulse uses SELECTION_EMISSIVE_FLOOR via Math.max in SelectionController._tickIdlePulse (spec §5.5 M3 Row — anchor migrated from ImpactCoordinator._tick to SelectionController._tickIdlePulse; SELECTION_EMISSIVE_FLOOR import-source IC → SC)', () => {
    // Live-observed pre-fix: even after the flash-override fix landed, only
    // MATURE/database clusters showed the continuous engulfment glow on
    // selection. Root cause: the IDLE PULSE for selected nodes (NOT the
    // flash) is what produces the sustained breathing effect. Pre-fix the
    // formula was `baseEmissive + Math.sin(...) * 0.2 + 0.2` (range
    // [base, base+0.4]). For MATURE clusters with baseEmissive ~1.1 this
    // stayed above the UnrealBloomPass threshold (0.85) for the entire
    // pulse cycle. For ACTIVE clusters with baseEmissive ~0.5-0.6 the
    // pulse only crossed bloom near its peak and sat sub-bloom most of
    // the cycle — producing a visually dead selection state.
    //
    // Fix: root the pulse at `Math.max(baseEmissive, SELECTION_EMISSIVE_FLOOR)`
    // where the floor is >= the bloom threshold + margin (1.0). The named
    // constant is exported from SelectionController.ts (Sub-project E
    // migration; pre-migration it was exported from IC).
    //
    // Post-Sub-project-E: this anchor lives in `SelectionController._tickIdlePulse(...)`.
    // The breathing handler retains a guard branch (no-op body) so the bare
    // `else { = baseEmissive }` does NOT overwrite SC's write; that guard
    // is pinned by cleanup-contract.test.ts test #12 (post-rev-2). Equivalent
    // runtime coverage: SelectionController.test.ts (idle-pulse behavior).
    const mod = import.meta.glob<string>(['./SelectionController.ts'], {
      query: '?raw',
      import: 'default',
      eager: true,
    });
    const src = mod['./SelectionController.ts'];
    expect(typeof src).toBe('string');

    // Extract the _tickIdlePulse(...) method body via a brace-balanced slice
    // from the header through the matching close.
    const headerRegex = /private\s+_tickIdlePulse\s*\(\s*delta\s*:\s*number\s*\)\s*:\s*void\s*\{/;
    const m = headerRegex.exec(src as string);
    expect(m).not.toBeNull();
    const openIdx = m!.index + m![0].length - 1;
    let depth = 1;
    let i = openIdx + 1;
    while (i < (src as string).length && depth > 0) {
      const ch = (src as string)[i];
      if (ch === '{') depth++;
      else if (ch === '}') depth--;
      i++;
    }
    const tickBody = (src as string).slice(openIdx + 1, i - 1);

    // The named floor must be referenced (not a magic 1.0 literal) inside
    // _tickIdlePulse. The export is at module scope of SC.ts, so a bare
    // `SELECTION_EMISSIVE_FLOOR` identifier appears inside the body.
    expect(tickBody).toMatch(/SELECTION_EMISSIVE_FLOOR/);

    // The floor must be USED via Math.max against baseEmissive so the lift
    // is conditional (mature clusters preserved at their natural baseline).
    expect(tickBody).toMatch(
      /Math\.max\(\s*baseEmissive\s*,\s*SELECTION_EMISSIVE_FLOOR\s*\)/,
    );
  });


  it('source: ImpactCoordinator.fire onImpact body passes freshNode.size directly to envelopePool.acquire (canon F19 — no MIN_SCALE floor that produced the 10x balloon) (spec §5.3 M3 Row 5 — anchor moved from _triggerBeamImpact helper to coordinator)', () => {
    // Canon F19 (.claude/skills/brand-guidelines/references/3d-visualization.md
    // line 276): `envelopePool?.acquire(group, node.size, envelopeShape,
    // color);` — pass the cluster's data-driven size verbatim. Line 294 of
    // the canon mandates `PEAK_SWELL = 1.18` so the visible plasma skin
    // sits "just outside the cluster silhouette without overlapping
    // neighbors."
    //
    // Operator-reported regression: previously a `ENVELOPE_MIN_SCALE = 8.0`
    // floor combined with `PEAK_SWELL = 1.28` produced peak envelope at
    // 8 × 1.28 = ~10.24 screen units regardless of cluster size. On click
    // every cluster appeared to balloon to ~10x its visual size — heavy
    // bloom amplified the bright additive envelope into the giant pink
    // balloon visible in screenshots. The floor was originally added for
    // "tiny ACTIVE cluster visibility" but it violated canon and produced
    // worse symptoms than the visibility issue it tried to solve.
    //
    // Canon-compliant behavior: pass `freshNode.size` directly. Post-Sub-
    // project-C the canonical acquire site lives inside
    // `ImpactCoordinator.fire(...)`'s `onImpact` callback body — the
    // `_triggerBeamImpact` helper is gone.
    const mod = import.meta.glob<string>(['./ImpactCoordinator.ts'], {
      query: '?raw',
      import: 'default',
      eager: true,
    });
    const src = mod['./ImpactCoordinator.ts'];
    expect(typeof src).toBe('string');

    // Extract the `fire(request: ImpactRequest): void { ... }` body via a
    // brace-balanced slice from the header through the matching close.
    const headerRegex = /\bfire\s*\(\s*request\s*:\s*ImpactRequest\s*\)\s*:\s*void\s*\{/;
    const m = headerRegex.exec(src as string);
    expect(m).not.toBeNull();
    const openIdx = m!.index + m![0].length - 1;
    let depth = 1;
    let i = openIdx + 1;
    while (i < (src as string).length && depth > 0) {
      const ch = (src as string)[i];
      if (ch === '{') depth++;
      else if (ch === '}') depth--;
      i++;
    }
    const fireBody = (src as string).slice(openIdx + 1, i - 1);

    // Positive contract: envelopePool.acquire(currentGroup, freshNode.size, ...)
    // appears in the onImpact body. The coordinator uses `.acquire(` directly
    // (no optional-chain) because the dep is non-null inside the closure.
    expect(fireBody).toMatch(
      /envelopePool[?!]?\.acquire\(\s*currentGroup\s*,\s*freshNode\.size\s*,/,
    );

    // Anti-regression (whole file): the floor constant must NOT appear
    // anywhere in ImpactCoordinator.ts. Pre-balloon-fix the helper used
    // `ENVELOPE_MIN_SCALE = 8.0`; the canonical post-fix source has zero
    // mentions of that symbol.
    expect(src as string).not.toMatch(/ENVELOPE_MIN_SCALE/);

    // Anti-regression (fire body only): no `Math.max(freshNode.size, ...)`
    // inside the coordinator's onImpact body — the freshly-resolved size
    // is passed verbatim. (Note: SELECTION_EMISSIVE_FLOOR uses
    // `Math.max(baseEmissive, ...)` inside `_tick`, which is unrelated
    // and lives in a different method — scoping to `fireBody` prevents
    // a false positive.)
    expect(fireBody).not.toMatch(/Math\.max\(\s*freshNode\.size\s*,/);
  });


  it('source: post-engulfment idle pulse is gated on engulfed-state + flash-active in SelectionController._tickIdlePulse (Engulfment-Timing regression) (spec §5.5 M3 Row — anchors migrated from ImpactCoordinator._tick / _deps.isFlashActive callbacks to SelectionController._tickIdlePulse / internal _state + _flash.isActive)', () => {
    // Live-observed pre-fix: operator reported "fully engulfs and triggers
    // BEFORE the beam actually triggers and then becomes flat when the beam
    // actually hits it." Root cause: the idle ambient pulse (canon T3.4)
    // fired synchronously on selection click — `mat.emissiveIntensity =
    // max(baseEmissive, SELECTION_EMISSIVE_FLOOR) + idlePulse` ran every
    // frame the breathing loop saw `isSelected`. With the floor lifted to
    // 1.0 it produced a visible bloomed glow IMMEDIATELY on click, before
    // the beam had finished its 700ms POV→target travel.
    //
    // Fix: the post-engulfment idle pulse is gated on SC's internal state
    // machine — only fires when `_state === 'engulfed'`. The engulfed
    // transition happens after the GLOW_TOTAL_MS=1380ms decay timer
    // elapses (set in onImpact). The mid-flash protection
    // (`_flash.isActive(selectedId)` skip) prevents the pulse from
    // competing with FlashController._tick's attack/hold/decay ramp.
    //
    // Post-Sub-project-E: this anchor lives in `SelectionController._tickIdlePulse(...)`.
    // Both guards are INTERNAL to SC (no dep callbacks): the engulfed
    // state lives on `this._state` and the flash predicate reads through
    // SC's owned `FlashController` reference via `this._flash.isActive(...)`.
    // Equivalent runtime coverage: SelectionController.test.ts (idle
    // pulse + state transitions) + SC.integration.test.ts INT-4 (flash +
    // idle pulse don't compete).
    const mod = import.meta.glob<string>(['./SelectionController.ts'], {
      query: '?raw',
      import: 'default',
      eager: true,
    });
    const src = mod['./SelectionController.ts'];
    expect(typeof src).toBe('string');

    const headerRegex = /private\s+_tickIdlePulse\s*\(\s*delta\s*:\s*number\s*\)\s*:\s*void\s*\{/;
    const m = headerRegex.exec(src as string);
    expect(m).not.toBeNull();
    const openIdx = m!.index + m![0].length - 1;
    let depth = 1;
    let i = openIdx + 1;
    while (i < (src as string).length && depth > 0) {
      const ch = (src as string)[i];
      if (ch === '{') depth++;
      else if (ch === '}') depth--;
      i++;
    }
    const tickBody = (src as string).slice(openIdx + 1, i - 1);

    // (a) Engulfed-state guard: `if (this._state !== 'engulfed') return;`
    // (replaces the pre-Sub-project-E `if (... !this.isEngulfed(selectedId)) return;`
    // pattern that lived in ImpactCoordinator._tick).
    expect(tickBody).toMatch(
      /if\s*\(\s*this\._state\s*!==\s*['"]engulfed['"]\s*\)\s*return/,
    );

    // (b) Flash-active skip via internal FC reference (replaces the
    // `_deps.isFlashActive(selectedId)` dep callback pattern). SC owns FC
    // directly per spec §3.1 — no callback indirection.
    expect(tickBody).toMatch(
      /this\._flash\.isActive\s*\(\s*this\._selectedId\s*\)/,
    );

    // (c) The body writes mat.emissiveIntensity and references the pulse
    // formula shape — `Math.sin(this._pulseTime * 0.4) * 0.1 + 0.1`
    // produces the [floor, floor+0.2] range pinned by canon T3.4.
    expect(tickBody).toMatch(/mat\.emissiveIntensity\s*=/);
    expect(tickBody).toMatch(
      /Math\.sin\(\s*this\._pulseTime\s*\*\s*0\.4\s*\)\s*\*\s*0\.1\s*\+\s*0\.1/,
    );
  });

  it('source: flash attack ramps from `startIntensity` (current displayed value), not `baselineEmissive` — no dip when beam hits a node mid-pulse (Flash-Dip regression) (spec §5.5 M3 Row — anchor moved from SemanticTopology.svelte to FlashController.ts)', () => {
    // Live-observed: operator reported "becomes flat when the beam actually
    // hits it." Root cause: pre-fix the flash attack formula was
    // `state.baselineEmissive + (peak - state.baselineEmissive) * ease`
    // which produced an emissive ramp from `baselineEmissive` (the
    // data-driven low value, e.g., 0.6 for ACTIVE clusters) up to peak.
    // If the idle pulse had elevated `mat.emissiveIntensity` ABOVE baseline
    // (which it does for selected nodes), the first frame of the attack
    // phase reset the visible intensity DOWN to baseline before ramping
    // up — producing a visible "dim" frame at the moment of impact.
    //
    // Fix: `FlashController.flash()` captures `mat.emissiveIntensity` at
    // acquire time as `state.startIntensity = max(currentIntensity, baseline)`,
    // and the attack-phase formula ramps from `startIntensity` → peak.
    // The decay phase still ramps `peak` → `baselineEmissive` (so the
    // post-engulfment value lands at the data-driven baseline, matching
    // the operator-spec'd "fluidly dissipates" close).
    //
    // Post-Sub-project-E: the state record + acquire-time capture + attack
    // formula all live in `FlashController.ts`. The identifiers are
    // unchanged (`startIntensity`, `baseline`, `state.startIntensity`,
    // `peak`, `ease`) — only the source file moved.
    const mod = import.meta.glob<string>(['./FlashController.ts'], {
      query: '?raw',
      import: 'default',
      eager: true,
    });
    const src = mod['./FlashController.ts'];
    expect(typeof src).toBe('string');

    // The state record must include `startIntensity`.
    expect(src as string).toMatch(/startIntensity:\s*number/);

    // FC.flash() must compute startIntensity from current emissive.
    expect(src as string).toMatch(
      /startIntensity[\s\S]{0,200}Math\.max\(\s*mat\.emissiveIntensity\s*,\s*baseline\s*\)/,
    );

    // FC._tick attack-phase formula must use startIntensity.
    expect(src as string).toMatch(
      /mat\.emissiveIntensity\s*=\s*state\.startIntensity\s*\+\s*\(peak\s*-\s*state\.startIntensity\)\s*\*\s*ease/,
    );
  });


  // Sub-project E (spec §5.5 M3) — DELETED `source: engulfment gate clear
  // is keyed off ACTUAL selection transitions via impactCoordinator?.clearEngulfed()`.
  // `_prevSelectedId` migrated to SC's internal `_previousId`; same-id
  // re-selects no-op via `if (nodeId === this._selectedId) return;` guard
  // inside SC.select(). The engulfed-state lifecycle now lives entirely
  // inside SC's state machine, NOT as a $effect transition gate on the
  // component. Replacement coverage: SelectionController.test.ts #12
  // (same-id no-op via early return) + #8 (cancel-via-idle on different
  // id) + SC.integration.test.ts INT-2 (re-select mid-impact cancels +
  // re-enters focusing).


});
