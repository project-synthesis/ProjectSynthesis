// frontend/src/lib/components/taxonomy/SelectionController.ts
//
// Sub-project E — selection state machine.
//
// Spec: docs/superpowers/specs/2026-05-18-selection-state-machine-design.md §3.2
//
// Owns ALL selection state: highlight target + highlight color snapshot +
// previous id + engulfed set + flash states (via FlashController). State
// transitions are first-class events through select / deselect / onImpact /
// onDecayTimer.
//
// Replaces the canon F16 highlight-survival hack at the end of rebuildScene
// with a deterministic afterRebuild() call from the orchestrator.

import * as THREE from 'three';
import type { TopologyRenderer } from './TopologyRenderer';
import type { AnimationCoordinator } from './AnimationCoordinator';
import type { ImpactCoordinator } from './ImpactCoordinator';
import type { SceneNode } from './TopologyData';
import { FlashController } from './FlashController';

export type SelectionState =
  | 'idle'
  | 'focusing'
  | 'focused'
  | 'impacting'
  | 'engulfed';

const GLOW_TOTAL_MS = 1380;

// Module-scope scratch vector (m1): re-used by `select()` fallback target when
// the node mesh is not yet built. Hoisted to avoid per-call allocation under
// rapid selection flicks.
const _scratchVec3 = new THREE.Vector3();

// Module-scope adjacency table for the selection state machine. Each key lists
// the legal successor states. Hoisted from inside `isLegalTransition` so the
// object literal is not re-allocated on every `_transition` call.
const ADJ: Record<SelectionState, SelectionState[]> = {
  idle: ['focusing'],
  focusing: ['focused', 'idle'],
  focused: ['impacting', 'idle'],
  impacting: ['engulfed', 'idle'],
  engulfed: ['focusing', 'idle'],
};

export interface SelectionControllerDeps {
  renderer: TopologyRenderer;
  animationCoordinator: AnimationCoordinator;
  impactCoordinator: ImpactCoordinator;
  getNodeMesh: (id: string) => THREE.Mesh | undefined;
  getSceneNode: (id: string) => SceneNode | undefined;
  getBeamGroup: (id: string) => THREE.Group | undefined;
  highlightColor: number;
}

export class SelectionController {
  private _deps: SelectionControllerDeps;
  private _state: SelectionState = 'idle';
  private _selectedId: string | null = null;
  private _previousId: string | null = null;
  private _highlightedId: string | null = null;
  private _highlightedColor: number | null = null;
  private _engulfedSet: Set<string> = new Set();
  private _flash: FlashController;
  private _pulseTime = 0;
  private _decayTimer: ReturnType<typeof setTimeout> | null = null;
  private _removeTick: (() => void) | null = null;
  private _disposed = false;

  constructor(deps: SelectionControllerDeps) {
    this._deps = deps;
    this._flash = new FlashController({
      animationCoordinator: deps.animationCoordinator,
      getNodeMesh: deps.getNodeMesh,
      isHighlighted: (id) => this._highlightedId === id,
    });
    this._removeTick = deps.animationCoordinator.register(
      'impact',
      (delta) => this._tickIdlePulse(delta),
    );
  }

  /** Current selection state. One of `idle`, `focusing`, `focused`, `impacting`, `engulfed`. */
  get state(): SelectionState { return this._state; }

  /** Currently-selected cluster id, or `null` if idle. */
  get selectedId(): string | null { return this._selectedId; }

  /**
   * True if the node received its first 'click' impact since selection.
   * Set by `onImpact`; cleared by `select` (cancel-via-idle path) and
   * `dispose`. The engulfed set is per-selection scope — re-selecting the
   * same id after deselect clears it.
   */
  isEngulfed(nodeId: string): boolean {
    return this._engulfedSet.has(nodeId);
  }

  /**
   * Begin selection. Cancels any in-flight transition via the cancel-via-idle
   * pattern (program-context §513), then transitions to `focusing`. Passing
   * `null` transitions to `idle` (equivalent to `deselect()`).
   *
   * Side effects:
   *   - Clears engulfed-set + pulse time
   *   - Applies highlight (BOTH color + emissive swap to `highlightColor` per F16,
   *     and restores the previous-selected mesh on switch)
   *   - Invokes `renderer.focusOn(target, undefined, undefined, onComplete)` —
   *     the 4-arg form so the focus-tween done-callback drives the
   *     focusing → focused transition
   *   - Fires `impactCoordinator.fire({trigger:'click', node, group})` when
   *     `sceneNode` + beam-group both resolve (skipped silently otherwise)
   *
   * Edge cases:
   *   - Same-id calls are no-ops (idempotent).
   *   - Lenient on disposed: no-op after `dispose()`.
   *   - When the node mesh is not yet built, the focus target falls back to
   *     `_scratchVec3.set(0, 0, 0)` (module-scope; no allocation).
   */
  select(nodeId: string | null): void {
    if (this._disposed) return;
    if (nodeId === this._selectedId) return;

    if (this._state !== 'idle') {
      this._clearPendingDecay();
      this._transition('idle');
    }

    this._previousId = this._selectedId;
    this._engulfedSet.clear();
    this._pulseTime = 0;

    if (nodeId === null) {
      this._clearHighlight();
      this._selectedId = null;
      return;
    }

    this._selectedId = nodeId;
    this._applyHighlight(nodeId);
    this._transition('focusing');

    const mesh = this._deps.getNodeMesh(nodeId);
    const target = mesh?.position ?? _scratchVec3.set(0, 0, 0);
    this._deps.renderer.focusOn(
      target,
      undefined,
      undefined,
      () => this._onFocusAnimationComplete(),
    );

    const sceneNode = this._deps.getSceneNode(nodeId);
    const group = this._deps.getBeamGroup(nodeId);
    if (sceneNode && group) {
      this._deps.impactCoordinator.fire({
        trigger: 'click',
        node: sceneNode,
        group,
      });
    }
  }

  /** Clear selection. Identical to `select(null)` — provided for clarity at call sites. */
  deselect(): void {
    this.select(null);
  }

  /**
   * Called by ImpactCoordinator's onImpact callback when a 'click' beam arrives
   * at the selected cluster. Transitions `focused` → `impacting` + adds the
   * node to the engulfed set + starts the decay timer for the eventual
   * `impacting` → `engulfed` transition (B3, GLOW_TOTAL_MS = 1380 ms).
   *
   * No-op if:
   *   - `nodeId !== _selectedId` (race-safe — stale beam from prior selection
   *     does not corrupt state)
   *   - `_state !== 'focused'` (already past the impact window, or focus tween
   *     not done yet)
   *   - disposed
   *
   * The decay timer self-cancels (re-checks `_state` + `_selectedId` + disposed)
   * before firing the engulfed transition, so a `select(other)` mid-decay
   * leaves no zombie transition.
   */
  onImpact(nodeId: string): void {
    if (this._disposed) return;
    if (nodeId !== this._selectedId) return;
    if (this._state !== 'focused') return;
    this._engulfedSet.add(nodeId);
    this._transition('impacting');
    this._clearPendingDecay();
    this._decayTimer = setTimeout(() => {
      this._decayTimer = null;
      if (this._disposed) return;
      if (this._state !== 'impacting') return;
      if (this._selectedId !== nodeId) return;
      this._transition('engulfed');
    }, GLOW_TOTAL_MS);
  }

  /**
   * Called by the orchestrator AFTER `rebuildScene` completes. Clears stale
   * `_highlightedId` / `_highlightedColor` snapshots (the old mesh refs were
   * disposed by the rebuild) then re-applies the highlight to the rebuilt
   * selected mesh.
   *
   * Replaces the canon F16 highlight-survival hack at the end of
   * `rebuildScene` with a deterministic, single-callsite handoff.
   *
   * No-op if disposed or `_selectedId === null`.
   */
  afterRebuild(): void {
    if (this._disposed) return;
    if (this._selectedId === null) return;
    this._highlightedId = null;
    this._highlightedColor = null;
    this._applyHighlight(this._selectedId);
  }

  /** Delegate to `FlashController.flash()`. Lenient on disposed. */
  flash(nodeId: string, color: THREE.Color): void {
    if (this._disposed) return;
    this._flash.flash(nodeId, color);
  }

  /** True if FlashController currently has an active flash state for this node. */
  isFlashActive(nodeId: string): boolean {
    return this._flash.isActive(nodeId);
  }

  /**
   * Tear down. Idempotent — a second call is a no-op.
   *
   * Side effects:
   *   - Cancels the impact-phase tick registration (AnimationCoordinator)
   *   - Clears any pending decay timer
   *   - Disposes the inner FlashController
   *   - Clears all internal state (engulfed set, highlight snapshots,
   *     selected/previous ids)
   *
   * Subsequent calls to `select`, `onImpact`, `flash`, `afterRebuild` are all
   * no-ops (lenient on disposed).
   */
  dispose(): void {
    if (this._disposed) return;
    this._disposed = true;
    this._clearPendingDecay();
    this._removeTick?.();
    this._removeTick = null;
    this._flash.dispose();
    this._engulfedSet.clear();
    this._highlightedId = null;
    this._highlightedColor = null;
    this._selectedId = null;
    this._previousId = null;
  }

  // ── Private ───────────────────────────────────────────────────────

  /**
   * Transition guard. Throws in dev mode (`import.meta.env.DEV`) if `next` is
   * not a legal successor of `_state` per the module-scope `ADJ` table; in
   * production, console.warns and leaves `_state` unchanged.
   */
  private _transition(next: SelectionState): void {
    if (!isLegalTransition(this._state, next)) {
      const msg = `[SelectionController] illegal transition: ${this._state} → ${next}`;
      if (import.meta.env.DEV) throw new Error(msg);
      console.warn(msg);
      return;
    }
    this._state = next;
  }

  /**
   * Camera focus-tween done-callback. Transitions `focusing` → `focused`.
   * No-op if disposed or the state already moved away from `focusing`
   * (cancel-via-idle path — `select(other)` raced the tween).
   */
  private _onFocusAnimationComplete(): void {
    if (this._disposed) return;
    if (this._state !== 'focusing') return;
    this._transition('focused');
  }

  /**
   * Apply the F16 highlight to `nodeId`'s mesh.
   *
   * Sequence:
   *   - If a different mesh is currently highlighted, restore its previously
   *     snapshotted color + emissive (the dual-restore branch of B5).
   *   - Snapshot the new mesh's `color.getHex()` into `_highlightedColor`
   *     (B4 snapshot — single source of truth for restoration).
   *   - Dual swap to `highlightColor`: BOTH `mat.color` AND `mat.emissive`
   *     (the F16 dual-swap rule).
   *
   * If `getNodeMesh(nodeId)` returns undefined (mesh not yet built), nulls
   * out the snapshots — `afterRebuild()` will retry once meshes exist.
   */
  private _applyHighlight(nodeId: string): void {
    if (this._highlightedId && this._highlightedId !== nodeId) {
      const prevMesh = this._deps.getNodeMesh(this._highlightedId);
      if (prevMesh && this._highlightedColor !== null) {
        const prevMat = prevMesh.material as THREE.MeshStandardMaterial;
        prevMat.color.setHex(this._highlightedColor);
        prevMat.emissive.setHex(this._highlightedColor);
      }
    }
    const mesh = this._deps.getNodeMesh(nodeId);
    if (!mesh) {
      this._highlightedId = null;
      this._highlightedColor = null;
      return;
    }
    const mat = mesh.material as THREE.MeshStandardMaterial;
    this._highlightedColor = mat.color.getHex();
    this._highlightedId = nodeId;
    mat.color.setHex(this._deps.highlightColor);
    mat.emissive.setHex(this._deps.highlightColor);
  }

  /**
   * Restore the snapshotted color + emissive on the currently-highlighted
   * mesh (B5 dual restore), then null out the highlight snapshots. No-op if
   * nothing is currently highlighted. Mesh-missing is tolerated (snapshots
   * are still cleared) so a disposed mesh does not strand state.
   */
  private _clearHighlight(): void {
    if (this._highlightedId === null) return;
    const mesh = this._deps.getNodeMesh(this._highlightedId);
    if (mesh && this._highlightedColor !== null) {
      const mat = mesh.material as THREE.MeshStandardMaterial;
      mat.color.setHex(this._highlightedColor);
      mat.emissive.setHex(this._highlightedColor);
    }
    this._highlightedId = null;
    this._highlightedColor = null;
  }

  /** Cancel any pending `impacting → engulfed` decay timer. Safe to call when none is set. */
  private _clearPendingDecay(): void {
    if (this._decayTimer !== null) {
      clearTimeout(this._decayTimer);
      this._decayTimer = null;
    }
  }

  /**
   * Per-frame tick for the post-impact idle pulse (T3.4 — soft breathing on
   * the engulfed cluster while the user dwells on it).
   *
   * Early-returns guard the hot path against doing any work when the pulse
   * is not visible:
   *   - disposed
   *   - state !== 'engulfed' (only pulses post-decay-timer)
   *   - no selection
   *   - flash currently active (flash owns emissiveIntensity — yielding
   *     prevents the pulse from clobbering the flash envelope)
   *   - mesh not yet built
   *
   * Emissive floor: `Math.max(baseEmissive, SELECTION_EMISSIVE_FLOOR)` keeps
   * the dim-side of the pulse above the bloom-pass threshold so the cluster
   * never visually drops out under bloom-headroom edge cases (F-canon dim-pop
   * guard).
   */
  private _tickIdlePulse(delta: number): void {
    if (this._disposed) return;
    if (this._state !== 'engulfed') return;
    if (this._selectedId === null) return;
    if (this._flash.isActive(this._selectedId)) return;
    const mesh = this._deps.getNodeMesh(this._selectedId);
    if (!mesh) return;
    this._pulseTime += delta;
    const mat = mesh.material as THREE.MeshStandardMaterial;
    const baseEmissive = (mesh.userData.baseEmissive as number) ?? mat.emissiveIntensity;
    const idlePulse = Math.sin(this._pulseTime * 0.4) * 0.1 + 0.1;
    mat.emissiveIntensity = Math.max(baseEmissive, SELECTION_EMISSIVE_FLOOR) + idlePulse;
  }
}

export const SELECTION_EMISSIVE_FLOOR = 1.0;

function isLegalTransition(from: SelectionState, to: SelectionState): boolean {
  return ADJ[from].includes(to);
}
