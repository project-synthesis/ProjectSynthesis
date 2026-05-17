// frontend/src/lib/components/taxonomy/AnimationCoordinator.ts
//
// Per-frame animation pipeline for the Pattern Graph 3D scene.
// Owns the single renderer.addAnimationCallback registration; dispatches
// to phase-specific handlers in fixed PHASE_ORDER (impact → physics →
// breathing → ambient → camera).
//
// Spec: docs/superpowers/specs/2026-05-17-animation-coordinator-design.md
// Brand canon: .claude/skills/brand-guidelines/references/3d-visualization.md
//   "Animation Tick Ordering" section (Sub-project B inserts this).

import type { TopologyRenderer } from './TopologyRenderer';

export type AnimationPhase =
  | 'impact'
  | 'physics'
  | 'breathing'
  | 'ambient'
  | 'camera';

export type AnimationHandler = (delta: number) => void;

const PHASE_ORDER: readonly AnimationPhase[] = [
  'impact',
  'physics',
  'breathing',
  'ambient',
  'camera',
] as const;

export class AnimationCoordinator {
  constructor(renderer: TopologyRenderer) {
    // Functional-but-incorrect stub: registers one no-op callback so test #1
    // fails on call-count comparison shape, not on construction exception.
    renderer.addAnimationCallback(() => {});
  }

  register(_phase: AnimationPhase, _handler: AnimationHandler): () => void {
    return () => {};
  }

  dispose(): void {}
}
