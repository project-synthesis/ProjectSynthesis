// frontend/src/lib/components/taxonomy/AnimationCoordinator.ts
//
// Per-frame animation pipeline for the Pattern Graph 3D scene.
//
// Spec: docs/superpowers/specs/2026-05-17-animation-coordinator-design.md
// Brand canon: .claude/skills/brand-guidelines/references/3d-visualization.md
//   "Animation Tick Ordering" section.
//
// Contract summary:
//   - One AnimationCoordinator per SemanticTopology mount.
//   - Owns the single renderer.addAnimationCallback(() => this._tick())
//     registration; dispatches to phase-specific handlers in fixed
//     PHASE_ORDER (impact → physics → breathing → ambient → camera).
//   - Within each phase, handlers run in registration order (FIFO). For
//     `impact` the within-phase order is load-bearing: beam.update fires
//     onImpact synchronously, mutating envelope/flash/physics state; the
//     consumers (envelope.update, flash.update) must run AFTER beam.
//   - register(phase, handler) returns an unsubscribe function (same
//     shape as the pre-refactor renderer.addAnimationCallback API).
//   - Strict on unknown phase: register('typo', h) throws.
//   - Lenient on disposed: register(validPhase, h) AFTER dispose returns
//     a no-op unsubscribe (cleanup-return-mid-rebuild race).
//   - _tick silent-no-ops post-dispose (RAF-after-dispose race).
//   - Per-handler try/catch isolates exceptions so one throwing handler
//     does not freeze the entire tick chain.
//   - Zero per-frame allocations in _tick.

import type { TopologyRenderer } from './TopologyRenderer';

export type AnimationPhase =
  | 'impact'
  | 'physics'
  | 'breathing'
  | 'ambient'
  | 'camera';

export type AnimationHandler = (delta: number) => void;

/** Fixed phase ordering — see brand canon "Animation Tick Ordering". */
const PHASE_ORDER: readonly AnimationPhase[] = [
  'impact',
  'physics',
  'breathing',
  'ambient',
  'camera',
] as const;

export class AnimationCoordinator {
  private _phases: Map<AnimationPhase, AnimationHandler[]>;
  private _lastTime: number;
  private _removeTick: (() => void) | null = null;
  private _disposed = false;

  constructor(renderer: TopologyRenderer) {
    this._phases = new Map(
      PHASE_ORDER.map((p) => [p, [] as AnimationHandler[]]),
    );
    this._lastTime = performance.now();
    this._removeTick = renderer.addAnimationCallback(() => this._tick());
  }

  /**
   * Register a per-frame handler in the named phase. Returns an unsubscribe
   * function. Within a phase, handlers run in REGISTRATION ORDER — for the
   * `impact` phase this order is load-bearing (beam first, then envelope,
   * then flash; see canon "Animation Tick Ordering" + spec §3.3).
   *
   * Strict on unknown phase: throws if `phase` is not in PHASE_ORDER.
   * Lenient on disposed: returns a no-op unsubscribe (handles the
   * cleanup-return-mid-rebuild race per spec §9 decision #8).
   */
  register(phase: AnimationPhase, handler: AnimationHandler): () => void {
    if (this._disposed) {
      // Lenient on disposed (per spec §9 decision #8): cleanup-return-mid-
      // rebuild race can call register after dispose. Throw would crash the
      // unmount sequence; no-op + return no-op unsubscribe keeps things safe.
      return () => {};
    }
    const slot = this._phases.get(phase);
    if (!slot) {
      // Strict on unknown phase: programmer error, not a lifecycle race.
      throw new Error(
        `AnimationCoordinator.register: unknown phase "${phase}"`,
      );
    }
    slot.push(handler);
    return () => {
      const idx = slot.indexOf(handler);
      if (idx >= 0) slot.splice(idx, 1);
    };
  }

  /**
   * Per-frame tick. Iterates the 5 phases in PHASE_ORDER, calling each
   * phase's handlers in registration order. Per-handler try/catch isolates
   * exceptions so one throwing handler does not kill the chain.
   *
   * Mid-tick mutation semantics:
   *   - register mid-tick: new handler's invocation timing is V8-defined
   *     (implementation-detail; do not rely on it).
   *   - dispose mid-tick: _disposed is checked between each handler call;
   *     handlers in subsequent phases (and remaining handlers in the
   *     current phase) do NOT run this tick.
   *
   * Zero per-frame allocation: no `new` inside this body.
   */
  private _tick(): void {
    if (this._disposed) return;
    const now = performance.now();
    const delta = (now - this._lastTime) / 1000;
    this._lastTime = now;
    for (const phase of PHASE_ORDER) {
      const handlers = this._phases.get(phase);
      if (!handlers) continue;
      for (const h of handlers) {
        if (this._disposed) return;
        try {
          h(delta);
        } catch (e) {
          // eslint-disable-next-line no-console
          console.error(
            `[AnimationCoordinator] phase=${phase} handler threw:`,
            e,
          );
        }
      }
    }
  }

  /**
   * Cancel the per-frame tick + clear all handler slots. Idempotent.
   * After dispose:
   *   - further register() calls return a no-op unsubscribe (lenient);
   *   - any RAF-queued _tick is a silent no-op (no exception).
   */
  dispose(): void {
    if (this._disposed) return;
    this._disposed = true;
    this._removeTick?.();
    this._removeTick = null;
    for (const phase of PHASE_ORDER) {
      const handlers = this._phases.get(phase);
      if (handlers) handlers.length = 0;
    }
  }
}
