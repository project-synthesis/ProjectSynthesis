// frontend/src/lib/components/taxonomy/scene-cleanup.ts
//
// Scene cleanup helper for the Pattern Graph rebuildScene cycle.
// Preserves children tagged `userData.persistent = true`; disposes
// geometries + materials of ephemeral Mesh/LineSegments/Line/Points
// children.
//
// Spec: docs/superpowers/specs/2026-05-16-lifecycle-hardening-design.md
import * as THREE from 'three';

export function cleanupScene(_scene: THREE.Scene): void {
  throw new Error('cleanupScene: not implemented');
}
