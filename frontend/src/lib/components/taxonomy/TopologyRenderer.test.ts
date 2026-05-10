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

  // Vector2 — needed by UnrealBloomPass constructor (canon F13).
  class Vector2 {
    constructor(public x: number = 0, public y: number = 0) {}
  }

  return {
    Scene,
    PerspectiveCamera,
    WebGLRenderer,
    Color,
    Mesh,
    Vector3,
    Vector2,
    AmbientLight,
    DirectionalLight,
    HemisphereLight,
    PCFShadowMap,
  };
});

vi.mock('three/addons/controls/OrbitControls.js', () => {
  // `controls.target.clone()` MUST return an object that supports the
  // same Vector3 surface used by `computeFocusEndpoint` — `.subVectors`,
  // `.length`, `.divideScalar`, etc. Inline a minimal Vector3-shape so
  // method calls on `cloneTarget` succeed; pre-fix, `clone()` returned a
  // plain object literal with `{x, y, z, lerpVectors}` and would throw
  // `TypeError: subVectors is not a function` if the focus-on call
  // pattern changes. (Code-quality reviewer M7.)
  function makeTargetVec() {
    return {
      x: 0, y: 0, z: 0,
      clone() { return makeTargetVec(); },
      subVectors(a: { x: number; y: number; z: number }, b: { x: number; y: number; z: number }) {
        this.x = a.x - b.x; this.y = a.y - b.y; this.z = a.z - b.z; return this;
      },
      normalize() { return this; },
      multiplyScalar() { return this; },
      add() { return this; },
      set(x: number, y: number, z: number) { this.x = x; this.y = y; this.z = z; return this; },
      length() { return Math.sqrt(this.x * this.x + this.y * this.y + this.z * this.z); },
      divideScalar(s: number) {
        if (s !== 0) { this.x /= s; this.y /= s; this.z /= s; } return this;
      },
      lerpVectors: vi.fn(),
    };
  }
  class OrbitControls {
    enableDamping = false;
    dampingFactor = 0;
    minDistance = 0;
    maxDistance = 0;
    addEventListener = vi.fn();
    update = vi.fn();
    dispose = vi.fn();
    target = makeTargetVec();
  }
  return { OrbitControls };
});

// Cinematic post-processing mocks (canon F13). The composer pipeline is
// driven by EffectComposer + RenderPass + UnrealBloomPass + FilmPass.
// In jsdom none of these can do real GL work — the mocks provide just
// enough shape for the constructor to wire them, the render loop to
// `composer.render()` and `composer.setSize()`, and dispose() to walk
// the passes array.
vi.mock('three/addons/postprocessing/EffectComposer.js', () => {
  class EffectComposer {
    passes: unknown[] = [];
    constructor(_renderer: unknown) {}
    addPass(pass: unknown) { this.passes.push(pass); }
    render = vi.fn();
    setSize = vi.fn();
    dispose = vi.fn();
  }
  return { EffectComposer };
});

vi.mock('three/addons/postprocessing/RenderPass.js', () => {
  class RenderPass {
    constructor(_scene: unknown, _camera: unknown) {}
    dispose = vi.fn();
  }
  return { RenderPass };
});

vi.mock('three/addons/postprocessing/UnrealBloomPass.js', () => {
  class UnrealBloomPass {
    constructor(
      public resolution: unknown,
      public strength: number,
      public radius: number,
      public threshold: number,
    ) {}
    dispose = vi.fn();
  }
  return { UnrealBloomPass };
});

vi.mock('three/addons/postprocessing/FilmPass.js', () => {
  class FilmPass {
    constructor(public intensity: number, public grayscale: boolean) {}
    dispose = vi.fn();
  }
  return { FilmPass };
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

  it('focusOn called consecutively cancels in-flight animation', async () => {
    // Spec § 4.6 third bullet: "focusOn consecutively cancels any
    // in-flight focus animation". The implementation cancels via
    // `cancelAnimationFrame(this._focusAnimId)` at the top of focusOn.
    // Verified here by stubbing RAF + cancelRAF and asserting the second
    // call invokes cancelAnimationFrame with the id the first call
    // registered. (Endpoint correctness is covered by focus-math.test.ts;
    // this gate covers the cancellation wiring.)
    const r = new TopologyRenderer(canvas);
    const THREE = await import('three');
    const RAF_ID = 12345;
    const rafSpy = vi.spyOn(globalThis, 'requestAnimationFrame').mockReturnValue(RAF_ID);
    const cancelSpy = vi.spyOn(globalThis, 'cancelAnimationFrame').mockImplementation(() => {});

    r.focusOn(new THREE.Vector3(1, 2, 3), 50);
    // First call's synchronous animate() runs once with t=0 (no advance)
    // and queues the next RAF tick — at that point _focusAnimId === RAF_ID.
    expect(rafSpy).toHaveBeenCalled();

    r.focusOn(new THREE.Vector3(4, 5, 6), 80);
    // Second call must observe _focusAnimId !== null and cancel it before
    // starting fresh.
    expect(cancelSpy).toHaveBeenCalledWith(RAF_ID);

    rafSpy.mockRestore();
    cancelSpy.mockRestore();
  });
});

describe('LODTier type', () => {
  it('accepts valid tier values', () => {
    const tiers: LODTier[] = ['far', 'mid', 'near'];
    expect(tiers).toHaveLength(3);
  });
});
