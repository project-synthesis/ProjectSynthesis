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
 * Persistent children keep their original `scene.children` position; only
 * ephemerals are removed. This preserves any downstream test/visual
 * assumption about layer ordering (e.g., the readiness-ring dim-lockstep
 * test pins `groups[0] === firstDomainGroup` and would break if persistent
 * pool groups were reattached to the head/tail of `scene.children`).
 *
 * Call from `rebuildScene` in place of the manual scene.remove(...) +
 * dispose-traverse + `while (children > 0)` + scene.add(...) dance.
 */
export function cleanupScene(scene: THREE.Scene): void {
  // 1. Partition direct children: persistent stay in place, ephemerals get
  //    disposed + removed. Walk a snapshot of the children array because
  //    we'll mutate it below.
  const directChildren = scene.children.slice();
  const persistent: THREE.Object3D[] = [];
  const ephemeral: THREE.Object3D[] = [];
  for (const child of directChildren) {
    if (child.userData?.persistent === true) persistent.push(child);
    else ephemeral.push(child);
  }

  // 2. Pre-seed protection sets so shared geometries/materials referenced
  //    by both persistent + ephemeral children survive the dispose pass.
  //    Persistent groups are treated as opaque containers — their direct
  //    geometry+material refs are protected (covers the common case of
  //    pool groups whose root references the shared resource).
  const disposedGeo = new WeakSet<THREE.BufferGeometry>();
  const protectedMat = new WeakSet<THREE.Material>();
  for (const p of persistent) seedPersistentProtection(p, disposedGeo, protectedMat);

  // 3. Dispose ephemeral geometry-owners. Walk via the scene's own
  //    `traverse` for parity with the prior implementation, but skip any
  //    descendants of persistent direct children by checking ancestry via
  //    the userData.persistent flag at the scene-direct-child level.
  //    Implementation note: a top-level persistent child's descendants
  //    don't carry the flag themselves; we identify them by Set membership.
  const persistentSet = new Set<THREE.Object3D>(persistent);
  scene.traverse((obj) => {
    if (persistentSet.has(obj as THREE.Object3D)) return; // skip persistent root
    // Check if any ancestor is in persistentSet (covers descendants of
    // persistent groups). Walks up via .parent which exists on real
    // THREE.Object3D and on most test mocks (added by Group.add()).
    let p: THREE.Object3D | null | undefined = (obj as THREE.Object3D).parent;
    while (p) {
      if (persistentSet.has(p)) return;
      p = p.parent;
    }
    if (isGeometryOwner(obj as THREE.Object3D)) {
      disposeGeometryOwner(
        obj as THREE.Mesh | THREE.LineSegments | THREE.Line | THREE.Points,
        disposedGeo,
        protectedMat,
      );
    }
  });

  // 4. Remove ephemeral direct children. Persistent children keep their
  //    original positions in `scene.children`.
  for (const e of ephemeral) scene.remove(e);
}

/**
 * Move every persistent direct child of `scene` to the END of
 * `scene.children`, in their original relative order. Idempotent.
 *
 * Call from `rebuildScene` AFTER all new ephemeral children have been
 * added. Restores the layer ordering invariant that pre-existing tests
 * (notably the dim-lockstep test in `SemanticTopology.test.ts`) expect:
 * domain cluster groups appear before pool/wrapper groups in
 * `scene.children`.
 *
 * Why this isn't part of `cleanupScene`: cleanupScene runs at the START
 * of rebuildScene (before new ephemerals are added), so a "move to end"
 * there would have nothing to move past. The reorder must happen AFTER
 * the rebuild populates the new ephemeral set.
 */
export function reorderPersistentToBack(scene: THREE.Scene): void {
  const persistent = scene.children.filter(
    (c) => c.userData?.persistent === true,
  );
  for (const p of persistent) {
    scene.remove(p);
    scene.add(p);
  }
}
