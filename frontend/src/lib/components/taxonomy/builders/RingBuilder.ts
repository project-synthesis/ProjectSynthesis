// frontend/src/lib/components/taxonomy/builders/RingBuilder.ts
//
// Two ring classes: per-domain readiness rings (F4) + per-cluster template
// rings (F3). Both live inside lazy-constructed persistent parent groups
// (`_readinessRingGroup` + `_templateRingGroup`, both `userData.persistent =
// true`) so the Sub-project A flag-aware `cleanupScene` skips them across
// rebuilds. Template rings are pooled (initial 50 → grow 50 → cap 500) and
// reused via a free-list to avoid per-rebuild allocations.
//
// Migrated from SemanticTopology.svelte:
//   - Template ring pool infrastructure: ~lines 164-285 at HEAD `d79f3dee`
//   - Readiness ring sync block: ~lines 1171-1224
//   - Readiness ring entry pruning: ~lines 957-969 (moved here from
//     orchestrator pre-cleanup — spec §3.6 NOTE)
//
// Reads from ctx:
//   - sceneNodeMap (read indirectly via SceneData.nodes — RingBuilder iterates
//     data.nodes directly to identify templated clusters + readiness-tiered
//     domains; sceneNodeMap is the orchestrator's pre-populated copy).
//
// Writes to ctx: None. Ring meshes live inside the persistent parent groups
// the builder owns; downstream consumers (breathing handler, LOD callback)
// read via the public accessors instead of through ctx.
//
// Public accessors (rev-2 plan fix — addresses B4):
//   - getTemplateRing(id):    THREE.Mesh | undefined
//   - getReadinessRing(id):   ReadinessRingEntry | undefined
//   - readinessRingCount():   number
//   - readinessRingIds():     string[]
//
// Spec: docs/superpowers/specs/2026-05-17-scene-builder-extraction-design.md

import * as THREE from 'three';
import type { SceneData, SceneNode } from '../TopologyData';
import type { SceneBuilder } from './SceneBuilder';
import type { BuilderContext } from './BuilderContext';
import type { ReadinessTier } from '../readiness-tier';
import { readinessTierColor } from '../readiness-tier';

// ── Constants — migrated from SemanticTopology.svelte module scope ──

/** Pool seed size on first build — covers small renders without re-growing. */
const TEMPLATE_RING_POOL_INITIAL = 50;
/** Pool growth chunk for subsequent shortfalls. */
const TEMPLATE_RING_POOL_GROW_CHUNK = 50;
/** Hard cap on pool high-water mark — spillover allocates one-frame meshes. */
const TEMPLATE_RING_POOL_MAX = 500;

/** Readiness ring sits just outside the domain silhouette at this scalar. */
const READINESS_RING_RADIUS_FACTOR = 1.25;
/** Readiness ring radial thickness — matches brand 1px-contour spec. */
const READINESS_RING_THICKNESS = 0.05;
/** Readiness ring segment count — finely sampled for a smooth contour. */
const READINESS_RING_SEGMENTS = 64;
/** Multiplier on node.opacity for the readiness ring's resting opacity. */
const READINESS_RING_OPACITY_FACTOR = 0.9;

/** F3 template ring cyan — canon hex. */
const TEMPLATE_RING_COLOR = 0x00e5ff;
/** F3 template ring resting opacity — see brand 3d-visualization reference. */
const TEMPLATE_RING_RESTING_OPACITY = 0.35;
/** Template ring base inner radius (geometry is unit-sized; world-space
 *  positioning happens via mesh.position). */
const TEMPLATE_RING_INNER_RADIUS = 1.25;
/** Template ring base outer radius. */
const TEMPLATE_RING_OUTER_RADIUS = 1.35;
/** Template ring segments — canon F3. */
const TEMPLATE_RING_SEGMENTS = 64;

/**
 * Per-domain readiness ring registry entry. Public so callers (e.g. the
 * breathing handler in SemanticTopology.svelte) can read entry fields after
 * acquiring via `getReadinessRing(id)`.
 *
 * `lastTier` is the tier the ring is currently displaying or tweening
 * toward. `tween` is the in-flight color tween handle (if any); cancelled
 * on disposal or when superseded so RAF callbacks cannot outlive the
 * material they write to.
 */
export interface ReadinessRingEntry {
  mesh: THREE.Mesh;
  material: THREE.MeshBasicMaterial;
  lastTier: ReadinessTier;
  lastSize: number;
  domain: string;
  nodeOpacity: number;
  tween: { cancel(): void } | null;
}

/**
 * Predicate — node carries an active readiness ring. Centralizes the
 * "domain + tier" rule so the build pass and the pruning pass cannot
 * drift.
 */
function hasReadinessRing(node: SceneNode): boolean {
  return node.state === 'domain' && node.readinessTier != null;
}

/**
 * Builds the two ring classes — readiness rings (per-domain contour rings
 * colored by composite tier; canon F4) and template rings (per-cluster
 * indicator; canon F3) — into two LAZY persistent parent groups that
 * survive `cleanupScene` across rebuilds (Sub-project A flag pattern).
 *
 * Lifecycle:
 *   - Both `_readinessRingGroup` + `_templateRingGroup` are lazily
 *     constructed on the first `build()` call and tagged
 *     `userData.persistent = true` so the flag-aware `cleanupScene` skips
 *     them. Both groups stay attached to `scene` across every subsequent
 *     rebuild — the builder reconciles ring entries inside them rather
 *     than replacing the groups.
 *   - Template rings are pooled: pool starts empty, grows by
 *     {@link TEMPLATE_RING_POOL_INITIAL} on the first build that needs
 *     ring meshes, then in {@link TEMPLATE_RING_POOL_GROW_CHUNK}
 *     increments up to {@link TEMPLATE_RING_POOL_MAX}.
 *   - Readiness rings are not pooled (one Mesh + Material per domain;
 *     domains are typically <20). Entries are reconciled per rebuild:
 *     existing entries reused, missing ones pruned + disposed.
 *   - `dispose()` is idempotent: detaches both persistent groups from
 *     their parent, clears every internal map, removes every pool mesh
 *     from the scene tree, then no-ops on subsequent calls.
 */
export class RingBuilder implements SceneBuilder {
  // ── Persistent parent groups (lazy + flag) ──
  private _readinessRingGroup: THREE.Group | null = null;
  private _templateRingGroup: THREE.Group | null = null;

  // ── Template ring pool state (high-water mark + free list) ──
  private readonly _templateRingPool: THREE.Mesh[] = [];
  private readonly _templateRingPoolSet: Set<THREE.Mesh> = new Set();
  private readonly _freeTemplateRings: THREE.Mesh[] = [];
  private readonly _templateRingById: Map<string, THREE.Mesh> = new Map();
  private _templateRingWarnedThisRebuild = false;

  // ── Readiness ring registry ──
  private readonly _readinessRings: Map<string, ReadinessRingEntry> = new Map();

  private _disposed = false;

  /**
   * Reconcile readiness + template rings against the current SceneData.
   *
   * Workflow:
   *   1. Lazy-construct both persistent groups + tag persistence flag
   *      on first call.
   *   2. Prune readiness entries whose owning domain disappeared, lost
   *      its tier, or became hidden. Disposes each pruned entry's
   *      material + geometry (GPU resource hygiene). Migrated from
   *      orchestrator pre-cleanup (spec §3.6 NOTE).
   *   3. Sync template rings to the current templated-cluster set:
   *      release rings for clusters that no longer carry a template
   *      indicator, then ensure pool capacity + acquire for new
   *      attachments.
   *   4. Build / update one readiness ring per visible structural node
   *      with a tier. Existing entries get position + tier updates;
   *      new entries get a fresh Mesh + Material.
   *
   * Idempotent across rebuilds (the persistent groups stay attached;
   * entries are reconciled in-place rather than dropped + re-added).
   *
   * @param data  - Topology snapshot whose `nodes` drive the build.
   * @param scene - Target THREE.Scene the persistent groups attach to.
   * @param _ctx  - Shared per-rebuild context (RingBuilder neither
   *   reads from nor writes to ctx — kept in the signature to satisfy
   *   the SceneBuilder interface).
   */
  build(data: SceneData, scene: THREE.Scene, _ctx: BuilderContext): void {
    if (this._disposed) return;

    // ── 1. Lazy construct + flag persistence ──
    if (!this._readinessRingGroup) {
      this._readinessRingGroup = new THREE.Group();
      this._readinessRingGroup.userData.isReadinessRingGroup = true;
      this._readinessRingGroup.userData.persistent = true;
    }
    if (this._readinessRingGroup.parent !== scene) {
      scene.add(this._readinessRingGroup);
    }
    if (!this._templateRingGroup) {
      this._templateRingGroup = new THREE.Group();
      this._templateRingGroup.userData.isTemplateRingGroup = true;
      this._templateRingGroup.userData.persistent = true;
    }
    if (this._templateRingGroup.parent !== scene) {
      scene.add(this._templateRingGroup);
    }

    // ── 2. Prune readiness entries whose owning node disappeared/changed ──
    // Mirrors the orchestrator pre-cleanup block (~lines 957-969 at HEAD
    // d79f3dee); migrated here per spec §3.6 NOTE because the registry
    // lives in this builder.
    const currentDomainIds = new Set(
      data.nodes.filter((n) => n.visible && hasReadinessRing(n)).map((n) => n.id),
    );
    for (const [id, entry] of [...this._readinessRings]) {
      if (!currentDomainIds.has(id)) {
        RingBuilder._disposeRingEntry(entry);
        this._readinessRingGroup.remove(entry.mesh);
        this._readinessRings.delete(id);
      }
    }

    // ── 3. Template ring sync — release missing, acquire new ──
    this._syncTemplateRings(data.nodes);

    // ── 4. Build / update readiness rings ──
    for (const node of data.nodes) {
      if (!node.visible) continue;
      if (!hasReadinessRing(node)) continue;
      // hasReadinessRing guarantees readinessTier is defined.
      const tier = node.readinessTier!;
      const existing = this._readinessRings.get(node.id);
      if (existing) {
        // Update-in-place. Tier / size drift handling is intentionally
        // omitted here — the breathing handler + LOD callback in
        // SemanticTopology.svelte own the per-frame ring reconciliation
        // (color tween + size drift). The build pass only writes the
        // position + opacity baseline; consumers can read the latest
        // fields via getReadinessRing(id).
        existing.mesh.position.set(...node.position);
        existing.lastTier = tier;
        existing.lastSize = node.size;
        existing.domain = node.domain;
        existing.nodeOpacity = node.opacity;
        continue;
      }
      const radius = node.size * READINESS_RING_RADIUS_FACTOR;
      const geom = new THREE.RingGeometry(
        radius,
        radius + READINESS_RING_THICKNESS,
        READINESS_RING_SEGMENTS,
      );
      const color = readinessTierColor(tier);
      const mat = new THREE.MeshBasicMaterial({
        color: new THREE.Color(color),
        transparent: true,
        opacity: node.opacity * READINESS_RING_OPACITY_FACTOR,
        depthWrite: false,
        side: THREE.DoubleSide,
      });
      const mesh = new THREE.Mesh(geom, mat);
      mesh.position.set(...node.position);
      mesh.userData = { kind: 'readiness_ring', nodeId: node.id };
      this._readinessRingGroup.add(mesh);
      this._readinessRings.set(node.id, {
        mesh,
        material: mat,
        lastTier: tier,
        lastSize: node.size,
        domain: node.domain,
        nodeOpacity: node.opacity,
        tween: null,
      });
    }
  }

  /**
   * Idempotent teardown — detach both persistent parent groups from the
   * scene, clear every internal map + array, and physically remove every
   * pool mesh from the scene tree. After return:
   *
   *   - `_readinessRingGroup.parent === null`
   *   - `_templateRingGroup.parent === null`
   *   - `_readinessRings.size === 0`
   *   - `_templateRingById.size === 0`
   *   - every pool mesh is no longer a descendant of `scene`
   *
   * Subsequent calls are no-ops (post-state unchanged).
   */
  dispose(): void {
    if (this._disposed) return;
    this._disposed = true;

    // Cancel any in-flight tweens + dispose GPU resources before
    // detaching the parent groups (avoids RAF write-after-free).
    for (const [, entry] of this._readinessRings) {
      RingBuilder._disposeRingEntry(entry);
    }
    this._readinessRings.clear();

    // Detach persistent groups from their parents. Clear children so
    // any subsequent traverse finds nothing.
    if (this._readinessRingGroup?.parent) {
      this._readinessRingGroup.parent.remove(this._readinessRingGroup);
    }
    if (this._readinessRingGroup) {
      // Remove all children so the group is empty post-dispose.
      while (this._readinessRingGroup.children.length > 0) {
        this._readinessRingGroup.remove(this._readinessRingGroup.children[0]);
      }
    }
    if (this._templateRingGroup?.parent) {
      this._templateRingGroup.parent.remove(this._templateRingGroup);
    }
    if (this._templateRingGroup) {
      // Remove every pool mesh from the template group + dispose
      // geometry/material. The pool mesh objects themselves are kept
      // in `_templateRingPool` only for length-counting symmetry but
      // are no longer attached to any scene object.
      while (this._templateRingGroup.children.length > 0) {
        this._templateRingGroup.remove(this._templateRingGroup.children[0]);
      }
    }

    // Dispose every pool mesh's GPU resources.
    for (const mesh of this._templateRingPool) {
      if (mesh.parent) mesh.parent.remove(mesh);
      if (typeof (mesh.geometry as THREE.BufferGeometry)?.dispose === 'function') {
        mesh.geometry.dispose();
      }
      const mat = mesh.material as THREE.MeshBasicMaterial;
      if (typeof mat?.dispose === 'function') mat.dispose();
    }

    // Clear pool state.
    this._templateRingPool.length = 0;
    this._templateRingPoolSet.clear();
    this._freeTemplateRings.length = 0;
    this._templateRingById.clear();
  }

  /**
   * Public accessor — returns the active template-ring `THREE.Mesh` for
   * the given cluster id, or `undefined` if no ring is registered (the
   * cluster has no template state, was released, or the builder has been
   * disposed). Used by the per-frame breathing handler in
   * SemanticTopology.svelte to read the live mesh without importing
   * RingBuilder's internal pool maps (spec rev-2 — B4).
   *
   * @param id - Cluster id whose template ring is being looked up.
   * @returns The active mesh or `undefined`.
   */
  getTemplateRing(id: string): THREE.Mesh | undefined {
    if (this._disposed) return undefined;
    return this._templateRingById.get(id);
  }

  /**
   * Public accessor — returns the `ReadinessRingEntry` for the given
   * domain id (with `.mesh`, `.material`, `.lastTier`, `.lastSize`,
   * `.domain`, `.nodeOpacity`, `.tween` populated), or `undefined` if
   * unknown / post-dispose. Used by the per-frame breathing + LOD
   * callbacks to compose opacity / billboard updates without importing
   * RingBuilder's internal map (spec rev-2 — B4).
   *
   * @param id - Domain id whose ring entry is being looked up.
   * @returns The active entry or `undefined`.
   */
  getReadinessRing(id: string): ReadinessRingEntry | undefined {
    if (this._disposed) return undefined;
    return this._readinessRings.get(id);
  }

  /**
   * Public accessor — total active readiness-ring entries currently
   * managed by this builder. Used by source-grep / orchestration tests
   * that want to assert "N readiness rings live in the registry" without
   * traversing the scene tree.
   *
   * @returns Count of active readiness-ring entries (0 post-dispose).
   */
  readinessRingCount(): number {
    if (this._disposed) return 0;
    return this._readinessRings.size;
  }

  /**
   * Public accessor — iterate active readiness-ring ids. Returns a fresh
   * array each call so callers cannot accidentally mutate the underlying
   * Map's key iterator.
   *
   * @returns Array of active readiness-ring node ids (empty post-dispose).
   */
  readinessRingIds(): string[] {
    if (this._disposed) return [];
    return [...this._readinessRings.keys()];
  }

  /**
   * Test-helper / internal observability hook — current pool high-water
   * mark (i.e. all-time peak count of allocated pool meshes since
   * construction or last `dispose`). The pool-lifecycle tests assert
   * pool growth + cap behavior via this method. Not a stable public
   * API — guarded behind a private name so `getTemplateRing` /
   * `getReadinessRing` remain the canonical accessors.
   *
   * @returns Number of meshes currently in the pool array.
   */
  templateRingPoolSize(): number {
    return this._templateRingPool.length;
  }

  // ── Internal helpers ──

  /**
   * Reconcile template-ring meshes against the current cluster set: release
   * meshes for clusters that lost their template state, grow the pool if
   * additional acquisitions are needed, then attach a ring per templated
   * cluster (reuse from `_templateRingById` if already present).
   *
   * Mirrors `_syncTemplateRings` from SemanticTopology.svelte module scope
   * (~line 261 at HEAD `d79f3dee`) without the T2.2 entry/exit animations
   * — those are visual polish driven by the orchestrator per-frame chain
   * post-migration, NOT scene construction. Tests can assert the lifecycle
   * invariants (acquire/release/cap/free-list) deterministically without
   * RAF flakiness.
   *
   * @param nodes - All SceneData nodes for this rebuild.
   */
  private _syncTemplateRings(nodes: SceneNode[]): void {
    if (!this._templateRingGroup) return; // Defensive — build() lazy-inits before this runs.
    this._templateRingWarnedThisRebuild = false;

    const templated = nodes.filter((n) => (n.template_count ?? 0) > 0 && n.visible);

    // Release template rings for clusters that are no longer templated/visible.
    for (const [cid, mesh] of [...this._templateRingById]) {
      if (!templated.find((c) => c.id === cid)) {
        this._releaseTemplateRing(cid, mesh);
      }
    }

    // Ensure the pool has enough free meshes for new attachments before
    // the acquire loop runs. Otherwise repeated `_acquireTemplateRing`
    // calls would each fall through to spill allocation when the pool is
    // empty + at-cap, defeating the pool's purpose.
    const newAttachments = templated.filter((c) => !this._templateRingById.has(c.id)).length;
    this._ensureTemplateRingPool(newAttachments);

    for (const c of templated) {
      let mesh = this._templateRingById.get(c.id);
      if (!mesh) {
        mesh = this._acquireTemplateRing();
        this._templateRingById.set(c.id, mesh);
        mesh.userData = { kind: 'template_ring', clusterId: c.id };
        this._templateRingGroup.add(mesh);
      } else {
        // Update clusterId tag in case the template ring was reused.
        mesh.userData.clusterId = c.id;
      }
      mesh.visible = true;
      mesh.position.set(...c.position);
      const colorHex = parseInt(c.color.replace('#', ''), 16);
      (mesh.material as THREE.MeshBasicMaterial).color.setHex(colorHex);
    }
  }

  /**
   * Ensure `_freeTemplateRings` has at least `extraNeeded` entries by
   * growing the pool in chunks. First call seeds with
   * {@link TEMPLATE_RING_POOL_INITIAL}; subsequent calls grow by
   * {@link TEMPLATE_RING_POOL_GROW_CHUNK}. Never exceeds
   * {@link TEMPLATE_RING_POOL_MAX}; emits a one-time per-rebuild warning
   * when the cap is hit and excess clusters spill to one-frame
   * allocations.
   *
   * @param extraNeeded - Count of new ring attachments this rebuild
   *   requires (beyond what's already in `_freeTemplateRings`).
   */
  private _ensureTemplateRingPool(extraNeeded: number): void {
    while (
      this._freeTemplateRings.length < extraNeeded &&
      this._templateRingPool.length < TEMPLATE_RING_POOL_MAX
    ) {
      const seed =
        this._templateRingPool.length === 0
          ? TEMPLATE_RING_POOL_INITIAL
          : TEMPLATE_RING_POOL_GROW_CHUNK;
      const grow = Math.min(seed, TEMPLATE_RING_POOL_MAX - this._templateRingPool.length);
      for (let i = 0; i < grow; i++) {
        const m = RingBuilder._createTemplateRingMesh();
        this._templateRingPool.push(m);
        this._templateRingPoolSet.add(m);
        this._freeTemplateRings.push(m);
      }
    }
    if (
      !this._templateRingWarnedThisRebuild &&
      extraNeeded > this._freeTemplateRings.length &&
      this._templateRingPool.length >= TEMPLATE_RING_POOL_MAX
    ) {
      // eslint-disable-next-line no-console
      console.warn(
        `[RingBuilder] template ring pool at cap ${TEMPLATE_RING_POOL_MAX}; ${
          extraNeeded - this._freeTemplateRings.length
        } clusters spill to one-frame allocation`,
      );
      this._templateRingWarnedThisRebuild = true;
    }
  }

  /**
   * Pop a free ring from the pool, or one-frame-allocate when the cap is
   * hit. Spill meshes are NOT tracked in `_templateRingPool` /
   * `_templateRingPoolSet`, so they cannot be returned to the pool on
   * release — they're disposed when their cluster disappears.
   *
   * @returns A ready-to-attach template ring mesh.
   */
  private _acquireTemplateRing(): THREE.Mesh {
    return this._freeTemplateRings.pop() ?? RingBuilder._createTemplateRingMesh();
  }

  /**
   * Release a template ring back to the free list (if it's a pool
   * member) and detach from its parent group. Reset visual state so the
   * next acquire starts from a clean baseline.
   *
   * @param cid  - Cluster id whose ring is being released.
   * @param mesh - The ring mesh to release.
   */
  private _releaseTemplateRing(cid: string, mesh: THREE.Mesh): void {
    // Reset visual state for reuse.
    const mat = mesh.material as THREE.MeshBasicMaterial;
    mat.opacity = TEMPLATE_RING_RESTING_OPACITY;
    mesh.scale.setScalar(1.0);
    mesh.visible = false;
    if (mesh.parent) mesh.parent.remove(mesh);
    this._templateRingById.delete(cid);
    if (this._templateRingPoolSet.has(mesh)) this._freeTemplateRings.push(mesh);
  }

  /**
   * Construct one F3 template ring mesh (canon F3 — cyan 1px contour
   * around templated clusters). The geometry is unit-sized; positioning
   * + per-cluster color writes happen at acquire time.
   *
   * Pure factory — kept static for cross-builder pattern parity with
   * {@link ClusterBuilder._computeEmissive} +
   * {@link DomainBuilder._buildUniqueVertexGeometry}.
   *
   * @returns A configured template ring mesh ready to acquire.
   */
  private static _createTemplateRingMesh(): THREE.Mesh {
    const geom = new THREE.RingGeometry(
      TEMPLATE_RING_INNER_RADIUS,
      TEMPLATE_RING_OUTER_RADIUS,
      TEMPLATE_RING_SEGMENTS,
    );
    const mat = new THREE.MeshBasicMaterial({
      color: TEMPLATE_RING_COLOR,
      transparent: true,
      opacity: TEMPLATE_RING_RESTING_OPACITY,
      side: THREE.DoubleSide,
    });
    const mesh = new THREE.Mesh(geom, mat);
    mesh.visible = false;
    mesh.userData = { kind: 'template_ring' };
    return mesh;
  }

  /**
   * Per-entry teardown for a readiness ring — cancel any in-flight tween
   * BEFORE disposing GPU resources so the RAF step can't write to a
   * disposed material. Scene-graph removal stays at the call site (the
   * pruning loop removes individual meshes from the group; dispose drops
   * the whole group).
   *
   * Dispose lookups are null-safe because some THREE mocks lack the
   * methods.
   *
   * @param entry - The readiness-ring entry whose resources are released.
   */
  private static _disposeRingEntry(entry: ReadinessRingEntry): void {
    entry.tween?.cancel();
    if (typeof entry.mesh.geometry?.dispose === 'function') {
      entry.mesh.geometry.dispose();
    }
    if (typeof entry.material?.dispose === 'function') {
      entry.material.dispose();
    }
  }
}
