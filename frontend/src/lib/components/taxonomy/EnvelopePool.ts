// frontend/src/lib/components/taxonomy/EnvelopePool.ts
//
// Plasma-envelopement pool. Mirrors `BeamPool.ts` exactly — pre-allocated
// 10 instances, each with its own ShaderMaterial; envelopes parent to the
// pool's own group (not to target node groups) so a `rebuildScene` cleanup
// of node groups mid-effect cannot crash an active envelope. Per-frame
// world-position copy from target tracks node movement; missing-target
// detection terminates gracefully when the target is removed from the
// scene graph.
//
// * State machine: idle → attack (120ms) → hold (580ms) → decay (680ms) → idle
// * Total active duration: 1380ms. Cubic-ease-out for attack + decay.
//
// Brand: `.claude/skills/brand-guidelines/references/3d-visualization.md`
//        canon F19 "Envelopement Burst" (added in this cycle)
import * as THREE from 'three';
import {
  ENVELOPE_VERTEX_SHADER,
  ENVELOPE_FRAGMENT_SHADER,
  createEnvelopeUniforms,
} from './EnvelopeShader';

const POOL_SIZE = 10;

/**
 * Phase durations in milliseconds. Total lifecycle = 1380ms.
 * Synchronizes with the beam sustain window so the envelope finishes
 * dissipating around the time the beam's terminate phase begins.
 *
 * The 120ms attack (was 220ms) tightens the landing — the node responds
 * snappily at beam impact and reaches full engulfment faster. HOLD_MS now
 * spans 580ms (was 180ms) to cover the full 800ms beam sustain window so the
 * node remains engulfed the entire time the beam is connected. DECAY_MS extends
 * to 680ms (was 580ms) so the plasma skin dissolves alongside the beam's own
 * terminate phase (250ms) rather than vanishing abruptly beforehand.
 */
export const ATTACK_MS = 120;
export const HOLD_MS = 580;
export const DECAY_MS = 680;

/**
 * Peak envelope swell, multiplied with `baseScale` (the node fill size) to
 * compute the maximum plasma-skin radius. 1.28 is clearly visible at impact
 * — the engulfment reads as a decisive burst of plasma surrounding the node,
 * consistent with its status as the culmination of the beam journey.
 */
export const PEAK_SWELL = 1.28;

export type NodeShape = 'cluster' | 'domain';
type EnvelopeState = 'idle' | 'attack' | 'hold' | 'decay';

interface EnvelopeInstance {
  mesh: THREE.Mesh;
  material: THREE.ShaderMaterial;
  state: EnvelopeState;
  stateTime: number;
  baseScale: number;
  color: THREE.Color;
  targetGroup: THREE.Object3D | null;
  // Captured at acquire time — `true` if the target was a child of some
  // parent group when the envelope was acquired. The missing-target
  // detection in `update()` only auto-terminates if the target WAS
  // attached at acquire AND has since lost its parent. Orphan targets
  // (parent-less from the start) keep the envelope alive — supports
  // tests that exercise the state machine with bare Object3D fixtures
  // and accommodates any caller that builds targets outside the scene
  // graph briefly.
  targetWasAttached: boolean;
}

export class EnvelopePool {
  readonly group: THREE.Group;
  private _envelopes: EnvelopeInstance[] = [];
  // Pool-instance-owned shared geometries — same parameters as the node fill
  // geometries in `SemanticTopology.svelte:749-768` so the envelope shape
  // matches the node fill it wraps.
  private _clusterGeo: THREE.IcosahedronGeometry;
  private _domainGeo: THREE.DodecahedronGeometry;
  private _scratchPos = new THREE.Vector3();
  private _disposed = false;

  constructor() {
    this.group = new THREE.Group();
    this.group.name = 'envelope-pool';

    this._clusterGeo = new THREE.IcosahedronGeometry(1, 2);
    this._domainGeo = new THREE.DodecahedronGeometry(1, 2);

    for (let i = 0; i < POOL_SIZE; i++) {
      const material = new THREE.ShaderMaterial({
        uniforms: createEnvelopeUniforms(),
        vertexShader: ENVELOPE_VERTEX_SHADER,
        fragmentShader: ENVELOPE_FRAGMENT_SHADER,
        transparent: true,
        blending: THREE.AdditiveBlending,
        depthWrite: false,
        side: THREE.FrontSide,
      });
      // Default geometry; swapped on each acquire to match node shape.
      const mesh = new THREE.Mesh(this._clusterGeo, material);
      mesh.visible = false;
      mesh.frustumCulled = false;
      this.group.add(mesh);
      this._envelopes.push({
        mesh,
        material,
        state: 'idle',
        stateTime: 0,
        baseScale: 1.0,
        color: new THREE.Color(),
        targetGroup: null,
        targetWasAttached: false,
      });
    }
  }

  /**
   * Fire a plasma envelope around `targetGroup`'s world position. Returns
   * `true` on success, `false` when the pool is exhausted (all 10 envelopes
   * active). Re-acquiring the same `targetGroup` while a prior envelope is
   * still active reuses that instance and resets its state to `attack` —
   * prevents double-stacked envelopes on rapid re-clicks of the same node.
   */
  acquire(
    targetGroup: THREE.Object3D,
    baseScale: number,
    shape: NodeShape,
    color: THREE.Color,
  ): boolean {
    // Re-acquire on already-active target: reuse the instance.
    const existing = this._envelopes.find(
      (e) => e.state !== 'idle' && e.targetGroup === targetGroup,
    );
    const env = existing ?? this._envelopes.find((e) => e.state === 'idle');
    if (!env) return false;

    env.state = 'attack';
    env.stateTime = 0;
    env.baseScale = baseScale;
    env.color.copy(color);
    env.targetGroup = targetGroup;
    env.targetWasAttached = !!targetGroup.parent;

    env.mesh.geometry =
      shape === 'domain' ? this._domainGeo : this._clusterGeo;

    env.material.uniforms.uColorStart.value.copy(color);
    env.material.uniforms.uColorEnd.value.copy(color);
    env.material.uniforms.uOpacity.value = 0;
    env.material.uniforms.uTime.value = 0;
    env.material.uniforms.uFlowSpeed.value = 2.0;

    env.mesh.visible = true;
    env.mesh.scale.setScalar(baseScale);

    // Initial position copy from target so the envelope materializes at
    // the correct world location even if the next render runs before
    // `update()` ticks.
    targetGroup.getWorldPosition(this._scratchPos);
    env.mesh.position.copy(this._scratchPos);

    return true;
  }

  /**
   * Per-frame state-machine advance. Call from the renderer's animation
   * callback. Idle envelopes are skipped (cheap no-op).
   *
   * Phase transitions are handled BEFORE visuals are applied so that a
   * single delta spanning a boundary (e.g., a 32ms frame that crosses the
   * attack→hold edge) renders the correct phase visual on that same frame.
   * Leftover stateTime carries forward via subtraction so timing is
   * conserved across boundaries.
   */
  update(delta: number): void {
    if (this._disposed) return;
    const deltaMs = delta * 1000;
    for (const env of this._envelopes) {
      if (env.state === 'idle') continue;

      env.material.uniforms.uTime.value += delta;

      // Missing-target detection: if the target group WAS attached to a
      // parent at acquire time and has since lost it, treat this as the
      // node being removed mid-effect (e.g., rebuildScene disposed its
      // cluster group) and terminate gracefully. Targets that were never
      // attached are tolerated — orphan targets keep the envelope alive
      // so the state machine remains testable and a temporarily-detached
      // target during component setup doesn't kill an active envelope.
      if (!env.targetGroup) {
        this._reset(env);
        continue;
      }
      if (env.targetWasAttached && !env.targetGroup.parent) {
        this._reset(env);
        continue;
      }
      env.targetGroup.getWorldPosition(this._scratchPos);
      env.mesh.position.copy(this._scratchPos);

      // Advance state through any phase boundaries crossed by this delta.
      // Bounded iteration (4 max) for safety — even a 1-second delta
      // can only cross 3 boundaries (attack→hold→decay→idle).
      env.stateTime += deltaMs;
      for (let i = 0; i < 4; i++) {
        const dur = phaseDuration(env.state);
        if (env.state === 'idle' || env.stateTime < dur) break;
        env.stateTime -= dur;
        env.state = nextPhase(env.state);
      }

      if (env.state === 'idle') {
        this._reset(env);
        continue;
      }

      // Apply visuals based on the current (post-advance) phase.
      switch (env.state) {
        case 'attack': {
          const t = env.stateTime / ATTACK_MS;
          const ease = easeOutCubic(t);
          env.material.uniforms.uOpacity.value = ease;
          env.mesh.scale.setScalar(
            env.baseScale * (1 + (PEAK_SWELL - 1) * ease),
          );
          break;
        }
        case 'hold': {
          env.material.uniforms.uOpacity.value = 1.0;
          env.mesh.scale.setScalar(env.baseScale * PEAK_SWELL);
          break;
        }
        case 'decay': {
          const t = env.stateTime / DECAY_MS;
          const ease = easeOutCubic(t);
          env.material.uniforms.uOpacity.value = 1 - ease;
          env.mesh.scale.setScalar(
            env.baseScale * (PEAK_SWELL - (PEAK_SWELL - 1) * ease),
          );
          break;
        }
      }
    }
  }

  private _reset(env: EnvelopeInstance): void {
    env.state = 'idle';
    env.stateTime = 0;
    env.targetGroup = null;
    env.targetWasAttached = false;
    env.mesh.visible = false;
    env.mesh.scale.setScalar(1);
    env.material.uniforms.uOpacity.value = 0;
    env.material.uniforms.uTime.value = 0;
  }

  /**
   * Release every GPU resource owned by the pool — per-instance ShaderMaterial
   * (10), shared cluster + domain geometries. Idempotent: callable twice
   * without error. After dispose, `update()` is a no-op so a still-pending
   * animation callback won't crash on disposed materials.
   */
  dispose(): void {
    if (this._disposed) return;
    this._disposed = true;
    for (const env of this._envelopes) {
      env.material.dispose();
      env.targetGroup = null;
    }
    this._clusterGeo.dispose();
    this._domainGeo.dispose();
    this._envelopes = [];
  }
}

function easeOutCubic(t: number): number {
  return 1 - Math.pow(1 - t, 3);
}

function phaseDuration(state: EnvelopeState): number {
  switch (state) {
    case 'attack':
      return ATTACK_MS;
    case 'hold':
      return HOLD_MS;
    case 'decay':
      return DECAY_MS;
    case 'idle':
      return Infinity;
  }
}

function nextPhase(state: EnvelopeState): EnvelopeState {
  switch (state) {
    case 'attack':
      return 'hold';
    case 'hold':
      return 'decay';
    case 'decay':
      return 'idle';
    case 'idle':
      return 'idle';
  }
}
