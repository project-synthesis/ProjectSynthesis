/**
 * ClusterPhysics — spring physics integration tests.
 *
 * Spec: docs/superpowers/specs/2026-05-09-pattern-graph-depth-design.md § 4.7
 * Brand: .claude/skills/brand-guidelines/references/3d-visualization.md "Spring Physics Constants"
 *
 * Required behavior (replacing main's linear lerp):
 *   - Semi-implicit Euler with k=120, d=12, dt clamp 0.1s
 *   - velocityFloor=1e-4 snap-to-target when both |velocity| and |displacement| drop below floor
 *   - No NaN/Infinity from any positive delta input
 *
 * These tests should FAIL against main's `Math.pow(0.01, delta / 0.5)` linear lerp
 * (no velocity, no overshoot, asymptotic-not-exact) and PASS once spring physics + snap land.
 */
import { describe, expect, test } from 'vitest';
import { ClusterPhysics } from './ClusterPhysics';

describe('ClusterPhysics — spring physics', () => {
  test('records non-monotonic trajectory (overshoot indicates spring, not linear lerp)', () => {
    const physics = new ClusterPhysics();
    physics.onBeamImpact('a', 1.0);

    const trajectory: number[] = [];
    for (let i = 0; i < 60; i++) {
      physics.update(0.016, (_id, scale) => trajectory.push(scale));
    }

    // Linear lerp produces monotonically increasing trajectory toward target=1.02.
    // Spring (k=120, d=12) is underdamped (ζ ≈ 0.55) and overshoots before settling.
    // Test: peak strictly greater than the target (1.02) — only possible with overshoot.
    const peak = Math.max(...trajectory);
    expect(peak).toBeGreaterThan(1.02);
  });

  test('snaps to exact target when velocity and displacement drop below velocityFloor', () => {
    const physics = new ClusterPhysics();
    physics.onBeamImpact('a', 1.0);

    let finalScale = -Infinity;
    // Settle for plenty of time — after 200 frames at 60fps (~3.3s), spring is done.
    for (let i = 0; i < 200; i++) {
      physics.update(0.016, (_id, scale) => {
        finalScale = scale;
      });
    }

    // With velocityFloor snap, final scale is EXACTLY targetScale (1.02), not asymptotic.
    // Linear lerp would give 1.0199999... never exactly 1.02.
    // Spring without snap would oscillate forever at sub-floor amplitude.
    expect(finalScale).toBe(1.02);
  });

  test('clamps large delta — no NaN, no Infinity, no runaway scale', () => {
    const physics = new ClusterPhysics();
    physics.onBeamImpact('a', 1.0);

    let observedScale = 0;
    physics.update(10.0, (_id, scale) => {
      observedScale = scale;
    });

    // Without dt clamp, semi-implicit Euler with delta=10 explodes (velocity → enormous).
    // Linear lerp with delta=10 saturates at target. Spring with clamp stays bounded.
    expect(Number.isFinite(observedScale)).toBe(true);
    expect(Math.abs(observedScale)).toBeLessThan(10);
  });
});
