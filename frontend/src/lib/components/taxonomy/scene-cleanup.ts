// frontend/src/lib/components/taxonomy/scene-cleanup.ts
//
// Scene cleanup helper for the Pattern Graph rebuildScene cycle.
//
// Spec: docs/superpowers/specs/2026-05-16-lifecycle-hardening-design.md (revision 9)
// Brand canon: .claude/skills/brand-guidelines/references/3d-visualization.md
//   "Persistence Contract" section (inserted before "Disposal Contract").
//
// Contract:
//   - Children tagged `userData.persistent === true` are detached
//     BEFORE the dispose traverse and reattached AFTER the wipe.
//     Their geometries + materials are NEVER disposed by this helper —
//     not even when shared with an ephemeral child still in scene
//     (pre-seeded WeakSets protect them per spec §5.1 test #6).
//   - Ephemeral children (no flag) have their geometry + materials
//     disposed before removal.
//   - Type coverage: Mesh + LineSegments + Line + Points. Lights +
//     Sprites + other Object3D subclasses are not touched by the
//     dispose traverse. Sprite disposal is owned by TopologyLabels'
//     `labels.clear()` per F3 canon. Light shadow-maps survive only
//     through the persistent flag (no shadow-casting lights are
//     ephemeral in this directory).
//   - WeakSet dedup for shared geometries + WeakSet protection for
//     persistent-shared geometries + materials.
//   - Use DOT-ASSIGNMENT to set the flag (obj.userData.persistent = true);
//     object-replacement of userData (assigning a new literal containing the
//     persistent key) is banned by cleanup-contract.test.ts because it
//     clobbers other userData fields like F10's `isNeuralDust`.
//   - Helper coverage `Mesh + LineSegments + Line + Points`
//     COMPLEMENTS the renderer-level dispose at
//     `TopologyRenderer.ts:247-271` which handles
//     `Mesh + LineSegments + Points + Sprite + Light.shadow.map`.
//     Together they cover every geometry-owning class used in this
//     directory.

import * as THREE from 'three';

/**
 * Type predicate: returns true for any Object3D subclass that owns a
 * BufferGeometry + Material (single or array) we should dispose.
 * Lights, Sprites, and bare Object3D (e.g., Group) return false.
 */
function isGeometryOwner(
  obj: THREE.Object3D,
): obj is THREE.Mesh | THREE.LineSegments | THREE.Line | THREE.Points {
  return (
    (obj as THREE.Mesh).isMesh === true ||
    (obj as THREE.LineSegments).isLineSegments === true ||
    (obj as THREE.Line).isLine === true ||
    (obj as THREE.Points).isPoints === true
  );
}

/**
 * Add the geometry + material(s) of a persistent geometry-owner to the
 * protection sets so the dispose traverse skips them even when an
 * ephemeral child shares the same references.
 */
function seedPersistentProtection(
  owner: THREE.Object3D,
  disposedGeo: WeakSet<THREE.BufferGeometry>,
  protectedMat: WeakSet<THREE.Material>,
): void {
  const geo = (owner as THREE.Mesh).geometry;
  if (geo) disposedGeo.add(geo);
  const mat = (owner as THREE.Mesh).material;
  if (Array.isArray(mat)) {
    for (const m of mat) if (m) protectedMat.add(m);
  } else if (mat) {
    protectedMat.add(mat as THREE.Material);
  }
}

/**
 * Dispose the geometry + material(s) of an ephemeral geometry-owner.
 * Skips shared resources already in `disposedGeo` / `protectedMat`.
 * Uses optional chaining so missing dispose methods (test mocks,
 * partially-constructed objects) are tolerated.
 */
function disposeGeometryOwner(
  owner: THREE.Mesh | THREE.LineSegments | THREE.Line | THREE.Points,
  disposedGeo: WeakSet<THREE.BufferGeometry>,
  protectedMat: WeakSet<THREE.Material>,
): void {
  const geo = owner.geometry;
  if (geo && !disposedGeo.has(geo)) {
    geo.dispose?.();
    disposedGeo.add(geo);
  }
  const mat = owner.material;
  if (Array.isArray(mat)) {
    for (const m of mat) {
      if (m && !protectedMat.has(m)) m.dispose?.();
    }
  } else if (mat && !protectedMat.has(mat as THREE.Material)) {
    (mat as THREE.Material).dispose?.();
  }
}

/**
 * Wipe ephemeral scene children, preserving any object tagged
 * `userData.persistent = true`. Disposes geometries + materials of
 * ephemeral Mesh/LineSegments/Line/Points before removal.
 *
 * Call from `rebuildScene` in place of the manual scene.remove(...) +
 * dispose-traverse + `while (children > 0)` + scene.add(...) dance.
 */
export function cleanupScene(scene: THREE.Scene): void {
  // 1. Detach persistent children.
  const persistent: THREE.Object3D[] = [];
  for (const child of scene.children) {
    if (child.userData?.persistent === true) persistent.push(child);
  }
  for (const p of persistent) scene.remove(p);

  // 2. Pre-seed protection sets so shared geometries/materials referenced
  //    by both persistent + ephemeral children survive the traverse.
  const disposedGeo = new WeakSet<THREE.BufferGeometry>();
  const protectedMat = new WeakSet<THREE.Material>();
  for (const p of persistent) seedPersistentProtection(p, disposedGeo, protectedMat);

  // 3. Dispose remaining (ephemeral) geometry-owners.
  scene.traverse((obj) => {
    if (isGeometryOwner(obj)) disposeGeometryOwner(obj, disposedGeo, protectedMat);
  });

  // 4. Remove all remaining (ephemeral) children.
  while (scene.children.length > 0) scene.remove(scene.children[0]);

  // 5. Reattach persistent children.
  for (const p of persistent) scene.add(p);
}
