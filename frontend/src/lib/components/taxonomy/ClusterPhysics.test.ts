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

  test('caps targetScale at MAX_ACCRETION_MULTIPLIER × baseScale (balloon-growth regression)', () => {
    // Pre-fix bug: onBeamImpact did `state.targetScale += 0.02;` with no
    // upper bound. Repeated impacts (rapid clicks, optimization events
    // bursting at the same domain, seed-batch beams hitting the same
    // cluster) accumulated without limit and ballooned the cluster mesh
    // to many times its data-driven size — visible in the live Pattern
    // Graph as a giant balloon on the selected node.
    const physics = new ClusterPhysics();
    const baseScale = 1.0;

    // 200 beam impacts on the same cluster — would push targetScale to
    // baseScale + 200 × 0.02 = 5.0 without the cap. With cap at 2.0×
    // baseScale, targetScale stops at 2.0.
    for (let i = 0; i < 200; i++) physics.onBeamImpact('a', baseScale);

    let finalScale = -Infinity;
    for (let i = 0; i < 300; i++) {
      physics.update(0.016, (_id, scale) => {
        finalScale = scale;
      });
    }

    // Cap = 2.0 × baseScale. At this displacement the spring's continuous
    // force keeps velocity slightly above velocity-floor (1e-4) so the
    // exact-snap branch doesn't fire as it does for small displacements;
    // the system settles asymptotically to within ~1e-4 of target. The
    // bug-regression contract is "scale never balloons past the cap",
    // satisfied by toBeCloseTo(2.0, 3).
    expect(finalScale).toBeCloseTo(2.0, 3);
    expect(finalScale).toBeLessThanOrEqual(2.0);
  });

  test('setBaseScale clamps stale-inflated targetScale to cap (defensive against rebuild + cap-tuning)', () => {
    // Once accretion has pushed targetScale up, prior code left it inflated
    // across rebuildScene calls because `setBaseScale` only raised, never
    // lowered, targetScale. Fix: clamp to MAX_ACCRETION_MULTIPLIER × the
    // new baseScale so stale state can't survive a baseScale change.
    const physics = new ClusterPhysics();

    // Pump targetScale up at baseScale=1.0 → cap = 2.0.
    for (let i = 0; i < 200; i++) physics.onBeamImpact('a', 1.0);
    // Settle so internal state stabilizes.
    for (let i = 0; i < 300; i++) physics.update(0.016, () => {});

    // Now rebuildScene shrinks baseScale to 0.5 (e.g., cluster lost members).
    // New cap should be 0.5 × 2.0 = 1.0. targetScale was 2.0, must clamp down.
    physics.setBaseScale('a', 0.5);

    let finalScale = -Infinity;
    for (let i = 0; i < 300; i++) {
      physics.update(0.016, (_id, scale) => {
        finalScale = scale;
      });
    }

    // New cap = 0.5 × 2.0 = 1.0; same asymptotic-vs-snap caveat as the
    // accretion-cap test above. Asymptote within 1e-4 of 1.0, never above.
    expect(finalScale).toBeCloseTo(1.0, 3);
    expect(finalScale).toBeLessThanOrEqual(1.0);
  });

  test('cap anchors to data-driven scale, not integrated baseScale (regression: spring-grown baseScale must not expand the cap window)', () => {
    // Pre-fix bug: the first cap attempt (commit 78391862) used
    // `state.baseScale * MAX_ACCRETION_MULTIPLIER` as the cap. But baseScale
    // is the SPRING-INTEGRATED value — it grows along with targetScale as
    // the spring chases the moving target. Result: each impact's cap window
    // expanded to roughly 2× the current (already-inflated) baseScale, so
    // the cap NEVER bit for typical interaction (you'd need >50 impacts in
    // a single 16ms frame to trip it). Live symptom: cluster ballooned to
    // many times its data-driven size despite the cap.
    //
    // Fix: anchor cap to `state.dataDrivenScale` (set by setBaseScale from
    // TopologyData's canonical size). This test pins that anchor: between
    // impacts, the spring grows baseScale → up to targetScale; the cap must
    // STILL reference dataDrivenScale and not let targetScale climb past
    // dataDrivenScale × MAX_ACCRETION_MULTIPLIER.
    const physics = new ClusterPhysics();
    const dataDriven = 2.5;

    // First impact creates the state; subsequent setBaseScale records the
    // canonical dataDrivenScale. (In production this happens in the other
    // order — setBaseScale runs in rebuildScene before any clicks — but the
    // contract must hold either way.)
    physics.onBeamImpact('a', dataDriven);
    physics.setBaseScale('a', dataDriven);

    // Interleave many impacts with settle-frames so the spring fully
    // integrates each impact's target before the next impact lands. This
    // walks baseScale toward whatever targetScale is — under the pre-fix
    // bug, that would make the cap window expand each iteration.
    // Track the MAX scale observed across the entire run; under the pre-fix
    // bug this would climb past 5.0 indefinitely (the live symptom showed
    // ~20+ scene units against a 2.5 data-driven scale).
    let maxScaleObserved = -Infinity;
    const observer = (_id: string, scale: number) => {
      if (scale > maxScaleObserved) maxScaleObserved = scale;
    };
    for (let burst = 0; burst < 100; burst++) {
      physics.onBeamImpact('a', dataDriven);
      for (let i = 0; i < 100; i++) physics.update(0.016, observer);
    }
    // Final settle so the spring snaps to target.
    for (let i = 0; i < 300; i++) physics.update(0.016, observer);

    // Cap = dataDriven × 2.0 = 5.0. With the cap correctly anchored to
    // dataDrivenScale (not the inflating integrated baseScale), the max
    // observed scale stays bounded near 5.0. Spring overshoot can briefly
    // exceed targetScale by ~13% but the cap holds the asymptote.
    // Under the pre-fix bug, this test would have observed scale > 50+
    // (linear accretion past 100 bursts × 0.02 = 2.0 per burst eventually
    // grew baseScale unboundedly because cap = baseScale × 2). So the
    // negative-space assertion (scale << 50) is the load-bearing one;
    // the lower bound just confirms accretion happens.
    expect(maxScaleObserved).toBeLessThan(6.0);
    expect(maxScaleObserved).toBeGreaterThan(dataDriven); // accretion happened
  });

  test('successive impacts pump scale up until cap then plateau (responsive feedback preserved under cap)', () => {
    // Sanity: single impacts still feel responsive. The cap should only
    // bite at the tail of many impacts, not on the first few.
    const physics = new ClusterPhysics();
    const peaks: number[] = [];
    const baseScale = 1.0;

    for (let burst = 0; burst < 5; burst++) {
      physics.onBeamImpact('a', baseScale);
      let peak = -Infinity;
      for (let i = 0; i < 60; i++) {
        physics.update(0.016, (_id, scale) => {
          if (scale > peak) peak = scale;
        });
      }
      peaks.push(peak);
    }

    // First impacts produce monotonically growing peaks (responsive).
    expect(peaks[1]).toBeGreaterThan(peaks[0]);
    expect(peaks[2]).toBeGreaterThan(peaks[1]);
    // All peaks remain bounded by the cap (1.0 × 2.0 = 2.0) — overshoot
    // can briefly exceed targetScale, but never explodes past, e.g., 3.0.
    for (const p of peaks) expect(p).toBeLessThan(3.0);
  });
});
