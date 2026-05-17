// frontend/src/lib/components/taxonomy/scene-cleanup.test.ts
//
// Runtime unit tests for cleanupScene helper. Uses minimal THREE mocks
// for fast feedback + isolation from real-Three semantics drift. Real-
// Three integration tests live in scene-cleanup.integration.test.ts.
//
// Spec: docs/superpowers/specs/2026-05-16-lifecycle-hardening-design.md §5.1
import { describe, it, expect, vi } from 'vitest';
import { cleanupScene } from './scene-cleanup';

// Minimal THREE.js mock — matches the convention used in other taxonomy tests
function makeGeometry() {
  return { dispose: vi.fn() };
}
function makeMaterial() {
  return { dispose: vi.fn() };
}
function makeMesh(opts: {
  persistent?: boolean;
  geometry?: ReturnType<typeof makeGeometry>;
  material?: ReturnType<typeof makeMaterial> | ReturnType<typeof makeMaterial>[];
  userData?: Record<string, unknown> | undefined;
  isMesh?: boolean;
  isLineSegments?: boolean;
  isLine?: boolean;
  isPoints?: boolean;
} = {}) {
  const userData =
    'userData' in opts
      ? opts.userData
      : opts.persistent
      ? { persistent: true }
      : {};
  return {
    isMesh: opts.isMesh ?? true,
    isLineSegments: opts.isLineSegments ?? false,
    isLine: opts.isLine ?? false,
    isPoints: opts.isPoints ?? false,
    geometry: opts.geometry ?? makeGeometry(),
    material: opts.material ?? makeMaterial(),
    userData,
    position: { x: 0, y: 0, z: 0 },
    rotation: { x: 0, y: 0, z: 0 },
    scale: { x: 1, y: 1, z: 1 },
    visible: true,
  };
}
function makeLight(persistent = true) {
  return {
    isMesh: false,
    isLineSegments: false,
    isLine: false,
    isPoints: false,
    userData: persistent ? { persistent: true } : {},
  };
}
function makeScene(children: unknown[]) {
  const arr = [...children];
  return {
    children: arr,
    remove(child: unknown) {
      const idx = arr.indexOf(child);
      if (idx >= 0) arr.splice(idx, 1);
    },
    add(child: unknown) {
      arr.push(child);
    },
    traverse(fn: (obj: unknown) => void) {
      for (const c of arr) fn(c);
    },
  };
}

describe('cleanupScene', () => {
  it('#1 — preserves 1 persistent group, disposes 2 ephemeral meshes', () => {
    const persistent = makeMesh({ persistent: true, geometry: makeGeometry(), material: makeMaterial() });
    const eph1 = makeMesh({ geometry: makeGeometry(), material: makeMaterial() });
    const eph2 = makeMesh({ geometry: makeGeometry(), material: makeMaterial() });
    const scene = makeScene([persistent, eph1, eph2]);

    cleanupScene(scene as unknown as THREE.Scene);

    expect(scene.children).toEqual([persistent]);
    expect(eph1.geometry.dispose).toHaveBeenCalledTimes(1);
    expect(eph2.geometry.dispose).toHaveBeenCalledTimes(1);
    expect(persistent.geometry.dispose).not.toHaveBeenCalled();
  });

  it('#2 — empty scene: no exceptions, scene still empty', () => {
    const scene = makeScene([]);
    expect(() => cleanupScene(scene as unknown as THREE.Scene)).not.toThrow();
    expect(scene.children).toEqual([]);
  });

  it('#3 — 3 persistent + 0 ephemeral: all 3 preserved, no disposes', () => {
    const p1 = makeMesh({ persistent: true });
    const p2 = makeMesh({ persistent: true });
    const p3 = makeMesh({ persistent: true });
    const scene = makeScene([p1, p2, p3]);

    cleanupScene(scene as unknown as THREE.Scene);

    expect(scene.children).toEqual([p1, p2, p3]);
    expect(p1.geometry.dispose).not.toHaveBeenCalled();
    expect(p2.geometry.dispose).not.toHaveBeenCalled();
    expect(p3.geometry.dispose).not.toHaveBeenCalled();
  });

  it('#4 — shared geometry deduplicated via WeakSet', () => {
    const sharedGeo = makeGeometry();
    const eph1 = makeMesh({ geometry: sharedGeo });
    const eph2 = makeMesh({ geometry: sharedGeo });
    const scene = makeScene([eph1, eph2]);

    cleanupScene(scene as unknown as THREE.Scene);

    expect(sharedGeo.dispose).toHaveBeenCalledTimes(1);
  });

  it('#5 — material array: both materials disposed', () => {
    const m1 = makeMaterial();
    const m2 = makeMaterial();
    const eph = makeMesh({ material: [m1, m2] });
    const scene = makeScene([eph]);

    cleanupScene(scene as unknown as THREE.Scene);

    expect(m1.dispose).toHaveBeenCalledTimes(1);
    expect(m2.dispose).toHaveBeenCalledTimes(1);
  });

  it('#6 — persistent + ephemeral share geometry: persistent shielded, ephemeral wiped', () => {
    const sharedGeo = makeGeometry();
    const sharedMat = makeMaterial();
    const persistent = makeMesh({ persistent: true, geometry: sharedGeo, material: sharedMat });
    const eph = makeMesh({ geometry: sharedGeo, material: sharedMat });
    const scene = makeScene([persistent, eph]);

    cleanupScene(scene as unknown as THREE.Scene);

    expect(scene.children).toEqual([persistent]);
    expect(sharedGeo.dispose).toHaveBeenCalledTimes(0); // persistent detached before traverse
    expect(sharedMat.dispose).toHaveBeenCalledTimes(0);
  });

  it('#6b — persistent NOT in scene, ephemeral shares its geometry: ephemeral wiped, geometry disposed (caller responsibility)', () => {
    // Pins the contract that cleanupScene protects only currently-in-scene
    // persistent children. Out-of-scene persistents are the caller's concern.
    const sharedGeo = makeGeometry();
    const sharedMat = makeMaterial();
    const eph = makeMesh({ geometry: sharedGeo, material: sharedMat });
    const scene = makeScene([eph]);

    cleanupScene(scene as unknown as THREE.Scene);

    expect(scene.children).toEqual([]);
    expect(sharedGeo.dispose).toHaveBeenCalledTimes(1); // reachable via ephemeral only
  });

  it('#7 — persistent light with no geometry: preserved, no exceptions, no disposes', () => {
    const light = makeLight(true);
    const scene = makeScene([light]);

    cleanupScene(scene as unknown as THREE.Scene);

    expect(scene.children).toEqual([light]);
  });

  it('#8 — persistent flag on nested child is ignored (only direct scene children honored)', () => {
    const innerPersistent = makeMesh({ persistent: true });
    const parent = {
      isMesh: false,
      isLineSegments: false,
      isLine: false,
      isPoints: false,
      userData: {},
      children: [innerPersistent],
    };
    const scene = makeScene([parent]);

    cleanupScene(scene as unknown as THREE.Scene);

    expect(scene.children).toEqual([]); // parent (ephemeral) gone
  });

  it('#9 — child with userData=undefined: treated as ephemeral, no throw', () => {
    const eph = makeMesh({ userData: undefined });
    const scene = makeScene([eph]);

    expect(() => cleanupScene(scene as unknown as THREE.Scene)).not.toThrow();
    expect(scene.children).toEqual([]);
  });

  it('#10 — object identity + transform preserved across detach/reattach', () => {
    const persistent = makeMesh({ persistent: true });
    persistent.position = { x: 5, y: 10, z: 15 };
    persistent.rotation = { x: 0.1, y: 0.2, z: 0.3 };
    persistent.scale = { x: 2, y: 2, z: 2 };
    persistent.visible = true;
    (persistent.userData as Record<string, unknown>).customTag = 'beam-pool';
    const originalRef = persistent;
    const scene = makeScene([persistent]);

    cleanupScene(scene as unknown as THREE.Scene);

    expect(scene.children).toEqual([originalRef]); // identity
    expect(persistent.position).toEqual({ x: 5, y: 10, z: 15 });
    expect(persistent.rotation).toEqual({ x: 0.1, y: 0.2, z: 0.3 });
    expect(persistent.scale).toEqual({ x: 2, y: 2, z: 2 });
    expect(persistent.visible).toBe(true);
    expect((persistent.userData as Record<string, unknown>).customTag).toBe('beam-pool');
    expect((persistent.userData as Record<string, unknown>).persistent).toBe(true);
  });

  it('#11 — ephemeral Points disposed (type-coverage widening)', () => {
    const eph = makeMesh({ isMesh: false, isPoints: true, geometry: makeGeometry(), material: makeMaterial() });
    const scene = makeScene([eph]);

    cleanupScene(scene as unknown as THREE.Scene);

    expect(scene.children).toEqual([]);
    expect(eph.geometry.dispose).toHaveBeenCalledTimes(1);
    expect((eph.material as ReturnType<typeof makeMaterial>).dispose).toHaveBeenCalledTimes(1);
  });

  it('#12 — ephemeral Line disposed (type-coverage widening)', () => {
    const eph = makeMesh({ isMesh: false, isLine: true, geometry: makeGeometry(), material: makeMaterial() });
    const scene = makeScene([eph]);

    cleanupScene(scene as unknown as THREE.Scene);

    expect(scene.children).toEqual([]);
    expect(eph.geometry.dispose).toHaveBeenCalledTimes(1);
    expect((eph.material as ReturnType<typeof makeMaterial>).dispose).toHaveBeenCalledTimes(1);
  });
});
