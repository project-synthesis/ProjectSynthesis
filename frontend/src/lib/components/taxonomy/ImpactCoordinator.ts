// frontend/src/lib/components/taxonomy/ImpactCoordinator.ts
//
// Sub-project C — single source for the canon F7→F19→F9 impact chain.
// Sub-project E §3.4 — shrunken API: selection lifecycle is owned
// entirely by SelectionController. IC no longer carries
// engulfed-set / idle-pulse / per-frame tick / SELECTION_EMISSIVE_FLOOR
// state. The `marksEngulfed` branch of the 'click' trigger routes the
// impacted node id through `deps.selectionController.onImpact(id)`
// inside the beam's onImpact callback body (so the SC owns the
// post-impact transition into the 'engulfed' state).
//
// Spec: docs/superpowers/specs/2026-05-17-impact-coordinator-design.md
//       docs/superpowers/specs/2026-05-18-selection-state-machine-design.md §3.4
// Brand canon: .claude/skills/brand-guidelines/references/3d-visualization.md
//   F7 (BeamPool) + F18 (selection) + F19 (envelope + flash) cross-reference
//   this coordinator.
//
// Contract summary:
//   - One ImpactCoordinator per SemanticTopology mount.
//   - `fire(request)` is the ONLY way to start a beam impact in
//     SemanticTopology.svelte (source-grep test #3 pins this).
//   - Per-trigger preset table maps each Trigger to its visual parameters
//     per the `TriggerPreset` interface. Adding a Trigger requires
//     touching this table + the enum + tests —
//     intentionally not extensible at call-site.
//   - Causal-ordering invariant: all F19 reactions (envelope, flash, physics)
//     fire inside the BeamPool onImpact callback body, never synchronously
//     with acquire. Pinned at source level by source-grep test #6.
//   - No per-frame role: IC has no animation tick of its own. Selection
//     idle-pulse lives on `SelectionController._tickIdlePulse`, registered
//     with the AnimationCoordinator there.
//   - No selection state of its own: there is no `_selectionEngulfed`
//     set + no `isEngulfed`/`clearEngulfed` API on IC anymore. Callers
//     read engulfed state from `SelectionController.isEngulfed(id)`.

import * as THREE from 'three';
import type { BeamPool } from './BeamPool';
import type { EnvelopePool } from './EnvelopePool';
import type { ClusterPhysics } from './ClusterPhysics';
import type { AnimationCoordinator } from './AnimationCoordinator';
import type { TopologyRenderer } from './TopologyRenderer';
import type { SceneNode } from './TopologyData';
import type { SelectionController } from './SelectionController';

export type Trigger = 'entrance' | 'post-growth' | 'optimization' | 'click';

export interface TriggerPreset {
  /** Beam sustainMs base value (sizeFactor bonus added separately per `sizeFactorSustainBonus`). */
  sustainMs: number;
  /**
   * Final beam radius is `node.size * 0.04 * (applySizeFactor ? sizeFactor : 1.0) * thicknessMultiplier`.
   * Post-growth's 2.0 makes its beam visibly thicker; other triggers use 1.0.
   */
  thicknessMultiplier: number;
  /** Whether to multiply beam radius by sizeFactor (clamped 0.5..3.0 from node.size/50). */
  applySizeFactor: boolean;
  /**
   * Whether to apply a Math.max(radius, 0.1) floor on the computed beam radius.
   * Pre-refactor only the 'click' site had this floor (preserves visible beam
   * on very small nodes); other triggers had no floor.
   */
  applyClickRadiusFloor: boolean;
  /** Whether onImpact fires `clusterPhysics.onBeamImpact` (kinetic shake). */
  kineticDisplacement: boolean;
  /**
   * Optional size-factor-scaled sustain extension. Entrance + post-growth
   * use `sustainMs + sizeFactor * 500` (longer sustain on bigger nodes).
   * Optimization + click skip this (fixed sustainMs).
   */
  sizeFactorSustainBonus: boolean;
  /** Routes the impacted node through `selectionController.onImpact(id)` (only true for 'click'). */
  marksEngulfed: boolean;
}

/**
 * Per-trigger visual + behavioral parameters. Adding a new Trigger is a
 * three-line change here + the Trigger union + a unit test.
 *
 * Source-grep test #4 pins the table shape (every Trigger has an entry).
 */
export const TRIGGER_PRESETS: Readonly<Record<Trigger, Readonly<TriggerPreset>>> = {
  entrance: {
    sustainMs: 1500,
    thicknessMultiplier: 1.0,
    applySizeFactor: true,
    applyClickRadiusFloor: false,
    kineticDisplacement: false,
    sizeFactorSustainBonus: true,
    marksEngulfed: false,
  },
  'post-growth': {
    sustainMs: 3500,
    thicknessMultiplier: 2.0,
    applySizeFactor: true,
    applyClickRadiusFloor: false,
    kineticDisplacement: true,
    sizeFactorSustainBonus: true,
    marksEngulfed: false,
  },
  optimization: {
    sustainMs: 800,
    thicknessMultiplier: 1.0,
    applySizeFactor: true,
    applyClickRadiusFloor: false,
    kineticDisplacement: true,
    sizeFactorSustainBonus: false,
    marksEngulfed: false,
  },
  click: {
    // Pre-refactor click site at SemanticTopology.svelte:2411 used
    // `Math.max(node.size * 0.04, 0.1)` WITHOUT sizeFactor multiplication
    // — preserves identical visual behavior so the migration is
    // observable-no-op.
    sustainMs: 800,
    thicknessMultiplier: 1.0,
    applySizeFactor: false,
    applyClickRadiusFloor: true,
    kineticDisplacement: false,
    sizeFactorSustainBonus: false,
    marksEngulfed: true,
  },
};

export interface ImpactRequest {
  trigger: Trigger;
  node: SceneNode;
  group: THREE.Group;
}

export interface ImpactCoordinatorDeps {
  beamPool: BeamPool;
  envelopePool: EnvelopePool;
  clusterPhysics: ClusterPhysics;
  /**
   * Adapter routes to `SelectionController.flash` (which delegates to its
   * internal FlashController). The coordinator's onImpact body calls this
   * via `deps.flashEmissive(nodeId, color)`. SemanticTopology.svelte wires
   * this as `(id, color) => selectionController?.flash(id, color)` per the
   * thunked-dep pattern at spec §3.5.
   */
  flashEmissive: (nodeId: string, color: THREE.Color) => void;
  /** Looks up the freshest SceneNode by id (sceneData may have changed mid-flight). Returns undefined for unknown ids — fire() falls back to request.node. */
  getSceneNode: (id: string) => SceneNode | undefined;
  /** Looks up the current group by id (rebuildScene may have replaced it). Returns undefined for unknown ids — fire() falls back to request.group. */
  getBeamGroup: (id: string) => THREE.Group | undefined;
  renderer: TopologyRenderer;
  animationCoordinator: AnimationCoordinator;
  /**
   * Selection state machine — IC routes the `marksEngulfed` branch of the
   * 'click' onImpact through `selectionController.onImpact(nodeId)` so the
   * SC owns engulfed-set + idle-pulse bookkeeping (Sub-project E §3.4).
   */
  selectionController: SelectionController;
}

/**
 * ImpactCoordinator triggers the canonical F7→F19 reaction chain on
 * exactly four trigger events: entrance, post-growth, optimization,
 * click. Per-trigger visual parameters are pinned in
 * {@link TRIGGER_PRESETS}.
 *
 * Selection lifecycle is owned by SelectionController (Sub-project E):
 * the 'click' trigger's `marksEngulfed === true` branch routes the
 * impacted node id through `selectionController.onImpact(id)` from
 * inside the beam's onImpact callback body (post-causal-arrival). IC
 * itself holds no selection state, no engulfed-set, and no per-frame
 * tick — it is a stateless coordinator that exists between
 * SemanticTopology call-sites and the BeamPool/EnvelopePool/Flash
 * primitives.
 */
export class ImpactCoordinator {
  private _deps: ImpactCoordinatorDeps;
  private _disposed = false;

  constructor(deps: ImpactCoordinatorDeps) {
    this._deps = deps;
  }

  /**
   * Fire a canonical impact chain: beam acquire → onImpact callback →
   * (optional) clusterPhysics kinetic shake + envelope acquire + emissive
   * flash + (only for 'click') selection routing through
   * `deps.selectionController.onImpact(nodeId)`. Per-trigger parameters
   * come from `TRIGGER_PRESETS[request.trigger]`.
   *
   * Selection routing path (`preset.marksEngulfed === true`, currently
   * only the 'click' trigger): inside the onImpact callback, after
   * envelope+flash have been queued, IC calls
   * `deps.selectionController.onImpact(freshNode.id)`. The SC's
   * `onImpact` is responsible for transitioning from `selected-armed`
   * into `engulfed` and starting the post-decay idle pulse — IC does
   * NOT manipulate any selection state itself. If the SC is in an
   * unrelated state when the impact lands (e.g. selection cleared
   * mid-flight), the SC silently no-ops; IC stays unaware.
   *
   * Lenient on disposed: returns silently if `dispose()` was called.
   *
   * Causal-ordering invariant (canon F7): all F19 reactions live inside
   * the onImpact callback body, never synchronously here. The beam takes
   * ~700ms to travel; firing reactions synchronously would render them
   * before the beam arrives. The same invariant applies to the SC
   * routing call — `selectionController.onImpact` MUST be invoked
   * inside the onImpact closure, not at fire() entry.
   */
  fire(request: ImpactRequest): void {
    if (this._disposed) return;
    const preset = TRIGGER_PRESETS[request.trigger];

    // Resolve fresh references — sceneData may have changed since the
    // caller captured `request.node`/`request.group` (e.g., rebuildScene
    // disposed and re-created the group). Falls back to caller-supplied
    // refs if the maps don't have the id (e.g., entrance burst fires
    // before scene maps are fully populated).
    const freshNode = this._deps.getSceneNode(request.node.id) ?? request.node;
    const currentGroup = this._deps.getBeamGroup(freshNode.id) ?? request.group;
    const envelopeColor = new THREE.Color(freshNode.color);
    const shape = freshNode.state === 'domain' ? 'domain' : 'cluster';

    // Beam config derived from preset.
    const sizeFactor = Math.min(Math.max(freshNode.size / 50, 0.5), 3.0);
    const sizeFactorMultiplier = preset.applySizeFactor ? sizeFactor : 1.0;
    const baseRadius =
      freshNode.size * 0.04 * sizeFactorMultiplier * preset.thicknessMultiplier;
    const radius = preset.applyClickRadiusFloor
      ? Math.max(baseRadius, 0.1)
      : baseRadius;
    const sustainMs = preset.sizeFactorSustainBonus
      ? preset.sustainMs + sizeFactor * 500
      : preset.sustainMs;

    // Single THREE.Color allocation per fire — reused for beam config AND
    // the onImpact envelope.acquire call. Pre-refactor `_triggerBeamImpact`
    // also allocated once; the per-fire allocation budget stays at 1 Color.
    this._deps.beamPool.acquire(
      currentGroup,
      {
        color: envelopeColor,
        radius,
        sustainMs,
        onImpact: () => {
          // Canon F7 causal-ordering invariant: all F19 reactions live
          // INSIDE this onImpact callback body. The beam takes ~700ms to
          // travel; a synchronous reaction outside this scope would render
          // before beam arrival (the bug class this coordinator prevents).
          if (preset.kineticDisplacement) {
            this._deps.clusterPhysics.onBeamImpact(freshNode.id, freshNode.size);
          }
          // Pass `freshNode.size` directly — canon F19 mandates no floor
          // (the v0.4.23 "10x balloon" regression was caused by
          // `Math.max(node.size, 8.0)` here; canon code sample at
          // `references/3d-visualization.md:276` is the authoritative shape).
          this._deps.envelopePool.acquire(currentGroup, freshNode.size, shape, envelopeColor);
          this._deps.flashEmissive(freshNode.id, envelopeColor);
          if (preset.marksEngulfed) {
            this._deps.selectionController.onImpact(freshNode.id);
          }
        },
      },
      this._deps.renderer.camera,
    );
  }

  /** Idempotent. */
  dispose(): void {
    if (this._disposed) return;
    this._disposed = true;
  }
}
