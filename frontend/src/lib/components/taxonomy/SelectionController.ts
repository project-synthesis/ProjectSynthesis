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
const _scratchVec3 = new THREE.Vector3();

export interface SelectionControllerDeps {
  renderer: TopologyRenderer;
  animationCoordinator: AnimationCoordinator;
  impactCoordinator: ImpactCoordinator;
  getNodeMesh: (id: string) => THREE.Mesh | undefined;
  getSceneNode: (id: string) => SceneNode | undefined;
  getBeamGroup: (id: string) => THREE.Group | undefined;
  getBaseEmissive: (id: string) => number | undefined;
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

  get state(): SelectionState { return this._state; }
  get selectedId(): string | null { return this._selectedId; }

  isEngulfed(nodeId: string): boolean {
    return this._engulfedSet.has(nodeId);
  }

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
    // TODO: remove `as any` after Cycle 3 appends onComplete? — Sub-project E B8
    (this._deps.renderer.focusOn as any)(
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

  deselect(): void {
    this.select(null);
  }

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

  afterRebuild(): void {
    if (this._disposed) return;
    if (this._selectedId === null) return;
    this._highlightedId = null;
    this._highlightedColor = null;
    this._applyHighlight(this._selectedId);
  }

  flash(nodeId: string, color: THREE.Color): void {
    if (this._disposed) return;
    this._flash.flash(nodeId, color);
  }

  isFlashActive(nodeId: string): boolean {
    return this._flash.isActive(nodeId);
  }

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

  private _transition(next: SelectionState): void {
    if (!isLegalTransition(this._state, next)) {
      const msg = `[SelectionController] illegal transition: ${this._state} → ${next}`;
      if (import.meta.env.DEV) throw new Error(msg);
      console.warn(msg);
      return;
    }
    this._state = next;
  }

  private _onFocusAnimationComplete(): void {
    if (this._disposed) return;
    if (this._state !== 'focusing') return;
    this._transition('focused');
  }

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

  private _clearPendingDecay(): void {
    if (this._decayTimer !== null) {
      clearTimeout(this._decayTimer);
      this._decayTimer = null;
    }
  }

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
  const ADJ: Record<SelectionState, SelectionState[]> = {
    idle: ['focusing'],
    focusing: ['focused', 'idle'],
    focused: ['impacting', 'idle'],
    impacting: ['engulfed', 'idle'],
    engulfed: ['focusing', 'idle'],
  };
  return ADJ[from].includes(to);
}
