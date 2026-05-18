// frontend/src/lib/components/taxonomy/builders/DomainBuilder.ts
//
// Per-domain THREE.Group construction. Migrated from
// SemanticTopology.svelte's unified `for (const node of data.nodes)`
// IF (isStructural) branch (HEAD baseline lines ~1082-1140) + upfront
// geometry setup (lines ~991-1008) + glow texture lazy-init
// (lines ~1106-1124).
//
// Writes to ctx:
//   - nodeMeshes[node.id]      → fill Mesh (canon F1 domain anchor)
//   - beamNodeGroups[node.id]  → parent THREE.Group
//   - domainGroups.push(group) → for the domain-rotation per-frame handler
//   - edgeUniforms.push(domainEdgeUniforms) → for the F5 uTime pulse
//   - nodePhaseOffsets[node.id]→ T1.1 breathing-desync phase
//
// Side effects:
//   - this._interaction?.registerNode(node.id, fill, node)
//   - globalThis.__semTopGlowTexture lazily set on first call (canon F2;
//     cleanup return in SemanticTopology.svelte owns disposal).
//
// Spec: docs/superpowers/specs/2026-05-17-scene-builder-extraction-design.md

import * as THREE from 'three';
import type { SceneData, SceneNode } from '../TopologyData';
import type { SceneBuilder } from './SceneBuilder';
import type { BuilderContext } from './BuilderContext';
import type { TopologyInteraction } from '../TopologyInteraction';
import {
  DOMAIN_EDGE_VERTEX,
  DOMAIN_EDGE_FRAGMENT,
  createDomainEdgeUniforms,
} from '../DomainEdgeShader';

/** F1 structural emissive baseline — domain anchors are recessed context. */
const STRUCTURAL_BASE_EMISSIVE = 0.4;
/** F1 structural fill scalar — dark tinted interior so the EdgesGeometry
 *  outlines + vertex glow points read as the foreground silhouette. */
const STRUCTURAL_FILL_SCALAR = 0.08;
/** F2 glow CanvasTexture footprint — 64×64 radial gradient. */
const GLOW_TEXTURE_SIZE = 64;

/**
 * Result of the F1 structural emissive computation — bundled so the build
 * loop assigns colour + intensity atomically, mirroring ClusterBuilder's
 * EmissiveResult shape for cross-builder pattern parity.
 */
interface StructuralEmissiveResult {
  color: THREE.Color;
  intensity: number;
}

/**
 * Builds per-domain THREE.Group instances (Dodecahedron fill +
 * EdgesGeometry outlines + vertex-glow Points cores) and wires them into
 * the shared {@link BuilderContext}. Splits the structural branch out of
 * the legacy unified `for (const node of data.nodes)` loop per spec §3.3 M2.
 *
 * Lifecycle:
 *   - Shared geometries (`_fillGeo`, `_edgesGeo`, `_pointsGeo`) constructed
 *     once in the constructor and reused across rebuilds.
 *   - The F2 glow CanvasTexture is lazily built on the first `build()` call
 *     that processes a structural node; cached on `globalThis.__semTopGlowTexture`
 *     so the cleanup return in SemanticTopology.svelte retains ownership of
 *     disposal (existing cleanup-contract path).
 *   - `build()` runs per scene rebuild; produces ephemeral groups that
 *     `cleanupScene` (Sub-project A) disposes at the start of the next
 *     rebuild — `userData.isStructural = true` is preserved for selection
 *     + label code reads but NOT `userData.persistent`.
 *   - `dispose()` releases the shared geometries on component unmount.
 */
export class DomainBuilder implements SceneBuilder {
  // Shared dodecahedron geometries — constructed once per DomainBuilder
  // lifetime and reused across every per-rebuild build() invocation.
  private readonly _fillGeo: THREE.DodecahedronGeometry;
  private readonly _edgesBase: THREE.DodecahedronGeometry;
  private readonly _edgesGeo: THREE.EdgesGeometry;
  private readonly _pointsGeo: THREE.BufferGeometry;
  private _glowTextureBuilt = false;
  private _disposed = false;

  constructor(private readonly _interaction: TopologyInteraction | null) {
    // High-tessellation fill mesh; the edge outlines are derived from a
    // coarser subdiv-0 base so EdgesGeometry extracts only the 30 pentagonal
    // structural edges (no subdivision diagonals).
    this._fillGeo = new THREE.DodecahedronGeometry(1, 2);
    this._edgesBase = new THREE.DodecahedronGeometry(1, 0);
    this._edgesGeo = new THREE.EdgesGeometry(this._edgesBase, 1);
    // F2 vertex glow points: extract the 20 unique dodecahedron vertices
    // from the subdiv-0 base. The position attribute repeats vertices per
    // face — dedup by quantized-coordinate key.
    this._pointsGeo = DomainBuilder._buildUniqueVertexGeometry(this._edgesBase);
  }

  /**
   * Iterate visible structural nodes (`state === 'domain' || state ===
   * 'project'`) in `data.nodes`, construct one ephemeral domain group per
   * node, and write the shared state every downstream consumer (edge
   * builder, ring builder, breathing handler, impact coordinator, domain
   * rotation handler) reads. Non-structural nodes are skipped — those
   * belong to ClusterBuilder.
   *
   * Idempotent across rebuilds ONLY when `cleanupScene` runs between
   * invocations; back-to-back `build` calls without cleanup duplicate
   * groups in the scene.
   *
   * Writes to `ctx`:
   *   - `nodeMeshes[node.id]`        → fill Mesh (canon F1 domain anchor)
   *   - `beamNodeGroups[node.id]`    → parent THREE.Group
   *   - `domainGroups.push(group)`   → for the domain-rotation handler
   *   - `edgeUniforms.push(...)`     → domain-edge uniforms (M7 — F5 uTime pulse)
   *   - `nodePhaseOffsets[node.id]`  → T1.1 breathing-desync phase
   *
   * Side effects:
   *   - `this._interaction?.registerNode(node.id, fill, node)`
   *   - `globalThis.__semTopGlowTexture` lazily set on first call (canon F2).
   *
   * @param data  - Topology snapshot whose `nodes` drive the build loop.
   * @param scene - Target THREE.Scene the domain groups are appended to.
   * @param ctx   - Shared per-rebuild context the builder mutates.
   */
  build(data: SceneData, scene: THREE.Scene, ctx: BuilderContext): void {
    if (this._disposed) return;
    for (const node of data.nodes) {
      if (!node.visible) continue;
      // Cluster nodes (state !== 'domain' && state !== 'project') are owned
      // by ClusterBuilder. Splits the prior unified loop per spec §3.3 M2.
      if (node.state !== 'domain' && node.state !== 'project') continue;

      const group = new THREE.Group();
      group.position.set(...node.position);
      group.userData = { isStructural: true, isSubDomain: node.isSubDomain };

      const { color: emissiveColor, intensity: emissiveIntensity } =
        DomainBuilder._computeStructuralEmissive(node);

      const fillMat = new THREE.MeshStandardMaterial({
        color: new THREE.Color(node.color).multiplyScalar(STRUCTURAL_FILL_SCALAR),
        emissive: emissiveColor,
        emissiveIntensity,
        roughness: 0.6,
        metalness: 0.0,
        transparent: true,
        opacity: node.opacity * 0.9,
      });

      ctx.nodePhaseOffsets.set(node.id, DomainBuilder._computePhaseOffset(node.id));

      const fill = new THREE.Mesh(this._fillGeo, fillMat);
      // Self-shadowing: domain anchors cast + receive shadows so the
      // directional key light models depth onto neighboring clusters.
      fill.castShadow = true;
      fill.receiveShadow = true;
      fill.userData = { isClusterFill: true, baseEmissive: emissiveIntensity };
      fill.scale.setScalar(node.size);
      group.add(fill);

      // T1.3 — Domain edge ShaderMaterial heartbeat (half-frequency of
      // hierarchical child edges). Push to ctx.edgeUniforms so the F5
      // uTime tick covers both branches (spec M7).
      const domainEdgeColor = parseInt(node.color.replace('#', ''), 16);
      const domainEdgeUniforms = createDomainEdgeUniforms(domainEdgeColor, node.opacity * 0.9);
      ctx.edgeUniforms.push(domainEdgeUniforms);
      const edgeMat = new THREE.ShaderMaterial({
        uniforms: domainEdgeUniforms,
        vertexShader: DOMAIN_EDGE_VERTEX,
        fragmentShader: DOMAIN_EDGE_FRAGMENT,
        transparent: true,
        depthWrite: false,
      });
      const edges = new THREE.LineSegments(this._edgesGeo, edgeMat);
      edges.scale.setScalar(node.size);
      group.add(edges);

      // F2 vertex glow texture — lazy build on first call. Canon citation
      // points at the globalThis.__semTopGlowTexture handle; the cleanup
      // return in SemanticTopology.svelte owns disposal. JSDOM falls back
      // to undefined (no canvas context), which PointsMaterial.map=undefined
      // tolerates.
      if (!this._glowTextureBuilt) {
        this._glowTextureBuilt = true;
        const canvas = document.createElement('canvas');
        canvas.width = GLOW_TEXTURE_SIZE;
        canvas.height = GLOW_TEXTURE_SIZE;
        const c2d = canvas.getContext('2d');
        if (c2d) {
          const mid = GLOW_TEXTURE_SIZE / 2;
          const gradient = c2d.createRadialGradient(mid, mid, 0, mid, mid, mid);
          gradient.addColorStop(0, 'rgba(255, 255, 255, 1)');
          gradient.addColorStop(0.3, 'rgba(255, 255, 255, 0.8)');
          gradient.addColorStop(0.6, 'rgba(255, 255, 255, 0.2)');
          gradient.addColorStop(1, 'rgba(255, 255, 255, 0)');
          c2d.fillStyle = gradient;
          c2d.fillRect(0, 0, GLOW_TEXTURE_SIZE, GLOW_TEXTURE_SIZE);
          (globalThis as unknown as { __semTopGlowTexture?: THREE.CanvasTexture })
            .__semTopGlowTexture = new THREE.CanvasTexture(canvas);
        }
      }

      const pointsMat = new THREE.PointsMaterial({
        color: node.color,
        size: 0.35,
        map: (globalThis as unknown as { __semTopGlowTexture?: THREE.CanvasTexture })
          .__semTopGlowTexture,
        transparent: true,
        opacity: node.opacity * 0.95,
        blending: THREE.AdditiveBlending,
        depthWrite: false,
        sizeAttenuation: true,
      });
      const points = new THREE.Points(this._pointsGeo, pointsMat);
      points.scale.setScalar(node.size);
      group.add(points);

      scene.add(group);
      ctx.nodeMeshes.set(node.id, fill);
      ctx.beamNodeGroups.set(node.id, group);
      ctx.domainGroups.push(group);
      this._interaction?.registerNode(node.id, fill, node);
    }
  }

  /**
   * Release the shared dodecahedron geometries. Idempotent — subsequent
   * calls no-op. After `dispose()`, `build()` short-circuits without
   * mutating the scene or ctx.
   *
   * Note: `globalThis.__semTopGlowTexture` is NOT disposed here — the
   * canon F2 cleanup is owned by SemanticTopology.svelte's cleanup return
   * (which disposes + nulls the global handle per the cleanup-contract
   * assertions). Builders are constructed once per component lifetime so
   * the global handle's lifecycle matches the cleanup return's contract.
   */
  dispose(): void {
    if (this._disposed) return;
    this._disposed = true;
    this._fillGeo.dispose();
    this._edgesBase.dispose();
    this._edgesGeo.dispose();
    this._pointsGeo.dispose();
  }

  /**
   * Compute the canon F1 emissive colour + intensity for a structural
   * node (domain or project anchor). Structural nodes are recessed
   * context — `baseEmissive = 0.4` with no coherence boost and no
   * avgScore modulation. The single computation is bundled into a result
   * struct so the build loop reads as a sequence of scene mutations with
   * one call-site for the emissive math.
   *
   * Pure function — kept static for cross-builder pattern parity with
   * ClusterBuilder._computeEmissive (which threads coherence + score
   * modulation that DOES NOT apply to structural anchors).
   *
   * @param node - The structural node whose F1 emissive is being computed.
   * @returns Bundled emissive colour + final intensity scalar.
   */
  private static _computeStructuralEmissive(node: SceneNode): StructuralEmissiveResult {
    const color = new THREE.Color(node.color);
    // Structural nodes: no coherence boost, no avgScore modulation.
    // baseEmissive * (1 + 0) * 1.0 → STRUCTURAL_BASE_EMISSIVE.
    return { color, intensity: STRUCTURAL_BASE_EMISSIVE };
  }

  /**
   * Canon T1.1 deterministic phase offset for desynchronized organic
   * breathing. Identical formula to {@link ClusterBuilder._computePhaseOffset}
   * so structural + cluster nodes share the same desync distribution.
   *
   * @param id - Node id whose phase offset is being computed.
   * @returns Phase offset in radians, [0, 2π).
   */
  private static _computePhaseOffset(id: string): number {
    let hash = 0;
    for (let ci = 0; ci < id.length; ci++) hash += id.charCodeAt(ci);
    return ((hash % 1000) / 1000) * Math.PI * 2;
  }

  /**
   * Extract the 20 unique dodecahedron vertices from a base geometry's
   * position attribute (which repeats vertices per face). Used in the
   * constructor to build the F2 vertex-glow Points geometry — pulled out
   * so the constructor reads as the sequence of geometry constructions
   * it is.
   *
   * Pure function — keyed by 4-decimal quantized coordinates so floating-
   * point round-trip through THREE's geometry attribute storage doesn't
   * spuriously deduplicate or fragment shared vertices.
   *
   * @param base - The DodecahedronGeometry whose vertices are deduplicated.
   * @returns BufferGeometry with the 20 unique vertex positions.
   */
  private static _buildUniqueVertexGeometry(
    base: THREE.DodecahedronGeometry,
  ): THREE.BufferGeometry {
    const verts = base.getAttribute('position');
    const uniqueVerts = new Map<string, [number, number, number]>();
    for (let i = 0; i < verts.count; i++) {
      const x = verts.getX(i);
      const y = verts.getY(i);
      const z = verts.getZ(i);
      const key = `${x.toFixed(4)},${y.toFixed(4)},${z.toFixed(4)}`;
      if (!uniqueVerts.has(key)) uniqueVerts.set(key, [x, y, z]);
    }
    const vertArray = new Float32Array([...uniqueVerts.values()].flat());
    const geo = new THREE.BufferGeometry();
    geo.setAttribute('position', new THREE.Float32BufferAttribute(vertArray, 3));
    return geo;
  }
}
