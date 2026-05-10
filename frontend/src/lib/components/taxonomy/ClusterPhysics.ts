// frontend/src/lib/components/taxonomy/ClusterPhysics.ts
//
// Spring physics for cluster scale, edge catenary droop, layout forces.
// Brand reference: .claude/skills/brand-guidelines/references/3d-visualization.md
// "Spring Physics Constants" table — k=120, d=12, dt clamp 0.1, velocityFloor=1e-4.

export interface ClusterPhysicsState {
  baseScale: number;
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

export class ClusterPhysics {
  private _states = new Map<string, ClusterPhysicsState>();

  setBaseScale(nodeId: string, baseScale: number): void {
    const existing = this._states.get(nodeId);
    if (existing) {
      existing.baseScale = baseScale;
      if (existing.targetScale < baseScale) {
        existing.targetScale = baseScale;
      }
    }
  }

  onBeamImpact(nodeId: string, currentScale: number): void {
    let state = this._states.get(nodeId);
    if (!state) {
      state = {
        baseScale: currentScale,
        targetScale: currentScale,
        scaleVelocity: 0,
        rippleIntensity: 0,
      };
      this._states.set(nodeId, state);
    }
    state.targetScale += ACCRETION_DELTA;
    state.rippleIntensity = 1.0;
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
