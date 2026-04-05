# Data-as-Matter: Plasma Beam System for Pattern Graph HUD

**Date:** 2026-04-05
**Status:** Approved
**Scope:** Plasma beam particle system + cluster physics for the 3D taxonomy visualization

---

## Summary

Add a plasma beam system to the Pattern Graph's diegetic HUD. When prompts are optimized, seeded, or the view initializes, sustained energy beams stream from the camera viewport into target cluster nodes. Clusters physically react — growing on accretion and rippling on impact. The system reinforces the "Data-as-Matter" aesthetic: data has physical weight, visible trajectory, and tangible effect on the structures it joins.

## Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Trigger events | Navigate + optimize + seed (all three) | Layered visual vocabulary per event type |
| Emitter placement | Camera-attached, screen-space | Dead Space-authentic diegetic UI; always visible regardless of camera angle |
| Emitter visibility | Invisible barrel | No persistent chrome; beams originate from viewport bottom-center edge |
| Beam style | Plasma stream (sustained catenary) | Continuous energy feel, thickness encodes throughput |
| Cluster impact | Accretion growth + resonance ripple | Physicalized matter — clusters grow and react to incoming data |
| Implementation | ShaderMaterial tubes | Most GPU-efficient for sustained beams; single draw call per beam |

## Architecture

### New Files

All under `frontend/src/lib/components/taxonomy/`:

| File | Purpose | ~LOC |
|------|---------|------|
| `PlasmaBeam.ts` | Beam geometry, catenary curve, state machine, lifecycle | ~250 |
| `BeamPool.ts` | Object pool (10 pre-allocated), acquire/release, batch orchestration | ~120 |
| `BeamShader.ts` | GLSL vertex + fragment source, uniform type definitions | ~80 |
| `ClusterPhysics.ts` | Per-node accretion growth + resonance ripple state management | ~100 |

### Modified Files

| File | Change |
|------|--------|
| `SemanticTopology.svelte` | Wire beam pool into scene, subscribe to SSE events, trigger beams on navigate/optimize/seed |
| `TopologyRenderer.ts` | Add `BeamPool.update()` + `ClusterPhysics.update()` to render loop, expose screen-to-world projection utility |
| `TopologyData.ts` | Expose node position lookup for beam targeting |

### Untouched Files

TopologyControls, TopologyInfoPanel, ActivityPanel, TopologyWorker, TopologyLabels, TopologyInteraction — the beam system is fully additive.

## Beam Rendering

### Geometry

Each beam is a `TubeGeometry` built along a `QuadraticBezierCurve3`:

- **Origin:** NDC `(0.0, -0.9, 0.1)` unprojected to world-space each frame (one shared unproject call)
- **Target:** Cluster node world position via `Object3D.getWorldPosition()`
- **Control point:** Midpoint between origin and target, sagged downward by `distance * 0.15` (catenary droop — energy has weight)

Tube specs:

| Parameter | Value |
|-----------|-------|
| Radial segments | 4 (octagonal cross-section) |
| Tubular segments | 12 |
| Base radius (single optimize) | 0.04 |
| Base radius (batch seed) | 0.12 |
| Radius pulse on absorb | ±20% |

### Shader

One `ShaderMaterial` instance per beam (10 total). All share the same GLSL source from `BeamShader.ts` but each has independent uniform values for color, opacity, and flow state.

**Uniforms:**

| Uniform | Type | Purpose |
|---------|------|---------|
| `uTime` | float | Drives scroll animation |
| `uColorStart` | vec3 | Neon cyan `#00e5ff` (emitter end) |
| `uColorEnd` | vec3 | Target domain color |
| `uOpacity` | float | 0→1 on fire, 1→0 on terminate |
| `uFlowSpeed` | float | UV scroll rate, default `2.0` |
| `uThickness` | float | Alpha modulation across radial UVs |

**Fragment logic:**

1. Mix `uColorStart` → `uColorEnd` along `vUv.x` (tube length)
2. Scroll `vUv.x` by `uTime * uFlowSpeed` through `sin()` energy pulse pattern — bright nodes flow along beam
3. Radial falloff: `smoothstep` from tube center to edge — bright core, dim shell
4. Multiply by `uOpacity` for lifecycle fade

**Blend mode:** `THREE.AdditiveBlending` — overlapping beams intensify rather than occlude. No transparency sorting needed.

**No glow, no bloom, no post-processing.** Brand-compliant: the plasma feel comes from motion (scrolling energy pattern) and color gradient, not effects.

## Beam Pool & Lifecycle

### Pool

- 10 `PlasmaBeam` instances pre-allocated at scene init, all `visible = false`
- `acquire(target, config) → PlasmaBeam | null` — first inactive beam, null if exhausted
- `release(beam)` — reset uniforms, hide, return to pool
- Pool added as `THREE.Group` child of the scene

### State Machine

```
IDLE → FIRING → SUSTAIN → TERMINATE → IDLE
```

| State | Duration | Behavior |
|-------|----------|----------|
| IDLE | — | `visible = false`, in pool |
| FIRING | 300ms | `uOpacity` 0→1, radius ramps from 0 to base, tube geometry built along catenary |
| SUSTAIN | Event-dependent | Scrolling energy, radius pulses on each prompt absorbed |
| TERMINATE | 400ms | `uOpacity` 1→0, radius contracts to 0, on complete → release to pool |

### Trigger Mapping

| Event | Source | Behavior |
|-------|--------|----------|
| Navigate (view enter) | `onMount` | One beam per existing cluster, staggered 30ms apart, sustain 200ms each, thin radius. Cap at 20 beams (largest clusters first). Materialization burst. |
| Single optimize | SSE `optimization_created` | One beam to assigned cluster. 300ms fire, 800ms sustain, 400ms terminate. |
| Batch seed | SSE `seed_batch_progress` | One beam per unique target cluster. Fire on first prompt to that cluster, sustain while more flow, terminate on batch complete. Radius pulses per absorbed prompt. |

## Cluster Physics

### Accretion Growth (permanent)

Each node tracks `baseScale` from the existing `log₂(memberCount)` formula.

- On beam delivery: `targetScale += 0.02` per prompt absorbed
- Scale lerps to target over 500ms (`easeOutQuart`)
- On next `taxonomy_changed` SSE: `baseScale` recalculates from actual `memberCount` — growth was speculative, data refresh makes it authoritative
- If data refresh results in smaller scale (prompt reassigned elsewhere), snap to data-driven value, no animation

Cluster visually grows immediately on beam impact, before backend propagation. Self-correcting.

### Resonance Ripple (transient)

The cluster wireframe mesh's material becomes a `ShaderMaterial` that replicates `MeshBasicMaterial` behavior (color, opacity, wireframe) plus a `uRipple` displacement uniform:

- On impact: `uRipple = 1.0`
- Decay: `uRipple *= 0.92` per frame (~1 second visible duration)
- Vertex shader: push each vertex outward along its normal by `uRipple * 0.15` (relative to geometry radius)
- Only the wireframe distorts — the fill mesh (dark interior) stays stable

### Per-Node State

```typescript
interface ClusterPhysicsState {
  baseScale: number;       // data-driven scale
  targetScale: number;     // lerp target (base + accretion deltas)
  scaleVelocity: number;   // smooth interpolation
  rippleIntensity: number; // 0.0–1.0, exponential decay
}
```

`Map<string, ClusterPhysicsState>` keyed by node ID. Entries created on first beam impact, never deleted (4 floats per node). `ClusterPhysics.update(delta)` iterates only nodes with `ripple > 0.001` or `scale !== target`.

## Screen-to-World Projection

**Origin:** NDC `(0.0, -0.9, 0.1)` — bottom-center, just inside near plane.

Per frame:
1. `_beamOrigin.set(0, -0.9, 0.1).unproject(camera)` — one call, shared
2. Per beam: `target.getWorldPosition(_targetPos)`
3. Control point: `midpoint + (0, -sag, 0)` where `sag = distance * 0.15`

**Curve rebuild throttling:**
- Cache last camera quaternion + target position per beam
- Rebuild only when: `quaternion.angleTo(lastQuat) > 0.035` (~2°) OR `target.distanceTo(lastTarget) > 0.5`
- FIRING/TERMINATE: rebuild every frame (short duration, accuracy matters)
- SUSTAIN: rebuild every 3rd frame

## Performance Budget

Target: 60fps (16.6ms frame budget).

| Operation | Cost/frame | Notes |
|-----------|-----------|-------|
| 1 unproject | ~0.01ms | One vec3 matrix multiply |
| 10 beam uniform updates | ~0.05ms | Typed array writes |
| 2-3 curve rebuilds (throttled) | ~0.3ms | Bezier eval + buffer upload |
| 10 beam draw calls | ~0.5ms | Simple tube geometry, additive blend |
| ClusterPhysics (15 nodes) | ~0.02ms | 4 float updates per node |
| **Total** | **~0.9ms** | **5.4% of frame budget** |

Batch seed worst case (50 prompts, 10 clusters): ~1.2ms (~7.2% of budget).

**Memory:** Zero runtime allocation. All geometry buffers and curve arrays pre-allocated as `Float32Array`. Uniform updates are typed array writes.

**Disposal:** `BeamPool.dispose()` on topology unmount — calls `geometry.dispose()` + `material.dispose()` on all 10 beams.

## Brand Compliance

- No `box-shadow`, `text-shadow`, `filter: drop-shadow()`, or glow effects of any kind
- No `radial-gradient` for ambient effects
- No bloom or post-processing passes
- Beam visual comes from: solid geometry + additive blending + UV scroll animation
- Colors from the defined neon palette + domain color system
- Terminology: "emission" and "contour", never "glow"
