/**
 * Per-frame allocation budget test — Pattern Graph 3D scope.
 *
 * Spec: docs/superpowers/specs/2026-05-09-pattern-graph-depth-design.md § 3.5 + § 4.2
 * Brand: .claude/skills/brand-guidelines/references/3d-visualization.md
 *        "Per-Frame Allocation Budget" + "Disposal Contract" sections.
 *
 * Two assertions:
 *   1. Module-level scratch table is declared in `SemanticTopology.svelte`
 *      (`_scratchVec3a`, `_scratchQuat`, `_scratchColor`, `Z_AXIS`). These are
 *      the canonical "borrow, mutate, return" objects that animation callbacks
 *      reuse instead of allocating fresh THREE primitives every frame.
 *   2. Runtime: per-frame primitives (`BeamPool.update`, `ClusterPhysics.update`)
 *      allocate ZERO `THREE.Vector3` / `Color` / `Quaternion` / `Matrix4`
 *      instances when ticked, verified via constructor counter wrappers.
 *
 * Notes on test mechanism (per spec § 4.2 + § 8 risks):
 *   - Uses real Three.js semantics (constructors and prototypes preserved).
 *     The spec § 4.2 prescribed `(THREE as any).Vector3 = WrappedCtor` for the
 *     wrap step, which the spec correctly anticipated would need a try/finally
 *     restore in afterEach. Modern ESM namespaces (Vite + Vitest) are however
 *     read-only — `Cannot assign to read only property 'Vector3'` fires
 *     immediately. The canonical Vitest-native equivalent is `vi.mock('three',
 *     async (importOriginal) => …)` which performs the same wrap at import
 *     resolution time. The spec's "does NOT use vi.mock" guidance was guarding
 *     against wholesale stubbing of Three.js; here we re-export every original
 *     binding via `...actual` and override only the four counted constructors,
 *     preserving 100% of real Three.js behavior. Counters live on a shared
 *     module-level `_counters` object that the `vi.mock` factory closes over.
 *   - `BeamPool` + `ClusterPhysics` are the production per-frame primitives
 *     (registered via `renderer.addAnimationCallback` in `SemanticTopology`).
 *     They cover the bulk of per-frame allocation surface; the remaining
 *     callbacks (billboard `lookAt`, `rotation.y += δ`, scalar LOD opacity)
 *     operate on existing members and use only setters — verified by the
 *     module-level scratch-table source-grep + the brand-compliance gate.
 */
import { beforeEach, describe, expect, test, vi } from 'vitest';

// Counter object closed over by the vi.mock factory below. Tests reset
// counters to zero in beforeEach before exercising the production code.
const _counters = { Vector3: 0, Color: 0, Quaternion: 0, Matrix4: 0 };

vi.mock('three', async (importOriginal) => {
  const actual = await importOriginal<typeof import('three')>();

  type CounterKey = keyof typeof _counters;

  function wrapCtor<T extends new (...args: never[]) => unknown>(name: CounterKey, Original: T): T {
    const Wrapper = function (this: unknown, ...args: never[]) {
      _counters[name]++;
      return Reflect.construct(Original, args, Wrapper as unknown as new () => unknown);
    } as unknown as T;
    Object.setPrototypeOf(Wrapper, Original);
    (Wrapper as unknown as { prototype: unknown }).prototype = Original.prototype;
    return Wrapper;
  }

  return {
    ...actual,
    Vector3: wrapCtor('Vector3', actual.Vector3),
    Color: wrapCtor('Color', actual.Color),
    Quaternion: wrapCtor('Quaternion', actual.Quaternion),
    Matrix4: wrapCtor('Matrix4', actual.Matrix4),
  };
});

import * as THREE from 'three';

import { BeamPool } from './BeamPool';
import { ClusterPhysics } from './ClusterPhysics';

// Vite resolves `import.meta.glob` at build/test time. The `?raw` query loads
// the file as a string. Used for source-grep assertions on SemanticTopology.
const semTopSourceMap = import.meta.glob<string>(
  ['./SemanticTopology.svelte'],
  { query: '?raw', import: 'default', eager: true },
);

function readSemTopSource(): string {
  const content = semTopSourceMap['./SemanticTopology.svelte'];
  if (typeof content !== 'string') {
    throw new Error('perf-budget: SemanticTopology.svelte not found in glob map');
  }
  return content;
}

function resetCounters(): void {
  _counters.Vector3 = 0;
  _counters.Color = 0;
  _counters.Quaternion = 0;
  _counters.Matrix4 = 0;
}

describe('Per-frame allocation budget — module-level scratch table presence', () => {
  test('SemanticTopology.svelte declares the canonical scratch table', () => {
    const src = readSemTopSource();
    // Expectations match the spec § 3.5 canonical names exactly:
    //   _scratchVec3a — borrowable Vector3 for per-frame ops
    //   _scratchQuat   — borrowable Quaternion
    //   _scratchColor  — borrowable Color
    //   Z_AXIS         — readonly +Z axis (never mutated; used by rotateOnAxis)
    const required = ['_scratchVec3a', '_scratchQuat', '_scratchColor', 'Z_AXIS'];
    const missing = required.filter((id) => !src.includes(id));
    expect(missing).toEqual([]);
  });

  test('Z_AXIS is initialized to (0, 0, 1)', () => {
    const src = readSemTopSource();
    // Match `new THREE.Vector3(0, 0, 1)` with permissive whitespace.
    // This catches accidental swaps to (1, 0, 0) or (0, 1, 0).
    expect(src).toMatch(/Z_AXIS\s*=\s*new\s+THREE\.Vector3\(\s*0\s*,\s*0\s*,\s*1\s*\)/);
  });
});

describe('Per-frame allocation budget — counter wrapper self-test', () => {
  beforeEach(() => {
    resetCounters();
  });

  test('counter wrappers correctly intercept new THREE.Vector3()', () => {
    // Sanity check the test harness itself. If this test fails, every other
    // runtime test in this file is also unreliable.
    new THREE.Vector3();
    new THREE.Vector3(1, 2, 3);
    new THREE.Color(0xff0000);
    new THREE.Quaternion();
    new THREE.Matrix4();

    expect(_counters.Vector3).toBe(2);
    expect(_counters.Color).toBe(1);
    expect(_counters.Quaternion).toBe(1);
    expect(_counters.Matrix4).toBe(1);
  });

  test('counter wrapper preserves instanceof identity', () => {
    const v = new THREE.Vector3(1, 2, 3);
    expect(v).toBeInstanceOf(THREE.Vector3);
    expect(v.x).toBe(1);
    expect(v.y).toBe(2);
    expect(v.z).toBe(3);
  });
});

describe('Per-frame allocation budget — runtime counters on BeamPool', () => {
  test('BeamPool.update allocates zero THREE primitives across 100 frames', () => {
    // BeamPool construction allocates internally — counts toward setup.
    const pool = new BeamPool();

    // Build a minimal stub camera. BeamPool.update reads `_origin.unproject(camera)`,
    // which calls `camera.matrixWorld.elements` + `camera.projectionMatrixInverse.elements`.
    // We reuse pre-allocated Matrix4 instances on the camera so per-frame counters
    // remain zero — same defensive pattern Three.js production uses.
    const camera = new THREE.PerspectiveCamera(60, 1, 0.1, 100);
    camera.position.set(0, 0, 80);
    camera.updateMatrixWorld(true);
    camera.updateProjectionMatrix();

    // Reset counters AFTER setup. This is the recording window.
    resetCounters();

    // Run 100 frames at 16ms each (60fps simulated time).
    const FRAME_COUNT = 100;
    const FRAME_DELTA = 0.016;
    for (let i = 0; i < FRAME_COUNT; i++) {
      pool.update(FRAME_DELTA, camera);
    }

    expect(_counters.Vector3).toBe(0);
    expect(_counters.Color).toBe(0);
    expect(_counters.Quaternion).toBe(0);
    expect(_counters.Matrix4).toBe(0);

    pool.dispose();
  });
});

describe('Per-frame allocation budget — runtime counters on ClusterPhysics', () => {
  test('ClusterPhysics.update allocates zero THREE primitives across 100 frames', () => {
    const physics = new ClusterPhysics();
    physics.onBeamImpact('a', 1.0);
    physics.onBeamImpact('b', 1.5);
    physics.onBeamImpact('c', 0.8);

    // Reset counters AFTER setup.
    resetCounters();

    // Run 100 frames at 16ms each (60fps simulated time). Three active states
    // all integrating spring + ripple decay simultaneously — the realistic load.
    const FRAME_COUNT = 100;
    const FRAME_DELTA = 0.016;
    for (let i = 0; i < FRAME_COUNT; i++) {
      physics.update(FRAME_DELTA, () => {
        // Per-callback work happens in production — we ignore the result
        // here. The relevant assertion is that the integrator itself does
        // not allocate.
      });
    }

    expect(_counters.Vector3).toBe(0);
    expect(_counters.Color).toBe(0);
    expect(_counters.Quaternion).toBe(0);
    expect(_counters.Matrix4).toBe(0);
  });
});
