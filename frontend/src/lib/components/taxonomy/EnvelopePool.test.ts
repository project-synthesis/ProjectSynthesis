/**
 * EnvelopePool — Cycle 2: plasma envelopement pool primitives.
 *
 * Brand: `.claude/skills/brand-guidelines/references/3d-visualization.md`
 *        canon F19 (envelopement burst)
 *
 * The pool mirrors `BeamPool.ts` exactly — pre-allocated 10 instances, each
 * with its own `ShaderMaterial` (per-instance uniforms), mesh.geometry
 * swapped between shared cluster (`IcosahedronGeometry(1, 2)`) and shared
 * domain (`DodecahedronGeometry(1, 2)`) singletons on `acquire`. Envelopes
 * parent to the pool's own `THREE.Group` (not the target node group), so a
 * `rebuildScene` cleanup of node groups mid-effect doesn't crash; missing
 * target is handled by per-frame existence-check in `update()`.
 *
 * State machine: idle → attack (220ms) → hold (180ms) → decay (580ms) → idle
 * Total active duration: 980ms. Cubic-ease-out for attack + decay. Earlier
 * 120/180/500 (total 800ms) read as a punchy "thud" alongside the emissive
 * flash; the smoothness re-tune extended attack + decay for a gradient swell.
 */
import { describe, expect, test } from 'vitest';
import * as THREE from 'three';
import {
  EnvelopePool,
  ATTACK_MS,
  HOLD_MS,
  DECAY_MS,
  PEAK_SWELL,
} from './EnvelopePool';

function makeTarget(x = 5, y = 0, z = 0): THREE.Object3D {
  const obj = new THREE.Object3D();
  obj.position.set(x, y, z);
  obj.updateMatrixWorld(true);
  return obj;
}

describe('EnvelopePool — phase duration constants (canon F19)', () => {
  test('ATTACK_MS is 220 (smoother swell — eases pressure off the impact onset)', () => {
    // Earlier 120ms read as a punchy "thud" landing alongside the
    // emissive flash. 220ms lets the plasma skin grow more
    // organically across ~13 frames at 60fps.
    expect(ATTACK_MS).toBe(220);
  });

  test('HOLD_MS is 180 (sustained plasma skin during beam sustain)', () => {
    expect(HOLD_MS).toBe(180);
  });

  test('DECAY_MS is 580 (smoother slow dissipation across ~35 frames at 60fps)', () => {
    expect(DECAY_MS).toBe(580);
  });

  test('PEAK_SWELL is 1.18 (visible plasma skin, no neighbor overlap)', () => {
    expect(PEAK_SWELL).toBeCloseTo(1.18);
  });

  test('total envelope lifecycle is 980 ms (~beam sustain window)', () => {
    expect(ATTACK_MS + HOLD_MS + DECAY_MS).toBe(980);
  });
});

describe('EnvelopePool — construction and API', () => {
  test('exposes group, acquire, update, dispose', () => {
    const pool = new EnvelopePool();
    expect(pool.group).toBeInstanceOf(THREE.Group);
    expect(typeof pool.acquire).toBe('function');
    expect(typeof pool.update).toBe('function');
    expect(typeof pool.dispose).toBe('function');
    pool.dispose();
  });

  test('pre-allocates capacity of 10 envelope meshes', () => {
    const pool = new EnvelopePool();
    expect(pool.group.children.length).toBe(10);
    pool.dispose();
  });

  test('all envelopes start hidden + idle (no visible artifacts on mount)', () => {
    const pool = new EnvelopePool();
    for (const child of pool.group.children) {
      const mesh = child as THREE.Mesh;
      expect(mesh.visible).toBe(false);
    }
    pool.dispose();
  });
});

describe('EnvelopePool — acquire + state machine', () => {
  test('acquire makes a mesh visible and parents it to the pool group', () => {
    const pool = new EnvelopePool();
    const target = makeTarget();
    const visibleBefore = pool.group.children.filter(c => (c as THREE.Mesh).visible).length;
    expect(visibleBefore).toBe(0);

    const ok = pool.acquire(target, 1.5, 'cluster', new THREE.Color(0xff00ff));
    expect(ok).toBe(true);

    const visibleAfter = pool.group.children.filter(c => (c as THREE.Mesh).visible).length;
    expect(visibleAfter).toBe(1);
    pool.dispose();
  });

  test('attack phase ramps opacity 0 → 1 with cubic ease-out', () => {
    const pool = new EnvelopePool();
    const target = makeTarget();
    pool.acquire(target, 1.0, 'cluster', new THREE.Color(0x00e5ff));

    const visibleMesh = pool.group.children.find(c => (c as THREE.Mesh).visible) as THREE.Mesh;
    const mat = visibleMesh.material as THREE.ShaderMaterial;
    expect(mat.uniforms.uOpacity.value).toBe(0);

    // Half-way through attack: cubic ease-out at t=0.5 = 1 - (0.5)^3 = 0.875
    pool.update(ATTACK_MS / 2 / 1000);
    expect(mat.uniforms.uOpacity.value).toBeGreaterThan(0.5);
    expect(mat.uniforms.uOpacity.value).toBeLessThanOrEqual(1.0);

    // Complete attack: opacity at full
    pool.update((ATTACK_MS / 2 + 5) / 1000);
    expect(mat.uniforms.uOpacity.value).toBeCloseTo(1.0, 2);
    pool.dispose();
  });

  test('hold phase keeps opacity at 1.0 and scale at PEAK_SWELL × baseScale', () => {
    const pool = new EnvelopePool();
    const target = makeTarget();
    const baseScale = 2.0;
    pool.acquire(target, baseScale, 'cluster', new THREE.Color(0x00e5ff));

    const mesh = pool.group.children.find(c => (c as THREE.Mesh).visible) as THREE.Mesh;
    const mat = mesh.material as THREE.ShaderMaterial;

    // Drive into hold (past attack)
    pool.update((ATTACK_MS + 10) / 1000);
    expect(mat.uniforms.uOpacity.value).toBeCloseTo(1.0, 2);
    expect(mesh.scale.x).toBeCloseTo(baseScale * PEAK_SWELL, 2);

    // Mid-hold: still at peak
    pool.update((HOLD_MS / 2) / 1000);
    expect(mat.uniforms.uOpacity.value).toBeCloseTo(1.0, 2);
    expect(mesh.scale.x).toBeCloseTo(baseScale * PEAK_SWELL, 2);
    pool.dispose();
  });

  test('decay phase ramps opacity 1 → 0 and scale PEAK_SWELL → 1.0 (cubic ease-out)', () => {
    const pool = new EnvelopePool();
    const target = makeTarget();
    pool.acquire(target, 1.0, 'cluster', new THREE.Color(0x00e5ff));

    const mesh = pool.group.children.find(c => (c as THREE.Mesh).visible) as THREE.Mesh;
    const mat = mesh.material as THREE.ShaderMaterial;

    // Drive past attack + hold into decay
    pool.update((ATTACK_MS + HOLD_MS + 10) / 1000);
    // Halfway through decay
    pool.update((DECAY_MS / 2) / 1000);
    expect(mat.uniforms.uOpacity.value).toBeLessThan(1.0);
    expect(mat.uniforms.uOpacity.value).toBeGreaterThan(0.0);
    expect(mesh.scale.x).toBeLessThan(PEAK_SWELL);
    expect(mesh.scale.x).toBeGreaterThan(1.0);
    pool.dispose();
  });

  test('decay completion returns mesh to idle (hidden, scale=1)', () => {
    const pool = new EnvelopePool();
    const target = makeTarget();
    pool.acquire(target, 1.0, 'cluster', new THREE.Color(0xff00ff));

    const mesh = pool.group.children.find(c => (c as THREE.Mesh).visible) as THREE.Mesh;
    const mat = mesh.material as THREE.ShaderMaterial;

    pool.update((ATTACK_MS + HOLD_MS + DECAY_MS + 10) / 1000);
    expect(mesh.visible).toBe(false);
    expect(mat.uniforms.uOpacity.value).toBe(0);
    pool.dispose();
  });
});

describe('EnvelopePool — geometry shape selection', () => {
  test('cluster shape uses IcosahedronGeometry', () => {
    const pool = new EnvelopePool();
    const target = makeTarget();
    pool.acquire(target, 1.0, 'cluster', new THREE.Color(0xff00ff));

    const mesh = pool.group.children.find(c => (c as THREE.Mesh).visible) as THREE.Mesh;
    expect(mesh.geometry).toBeInstanceOf(THREE.IcosahedronGeometry);
    pool.dispose();
  });

  test('domain shape uses DodecahedronGeometry', () => {
    const pool = new EnvelopePool();
    const target = makeTarget();
    pool.acquire(target, 1.0, 'domain', new THREE.Color(0xff00ff));

    const mesh = pool.group.children.find(c => (c as THREE.Mesh).visible) as THREE.Mesh;
    expect(mesh.geometry).toBeInstanceOf(THREE.DodecahedronGeometry);
    pool.dispose();
  });

  test('shape can be reassigned across acquires (mesh geometry swapped)', () => {
    const pool = new EnvelopePool();
    const target = makeTarget();

    pool.acquire(target, 1.0, 'cluster', new THREE.Color(0xff0000));
    let mesh = pool.group.children.find(c => (c as THREE.Mesh).visible) as THREE.Mesh;
    expect(mesh.geometry).toBeInstanceOf(THREE.IcosahedronGeometry);
    // Drive to idle
    pool.update((ATTACK_MS + HOLD_MS + DECAY_MS + 10) / 1000);

    pool.acquire(target, 1.0, 'domain', new THREE.Color(0x00ff00));
    mesh = pool.group.children.find(c => (c as THREE.Mesh).visible) as THREE.Mesh;
    expect(mesh.geometry).toBeInstanceOf(THREE.DodecahedronGeometry);
    pool.dispose();
  });
});

describe('EnvelopePool — color uniform applied per-acquire', () => {
  test('acquire copies color into uColorStart and uColorEnd uniforms', () => {
    const pool = new EnvelopePool();
    const target = makeTarget();
    const color = new THREE.Color(0xb44aff); // backend violet
    pool.acquire(target, 1.0, 'cluster', color);

    const mesh = pool.group.children.find(c => (c as THREE.Mesh).visible) as THREE.Mesh;
    const mat = mesh.material as THREE.ShaderMaterial;
    expect(mat.uniforms.uColorStart.value.getHex()).toBe(color.getHex());
    expect(mat.uniforms.uColorEnd.value.getHex()).toBe(color.getHex());
    pool.dispose();
  });
});

describe('EnvelopePool — pool exhaustion + re-acquire semantics', () => {
  test('acquire returns true when pool has free instances, false when exhausted', () => {
    const pool = new EnvelopePool();
    const targets = Array.from({ length: 11 }, (_, i) => makeTarget(i, 0, 0));

    for (let i = 0; i < 10; i++) {
      expect(pool.acquire(targets[i], 1.0, 'cluster', new THREE.Color())).toBe(true);
    }
    // 11th acquire on a fresh target: pool exhausted
    expect(pool.acquire(targets[10], 1.0, 'cluster', new THREE.Color())).toBe(false);
    pool.dispose();
  });

  test('re-acquiring the same target while still active restarts state to attack (no double-allocation)', () => {
    const pool = new EnvelopePool();
    const target = makeTarget();
    pool.acquire(target, 1.0, 'cluster', new THREE.Color(0xff00ff));

    // Drive into hold phase
    pool.update((ATTACK_MS + 50) / 1000);
    const visibleCountAfterFirst = pool.group.children.filter(c => (c as THREE.Mesh).visible).length;
    expect(visibleCountAfterFirst).toBe(1);

    // Re-acquire on same target
    pool.acquire(target, 1.0, 'cluster', new THREE.Color(0x00ffff));
    const visibleCountAfterReacquire = pool.group.children.filter(c => (c as THREE.Mesh).visible).length;
    // Same instance reused — still exactly 1 visible mesh
    expect(visibleCountAfterReacquire).toBe(1);

    // The reused instance is back in attack with new color
    const mesh = pool.group.children.find(c => (c as THREE.Mesh).visible) as THREE.Mesh;
    const mat = mesh.material as THREE.ShaderMaterial;
    expect(mat.uniforms.uColorStart.value.getHex()).toBe(0x00ffff);
    expect(mat.uniforms.uOpacity.value).toBe(0); // re-attack starts at 0
    pool.dispose();
  });
});

describe('EnvelopePool — missing target handling', () => {
  test('update terminates an envelope if its target group is removed from the scene graph', () => {
    const pool = new EnvelopePool();
    const target = makeTarget();
    const parentGroup = new THREE.Group();
    parentGroup.add(target);
    pool.acquire(target, 1.0, 'cluster', new THREE.Color(0xff00ff));

    pool.update(ATTACK_MS / 2 / 1000); // mid-attack
    let mesh = pool.group.children.find(c => (c as THREE.Mesh).visible) as THREE.Mesh;
    expect(mesh).toBeDefined();

    // Simulate the target being detached from the scene graph (rebuildScene
    // disposing node groups mid-effect). The pool tracks the group reference
    // by identity; we sever that by nulling its `parent` and dropping our
    // own reference. The envelope should detect the missing target and
    // gracefully terminate.
    parentGroup.remove(target);

    // Drive the pool — envelope should still be visible until next update
    // detects target unreachable. The plan calls for "missing target →
    // terminates immediately" which we implement by checking parent === null.
    pool.update(0.016);

    // Idle envelope is hidden
    const stillVisible = pool.group.children.filter(c => (c as THREE.Mesh).visible).length;
    expect(stillVisible).toBe(0);
    pool.dispose();
  });
});

describe('EnvelopePool — disposal contract', () => {
  test('dispose can be called twice without error', () => {
    const pool = new EnvelopePool();
    expect(() => {
      pool.dispose();
      pool.dispose();
    }).not.toThrow();
  });

  test('dispose releases material on every envelope instance', () => {
    const pool = new EnvelopePool();
    const materials = pool.group.children.map(
      c => (c as THREE.Mesh).material as THREE.ShaderMaterial,
    );
    let disposeCount = 0;
    for (const m of materials) {
      const original = m.dispose.bind(m);
      m.dispose = () => {
        disposeCount++;
        original();
      };
    }
    pool.dispose();
    expect(disposeCount).toBe(10);
  });
});
