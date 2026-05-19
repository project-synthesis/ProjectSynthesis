// frontend/src/lib/components/taxonomy/FlashController.ts
//
// Sub-project E — per-node emissive flash state machine.
// Extracted from inline `flashEmissive` + `_flashStates` + `_tickFlashStates`
// in SemanticTopology.svelte (pre-refactor lines 263-292, 386-478).
//
// Spec: docs/superpowers/specs/2026-05-18-selection-state-machine-design.md §3.3

import * as THREE from 'three';
import type { AnimationCoordinator } from './AnimationCoordinator';

export interface FlashState {
  startTime: number;
  baselineEmissive: number;
  startIntensity: number;
  domainEmissive: THREE.Color;
}

// Timing constants — verbatim from SemanticTopology.svelte:263-273 (B7).
const GLOW_ATTACK_MS = 120;
const GLOW_HOLD_MS = 580;
const GLOW_DECAY_MS = 680;
const GLOW_TOTAL_MS = GLOW_ATTACK_MS + GLOW_HOLD_MS + GLOW_DECAY_MS; // 1380ms
// Additive (not multiplicative) — see SemanticTopology.svelte:267-272 rationale.
const GLOW_PEAK_DELTA = 1.6;

/** HIGHLIGHT_COLOR — cyan canonical highlight per canon F16. Mirrors the
 *  module-scope constant in SemanticTopology.svelte. */
const HIGHLIGHT_COLOR = 0x00ffff;

export interface FlashControllerDeps {
  animationCoordinator: AnimationCoordinator;
  getNodeMesh: (id: string) => THREE.Mesh | undefined;
  /** Live-checked at cleanup time so post-flash emissive lands at
   *  HIGHLIGHT_COLOR if the node is still selected, or the domain color
   *  otherwise (B10). Mirrors real-source `_highlightedId === nodeId` check
   *  at SemanticTopology.svelte:444-449. */
  isHighlighted: (id: string) => boolean;
}

export class FlashController {
  private _deps: FlashControllerDeps;
  private _states: Map<string, FlashState> = new Map();
  private _removeTick: (() => void) | null = null;
  private _disposed = false;

  constructor(deps: FlashControllerDeps) {
    this._deps = deps;
    this._removeTick = deps.animationCoordinator.register('impact', () =>
      this._tick(performance.now()),
    );
  }

  flash(nodeId: string, color: THREE.Color): void {
    if (this._disposed) return;
    const mesh = this._deps.getNodeMesh(nodeId);
    if (!mesh) return;
    const mat = mesh.material as THREE.MeshStandardMaterial;
    const existing = this._states.get(nodeId);
    const trueBase = (mesh.userData.baseEmissive as number) ?? mat.emissiveIntensity;
    const baseline = existing ? existing.baselineEmissive : trueBase;
    const startIntensity = existing
      ? existing.startIntensity
      : Math.max(mat.emissiveIntensity, baseline);
    this._states.set(nodeId, {
      startTime: performance.now(),
      baselineEmissive: baseline,
      startIntensity,
      domainEmissive: color.clone(),
    });
    mat.emissive.copy(color);
  }

  isActive(nodeId: string): boolean {
    return this._states.has(nodeId);
  }

  dispose(): void {
    if (this._disposed) return;
    this._disposed = true;
    this._removeTick?.();
    this._removeTick = null;
    for (const [nodeId, state] of this._states) {
      const mesh = this._deps.getNodeMesh(nodeId);
      if (mesh) {
        const mat = mesh.material as THREE.MeshStandardMaterial;
        if (this._deps.isHighlighted(nodeId)) {
          mat.emissive.setHex(HIGHLIGHT_COLOR);
        } else {
          mat.emissive.copy(state.domainEmissive);
        }
        mat.emissiveIntensity = state.baselineEmissive;
      }
    }
    this._states.clear();
  }

  private _tick(now: number): void {
    if (this._disposed) return;
    if (this._states.size === 0) return;
    for (const [nodeId, state] of this._states) {
      const mesh = this._deps.getNodeMesh(nodeId);
      if (!mesh) {
        this._states.delete(nodeId);
        continue;
      }
      const mat = mesh.material as THREE.MeshStandardMaterial;
      const elapsed = now - state.startTime;
      const peak = state.baselineEmissive + GLOW_PEAK_DELTA;

      if (elapsed >= GLOW_TOTAL_MS) {
        if (this._deps.isHighlighted(nodeId)) {
          mat.emissive.setHex(HIGHLIGHT_COLOR);
        } else {
          mat.emissive.copy(state.domainEmissive);
        }
        mat.emissiveIntensity = state.baselineEmissive;
        this._states.delete(nodeId);
        continue;
      }

      mat.emissive.copy(state.domainEmissive);

      if (elapsed < GLOW_ATTACK_MS) {
        const t = elapsed / GLOW_ATTACK_MS;
        const ease = 1 - Math.pow(1 - t, 3);
        mat.emissiveIntensity = state.startIntensity + (peak - state.startIntensity) * ease;
      } else if (elapsed < GLOW_ATTACK_MS + GLOW_HOLD_MS) {
        mat.emissiveIntensity = peak;
      } else {
        const decayElapsed = elapsed - GLOW_ATTACK_MS - GLOW_HOLD_MS;
        const t = decayElapsed / GLOW_DECAY_MS;
        const ease = 1 - Math.pow(1 - t, 3);
        mat.emissiveIntensity = peak + (state.baselineEmissive - peak) * ease;
      }
    }
  }
}
