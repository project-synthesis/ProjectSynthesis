// frontend/src/lib/components/taxonomy/scene-cleanup.ts
//
// Scene cleanup helper for the Pattern Graph rebuildScene cycle.
//
// Spec: docs/superpowers/specs/2026-05-16-lifecycle-hardening-design.md
// Brand canon: .claude/skills/brand-guidelines/references/3d-visualization.md
//   "Persistence Contract" section.
//
// Contract:
//   - Children tagged `userData.persistent === true` are detached
//     BEFORE the dispose traverse and reattached AFTER the wipe.
//     Their geometries + materials are NEVER disposed by this helper.
//   - Ephemeral children (no flag) have their geometry + materials
//     disposed before removal.
//   - Type coverage: Mesh + LineSegments + Line + Points. Lights +
//     Sprites + other Object3D subclasses are not touched by the
//     dispose traverse (no geometry/material to free at this layer).
//   - WeakSet dedup for shared geometries (one dispose per geometry
//     instance per call).
//   - Use DOT-ASSIGNMENT to set the flag (obj.userData.persistent = true);
//     object-replacement (userData = { persistent: true }) is banned
//     by cleanup-contract.test.ts because it clobbers other userData
//     fields.

import * as THREE from 'three';

export function cleanupScene(scene: THREE.Scene): void {
  // 1. Detach persistent children (preserves their geometries + materials).
  const persistent: THREE.Object3D[] = [];
  for (const child of scene.children) {
    if (child.userData?.persistent === true) persistent.push(child);
  }
  for (const p of persistent) scene.remove(p);

  // Pre-seed protected sets from persistent children so shared geometries +
  // materials (also referenced by ephemerals still in scene) are shielded
  // from the dispose traverse — satisfies spec §5.1 test #6 invariant
  // "persistent detached before traverse → shared resources never disposed".
  const disposedGeo = new WeakSet<THREE.BufferGeometry>();
  const protectedMat = new WeakSet<THREE.Material>();
  for (const p of persistent) {
    const pGeo = (p as THREE.Mesh).geometry;
    if (pGeo) disposedGeo.add(pGeo);
    const pMat = (p as THREE.Mesh).material;
    if (Array.isArray(pMat)) {
      for (const m of pMat) if (m) protectedMat.add(m);
    } else if (pMat) {
      protectedMat.add(pMat as THREE.Material);
    }
  }

  // 2. Dispose remaining (only ephemeral now). WeakSet dedup for shared
  //    geometries so the same buffer isn't disposed twice.
  scene.traverse((obj) => {
    if (
      (obj as THREE.Mesh).isMesh ||
      (obj as THREE.LineSegments).isLineSegments ||
      (obj as THREE.Line).isLine ||
      (obj as THREE.Points).isPoints
    ) {
      const geo = (obj as THREE.Mesh).geometry;
      if (geo && !disposedGeo.has(geo)) {
        geo.dispose?.();
        disposedGeo.add(geo);
      }
      const mat = (obj as THREE.Mesh).material;
      if (Array.isArray(mat)) {
        for (const m of mat) {
          if (m && !protectedMat.has(m)) m.dispose?.();
        }
      } else if (mat && !protectedMat.has(mat as THREE.Material)) {
        (mat as THREE.Material).dispose?.();
      }
    }
  });

  // 3. Remove all remaining (ephemeral) children.
  while (scene.children.length > 0) scene.remove(scene.children[0]);

  // 4. Reattach persistent children.
  for (const p of persistent) scene.add(p);
}
