/**
 * PlasmaBeam — Cycle 1: onImpact callback + spec-aligned timing constants.
 *
 * Brand: `.claude/skills/brand-guidelines/references/3d-visualization.md` canon F7
 * Spec:  `docs/specs/2026-04-05-data-as-matter-design.md` § Trigger Mapping
 *
 * The state machine (firing → sustain → terminate → idle) lives in PlasmaBeam.
 * Cycle 1 adds an optional `onImpact` callback to `BeamConfig` that fires
 * exactly once at the firing → sustain transition (i.e., the moment the
 * catenary curve has fully extended to the target). All click-impact reactions
 * (cluster-physics ripple, plasma envelopement, emissive flash) wire through
 * this callback so they synchronize to actual beam arrival — fixing the
 * pre-existing anti-causal-ordering bug where ripple fired immediately on
 * click while the beam still travelled.
 *
 * Cycle 1 also realigns timing constants to the Data-as-Matter spec:
 *   - FIRING_MS:    600 → 300  (spec compliance)
 *   - TERMINATE_MS: 800 → 250  (snappier dissipation; rapid-click responsiveness)
 *
 * The tests below pin both invariants. They should FAIL on main's pre-fix code
 * (no `onImpact` field; FIRING_MS=600; TERMINATE_MS=800) and PASS once the
 * Cycle 1 implementation lands.
 */
import { describe, expect, test, vi } from 'vitest';
import * as THREE from 'three';
import { PlasmaBeam, FIRING_MS, TERMINATE_MS } from './PlasmaBeam';

function makeFixture() {
  const beam = new PlasmaBeam();
  const target = new THREE.Object3D();
  target.position.set(10, 0, 0);
  const camera = new THREE.PerspectiveCamera(75, 1, 0.1, 1000);
  camera.position.set(0, 0, 5);
  camera.lookAt(0, 0, 0);
  camera.updateMatrixWorld(true);
  const origin = new THREE.Vector3(0, -1, -0.99).unproject(camera);
  return { beam, target, camera, origin };
}

describe('PlasmaBeam — visual timing constants', () => {
  test('FIRING_MS is 700 (perceptible energy transmission — beam genuinely travels)', () => {
    // Earlier 300ms (a strict Data-as-Matter spec value) read as
    // "beam appears, then impact" with no transmission perception.
    // 700ms gives the user time to see the leading edge propagate
    // along the catenary before the cluster reacts.
    expect(FIRING_MS).toBe(700);
  });

  test('TERMINATE_MS is 250 (snappy dissipation; reduces residual on rapid clicks)', () => {
    expect(TERMINATE_MS).toBe(250);
  });
});

describe('PlasmaBeam — onImpact callback', () => {
  test('fires exactly once at the firing → sustain edge', () => {
    const { beam, target, camera, origin } = makeFixture();
    const onImpact = vi.fn();

    beam.fire(
      target,
      { color: new THREE.Color(0xff00ff), radius: 0.1, sustainMs: 500, onImpact },
      origin,
      camera,
    );
    expect(beam.state).toBe('firing');
    expect(onImpact).not.toHaveBeenCalled();

    // Almost-finished firing — still firing, callback not fired
    beam.update((FIRING_MS - 10) / 1000, origin, camera);
    expect(beam.state).toBe('firing');
    expect(onImpact).not.toHaveBeenCalled();

    // Cross the firing → sustain edge
    beam.update(20 / 1000, origin, camera);
    expect(beam.state).toBe('sustain');
    expect(onImpact).toHaveBeenCalledTimes(1);
  });

  test('does NOT re-fire during sustain or terminate phases', () => {
    const { beam, target, camera, origin } = makeFixture();
    const onImpact = vi.fn();

    beam.fire(
      target,
      { color: new THREE.Color(0x00ffff), radius: 0.1, sustainMs: 200, onImpact },
      origin,
      camera,
    );
    // Drive past firing → sustain
    beam.update((FIRING_MS + 10) / 1000, origin, camera);
    expect(beam.state).toBe('sustain');
    expect(onImpact).toHaveBeenCalledTimes(1);

    // Tick within sustain — no re-fire
    beam.update(0.05, origin, camera);
    beam.update(0.05, origin, camera);
    expect(onImpact).toHaveBeenCalledTimes(1);

    // Cross sustain → terminate
    beam.update(0.2, origin, camera);
    expect(beam.state).toBe('terminate');
    expect(onImpact).toHaveBeenCalledTimes(1);

    // Through terminate → idle
    beam.update((TERMINATE_MS + 10) / 1000, origin, camera);
    expect(beam.state).toBe('idle');
    expect(onImpact).toHaveBeenCalledTimes(1);
  });

  test('does NOT fire if beam is force-terminated mid-firing', () => {
    const { beam, target, camera, origin } = makeFixture();
    const onImpact = vi.fn();

    beam.fire(
      target,
      { color: new THREE.Color(0xff8800), radius: 0.1, sustainMs: 500, onImpact },
      origin,
      camera,
    );
    beam.update(0.05, origin, camera); // partway through firing
    expect(beam.state).toBe('firing');

    beam.terminate();
    expect(beam.state).toBe('terminate');
    expect(onImpact).not.toHaveBeenCalled();

    beam.update((TERMINATE_MS + 10) / 1000, origin, camera);
    expect(beam.state).toBe('idle');
    expect(onImpact).not.toHaveBeenCalled();
  });

  test('omitting onImpact in BeamConfig is supported (back-compat with existing call sites)', () => {
    const { beam, target, camera, origin } = makeFixture();

    expect(() => {
      beam.fire(
        target,
        { color: new THREE.Color(0xffffff), radius: 0.1, sustainMs: 100 },
        origin,
        camera,
      );
      beam.update((FIRING_MS + 10) / 1000, origin, camera);
      expect(beam.state).toBe('sustain');
      beam.update(0.2, origin, camera);
      expect(beam.state).toBe('terminate');
      beam.update((TERMINATE_MS + 10) / 1000, origin, camera);
      expect(beam.state).toBe('idle');
    }).not.toThrow();
  });

  test('uHead uniform progresses 0 → 1 during firing (visible leading-edge extension)', () => {
    const { beam, target, camera, origin } = makeFixture();
    beam.fire(
      target,
      { color: new THREE.Color(0x00e5ff), radius: 0.1, sustainMs: 100 },
      origin,
      camera,
    );
    // Internal uniform on the material — exposed via `mesh.material`.
    const mat = beam.mesh.material as THREE.ShaderMaterial;
    expect(mat.uniforms.uHead.value).toBe(0);

    // Drive 25% through firing
    beam.update(FIRING_MS * 0.25 / 1000, origin, camera);
    expect(mat.uniforms.uHead.value).toBeGreaterThan(0);
    expect(mat.uniforms.uHead.value).toBeLessThan(0.5);

    // Mid-firing
    beam.update(FIRING_MS * 0.25 / 1000, origin, camera);
    expect(mat.uniforms.uHead.value).toBeCloseTo(0.5, 1);

    // Complete firing — head clamps at 1
    beam.update((FIRING_MS * 0.55) / 1000, origin, camera);
    expect(beam.state).toBe('sustain');
    expect(mat.uniforms.uHead.value).toBeCloseTo(1, 2);
  });

  test('uHead stays at 1 during sustain and terminate phases', () => {
    const { beam, target, camera, origin } = makeFixture();
    beam.fire(
      target,
      { color: new THREE.Color(0xff0000), radius: 0.1, sustainMs: 80 },
      origin,
      camera,
    );
    // Drive into sustain
    beam.update((FIRING_MS + 10) / 1000, origin, camera);
    const mat = beam.mesh.material as THREE.ShaderMaterial;
    expect(mat.uniforms.uHead.value).toBeCloseTo(1, 2);

    // Mid-sustain — head still 1
    beam.update(0.04, origin, camera);
    expect(mat.uniforms.uHead.value).toBeCloseTo(1, 2);

    // Terminate phase
    beam.update(0.1, origin, camera);
    expect(beam.state).toBe('terminate');
    expect(mat.uniforms.uHead.value).toBeCloseTo(1, 2);
  });

  test('re-firing the same beam wires up the new callback (and old one does not re-fire)', () => {
    const { beam, target, camera, origin } = makeFixture();
    const onImpactA = vi.fn();
    const onImpactB = vi.fn();

    // First lifecycle
    beam.fire(
      target,
      { color: new THREE.Color(0xff0000), radius: 0.1, sustainMs: 50, onImpact: onImpactA },
      origin,
      camera,
    );
    beam.update((FIRING_MS + 10) / 1000, origin, camera);
    expect(onImpactA).toHaveBeenCalledTimes(1);
    beam.update(0.1, origin, camera); // exceed sustain
    beam.update((TERMINATE_MS + 10) / 1000, origin, camera);
    expect(beam.state).toBe('idle');

    // Second lifecycle with different callback
    beam.fire(
      target,
      { color: new THREE.Color(0x00ff00), radius: 0.1, sustainMs: 50, onImpact: onImpactB },
      origin,
      camera,
    );
    beam.update((FIRING_MS + 10) / 1000, origin, camera);

    // Old callback must not be invoked again; new one fires exactly once.
    expect(onImpactA).toHaveBeenCalledTimes(1);
    expect(onImpactB).toHaveBeenCalledTimes(1);
  });
});
