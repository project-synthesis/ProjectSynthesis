import { describe, it, expect, vi, beforeEach } from 'vitest';

// Mock Three.js classes — must be constructable with `new`
vi.mock('three', () => {
  class Vector3 {
    x: number; y: number; z: number;
    constructor(x = 0, y = 0, z = 0) { this.x = x; this.y = y; this.z = z; }
    clone() { return new Vector3(this.x, this.y, this.z); }
    add(v: Vector3) { this.x += v.x; this.y += v.y; this.z += v.z; return this; }
    subVectors(a: Vector3, b: Vector3) {
      this.x = a.x - b.x; this.y = a.y - b.y; this.z = a.z - b.z; return this;
    }
    normalize() { return this; }
    multiplyScalar() { return this; }
    lerpVectors() { return this; }
    set(x: number, y: number, z: number) { this.x = x; this.y = y; this.z = z; }
    distanceTo() { return 80; }
    // computeFocusEndpoint inputs use length() + divideScalar() for the
    // zero-direction guard. Implement minimally — Euclidean magnitude
    // and in-place scale.
    length() { return Math.sqrt(this.x * this.x + this.y * this.y + this.z * this.z); }
    divideScalar(s: number) {
      if (s !== 0) { this.x /= s; this.y /= s; this.z /= s; } return this;
    }
  }

  class Scene {
    background: unknown = null;
    children: unknown[] = [];
    add = vi.fn();
    remove = vi.fn();
    traverse = vi.fn();
  }

  class PerspectiveCamera {
    position = Object.assign(new Vector3(0, 0, 80), {
      clone: () => new Vector3(0, 0, 80),
      distanceTo: () => 80,
      lerpVectors: vi.fn(),
    });
    aspect = 1;
    updateProjectionMatrix = vi.fn();
  }

  class WebGLRenderer {
    setPixelRatio = vi.fn();
    setSize = vi.fn();
    render = vi.fn();
    dispose = vi.fn();
    domElement = document.createElement('canvas');
    // ShadowMap state — TopologyRenderer constructor configures these.
    shadowMap = { enabled: false, type: 0 };
  }

  class Color {
    constructor() {}
  }

  class Mesh {}

  // Light classes — minimal shape supporting the constructor + the
  // properties TopologyRenderer assigns (`position.set`, `castShadow`,
  // `shadow.mapSize.set`).
  class AmbientLight {
    constructor(public color: number, public intensity: number) {}
  }
  class DirectionalLight {
    position = { set: vi.fn() };
    castShadow = false;
    shadow = { mapSize: { set: vi.fn() } };
    constructor(public color: number, public intensity: number) {}
  }
  class HemisphereLight {
    constructor(
      public skyColor: number,
      public groundColor: number,
      public intensity: number,
    ) {}
  }

  // PCFShadowMap is a numeric constant in real Three.js; any non-zero
  // value works for the equality assertion the constructor makes.
  const PCFShadowMap = 1;

  return {
    Scene,
    PerspectiveCamera,
    WebGLRenderer,
    Color,
    Mesh,
    Vector3,
    AmbientLight,
    DirectionalLight,
    HemisphereLight,
    PCFShadowMap,
  };
});

vi.mock('three/addons/controls/OrbitControls.js', () => {
  class OrbitControls {
    enableDamping = false;
    dampingFactor = 0;
    minDistance = 0;
    maxDistance = 0;
    addEventListener = vi.fn();
    update = vi.fn();
    dispose = vi.fn();
    target = {
      clone: () => ({ x: 0, y: 0, z: 0, lerpVectors: vi.fn() }),
      lerpVectors: vi.fn(),
    };
  }
  return { OrbitControls };
});

import { TopologyRenderer, type LODTier } from './TopologyRenderer';

describe('TopologyRenderer', () => {
  let canvas: HTMLCanvasElement;

  beforeEach(() => {
    vi.clearAllMocks();
    canvas = document.createElement('canvas');
    Object.defineProperty(canvas, 'clientWidth', { value: 800, configurable: true });
    Object.defineProperty(canvas, 'clientHeight', { value: 600, configurable: true });
  });

  it('constructs without error', () => {
    const r = new TopologyRenderer(canvas);
    expect(r).toBeDefined();
    expect(r.scene).toBeDefined();
    expect(r.camera).toBeDefined();
    expect(r.controls).toBeDefined();
  });

  it('initial lodTier is far', () => {
    const r = new TopologyRenderer(canvas);
    expect(r.lodTier).toBe('far');
  });

  it('start begins render loop without error', () => {
    const r = new TopologyRenderer(canvas);
    const rafSpy = vi.spyOn(globalThis, 'requestAnimationFrame').mockReturnValue(1);
    r.start();
    expect(rafSpy).toHaveBeenCalled();
    r.dispose();
    rafSpy.mockRestore();
  });

  it('resize updates camera aspect and renderer size', () => {
    const r = new TopologyRenderer(canvas);
    r.resize(1024, 768);
    expect(r.camera.aspect).toBeCloseTo(1024 / 768);
    expect(r.camera.updateProjectionMatrix).toHaveBeenCalled();
  });

  it('dispose cancels animation frame', () => {
    const r = new TopologyRenderer(canvas);
    const cancelSpy = vi.spyOn(globalThis, 'cancelAnimationFrame').mockImplementation(() => {});
    const rafSpy = vi.spyOn(globalThis, 'requestAnimationFrame').mockReturnValue(42);
    r.start();
    r.dispose();
    expect(cancelSpy).toHaveBeenCalledWith(42);
    cancelSpy.mockRestore();
    rafSpy.mockRestore();
  });

  it('onLodChange registers callback', () => {
    const r = new TopologyRenderer(canvas);
    const cb = vi.fn();
    r.onLodChange(cb);
    expect(cb).not.toHaveBeenCalled();
  });

  it('focusOn does not throw', async () => {
    const r = new TopologyRenderer(canvas);
    const THREE = await import('three');
    expect(() => r.focusOn(new THREE.Vector3(1, 2, 3))).not.toThrow();
  });
});

describe('LODTier type', () => {
  it('accepts valid tier values', () => {
    const tiers: LODTier[] = ['far', 'mid', 'near'];
    expect(tiers).toHaveLength(3);
  });
});
