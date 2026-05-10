/**
 * Focus-math test — Pattern Graph 3D scope.
 *
 * Spec: docs/superpowers/specs/2026-05-09-pattern-graph-depth-design.md § 4.6
 *
 * Replaces the smoke-only "focusOn does not throw" assertion at
 * `TopologyRenderer.test.ts:166` with real correctness assertions on the
 * extracted pure function `computeFocusEndpoint`.
 *
 * Three categories asserted:
 *
 *   1. **Adaptive-distance clamping** — `distance` is clamped to
 *      `[minDistance, maxDistance]`. Inputs above `maxDistance` produce a
 *      camera offset of magnitude `maxDistance`; inputs below `minDistance`
 *      produce magnitude `minDistance`. The OrbitControls config in
 *      `TopologyRenderer` is `[3, 200]`.
 *
 *   2. **Zero-length direction guard** — when the source camera position
 *      equals the source orbit target (subVectors → zero vector → normalize
 *      → NaN cascade), the helper substitutes a fallback unit axis (+Z by
 *      convention — matches the camera's default look direction in the
 *      scene's coordinate frame) so the final position is `Number.isFinite`
 *      on every component.
 *
 *   3. **Normal-case correctness** — when the inputs are well-formed and
 *      the requested distance is within `[minDistance, maxDistance]`, the
 *      output magnitude equals the requested distance (no implicit clamp).
 *
 * The function uses real Three.js (no mock) — the test depends on Vector3
 * arithmetic being numerically correct.
 */
import * as THREE from 'three';
import { describe, expect, test } from 'vitest';

import { computeFocusEndpoint } from './focus-math';

describe('computeFocusEndpoint — adaptive-distance clamping', () => {
  test('distance above maxDistance is clamped to maxDistance', () => {
    const startPos = new THREE.Vector3(0, 0, 80);
    const startTarget = new THREE.Vector3(0, 0, 0);
    const target = new THREE.Vector3(10, 0, 0);
    const result = computeFocusEndpoint(startPos, startTarget, target, 300, {
      minDistance: 3,
      maxDistance: 200,
    });
    // The camera offset (endPos - target) magnitude must equal maxDistance.
    const offset = new THREE.Vector3().subVectors(result.endPos, target);
    expect(offset.length()).toBeCloseTo(200, 5);
  });

  test('distance below minDistance is clamped to minDistance', () => {
    const startPos = new THREE.Vector3(0, 0, 80);
    const startTarget = new THREE.Vector3(0, 0, 0);
    const target = new THREE.Vector3(10, 0, 0);
    const result = computeFocusEndpoint(startPos, startTarget, target, 1, {
      minDistance: 3,
      maxDistance: 200,
    });
    const offset = new THREE.Vector3().subVectors(result.endPos, target);
    expect(offset.length()).toBeCloseTo(3, 5);
  });

  test('endTarget always equals the requested target (no clamp on target itself)', () => {
    const startPos = new THREE.Vector3(0, 0, 80);
    const startTarget = new THREE.Vector3(0, 0, 0);
    const target = new THREE.Vector3(123, 456, 789);
    const result = computeFocusEndpoint(startPos, startTarget, target, 100, {
      minDistance: 3,
      maxDistance: 200,
    });
    expect(result.endTarget.x).toBeCloseTo(123, 5);
    expect(result.endTarget.y).toBeCloseTo(456, 5);
    expect(result.endTarget.z).toBeCloseTo(789, 5);
  });
});

describe('computeFocusEndpoint — zero-length direction guard', () => {
  test('startPos == startTarget: endPos has all-finite components', () => {
    // Pathological input: camera and orbit target coincide. `subVectors`
    // gives (0,0,0); `normalize()` produces NaN on every component;
    // arithmetic propagates NaN into endPos → camera writes NaN → render
    // breaks. The guard substitutes a fallback unit axis so endPos stays
    // finite.
    const startPos = new THREE.Vector3(5, 5, 5);
    const startTarget = new THREE.Vector3(5, 5, 5);
    const target = new THREE.Vector3(10, 0, 0);
    const result = computeFocusEndpoint(startPos, startTarget, target, 20, {
      minDistance: 3,
      maxDistance: 200,
    });
    expect(Number.isFinite(result.endPos.x)).toBe(true);
    expect(Number.isFinite(result.endPos.y)).toBe(true);
    expect(Number.isFinite(result.endPos.z)).toBe(true);
  });

  test('startPos == startTarget: endPos is at requested distance from target', () => {
    // Even with the fallback axis, the offset magnitude must equal the
    // (clamped) requested distance — the guard substitutes a *direction*,
    // not a magnitude.
    const startPos = new THREE.Vector3(5, 5, 5);
    const startTarget = new THREE.Vector3(5, 5, 5);
    const target = new THREE.Vector3(10, 0, 0);
    const result = computeFocusEndpoint(startPos, startTarget, target, 20, {
      minDistance: 3,
      maxDistance: 200,
    });
    const offset = new THREE.Vector3().subVectors(result.endPos, target);
    expect(offset.length()).toBeCloseTo(20, 5);
  });

  test('near-zero direction (sub-epsilon delta) also triggers the guard', () => {
    // Floating-point inputs may produce a near-zero (but technically
    // non-zero) direction. Three.js normalize() returns the zero vector
    // for length below 1e-15 or so; the guard must handle this case too.
    const startPos = new THREE.Vector3(5, 5, 5);
    const startTarget = new THREE.Vector3(5 + 1e-20, 5, 5);
    const target = new THREE.Vector3(10, 0, 0);
    const result = computeFocusEndpoint(startPos, startTarget, target, 20, {
      minDistance: 3,
      maxDistance: 200,
    });
    expect(Number.isFinite(result.endPos.x)).toBe(true);
    expect(Number.isFinite(result.endPos.y)).toBe(true);
    expect(Number.isFinite(result.endPos.z)).toBe(true);
  });
});

describe('computeFocusEndpoint — normal-case correctness', () => {
  test('distance within bounds is preserved exactly', () => {
    const startPos = new THREE.Vector3(0, 0, 80);
    const startTarget = new THREE.Vector3(0, 0, 0);
    const target = new THREE.Vector3(0, 0, 0);
    const result = computeFocusEndpoint(startPos, startTarget, target, 50, {
      minDistance: 3,
      maxDistance: 200,
    });
    // Direction is +Z (startPos - startTarget = (0,0,80) → +Z), so
    // endPos should be target + (0, 0, 50) = (0, 0, 50).
    expect(result.endPos.x).toBeCloseTo(0, 5);
    expect(result.endPos.y).toBeCloseTo(0, 5);
    expect(result.endPos.z).toBeCloseTo(50, 5);
  });

  test('input vectors are not mutated (caller-owned objects)', () => {
    // The helper must clone inputs that it stores in the output — callers
    // depend on `startPos` / `startTarget` / `target` not changing.
    const startPos = new THREE.Vector3(0, 0, 80);
    const startTarget = new THREE.Vector3(0, 0, 0);
    const target = new THREE.Vector3(10, 20, 30);
    const startPosBefore = startPos.clone();
    const startTargetBefore = startTarget.clone();
    const targetBefore = target.clone();
    computeFocusEndpoint(startPos, startTarget, target, 50, {
      minDistance: 3,
      maxDistance: 200,
    });
    expect(startPos.equals(startPosBefore)).toBe(true);
    expect(startTarget.equals(startTargetBefore)).toBe(true);
    expect(target.equals(targetBefore)).toBe(true);
  });
});
