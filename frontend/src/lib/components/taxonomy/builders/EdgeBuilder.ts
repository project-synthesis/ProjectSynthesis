// frontend/src/lib/components/taxonomy/builders/EdgeBuilder.ts
//
// Three edge classes: hierarchical (catenary curves) + similarity + injection.
// Each class lives in its own ephemeral sub-group so cleanupScene + LOD
// toggling can target each class independently.
//
// Migrated from SemanticTopology.svelte:
//   - Hierarchical bucketing + curve construction: ~lines 1480-1537
//   - Similarity branch: ~lines 1540-1559 (clustersStore.showSimilarityEdges
//     gate + opacity resolver)
//   - Injection branch: ~lines 1561-1580
//   - buildCurvePositions helper: ~lines 885-936
//   - buildMergedCurveGeometry helper: ~lines 854-880
//
// Writes to ctx:
//   - edgeUniforms.push(...) per hierarchical parent bucket (F5 uTime pulse)
//
// Reads from ctx:
//   - sceneNodeMap (endpoint lookups for all 3 edge classes)
//   - nodeMeshes (not currently used — positions come from sceneNodeMap
//     because edges build from SceneData.position, not mesh.position)
//
// Persistent parent: none. All 3 sub-groups (hierarchicalGroup,
// similarityEdgeGroup, injectionEdgeGroup) are EPHEMERAL — rebuilt every
// cycle alongside their child line meshes. Setting userData.persistent
// would orphan the lines on rebuild (spec §3.5 + Q&A #4).
//
// Spec: docs/superpowers/specs/2026-05-17-scene-builder-extraction-design.md

import * as THREE from 'three';
import type { SceneData, SceneNode, SceneEdge } from '../TopologyData';
import { computeHierarchicalOpacity } from '../TopologyData';
import type { SceneBuilder } from './SceneBuilder';
import type { BuilderContext } from './BuilderContext';
import {
  EDGE_DEPTH_VERTEX,
  EDGE_DEPTH_FRAGMENT,
  createEdgeDepthUniforms,
} from '../EdgeShader';

// ── Constants — migrated from SemanticTopology.svelte module scope ──

/** Segments per bezier curve for hierarchical edges. */
const CURVE_SEGMENTS = 12;
/** Hierarchical-edge proximity filter — suppress catenaries whose endpoints
 *  are nearly overlapping (would render as a degenerate squiggle). */
const EDGE_PROXIMITY_THRESHOLD = 5.0;
/** Fallback hierarchical-edge color when parent node lacks a color string. */
const EDGE_COLOR = 0x6699ff;
/** Similarity-edge color — magenta dashed line. */
const SIMILARITY_EDGE_COLOR = 0xff66cc;
/** Injection-edge color — warm gold/amber solid line. */
const INJECTION_EDGE_COLOR = 0xff9500;

/** Endpoint pair for hierarchical edge curve building.
 *  T2.3 extends with optional memberCount for per-vertex alpha encoding. */
interface HierEdge {
  from: [number, number, number];
  to: [number, number, number];
  memberCount?: number;
}

interface EdgeGroupOptions {
  type: 'similarity' | 'injection';
  color: number;
  dashed: boolean;
  tag: string;
  opacityFn: (edge: SceneEdge) => number;
}

/**
 * Per-build resolver pack threaded from the orchestrator so similarity /
 * injection opacity functions can read clustersStore caches without
 * coupling EdgeBuilder to Svelte stores. Each visibility predicate is
 * read once per build (gates sub-group.visible). Default resolvers
 * return fixed mid-range opacities for unit-test paths.
 */
export interface EdgeOpacityResolvers {
  similarity: (edge: SceneEdge) => number;
  injection: (edge: SceneEdge) => number;
  similarityVisible: () => boolean;
  injectionVisible: () => boolean;
}

const DEFAULT_RESOLVERS: EdgeOpacityResolvers = {
  similarity: () => 0.25,
  injection: () => 0.3,
  similarityVisible: () => true,
  injectionVisible: () => true,
};

/**
 * Builds the three edge classes — hierarchical catenaries (cluster ↔
 * domain), similarity edges (cluster ↔ cluster), injection edges (domain
 * ↔ domain) — into three ephemeral sub-groups attached to the scene.
 *
 * Lifecycle:
 *   - No shared state in the constructor — all geometries / materials
 *     are per-build because edge positions change every cycle (clusters
 *     move per ClusterPhysics).
 *   - `build()` constructs fresh sub-groups; cleanupScene (Sub-project A)
 *     disposes the previous cycle's groups at the start of the next
 *     rebuild. No `userData.persistent` flag — edge geometry changes per
 *     rebuild because cluster positions change (spec §3.5 EdgeBuilder).
 *   - `dispose()` is a no-op beyond the disposed flag; sub-groups are
 *     ephemeral and cleaned by cleanupScene, not by the builder.
 *
 * Reads `ctx.sceneNodeMap` for endpoint position + color lookups.
 * Writes `ctx.edgeUniforms.push(...)` per hierarchical parent bucket so
 * the F5 per-frame `uTime` tick covers all hierarchical edges; the
 * domain-edge branch lives in DomainBuilder which pushes into the same
 * `ctx.edgeUniforms` array.
 */
export class EdgeBuilder implements SceneBuilder {
  private _disposed = false;

  constructor(private readonly _resolvers: EdgeOpacityResolvers = DEFAULT_RESOLVERS) {}

  /**
   * Construct all three edge sub-groups for the given SceneData. Reads
   * `ctx.sceneNodeMap` for endpoint resolution; writes hierarchical-edge
   * shader uniforms into `ctx.edgeUniforms` so the F5 per-frame `uTime`
   * tick covers each parent bucket.
   *
   * @param data  - Topology snapshot whose `edges` drive the build.
   * @param scene - Target THREE.Scene the 3 sub-groups are appended to.
   * @param ctx   - Shared per-rebuild context the builder reads + mutates.
   */
  build(data: SceneData, scene: THREE.Scene, ctx: BuilderContext): void {
    if (this._disposed) return;

    // ── Hierarchical: bucket by parent + merge curves per bucket ───
    const edgesByParent = new Map<string, HierEdge[]>();
    for (const edge of data.edges) {
      if (edge.type !== 'hierarchical') continue;
      const from = ctx.sceneNodeMap.get(edge.from);
      const to = ctx.sceneNodeMap.get(edge.to);
      if (!from || !to) continue;
      if (edge.distance != null && edge.distance < EDGE_PROXIMITY_THRESHOLD) continue;
      let bucket = edgesByParent.get(edge.from);
      if (!bucket) {
        bucket = [];
        edgesByParent.set(edge.from, bucket);
      }
      // T2.3 — Carry child memberCount so _buildMergedCurveGeometry can
      // compute per-vertex alpha for weight-based visual encoding.
      bucket.push({ from: from.position, to: to.position, memberCount: to.memberCount });
    }

    // Count total children per parent (including proximity-suppressed
    // ones) so opacity scales by actual density, not by visible-edge count.
    const childCountByParent = new Map<string, number>();
    for (const edge of data.edges) {
      if (edge.type !== 'hierarchical') continue;
      childCountByParent.set(edge.from, (childCountByParent.get(edge.from) ?? 0) + 1);
    }

    const hierarchicalGroup = new THREE.Group();
    hierarchicalGroup.userData = { isInterClusterEdgeGroup: true };
    for (const [parentId, edges] of edgesByParent) {
      if (edges.length === 0) continue;
      const childCount = childCountByParent.get(parentId) ?? 1;
      const opacity = computeHierarchicalOpacity(childCount);
      const { positions, indices, alphas } = EdgeBuilder._buildMergedCurveGeometry(edges);

      const geo = new THREE.BufferGeometry();
      geo.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
      // T2.3 — Per-vertex alpha for member-count weight encoding.
      geo.setAttribute('aAlpha', new THREE.Float32BufferAttribute(alphas, 1));
      geo.setIndex(indices);

      // Inherit parent domain's color — edges fan out in the domain's hue.
      const parentNode = ctx.sceneNodeMap.get(parentId);
      const edgeColor = parentNode
        ? parseInt(parentNode.color.replace('#', ''), 16)
        : EDGE_COLOR;
      const uniforms = createEdgeDepthUniforms(edgeColor, opacity);
      // canon F5 — driven by per-frame edge-anim handler in orchestrator.
      ctx.edgeUniforms.push(uniforms);
      const mat = new THREE.ShaderMaterial({
        uniforms,
        vertexShader: EDGE_DEPTH_VERTEX,
        fragmentShader: EDGE_DEPTH_FRAGMENT,
        transparent: true,
        depthWrite: false,
      });
      const lines = new THREE.LineSegments(geo, mat);
      lines.userData = { isInterClusterEdge: true, baseOpacity: opacity, parentId };
      hierarchicalGroup.add(lines);
    }
    scene.add(hierarchicalGroup);

    // ── Similarity: between clusters ───
    const simGroup = EdgeBuilder._buildSecondaryEdgeGroup(data, ctx.sceneNodeMap, {
      type: 'similarity',
      color: SIMILARITY_EDGE_COLOR,
      dashed: true,
      tag: 'isSimilarityEdge',
      opacityFn: this._resolvers.similarity,
    });
    simGroup.visible = this._resolvers.similarityVisible();
    scene.add(simGroup);

    // ── Injection: between domains ───
    const injGroup = EdgeBuilder._buildSecondaryEdgeGroup(data, ctx.sceneNodeMap, {
      type: 'injection',
      color: INJECTION_EDGE_COLOR,
      dashed: false,
      tag: 'isInjectionEdge',
      opacityFn: this._resolvers.injection,
    });
    injGroup.visible = this._resolvers.injectionVisible();
    scene.add(injGroup);
  }

  /**
   * Release internal state. Idempotent — subsequent calls no-op. All 3
   * sub-groups are ephemeral; cleanupScene disposes them on the next
   * rebuildScene entry, so dispose has no per-build state to release.
   */
  dispose(): void {
    if (this._disposed) return;
    this._disposed = true;
  }

  /**
   * Build the bezier-curve position buffer for one hierarchical catenary.
   * Perpendicular offset is cross product with up vector (0,1,0); falls
   * back to cross with right vector (1,0,0) for near-vertical edges so
   * the curve always has a stable, non-degenerate arc plane.
   * `arcIndex` and `arcTotal` spread siblings into a fan around midpoint.
   *
   * Pure function — kept static so the build loop reads as the sequence
   * of scene mutations it is, with one call-site for the curve math.
   * Cross-builder pattern parity with
   * {@link ClusterBuilder._computeEmissive} +
   * {@link DomainBuilder._computeStructuralEmissive}.
   *
   * @param start    - Edge start position (parent / domain).
   * @param end      - Edge end position (child / cluster).
   * @param arcIndex - Sibling index in the fan (0-based).
   * @param arcTotal - Total siblings in the fan; 1 → isolated arc.
   * @returns Position buffer with (CURVE_SEGMENTS + 1) vertices × 3 axes.
   */
  private static _buildCurvePositions(
    start: [number, number, number],
    end: [number, number, number],
    arcIndex: number,
    arcTotal: number,
  ): Float32Array {
    const positions = new Float32Array((CURVE_SEGMENTS + 1) * 3);

    // Midpoint.
    const mx = (start[0] + end[0]) / 2;
    const my = (start[1] + end[1]) / 2;
    const mz = (start[2] + end[2]) / 2;

    // Edge direction + length.
    const dx = end[0] - start[0];
    const dy = end[1] - start[1];
    const dz = end[2] - start[2];
    const len = Math.sqrt(dx * dx + dy * dy + dz * dz) || 1;

    // Perpendicular offset — cross product with up vector (0,1,0):
    //   cross((dx,dy,dz), (0,1,0)) = (-dz, 0, dx)
    let px = -dz;
    let py = 0;
    let pz = dx;
    let pLen = Math.sqrt(px * px + py * py + pz * pz);
    if (pLen < 0.001) {
      // Near-vertical edge — fallback to cross with right vector (1,0,0):
      //   cross((dx,dy,dz), (1,0,0)) = (0, dz, -dy)
      px = 0;
      py = dz;
      pz = -dy;
      pLen = Math.sqrt(py * py + pz * pz) || 1;
    }
    px /= pLen;
    py /= pLen;
    pz /= pLen;

    // Fan offset: spread siblings apart. Center index = 0 offset.
    const spread = arcTotal > 1 ? (arcIndex - (arcTotal - 1) / 2) / arcTotal : 0;
    const arcMagnitude = len * 0.15 + spread * len * 0.2;

    const ctrlX = mx + px * arcMagnitude;
    const ctrlY = my + py * arcMagnitude;
    const ctrlZ = mz + pz * arcMagnitude;

    // Quadratic bezier: B(t) = (1-t)²·start + 2(1-t)t·ctrl + t²·end.
    for (let i = 0; i <= CURVE_SEGMENTS; i++) {
      const t = i / CURVE_SEGMENTS;
      const t1 = 1 - t;
      positions[i * 3] = t1 * t1 * start[0] + 2 * t1 * t * ctrlX + t * t * end[0];
      positions[i * 3 + 1] = t1 * t1 * start[1] + 2 * t1 * t * ctrlY + t * t * end[1];
      positions[i * 3 + 2] = t1 * t1 * start[2] + 2 * t1 * t * ctrlZ + t * t * end[2];
    }

    return positions;
  }

  /**
   * Merge multiple catenary curves into a single geometry's position +
   * index arrays so each parent bucket renders as one draw call.
   * T2.3 — per-vertex alpha encodes normalized memberCount weight
   * (heavier children render brighter; floor of 0.3 prevents vanish).
   *
   * Pure function — kept static for cross-builder pattern parity.
   *
   * @param edges - Sibling edges sharing one parent bucket.
   * @returns Bundled position buffer + line-segment index list + per-
   *   vertex alpha attribute the merged BufferGeometry consumes.
   */
  private static _buildMergedCurveGeometry(edges: HierEdge[]): {
    positions: number[];
    indices: number[];
    alphas: number[];
  } {
    const positions: number[] = [];
    const indices: number[] = [];
    const alphas: number[] = [];
    // T2.3 — Compute max memberCount across siblings for normalization.
    let maxMembers = 1;
    for (const e of edges) {
      if (e.memberCount != null && e.memberCount > maxMembers) maxMembers = e.memberCount;
    }
    let offset = 0;
    for (let i = 0; i < edges.length; i++) {
      const cp = EdgeBuilder._buildCurvePositions(edges[i].from, edges[i].to, i, edges.length);
      // Per-vertex alpha: normalized member count → [0.3, 1.0].
      const memberAlpha =
        edges[i].memberCount != null
          ? 0.3 + 0.7 * (edges[i].memberCount! / maxMembers)
          : 1.0;
      for (let j = 0; j < cp.length; j++) positions.push(cp[j]);
      for (let j = 0; j <= CURVE_SEGMENTS; j++) alphas.push(memberAlpha);
      for (let j = 0; j < CURVE_SEGMENTS; j++) indices.push(offset + j, offset + j + 1);
      offset += CURVE_SEGMENTS + 1;
    }
    return { positions, indices, alphas };
  }

  /**
   * Build a sibling-shared THREE.Group of straight LineSegments for one
   * of the secondary edge classes (similarity / injection). Tagged on
   * both group + each line so cleanup + LOD visibility toggles can target
   * the entire class independently.
   *
   * Pure function (no class state read) — kept static for cross-builder
   * pattern parity.
   *
   * @param data    - Topology snapshot whose `edges` drive the build.
   * @param nodeMap - sceneNodeMap reference for endpoint resolution.
   * @param opts    - Per-class options (type filter / color / dashed /
   *   userData tag / opacity resolver).
   * @returns Tagged THREE.Group containing one LineSegments per matched edge.
   */
  private static _buildSecondaryEdgeGroup(
    data: SceneData,
    nodeMap: Map<string, SceneNode>,
    opts: EdgeGroupOptions,
  ): THREE.Group {
    const group = new THREE.Group();
    group.userData = { [opts.tag]: true };
    const edges = data.edges.filter((e) => e.type === opts.type);
    for (const edge of edges) {
      const from = nodeMap.get(edge.from);
      const to = nodeMap.get(edge.to);
      if (!from || !to) continue;
      const opacity = opts.opacityFn(edge);
      const geo = new THREE.BufferGeometry();
      geo.setAttribute(
        'position',
        new THREE.Float32BufferAttribute([...from.position, ...to.position], 3),
      );
      const mat = opts.dashed
        ? new THREE.LineDashedMaterial({
            color: opts.color,
            transparent: true,
            opacity,
            dashSize: 0.3,
            gapSize: 0.2,
          })
        : new THREE.LineBasicMaterial({
            color: opts.color,
            transparent: true,
            opacity,
          });
      const line = new THREE.LineSegments(geo, mat);
      if (opts.dashed) line.computeLineDistances();
      line.userData = { [opts.tag]: true, baseOpacity: opacity };
      group.add(line);
    }
    return group;
  }
}
