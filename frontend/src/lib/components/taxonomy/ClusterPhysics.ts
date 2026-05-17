// frontend/src/lib/components/taxonomy/ClusterPhysics.ts
//
// Spring physics for cluster scale, edge catenary droop, layout forces.
// Brand reference: .claude/skills/brand-guidelines/references/3d-visualization.md
// "Spring Physics Constants" table — k=120, d=12, dt clamp 0.1, velocityFloor=1e-4.

export interface ClusterPhysicsState {
  baseScale: number;        // INTEGRATED scale (mutated by the spring each frame)
  dataDrivenScale: number;  // CANONICAL scale from TopologyData (set by setBaseScale; cap anchor)
  targetScale: number;
  scaleVelocity: number;
  rippleIntensity: number;
}

const ACCRETION_DELTA = 0.02;
const SPRING_K = 120.0;            // tension
const SPRING_D = 12.0;             // damping
const DT_MAX = 0.1;                // clamp per-frame timestep (seconds)
const VELOCITY_FLOOR = 1e-4;       // below this, snap to target + zero velocity
const RIPPLE_DECAY = 0.92;
const RIPPLE_EPSILON = 0.001;

// Per-cluster targetScale ceiling, as a multiple of the DATA-DRIVEN scale
// (the canonical size from TopologyData, NOT the integrated spring-physics
// baseScale which inflates as the cluster grows). Each beam impact adds
// ACCRETION_DELTA (0.02) to targetScale; without an anchor-relative cap,
// repeated impacts (rapid clicks, optimization events bursting against the
// same domain, seed-batch beams hitting the same cluster) accumulate without
// bound — and a cap relative to `state.baseScale` is moot because baseScale
// grows alongside targetScale and the cap window keeps expanding. The cap
// MUST reference `state.dataDrivenScale` (set by setBaseScale on each
// rebuildScene from TopologyData's canonical size) so the ceiling stays
// fixed regardless of how much accretion has occurred. 2.0× lets a cluster
// grow visibly responsive to interaction while bounding the worst case at
// twice the underlying member-driven scale.
const MAX_ACCRETION_MULTIPLIER = 2.0;

export class ClusterPhysics {
  private _states = new Map<string, ClusterPhysicsState>();

  setBaseScale(nodeId: string, dataDrivenScale: number): void {
    const existing = this._states.get(nodeId);
    if (existing) {
      // Record the canonical scale BEFORE adjusting the integrated baseScale —
      // the cap math below references dataDrivenScale, not the spring-mutated
      // baseScale, so the ceiling stays anchored to TopologyData's view of the
      // cluster regardless of how much accretion the spring has chased.
      existing.dataDrivenScale = dataDrivenScale;
      existing.baseScale = dataDrivenScale;
      const maxTarget = dataDrivenScale * MAX_ACCRETION_MULTIPLIER;
      if (existing.targetScale < dataDrivenScale) {
        existing.targetScale = dataDrivenScale;
      } else if (existing.targetScale > maxTarget) {
        // Clamp any stale-inflated targetScale to the current cap. Catches
        // (a) cap-tuning between releases lowering MAX_ACCRETION_MULTIPLIER,
        // (b) data-driven scale shrinking on rebuild (e.g., cluster lost
        //     members) leaving an old targetScale above the new ceiling,
        // (c) state from the pre-cap-fix era surviving the upgrade in
        //     production (the inflated `Observability Infrastructure` cluster
        //     symptom that prompted this anchor fix).
        existing.targetScale = maxTarget;
      }
    }
  }

  onBeamImpact(nodeId: string, currentScale: number): void {
    let state = this._states.get(nodeId);
    if (!state) {
      state = {
        baseScale: currentScale,
        dataDrivenScale: currentScale,
        targetScale: currentScale,
        scaleVelocity: 0,
        rippleIntensity: 0,
      };
      this._states.set(nodeId, state);
    }
    // Cap accretion at MAX_ACCRETION_MULTIPLIER × DATA-DRIVEN scale (NOT the
    // integrated baseScale). Pre-cap-fix this was `state.targetScale += 0.02`
    // with no upper bound. The first cap-fix attempt (commit 78391862) used
    // `state.baseScale * MAX_ACCRETION_MULTIPLIER` as the ceiling, but
    // baseScale is the SPRING-INTEGRATED value — it grows along with targetScale
    // and the cap window expanded indefinitely. Live symptom recurred (giant
    // pink balloon on selected cluster). Anchoring the cap to `dataDrivenScale`
    // (set by setBaseScale from TopologyData on each rebuildScene) keeps the
    // ceiling fixed at 2× the canonical member-driven size.
    state.targetScale = Math.min(
      state.targetScale + ACCRETION_DELTA,
      state.dataDrivenScale * MAX_ACCRETION_MULTIPLIER,
    );
    state.rippleIntensity = 1.0;

    // T3.3 — Trigger camera micro-shake
    if (typeof window !== 'undefined') {
      window.dispatchEvent(new CustomEvent('beam-impact'));
    }
  }

  update(
    delta: number,
    callback: (nodeId: string, scale: number, ripple: number) => void
  ): void {
    // Clamp dt — semi-implicit Euler instability scales with dt; cap at DT_MAX
    // to bound the per-frame velocity gain regardless of input delta. Real-world
    // long pauses (tab away/back) would otherwise produce a single explosive frame.
    const dt = Math.min(delta, DT_MAX);

    for (const [nodeId, state] of this._states) {
      let active = false;

      const displacement = state.targetScale - state.baseScale;

      // Semi-implicit Euler spring integration: force → velocity → position.
      // Apply damping AFTER force so the velocity at the time-step is the one
      // contributing to position update (more stable than explicit Euler).
      const force = displacement * SPRING_K;
      state.scaleVelocity += force * dt;
      state.scaleVelocity -= state.scaleVelocity * SPRING_D * dt;
      state.baseScale += state.scaleVelocity * dt;

      // Velocity floor snap: when both |velocity| and |displacement| drop below
      // floor, snap exactly to target and zero velocity. Prevents infinite
      // micro-oscillation at rest + makes equality assertions possible.
      if (
        Math.abs(state.scaleVelocity) < VELOCITY_FLOOR &&
        Math.abs(state.targetScale - state.baseScale) < VELOCITY_FLOOR
      ) {
        state.baseScale = state.targetScale;
        state.scaleVelocity = 0;
      } else {
        active = true;
      }

      if (state.rippleIntensity > RIPPLE_EPSILON) {
        state.rippleIntensity *= RIPPLE_DECAY;
        if (state.rippleIntensity <= RIPPLE_EPSILON) {
          state.rippleIntensity = 0;
        }
        active = true;
      }

      if (active) {
        callback(nodeId, state.baseScale, state.rippleIntensity);
      }
    }
  }

  clear(): void {
    this._states.clear();
  }
}
