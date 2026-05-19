// frontend/src/lib/components/taxonomy/FlashController.ts
//
// Sub-project E — per-node emissive flash state machine.
// Extracted from inline `flashEmissive` + `_flashStates` + `_tickFlashStates`
// in SemanticTopology.svelte (pre-refactor lines 263-292, 386-478).
//
// Spec: docs/superpowers/specs/2026-05-18-selection-state-machine-design.md §3.3

import * as THREE from 'three';
import type { AnimationCoordinator } from './AnimationCoordinator';

/**
 * Per-node flash bookkeeping captured at flash-acquire time.
 *
 * Each field is invariant for the lifetime of a single flash (a refire on an
 * active flash overwrites `startTime` + `domainEmissive` but preserves
 * `baselineEmissive` + `startIntensity` per F19/B6).
 */
export interface FlashState {
  /** `performance.now()` timestamp at acquire/refire — drives the 1380ms
   *  attack/hold/decay envelope in `_tick`. */
  startTime: number;
  /** Data-driven floor sourced from `mesh.userData.baseEmissive` — the value
   *  `emissiveIntensity` must return to once decay completes (F19). NOT the
   *  displayed `mat.emissiveIntensity` at acquire-time, which may have been
   *  inflated by the idle pulse. */
  baselineEmissive: number;
  /** The displayed `mat.emissiveIntensity` at acquire-time — the attack ramps
   *  UP from this value to `peak`, preventing a "dim flash" frame when the
   *  idle pulse had already elevated emissive above baseline (B6). */
  startIntensity: number;
  /** Domain (or highlight) emissive color used for the duration of attack +
   *  hold + decay. Cloned at acquire-time so the caller can mutate the source
   *  without disturbing the in-flight flash. */
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

/**
 * FlashController — per-node emissive flash state machine.
 *
 * Lifecycle:
 *   1. Construction registers a tick callback on the supplied
 *      `AnimationCoordinator` under the `'impact'` channel. The tick runs
 *      every frame the coordinator drives.
 *   2. `flash(nodeId, color)` stamps a new flash (or refires an active one)
 *      and immediately writes the domain emissive color to the material so
 *      there is no one-frame blip before the next tick.
 *   3. `_tick(now)` walks all active flashes each frame, advancing
 *      attack → hold → decay → cleanup. Cleanup decides the resting color
 *      via the live `isHighlighted` check (B10) so highlighted nodes land at
 *      cyan and non-highlighted nodes return to their domain color.
 *   4. `dispose()` unregisters the tick, restores each active node's
 *      baseline emissive (with live-checked color), and clears the state map.
 *      Idempotent: subsequent calls are no-ops.
 *
 * Invariants:
 *   - F19 baseline continuity: `baselineEmissive` is captured ONCE from
 *     `userData.baseEmissive` at first acquire and preserved across refires.
 *   - B6 mid-flash continuity: `startIntensity` is captured ONCE at first
 *     acquire so the attack ramps from the current displayed value, never
 *     causing a downward jump.
 *   - B9 dispose-restore: every node tracked at dispose-time has its
 *     baseline restored — no orphaned inflated emissive on teardown.
 *   - B10 cleanup-color: cleanup color is decided by a LIVE
 *     `isHighlighted` query, not a captured value, so a node selected mid-
 *     flash lands at HIGHLIGHT_COLOR.
 */
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

  /**
   * Stamp a new flash on the node OR refire an active one (preserving
   * baseline + startIntensity per F19 + B6).
   *
   * The real source captures the true baseline from `userData.baseEmissive`
   * (NOT `mat.emissiveIntensity`) — the idle pulse may have inflated the
   * displayed emissive, but `userData.baseEmissive` is the data-driven floor.
   *
   * `startIntensity` is captured at acquire-time so the attack ramps UP
   * from whatever the current displayed value is — preventing the "dim flash"
   * frame when the idle pulse had already elevated emissive above baseline.
   *
   * The domain emissive color is written immediately via `mat.emissive.copy`
   * to prevent a one-frame blip before the next tick paints the same color.
   *
   * No-op if disposed or the node mesh is not currently resolvable.
   */
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

  /** True if a flash state is currently registered for this node. */
  isActive(nodeId: string): boolean {
    return this._states.has(nodeId);
  }

  /**
   * Tear down the controller and restore baseline emissive on every tracked
   * node (F19 dispose-restore semantics — B9).
   *
   * For each active flash:
   *   - Resolves the mesh (skips silently if it has been removed).
   *   - Writes the cleanup color via a LIVE `isHighlighted` check —
   *     highlighted nodes land at HIGHLIGHT_COLOR, others at their captured
   *     domain emissive.
   *   - Restores `emissiveIntensity` to the captured baseline.
   *
   * Unregisters the tick callback and clears the state map. Idempotent —
   * subsequent calls return without side effects.
   */
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

  /**
   * Per-frame tick — advances every active flash through the 1380ms envelope.
   *
   * Branches:
   *   1. **Mesh gone** — silently drop the state (the node was removed mid-
   *      flash; no material to restore).
   *   2. **Cleanup** (`elapsed >= GLOW_TOTAL_MS`) — write cleanup color via
   *      live `isHighlighted` query (B10), restore baseline emissive, delete
   *      state. The live check ensures a node selected mid-flash lands at
   *      HIGHLIGHT_COLOR, not its captured domain color.
   *   3. **Attack** (`elapsed < GLOW_ATTACK_MS`) — cubic ease-out ramp from
   *      `startIntensity` up to `baselineEmissive + GLOW_PEAK_DELTA`. Using
   *      `startIntensity` (not `baselineEmissive`) preserves F19 mid-flash
   *      continuity — the attack always ramps from the current displayed
   *      value, never causing a downward jump on refire (B6).
   *   4. **Hold** (`elapsed < attack + hold`) — pinned at peak.
   *   5. **Decay** — cubic ease-out from peak back down to baseline.
   *
   * Domain color is written every active frame so an external mutation of
   * `mat.emissive` between ticks does not leak through.
   */
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
