// frontend/src/lib/components/taxonomy/builders/DustBuilder.ts
//
// The simplest builder: a single `_dustPoints: THREE.Points` ambient
// backdrop (canon F10 — 3000-point galaxy) that is lazy-constructed on
// the first `build()` call and persists across rebuilds via the Sub-
// project A persistence contract (`userData.persistent = true`).
// Subsequent builds are no-ops: the dust is atmospheric, not data-
// driven, and positions + colors are baked once for the builder's
// lifetime.
//
// Migrated from SemanticTopology.svelte:
//   - `_dustPoints` declaration: ~line 482 at HEAD `d79f3dee`
//   - Lazy-init block + T3.1 nearest-anchor color tinting: ~lines
//     1226-1287 (the per-frame drift handler at ~lines 1331-1337 stays
//     in the orchestrator — it's an AnimationCoordinator registration,
//     not scene construction).
//
// Reads from ctx: None. The builder iterates `data.nodes` directly to
//   collect `state === 'domain'` anchors for T3.1 vertex-color tinting
//   (spec §3.3 + rev-2 Mi3). Positions are SceneData-independent
//   (uniform random in [-150, 150]^3).
//
// Writes to ctx: None.
//
// Public accessors (rev-2 plan fix — addresses B2):
//   - dustPoints(): THREE.Points | null — the dust instance after build,
//     null before build + after dispose.
//
// Spec: docs/superpowers/specs/2026-05-17-scene-builder-extraction-design.md

import * as THREE from 'three';
import type { SceneData } from '../TopologyData';
import type { SceneBuilder } from './SceneBuilder';
import type { BuilderContext } from './BuilderContext';

// ── Constants — migrated from SemanticTopology.svelte module scope ──

/** Total dust particle count — canon F10 ambient-backdrop budget. */
const DUST_COUNT = 3000;
/** Half-extent of the uniform-random position cube (positions live in
 *  `[-DUST_HALF_EXTENT, DUST_HALF_EXTENT]^3`). */
const DUST_HALF_EXTENT = 150;
/** Default fallback hue when no `state === 'domain'` anchor is in range
 *  (canon T3.1 — cool blue for unanchored space). */
const DUST_FALLBACK_COLOR = 0x88ccff;
/** Nearest-anchor lerp inner radius — vertices inside this radius keep
 *  the anchor's color verbatim (no fallback tint). */
const DUST_LERP_INNER_RADIUS = 30;
/** Nearest-anchor lerp ramp width — vertices farther than
 *  `DUST_LERP_INNER_RADIUS + DUST_LERP_RAMP` fully fall back to the
 *  default blue. */
const DUST_LERP_RAMP = 50;
/** PointsMaterial render size — canon F10 fine-grained particle look. */
const DUST_POINT_SIZE = 0.15;
/** PointsMaterial resting opacity — additive blend keeps the cloud soft. */
const DUST_OPACITY = 0.25;

/**
 * Builds the 3000-point ambient particle backdrop (canon F10 + T3.1).
 *
 * Lifecycle:
 *   - `_dustPoints` is lazy-constructed on the first `build()` call;
 *     `userData.persistent = true` so the Sub-project A `cleanupScene`
 *     skips it across rebuilds.
 *   - Subsequent `build()` calls are no-ops — the dust is atmospheric
 *     and its positions + colors are stable for the builder's lifetime.
 *   - `dispose()` detaches the points from the scene, disposes the
 *     BufferGeometry + PointsMaterial, and clears the internal handle.
 *     Idempotent.
 */
export class DustBuilder implements SceneBuilder {
  /**
   * The dust THREE.Points instance — lazy-constructed on first build,
   * persists across rebuilds, cleared on dispose. Public accessor:
   * `dustPoints()`.
   */
  private _dustPoints: THREE.Points | null = null;
  private _disposed = false;

  /**
   * Lazy-construct the ambient dust cloud on first call; subsequent calls
   * are no-ops. Positions are uniform random in `[-150, 150]^3`. Vertex
   * colors are computed once via T3.1 nearest-anchor lookup against the
   * `state === 'domain'` nodes in `data.nodes`.
   *
   * @param data  - Topology snapshot. Only `data.nodes` filtered to
   *   structural domains is read; all other fields ignored (spec §3.3 +
   *   rev-2 Mi3 + Q&A #6).
   * @param scene - Target THREE.Scene the dust attaches to on first
   *   build. The persistent flag keeps it attached across rebuilds.
   * @param _ctx  - Shared per-rebuild context (DustBuilder neither reads
   *   from nor writes to ctx — kept in the signature to satisfy the
   *   SceneBuilder interface).
   */
  build(data: SceneData, scene: THREE.Scene, _ctx: BuilderContext): void {
    if (this._disposed) return;
    // Atmospheric backdrop — only constructed once.
    if (this._dustPoints) return;

    const geometry = DustBuilder._buildGeometry(data);
    const material = new THREE.PointsMaterial({
      size: DUST_POINT_SIZE,
      transparent: true,
      opacity: DUST_OPACITY,
      blending: THREE.AdditiveBlending,
      depthWrite: false,
      vertexColors: true,
    });
    const points = new THREE.Points(geometry, material);
    points.userData.isNeuralDust = true;
    points.userData.persistent = true;
    scene.add(points);
    this._dustPoints = points;
  }

  /**
   * Idempotent teardown — detach the dust from its parent + dispose GPU
   * resources. After return:
   *
   *   - `_dustPoints === null`
   *   - dust no longer a child of any scene
   *   - underlying BufferGeometry + PointsMaterial released
   *
   * Subsequent calls no-op (post-state unchanged).
   */
  dispose(): void {
    if (this._disposed) return;
    this._disposed = true;
    if (this._dustPoints) {
      if (this._dustPoints.parent) {
        this._dustPoints.parent.remove(this._dustPoints);
      }
      const geom = this._dustPoints.geometry as THREE.BufferGeometry | undefined;
      if (typeof geom?.dispose === 'function') geom.dispose();
      const mat = this._dustPoints.material as THREE.PointsMaterial | undefined;
      if (typeof mat?.dispose === 'function') mat.dispose();
      this._dustPoints = null;
    }
  }

  /**
   * Public accessor — returns the live dust `THREE.Points` instance, or
   * `null` if the builder has not yet been built or has been disposed.
   * Used by consumers (e.g. the per-frame ambient drift handler in
   * SemanticTopology.svelte) that need to read the live mesh without
   * importing DustBuilder's internal field (spec rev-2 — B2).
   *
   * @returns The active dust Points or `null`.
   */
  dustPoints(): THREE.Points | null {
    return this._dustPoints;
  }

  // ── Internal helpers ──

  /**
   * Build the BufferGeometry for the dust cloud: 3000 random positions
   * in `[-DUST_HALF_EXTENT, DUST_HALF_EXTENT]^3` + per-vertex colors
   * tinted toward each particle's nearest `state === 'domain'` anchor
   * (T3.1). Vertices farther than `DUST_LERP_INNER_RADIUS` from any
   * anchor lerp toward the canonical fallback blue `0x88ccff`.
   *
   * @param data - Topology snapshot. Only `data.nodes` filtered to
   *   structural domains is read.
   * @returns A fresh BufferGeometry with `position` + `color` attributes.
   */
  private static _buildGeometry(data: SceneData): THREE.BufferGeometry {
    const geometry = new THREE.BufferGeometry();
    const positions = new Float32Array(DUST_COUNT * 3);
    const colors = new Float32Array(DUST_COUNT * 3);

    // Pre-collect structural domains for fast nearest-anchor lookups
    // (T3.1 — per spec §3.3 + rev-2 Mi3 clarification).
    const anchors = data.nodes.filter((n) => n.state === 'domain');
    const fallbackColor = new THREE.Color(DUST_FALLBACK_COLOR);
    const tint = new THREE.Color();

    for (let i = 0; i < DUST_COUNT; i++) {
      const baseIndex = i * 3;
      const x = (Math.random() - 0.5) * (DUST_HALF_EXTENT * 2);
      const y = (Math.random() - 0.5) * (DUST_HALF_EXTENT * 2);
      const z = (Math.random() - 0.5) * (DUST_HALF_EXTENT * 2);
      positions[baseIndex] = x;
      positions[baseIndex + 1] = y;
      positions[baseIndex + 2] = z;

      // T3.1 nearest-anchor color lookup + distance-attenuated lerp toward
      // the canonical fallback blue. Returns the source color verbatim
      // when no anchors exist (rev-2 Mi3).
      DustBuilder._computeVertexColor(x, y, z, anchors, fallbackColor, tint);
      colors[baseIndex] = tint.r;
      colors[baseIndex + 1] = tint.g;
      colors[baseIndex + 2] = tint.b;
    }

    geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
    geometry.setAttribute('color', new THREE.BufferAttribute(colors, 3));
    return geometry;
  }

  /**
   * Compute the T3.1 vertex-color tint for a single dust position. Writes
   * the result into `out` (pre-allocated by the caller; reusing a single
   * `THREE.Color` across the 3000-vertex loop avoids 3000 per-build
   * allocations).
   *
   * Algorithm:
   *   1. Find the closest `state === 'domain'` anchor by squared distance.
   *   2. If no anchors exist, copy the fallback color into `out` and return.
   *   3. Otherwise, lerp from the anchor's hue toward the fallback with
   *      `t = clamp((dist - DUST_LERP_INNER_RADIUS) / DUST_LERP_RAMP, 0, 1)`.
   *      Inside the inner radius the anchor color wins; beyond
   *      `INNER + RAMP` the fallback wins; in between, smooth ramp.
   *
   * @param x - Vertex world-space X coordinate.
   * @param y - Vertex world-space Y coordinate.
   * @param z - Vertex world-space Z coordinate.
   * @param anchors - Pre-filtered list of structural domain anchors.
   * @param fallbackColor - The canon T3.1 fallback hue (0x88ccff).
   * @param out - Output color (mutated in place — caller pre-allocates).
   */
  private static _computeVertexColor(
    x: number,
    y: number,
    z: number,
    anchors: readonly { position: readonly [number, number, number] | number[]; color: string }[],
    fallbackColor: THREE.Color,
    out: THREE.Color,
  ): void {
    let nearestDistSq = Infinity;
    let nearestColorStr: string | null = null;
    for (const anchor of anchors) {
      const dx = x - anchor.position[0];
      const dy = y - anchor.position[1];
      const dz = z - anchor.position[2];
      const distSq = dx * dx + dy * dy + dz * dz;
      if (distSq < nearestDistSq) {
        nearestDistSq = distSq;
        nearestColorStr = anchor.color;
      }
    }
    if (nearestColorStr === null) {
      out.copy(fallbackColor);
      return;
    }
    out.set(nearestColorStr);
    const dist = Math.sqrt(nearestDistSq);
    const t = Math.min(Math.max((dist - DUST_LERP_INNER_RADIUS) / DUST_LERP_RAMP, 0), 1);
    out.lerp(fallbackColor, t);
  }
}
