// frontend/src/lib/components/taxonomy/focus-math.ts
//
// Pure helper extracted from `TopologyRenderer.focusOn` per spec § 4.6.
// Computes the end-state camera position for a focus-on animation, with
// adaptive-distance clamping and a zero-length direction guard. Unit-tested
// in `focus-math.test.ts`; the animation glue (RAF + lerp) stays in
// `TopologyRenderer.focusOn`.
//
// Brand reference: .claude/skills/brand-guidelines/references/3d-visualization.md
// has no specific guidance for camera framing, but the overall principle
// (Signal Over Noise) means we never produce NaN-poisoned camera state —
// the user always sees a coherent view of the scene.

import * as THREE from 'three';

export interface FocusEndpointOptions {
  /** Lower bound on the camera-to-target distance after clamping. */
  minDistance: number;
  /** Upper bound on the camera-to-target distance after clamping. */
  maxDistance: number;
}

export interface FocusEndpointResult {
  /** Final camera position after the focus animation completes. */
  endPos: THREE.Vector3;
  /** Final orbit-controls target after the focus animation completes. */
  endTarget: THREE.Vector3;
  /** The clamped distance actually used (post `[minDistance, maxDistance]` clamp). */
  finalDistance: number;
}

/**
 * Length below which `subVectors(startPos, startTarget)` is treated as
 * "zero direction." Three.js `Vector3.normalize()` returns the zero vector
 * (not NaN) for inputs whose squared length underflows to zero, but our
 * fallback path also catches inputs with magnitude well below floating-point
 * precision so subsequent arithmetic stays well-conditioned.
 */
const ZERO_DIRECTION_EPSILON = 1e-10;

/**
 * Compute the end-state camera position + target for a focus-on animation.
 *
 * Inputs are NOT mutated — every returned `Vector3` is a fresh allocation
 * (this helper runs once per `focusOn` call, not per frame, so it is not
 * subject to the per-frame allocation budget enforced by `perf-budget.test.ts`).
 *
 * Behavior:
 *   1. **Clamp distance** to `[minDistance, maxDistance]`.
 *   2. **Compute view direction** = `startPos - startTarget`, normalized.
 *      If the direction has near-zero magnitude (i.e. `startPos` ≈
 *      `startTarget`, the pathological camera-on-target case), substitute
 *      a fallback unit axis (+Z) so the result stays finite.
 *   3. **Compute end position** = `target + (direction * clampedDistance)`.
 *
 * @param startPos      Current camera world position.
 * @param startTarget   Current orbit-controls target.
 * @param target        Requested new target to focus on.
 * @param distance      Requested camera-to-target distance (pre-clamp).
 * @param options       Adaptive-distance clamp bounds.
 */
export function computeFocusEndpoint(
  startPos: THREE.Vector3,
  startTarget: THREE.Vector3,
  target: THREE.Vector3,
  distance: number,
  options: FocusEndpointOptions,
): FocusEndpointResult {
  // Clamp distance to the OrbitControls min/max range. Outside this range
  // produces an unreachable camera position (controls would clamp on next
  // tick anyway, but the in-flight animation should not visit invalid
  // intermediate states).
  const finalDistance = Math.max(
    options.minDistance,
    Math.min(options.maxDistance, distance),
  );

  // View direction: startPos → startTarget reversed.
  const direction = new THREE.Vector3().subVectors(startPos, startTarget);
  const directionLength = direction.length();

  if (directionLength < ZERO_DIRECTION_EPSILON) {
    // Camera-on-target pathological case. The fallback unit axis is +Z —
    // matches the camera's default look direction in the scene's
    // coordinate frame (see `TopologyRenderer` constructor:
    // `camera.position.set(0, 0, 80)` looking at origin).
    direction.set(0, 0, 1);
  } else {
    direction.divideScalar(directionLength);  // in-place normalize
  }
  direction.multiplyScalar(finalDistance);

  const endTarget = target.clone();
  const endPos = endTarget.clone().add(direction);

  return { endPos, endTarget, finalDistance };
}
