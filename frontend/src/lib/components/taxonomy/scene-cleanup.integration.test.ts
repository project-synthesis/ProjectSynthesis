// frontend/src/lib/components/taxonomy/scene-cleanup.integration.test.ts
//
// Real-Three integration tests for cleanupScene. Uses actual THREE.js
// classes (Scene, Mesh, LineSegments, Line, Points, geometries, materials,
// AmbientLight) — NOT mocks. Catches mock/real-Three semantics drift.
//
// vitest with jsdom runs real Three.js scene-graph manipulation fine.
// WebGLRenderer.render() is NOT invoked (jsdom has no GL context).
// BufferGeometry.dispose() and Material.dispose() emit 'dispose' events
// independent of GL context — observable via event listeners.
//
// Spec: docs/superpowers/specs/2026-05-16-lifecycle-hardening-design.md §5.7
import { describe, it, expect, vi } from 'vitest';
import * as THREE from 'three';
import { cleanupScene } from './scene-cleanup';

function spyOnDispose(target: { dispose: () => void }): ReturnType<typeof vi.spyOn> {
  return vi.spyOn(target, 'dispose');
}

describe('cleanupScene — real-Three integration', () => {
  it('INT-1 — persistent Group survives; ephemeral Mesh disposed', () => {
    const scene = new THREE.Scene();
    const persistent = new THREE.Group();
    persistent.userData.persistent = true;
    const ephGeo = new THREE.BoxGeometry();
    const ephMat = new THREE.MeshBasicMaterial();
    const eph = new THREE.Mesh(ephGeo, ephMat);

    const geoSpy = spyOnDispose(ephGeo);
    const matSpy = spyOnDispose(ephMat);

    scene.add(persistent);
    scene.add(eph);

    cleanupScene(scene);

    expect(scene.children).toEqual([persistent]);
    expect(geoSpy).toHaveBeenCalledTimes(1);
    expect(matSpy).toHaveBeenCalledTimes(1);
  });

  it('INT-2 — ephemeral LineSegments disposed', () => {
    const scene = new THREE.Scene();
    const geo = new THREE.BufferGeometry();
    geo.setAttribute('position', new THREE.BufferAttribute(new Float32Array([0, 0, 0, 1, 0, 0]), 3));
    const mat = new THREE.LineBasicMaterial();
    const line = new THREE.LineSegments(geo, mat);

    const geoSpy = spyOnDispose(geo);
    const matSpy = spyOnDispose(mat);

    scene.add(line);
    cleanupScene(scene);

    expect(scene.children).toEqual([]);
    expect(geoSpy).toHaveBeenCalledTimes(1);
    expect(matSpy).toHaveBeenCalledTimes(1);
  });

  it('INT-3 — ephemeral Line disposed (type-coverage widening)', () => {
    const scene = new THREE.Scene();
    const geo = new THREE.BufferGeometry();
    geo.setAttribute('position', new THREE.BufferAttribute(new Float32Array([0, 0, 0, 1, 0, 0]), 3));
    const mat = new THREE.LineBasicMaterial();
    const line = new THREE.Line(geo, mat);

    const geoSpy = spyOnDispose(geo);
    const matSpy = spyOnDispose(mat);

    scene.add(line);
    cleanupScene(scene);

    expect(scene.children).toEqual([]);
    expect(geoSpy).toHaveBeenCalledTimes(1);
    expect(matSpy).toHaveBeenCalledTimes(1);
  });

  it('INT-4 — ephemeral Points disposed (type-coverage widening; closes per-domain PointsMaterial leak)', () => {
    const scene = new THREE.Scene();
    const geo = new THREE.BufferGeometry();
    geo.setAttribute('position', new THREE.BufferAttribute(new Float32Array([0, 0, 0]), 3));
    const mat = new THREE.PointsMaterial();
    const points = new THREE.Points(geo, mat);

    const geoSpy = spyOnDispose(geo);
    const matSpy = spyOnDispose(mat);

    scene.add(points);
    cleanupScene(scene);

    expect(scene.children).toEqual([]);
    expect(geoSpy).toHaveBeenCalledTimes(1);
    expect(matSpy).toHaveBeenCalledTimes(1);
  });

  it('INT-5 — persistent AmbientLight preserved; no exceptions', () => {
    const scene = new THREE.Scene();
    const light = new THREE.AmbientLight(0xffffff, 0.3);
    light.userData.persistent = true;

    scene.add(light);
    expect(() => cleanupScene(scene)).not.toThrow();

    expect(scene.children).toEqual([light]);
  });

  it('INT-6 — persistent Group with userData fields + transform preserved', () => {
    const scene = new THREE.Scene();
    const persistent = new THREE.Group();
    persistent.userData.persistent = true;
    persistent.userData.customTag = 'beam-pool';
    persistent.position.set(5, 10, 15);
    persistent.rotation.set(0.1, 0.2, 0.3);
    persistent.scale.set(2, 2, 2);
    persistent.visible = true;
    const originalRef = persistent;

    scene.add(persistent);
    cleanupScene(scene);

    expect(scene.children[0]).toBe(originalRef);
    expect(persistent.position.x).toBe(5);
    expect(persistent.position.y).toBe(10);
    expect(persistent.position.z).toBe(15);
    expect(persistent.rotation.x).toBeCloseTo(0.1);
    expect(persistent.rotation.y).toBeCloseTo(0.2);
    expect(persistent.rotation.z).toBeCloseTo(0.3);
    expect(persistent.scale.x).toBe(2);
    expect(persistent.visible).toBe(true);
    expect(persistent.userData.customTag).toBe('beam-pool');
    expect(persistent.userData.persistent).toBe(true);
  });
});
