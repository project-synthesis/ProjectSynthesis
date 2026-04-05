// frontend/src/lib/components/taxonomy/ClusterPhysics.ts

export interface ClusterPhysicsState {
  baseScale: number;
  targetScale: number;
  rippleIntensity: number;
}

const ACCRETION_DELTA = 0.02;
const SCALE_LERP_MS = 500;
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
    for (const [nodeId, state] of this._states) {
      let active = false;

      const scaleDiff = state.targetScale - state.baseScale;
      if (Math.abs(scaleDiff) > 0.001) {
        const t = 1 - Math.pow(0.01, delta / (SCALE_LERP_MS / 1000));
        state.baseScale += scaleDiff * t;
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

  isActive(nodeId: string): boolean {
    const state = this._states.get(nodeId);
    if (!state) return false;
    return state.rippleIntensity > RIPPLE_EPSILON ||
      Math.abs(state.targetScale - state.baseScale) > 0.001;
  }
}
