// frontend/src/lib/components/taxonomy/ImpactCoordinator.ts
//
// Sub-project C — single source for the canon F7→F19→F9 impact chain.
//
// Spec: docs/superpowers/specs/2026-05-17-impact-coordinator-design.md
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
//   - Owns `_selectionEngulfed: Set<string>`; `isEngulfed(id)` exposes
//     read access to consumers (breathing handler, T3.4 idle pulse).
//   - Registers a dedicated handler in AnimationCoordinator's `impact` phase
//     to drive the T3.4 idle ambient pulse on engulfed nodes (extracted
//     from the breathing handler where it lives today).
//   - Causal-ordering invariant: all F19 reactions (envelope, flash, physics)
//     fire inside the BeamPool onImpact callback body, never synchronously
//     with acquire. Pinned at source level by source-grep test #6.

import * as THREE from 'three';
import type { BeamPool } from './BeamPool';
import type { EnvelopePool } from './EnvelopePool';
import type { ClusterPhysics } from './ClusterPhysics';
import type { AnimationCoordinator } from './AnimationCoordinator';
import type { TopologyRenderer } from './TopologyRenderer';
import type { SceneNode } from './TopologyData';

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
  /** Adds the impacted node to `_selectionEngulfed` (only true for 'click'). */
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
   * Stays inline in SemanticTopology (per §2 OUT) pending Sub-project E's
   * FlashController extraction. The coordinator's onImpact body calls this
   * via `deps.flashEmissive(nodeId, color)`.
   */
  flashEmissive: (nodeId: string, color: THREE.Color) => void;
  /** Looks up the freshest SceneNode by id (sceneData may have changed mid-flight). Returns undefined for unknown ids — fire() falls back to request.node. */
  getSceneNode: (id: string) => SceneNode | undefined;
  /** Looks up the current group by id (rebuildScene may have replaced it). Returns undefined for unknown ids — fire() falls back to request.group. */
  getBeamGroup: (id: string) => THREE.Group | undefined;
  /** Looks up the current node mesh by id (for T3.4 idle pulse emissive writes). Returns undefined for unknown ids — _tick early-returns. */
  getNodeMesh: (id: string) => THREE.Mesh | undefined;
  /** Returns currently-selected cluster id, or null. */
  getSelectedId: () => string | null;
  /** Returns true if the node currently has an active flash (`_flashStates.has(id)`). Used to gate the T3.4 idle pulse so it doesn't overwrite the flash's attack/hold/decay ramp. */
  isFlashActive: (id: string) => boolean;
  renderer: TopologyRenderer;
  animationCoordinator: AnimationCoordinator;
}

/**
 * Selection emissive floor (canon T3.4) — the T3.4 idle ambient pulse
 * writes `max(baseEmissive, SELECTION_EMISSIVE_FLOOR) + idlePulse`. 1.0
 * sits above the UnrealBloomPass threshold (0.85) so the pulse is
 * bloom-visible even on low-baseline clusters. Preserved verbatim from
 * pre-refactor `SemanticTopology.svelte:1508`.
 */
export const SELECTION_EMISSIVE_FLOOR = 1.0;

export class ImpactCoordinator {
  private _deps: ImpactCoordinatorDeps;
  /**
   * Set of node ids that have received their first 'click' impact since
   * selection. Populated by `fire(...)` when `preset.marksEngulfed === true`.
   * Cleared via `clearEngulfed()` on selection transition. Read publicly
   * via `isEngulfed(id)` — the T3.4 idle ambient pulse + the breathing
   * handler's emissive-overwrite guard both key off this set.
   */
  private _selectionEngulfed: Set<string> = new Set();
  private _removeTick: (() => void) | null = null;
  private _disposed = false;
  /**
   * T3.4 idle ambient pulse accumulator. Real-time seconds advance via
   * `delta` from AnimationCoordinator (`_pulseTime += delta` inside `_tick`).
   * Reset to 0 by `clearEngulfed()` so each selection cycle starts at
   * sine-phase zero (predictable per-selection phase, no visible jump on
   * rapid re-selection). Framerate-independent — pre-refactor used
   * `_breathingTime += 0.016` (fixed-step) which drifted on 30fps devices.
   */
  private _pulseTime = 0;

  constructor(deps: ImpactCoordinatorDeps) {
    this._deps = deps;
    // Register the idle-pulse + decay handler in the impact phase. The
    // impact phase already orders impact-related per-frame work; registering
    // the pulse here keeps the engulfment-driven emissive write in the same
    // phase as the beam/envelope/flash advances.
    this._removeTick = deps.animationCoordinator.register('impact', (delta) => {
      this._tick(delta);
    });
  }

  /**
   * Fire a canonical impact chain: beam acquire → onImpact callback →
   * (optional) clusterPhysics kinetic shake + envelope acquire + emissive
   * flash + selection-engulfment marker. Per-trigger parameters from
   * `TRIGGER_PRESETS[request.trigger]`.
   *
   * Lenient on disposed: returns silently if `dispose()` was called.
   *
   * Causal-ordering invariant (canon F7): all F19 reactions live inside
   * the onImpact callback body, never synchronously here. The beam takes
   * ~700ms to travel; firing reactions synchronously would render them
   * before the beam arrives.
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
            this._selectionEngulfed.add(freshNode.id);
          }
        },
      },
      this._deps.renderer.camera,
    );
  }

  /** True if the node received its first 'click' impact since selection. */
  isEngulfed(nodeId: string): boolean {
    return this._selectionEngulfed.has(nodeId);
  }

  /**
   * Clear the engulfed-marker set (called on selection change so a re-click
   * triggers a fresh engulfment burst). Pre-migration the call site at
   * `SemanticTopology.svelte:2365` used `_selectionEngulfed.clear()` directly
   * (clear-all on every selection transition); this method preserves that
   * shape. Calling with an `id` argument deletes only that single entry —
   * intended for future Sub-project E (Selection State Machine) which may
   * want finer-grained eviction.
   *
   * Side effect: resets `_pulseTime` so the T3.4 idle ambient pulse starts
   * from sine-phase zero on the NEXT engulfment (predictable per-selection
   * phase; no visible jump on rapid re-selection).
   */
  clearEngulfed(id?: string): void {
    if (this._disposed) return;
    if (id != null) {
      this._selectionEngulfed.delete(id);
    } else {
      this._selectionEngulfed.clear();
    }
    this._pulseTime = 0;
  }

  /**
   * T3.4 idle ambient pulse — slow emissive flare on the currently-selected
   * node AFTER its first beam impact (gated by `_selectionEngulfed`).
   * Extracted from the breathing handler's `else if (isSelected && ...)`
   * branch in `SemanticTopology.svelte`.
   *
   * Reads via `getNodeMesh` + `getSelectedId` + the flash-state getter;
   * writes only to `mat.emissiveIntensity` (no scale, no color). Mid-flash
   * protection: skips emissive writes if the active flash state already
   * owns the emissive ramp during attack/hold/decay; pulse takes over after.
   *
   * Note: in this sub-project, `_flashStates` is still read via a getter
   * since flash state stays inline in SemanticTopology pending Sub-project E.
   * The `isFlashActive` dep is the bridge — `(id) => _flashStates.has(id)`
   * supplied by SemanticTopology at construction.
   */
  private _tick(delta: number): void {
    if (this._disposed) return;
    this._pulseTime += delta;
    const selectedId = this._deps.getSelectedId();
    if (!selectedId || !this.isEngulfed(selectedId)) return;
    if (this._deps.isFlashActive(selectedId)) return; // flash owns ramp
    const mesh = this._deps.getNodeMesh(selectedId);
    if (!mesh) return;
    const mat = mesh.material as THREE.MeshStandardMaterial;
    const baseEmissive = (mesh.userData.baseEmissive as number) ?? mat.emissiveIntensity;
    // SELECTION_EMISSIVE_FLOOR = 1.0 — ensures the selected node's baseline
    // sits at or above the UnrealBloomPass threshold (0.85) so the pulse
    // contribution is bloom-visible. The pulse formula is preserved
    // verbatim from the pre-refactor breathing handler at
    // SemanticTopology.svelte:1508-1511.
    const idlePulse = Math.sin(this._pulseTime * 0.4) * 0.1 + 0.1;
    mat.emissiveIntensity =
      Math.max(baseEmissive, SELECTION_EMISSIVE_FLOOR) + idlePulse;
  }

  /** Idempotent. Cancels the impact-phase tick handler + clears state. */
  dispose(): void {
    if (this._disposed) return;
    this._disposed = true;
    this._removeTick?.();
    this._removeTick = null;
    this._selectionEngulfed.clear();
  }
}
