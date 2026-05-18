// frontend/src/lib/components/taxonomy/builders/ClusterBuilder.ts
//
// Per-cluster THREE.Group construction (Icosahedron fill + ripple wireframe).
// Migrated from SemanticTopology.svelte's unified `for (const node of
// data.nodes)` loop — specifically the cluster `else { ... }` branch
// (HEAD baseline ~lines 1142-1158, anchored by the unified-loop header
// comment `// Build nodes — two distinct visual tiers` at HEAD ~line 1027).
// The domain/project `if (isStructural)` branch (HEAD ~lines 1027-1141)
// belongs to DomainBuilder — see Cycle 2 (Tasks 6-10).
//
// Writes to ctx:
//   - nodeMeshes[node.id]            → fill Mesh (canon F1 cluster sphere)
//   - beamNodeGroups[node.id]        → parent THREE.Group
//   - clusterShaderMaterials[node.id]→ wire ShaderMaterial (F5 + F19)
//   - nodePhaseOffsets[node.id]      → T1.1 breathing-desync phase
//                                      (hash%1000)/1000 * 2π
//
// Side effects:
//   - this._interaction?.registerNode(node.id, fill, node)
//
// Per-rebuild groups are ephemeral; cleanupScene (Sub-project A) disposes
// them at the start of the next rebuild. No persistent flags set here.
//
// Spec: docs/superpowers/specs/2026-05-17-scene-builder-extraction-design.md

import * as THREE from 'three';
import type { SceneData } from '../TopologyData';
import type { SceneBuilder } from './SceneBuilder';
import type { BuilderContext } from './BuilderContext';
import type { TopologyInteraction } from '../TopologyInteraction';
import {
  RIPPLE_VERTEX_SHADER,
  RIPPLE_FRAGMENT_SHADER,
  createRippleUniforms,
} from '../BeamShader';

/** F1 hero member count — at this count, base emissive saturates at 1.4. */
const HERO_MEMBER_COUNT = 50;

export class ClusterBuilder implements SceneBuilder {
  // Shared cluster geometries — constructed once per ClusterBuilder
  // lifetime (NOT per-build) to avoid per-rebuild geometry allocation.
  // The orchestrator constructs ClusterBuilder in onMount + disposes in
  // the cleanup return; the geometries live for the component's lifetime.
  private readonly _fillGeo: THREE.IcosahedronGeometry;
  private readonly _wireGeo: THREE.WireframeGeometry;
  private _disposed = false;

  constructor(private readonly _interaction: TopologyInteraction | null) {
    // High-tessellation fill mesh; the wireframe is derived from a coarser
    // base for visual clarity (matches the prior `IcosahedronGeometry(1, 1)`
    // for `clusterWireGeo` at HEAD ~line 990).
    this._fillGeo = new THREE.IcosahedronGeometry(1, 2);
    const wireBase = new THREE.IcosahedronGeometry(1, 1);
    this._wireGeo = new THREE.WireframeGeometry(wireBase);
    // The wireBase is no longer needed once WireframeGeometry extracts the
    // edge buffer; dispose immediately to release the redundant attributes.
    wireBase.dispose();
  }

  build(data: SceneData, scene: THREE.Scene, ctx: BuilderContext): void {
    if (this._disposed) return;
    for (const node of data.nodes) {
      if (!node.visible) continue;
      // Structural nodes (state === 'domain' || state === 'project') are
      // owned by DomainBuilder. Splits the prior unified loop per spec
      // §3.3 M2.
      if (node.state === 'domain' || node.state === 'project') continue;

      const group = new THREE.Group();
      group.position.set(...node.position);
      group.userData = { isStructural: false, isSubDomain: node.isSubDomain };

      // Fill scalar (domain saturation tint via avgScore).
      let fillScalar = 0.15;
      if (node.avgScore != null) {
        fillScalar *= 0.7 + 0.3 * Math.min(1, Math.max(0, node.avgScore / 10));
      }

      // Canon F1 emissive intensity. memberCount=1 → 0.6 (base),
      // memberCount=50+ → 1.4 (hero), clamped.
      const baseEmissiveIntensity =
        0.6 + 0.8 * Math.min(1, Math.max(0, (node.memberCount - 1) / (HERO_MEMBER_COUNT - 1)));

      // T1.2 — Coherence-driven color temperature: lerp emissive toward white.
      const coherenceBoost = 0.1 * node.coherence;
      const emissiveColor = new THREE.Color(node.color);
      if (coherenceBoost > 0) {
        emissiveColor.lerp(new THREE.Color(0xffffff), coherenceBoost * 0.3);
      }

      // T2.1 — Score-driven emissive modulation.
      const scoreModifier =
        node.avgScore != null
          ? 0.85 + 0.15 * Math.min(1, Math.max(0, node.avgScore / 10))
          : 1.0;
      const emissiveIntensity = baseEmissiveIntensity * (1.0 + coherenceBoost) * scoreModifier;

      const fillMat = new THREE.MeshStandardMaterial({
        color: new THREE.Color(node.color).multiplyScalar(fillScalar),
        emissive: emissiveColor,
        emissiveIntensity,
        roughness: 0.6,
        metalness: 0.0,
        transparent: true,
        opacity: node.opacity * 0.9,
      });

      // T1.1 — Deterministic phase offset for desynchronized breathing.
      // Simple hash: sum of char codes mod 1000, mapped to [0, 2π].
      let hash = 0;
      for (let ci = 0; ci < node.id.length; ci++) hash += node.id.charCodeAt(ci);
      ctx.nodePhaseOffsets.set(node.id, ((hash % 1000) / 1000) * Math.PI * 2);

      const fill = new THREE.Mesh(this._fillGeo, fillMat);
      // Self-shadowing: clusters cast + receive shadows under the directional
      // key light. No floor plane → only mesh-to-mesh shadows render visibly.
      fill.castShadow = true;
      fill.receiveShadow = true;
      fill.userData = { isClusterFill: true, baseEmissive: emissiveIntensity };
      fill.scale.setScalar(node.size);
      group.add(fill);

      // Cluster: dense triangular wireframe (LineSegments topology) with
      // ripple shader (canon F5 + F19). The ripple shader's vertex
      // displacement still drives the color flash; the line topology omits
      // surface-normal-driven displacement but the brand intent (emissive
      // contour + flash on impact) is preserved.
      const wireUniforms = createRippleUniforms();
      wireUniforms.uColor.value = new THREE.Color(node.color);
      wireUniforms.uOpacity.value = node.opacity * (0.5 + 0.5 * node.coherence);
      const wireMat = new THREE.ShaderMaterial({
        uniforms: wireUniforms,
        vertexShader: RIPPLE_VERTEX_SHADER,
        fragmentShader: RIPPLE_FRAGMENT_SHADER,
        transparent: true,
        depthWrite: false,
      });
      const wire = new THREE.LineSegments(this._wireGeo, wireMat);
      wire.scale.setScalar(node.size);
      group.add(wire);

      scene.add(group);
      ctx.nodeMeshes.set(node.id, fill);
      ctx.beamNodeGroups.set(node.id, group);
      ctx.clusterShaderMaterials.set(node.id, wireMat);
      this._interaction?.registerNode(node.id, fill, node);
    }
  }

  dispose(): void {
    if (this._disposed) return;
    this._disposed = true;
    this._fillGeo.dispose();
    this._wireGeo.dispose();
  }
}
