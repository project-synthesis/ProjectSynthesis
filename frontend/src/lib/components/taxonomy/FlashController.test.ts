// frontend/src/lib/components/taxonomy/FlashController.test.ts
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import * as THREE from 'three';
import { FlashController } from './FlashController';
import type { AnimationCoordinator } from './AnimationCoordinator';

function makeMesh(baseEmissive: number, currentIntensity?: number): THREE.Mesh {
  const geo = new THREE.SphereGeometry(1);
  const mat = new THREE.MeshStandardMaterial({ emissive: 0xff8800 });
  mat.emissiveIntensity = currentIntensity ?? baseEmissive;
  const mesh = new THREE.Mesh(geo, mat);
  mesh.userData.baseEmissive = baseEmissive;
  return mesh;
}

function makeCoordinator() {
  const handlers: Array<{ phase: string; fn: (delta: number) => void }> = [];
  const register = vi.fn((phase: string, fn: (delta: number) => void) => {
    const entry = { phase, fn };
    handlers.push(entry);
    return () => {
      const i = handlers.indexOf(entry);
      if (i >= 0) handlers.splice(i, 1);
    };
  });
  return { register, handlers } as unknown as AnimationCoordinator & {
    handlers: typeof handlers;
  };
}

describe('FlashController', () => {
  let coord: ReturnType<typeof makeCoordinator>;
  let meshes: Map<string, THREE.Mesh>;
  let highlighted: Set<string>;
  let fc: FlashController;

  beforeEach(() => {
    vi.useFakeTimers();
    coord = makeCoordinator();
    meshes = new Map();
    highlighted = new Set();
    fc = new FlashController({
      animationCoordinator: coord as unknown as AnimationCoordinator,
      getNodeMesh: (id) => meshes.get(id),
      isHighlighted: (id) => highlighted.has(id),
    });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('constructor registers exactly one impact-phase handler', () => {
    expect(coord.register).toHaveBeenCalledTimes(1);
    expect(coord.register).toHaveBeenCalledWith('impact', expect.any(Function));
  });

  it('flash captures baselineEmissive from userData.baseEmissive (true baseline, not current)', () => {
    const mesh = makeMesh(0.6, 1.2); // baseEmissive=0.6, current=1.2 (idle pulse inflated)
    meshes.set('n1', mesh);
    fc.flash('n1', new THREE.Color(0xff8800));
    expect(fc.isActive('n1')).toBe(true);
    // Indirect: re-fire would reuse this baseline — verified in test 9.
  });

  it('flash captures startIntensity = max(current, baseline) at acquire-time (B6)', () => {
    const mesh = makeMesh(0.6, 1.4);
    meshes.set('n1', mesh);
    fc.flash('n1', new THREE.Color(0xff8800));
    // Advance 0 — at elapsed=0, attack formula reads startIntensity directly.
    const start = performance.now();
    vi.setSystemTime(start);
    // Trigger the tick at elapsed=0 — emissiveIntensity should equal startIntensity (1.4)
    // because ease=0 → start + (peak-start)*0 = start.
    coord.handlers[0].fn(0);
    expect(mesh.material).toBeInstanceOf(THREE.MeshStandardMaterial);
    const mat = mesh.material as THREE.MeshStandardMaterial;
    expect(mat.emissiveIntensity).toBeCloseTo(1.4, 2);
  });

  it('active flash modulates emissiveIntensity per attack/hold/decay', () => {
    const mesh = makeMesh(0.6, 0.6);
    meshes.set('n1', mesh);
    const mat = mesh.material as THREE.MeshStandardMaterial;
    fc.flash('n1', new THREE.Color(0xff8800));
    // Attack (60ms in, halfway through 120ms attack): ease ≈ 1 - (1-0.5)^3 = 0.875
    vi.advanceTimersByTime(60);
    coord.handlers[0].fn(0.016);
    const peak = 0.6 + 1.6;
    const expectedAttack = 0.6 + (peak - 0.6) * 0.875;
    expect(mat.emissiveIntensity).toBeCloseTo(expectedAttack, 1);
  });

  it('attack ramps startIntensity → peak via cubic ease over 120ms (B7 timing)', () => {
    const mesh = makeMesh(0.6, 0.6);
    meshes.set('n1', mesh);
    const mat = mesh.material as THREE.MeshStandardMaterial;
    fc.flash('n1', new THREE.Color(0xff8800));
    // At exactly 120ms (end of attack), emissive should equal peak.
    vi.advanceTimersByTime(120);
    coord.handlers[0].fn(0.016);
    expect(mat.emissiveIntensity).toBeCloseTo(2.2, 1); // peak = 0.6 + 1.6
  });

  it('hold phase: emissive = peak for 580ms', () => {
    const mesh = makeMesh(0.6, 0.6);
    meshes.set('n1', mesh);
    const mat = mesh.material as THREE.MeshStandardMaterial;
    fc.flash('n1', new THREE.Color(0xff8800));
    // Midway through hold (120 + 290 = 410ms in)
    vi.advanceTimersByTime(410);
    coord.handlers[0].fn(0.016);
    expect(mat.emissiveIntensity).toBeCloseTo(2.2, 1);
  });

  it('decay phase: emissive ramps peak → baseline via cubic ease over 680ms (B7)', () => {
    const mesh = makeMesh(0.6, 0.6);
    meshes.set('n1', mesh);
    const mat = mesh.material as THREE.MeshStandardMaterial;
    fc.flash('n1', new THREE.Color(0xff8800));
    // Midway through decay: elapsed = 120 + 580 + 340 = 1040ms (340/680 through decay)
    vi.advanceTimersByTime(1040);
    coord.handlers[0].fn(0.016);
    const peak = 2.2;
    const t = 340 / 680;
    const ease = 1 - Math.pow(1 - t, 3);
    const expected = peak + (0.6 - peak) * ease;
    expect(mat.emissiveIntensity).toBeCloseTo(expected, 1);
  });

  it('cleanup at GLOW_TOTAL_MS=1380ms resets emissiveIntensity to baseline + writes correct color via isHighlighted (B7, B10)', () => {
    const mesh = makeMesh(0.6, 0.6);
    meshes.set('n1', mesh);
    const mat = mesh.material as THREE.MeshStandardMaterial;
    fc.flash('n1', new THREE.Color(0xff8800));
    vi.advanceTimersByTime(1380);
    coord.handlers[0].fn(0.016);
    expect(fc.isActive('n1')).toBe(false);
    expect(mat.emissiveIntensity).toBeCloseTo(0.6, 2);
    // Not highlighted → domain color
    expect(mat.emissive.getHex()).toBe(0xff8800);

    // Now test highlight branch: fresh flash with highlight set
    highlighted.add('n2');
    const mesh2 = makeMesh(0.6, 0.6);
    meshes.set('n2', mesh2);
    fc.flash('n2', new THREE.Color(0xff8800));
    vi.advanceTimersByTime(1380);
    coord.handlers[0].fn(0.016);
    const mat2 = mesh2.material as THREE.MeshStandardMaterial;
    expect(mat2.emissive.getHex()).toBe(0x00ffff); // HIGHLIGHT_COLOR
  });

  it('mid-flash re-fire reuses prior baselineEmissive (F19 invariant)', () => {
    const mesh = makeMesh(0.6, 0.6);
    meshes.set('n1', mesh);
    const mat = mesh.material as THREE.MeshStandardMaterial;
    fc.flash('n1', new THREE.Color(0xff8800));
    // Inflate emissiveIntensity manually (simulating mid-flash state)
    mat.emissiveIntensity = 1.8;
    fc.flash('n1', new THREE.Color(0xff8800)); // refire
    // Advance to cleanup and verify it lands at original 0.6, not 1.8
    vi.advanceTimersByTime(1380);
    coord.handlers[0].fn(0.016);
    expect(mat.emissiveIntensity).toBeCloseTo(0.6, 2);
  });

  it('mid-flash re-fire reuses prior startIntensity (B6)', () => {
    const mesh = makeMesh(0.6, 1.4); // startIntensity captured at 1.4 on first flash
    meshes.set('n1', mesh);
    const mat = mesh.material as THREE.MeshStandardMaterial;
    fc.flash('n1', new THREE.Color(0xff8800));
    mat.emissiveIntensity = 0.6; // current dropped post-first-flash
    fc.flash('n1', new THREE.Color(0xff8800)); // refire
    // At elapsed=0 the attack reads startIntensity — should still be 1.4 (not lowered to 0.6)
    coord.handlers[0].fn(0);
    expect(mat.emissiveIntensity).toBeCloseTo(1.4, 1);
  });

  it('mid-flash re-fire replaces color but keeps baselineEmissive + startIntensity', () => {
    const mesh = makeMesh(0.6, 0.6);
    meshes.set('n1', mesh);
    const mat = mesh.material as THREE.MeshStandardMaterial;
    fc.flash('n1', new THREE.Color(0xff8800));
    fc.flash('n1', new THREE.Color(0xaa00ff)); // refire with different color
    // The domainEmissive should now be the new color — verified by checking
    // emissive after tick (during the burst it copies domainEmissive).
    vi.advanceTimersByTime(60);
    coord.handlers[0].fn(0.016);
    expect(mat.emissive.getHex()).toBe(0xaa00ff);
  });

  it('isActive returns true mid-flash, false after cleanup', () => {
    const mesh = makeMesh(0.6, 0.6);
    meshes.set('n1', mesh);
    fc.flash('n1', new THREE.Color(0xff8800));
    expect(fc.isActive('n1')).toBe(true);
    vi.advanceTimersByTime(1380);
    coord.handlers[0].fn(0.016);
    expect(fc.isActive('n1')).toBe(false);
  });

  it('dispose cancels handler + restores baseline emissive on every active flash + writes correct color via isHighlighted (B9)', () => {
    const meshA = makeMesh(0.6, 0.6);
    const meshB = makeMesh(0.5, 0.5);
    meshes.set('a', meshA);
    meshes.set('b', meshB);
    highlighted.add('b');
    fc.flash('a', new THREE.Color(0xff8800));
    fc.flash('b', new THREE.Color(0x22ff88));
    // Inflate both emissive intensities
    (meshA.material as THREE.MeshStandardMaterial).emissiveIntensity = 2.0;
    (meshB.material as THREE.MeshStandardMaterial).emissiveIntensity = 2.0;
    fc.dispose();
    expect((meshA.material as THREE.MeshStandardMaterial).emissiveIntensity).toBeCloseTo(0.6, 2);
    expect((meshA.material as THREE.MeshStandardMaterial).emissive.getHex()).toBe(0xff8800);
    expect((meshB.material as THREE.MeshStandardMaterial).emissiveIntensity).toBeCloseTo(0.5, 2);
    expect((meshB.material as THREE.MeshStandardMaterial).emissive.getHex()).toBe(0x00ffff);
    // Idempotent
    expect(() => fc.dispose()).not.toThrow();
    // Handler unregistered
    expect(coord.handlers.length).toBe(0);
  });

  it('missing mesh on tick silently removes entry without throwing', () => {
    const mesh = makeMesh(0.6, 0.6);
    meshes.set('n1', mesh);
    fc.flash('n1', new THREE.Color(0xff8800));
    meshes.delete('n1'); // mesh disappears mid-flash
    expect(() => coord.handlers[0].fn(0.016)).not.toThrow();
    expect(fc.isActive('n1')).toBe(false);
  });
});
