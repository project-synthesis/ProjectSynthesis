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
import type { SceneData } from '../TopologyData';
import type { SceneBuilder } from './SceneBuilder';
import type { BuilderContext } from './BuilderContext';
import type { TopologyInteraction } from '../TopologyInteraction';
import {
  DOMAIN_EDGE_VERTEX,
  DOMAIN_EDGE_FRAGMENT,
  createDomainEdgeUniforms,
} from '../DomainEdgeShader';

export class DomainBuilder implements SceneBuilder {
  // Shared geometries — once per builder lifetime.
  private readonly _fillGeo: THREE.DodecahedronGeometry;
  private readonly _edgesBase: THREE.DodecahedronGeometry;
  private readonly _edgesGeo: THREE.EdgesGeometry;
  private readonly _pointsGeo: THREE.BufferGeometry;
  private _glowTextureBuilt = false;
  private _disposed = false;

  constructor(private readonly _interaction: TopologyInteraction | null) {
    this._fillGeo = new THREE.DodecahedronGeometry(1, 2);
    // EdgesGeometry on subdiv-0 dodecahedron extracts only the 30 structural
    // edges (pentagonal outlines), ignoring subdivision diagonals.
    this._edgesBase = new THREE.DodecahedronGeometry(1, 0);
    this._edgesGeo = new THREE.EdgesGeometry(this._edgesBase, 1);
    // Extract 20 unique vertices for the F2 vertex glow points.
    const verts = this._edgesBase.getAttribute('position');
    const uniqueVerts = new Map<string, [number, number, number]>();
    for (let i = 0; i < verts.count; i++) {
      const x = verts.getX(i);
      const y = verts.getY(i);
      const z = verts.getZ(i);
      const key = `${x.toFixed(4)},${y.toFixed(4)},${z.toFixed(4)}`;
      if (!uniqueVerts.has(key)) uniqueVerts.set(key, [x, y, z]);
    }
    const vertArray = new Float32Array([...uniqueVerts.values()].flat());
    this._pointsGeo = new THREE.BufferGeometry();
    this._pointsGeo.setAttribute('position', new THREE.Float32BufferAttribute(vertArray, 3));
  }

  build(data: SceneData, scene: THREE.Scene, ctx: BuilderContext): void {
    if (this._disposed) return;
    for (const node of data.nodes) {
      if (!node.visible) continue;
      if (node.state !== 'domain' && node.state !== 'project') continue;

      const group = new THREE.Group();
      group.position.set(...node.position);
      group.userData = { isStructural: true, isSubDomain: node.isSubDomain };

      // Domain fill: dark tinted interior; structural baseEmissive = 0.4.
      const fillScalar = 0.08;
      const baseEmissiveIntensity = 0.4;
      const coherenceBoost = 0; // structural: no coherence-driven temperature shift
      const emissiveColor = new THREE.Color(node.color);
      const scoreModifier = 1.0; // structural: avgScore not in F1 modulation path
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

      // T1.1 phase offset (same formula as ClusterBuilder).
      let hash = 0;
      for (let ci = 0; ci < node.id.length; ci++) hash += node.id.charCodeAt(ci);
      ctx.nodePhaseOffsets.set(node.id, (hash % 1000) / 1000 * Math.PI * 2);

      const fill = new THREE.Mesh(this._fillGeo, fillMat);
      fill.castShadow = true;
      fill.receiveShadow = true;
      fill.userData = { isClusterFill: true, baseEmissive: emissiveIntensity };
      fill.scale.setScalar(node.size);
      group.add(fill);

      // T1.3 — Domain edge ShaderMaterial heartbeat (half-frequency of
      // hierarchical child edges). Pushed to ctx.edgeUniforms so the F5
      // uTime tick covers both branches.
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
      // points at the globalThis.__semTopGlowTexture handle for the
      // cleanup return's dispose path. JSDOM falls back to undefined
      // (no canvas context), which PointsMaterial.map=undefined tolerates.
      if (!this._glowTextureBuilt) {
        this._glowTextureBuilt = true;
        const canvas = document.createElement('canvas');
        canvas.width = 64;
        canvas.height = 64;
        const c2d = canvas.getContext('2d');
        if (c2d) {
          const gradient = c2d.createRadialGradient(32, 32, 0, 32, 32, 32);
          gradient.addColorStop(0, 'rgba(255, 255, 255, 1)');
          gradient.addColorStop(0.3, 'rgba(255, 255, 255, 0.8)');
          gradient.addColorStop(0.6, 'rgba(255, 255, 255, 0.2)');
          gradient.addColorStop(1, 'rgba(255, 255, 255, 0)');
          c2d.fillStyle = gradient;
          c2d.fillRect(0, 0, 64, 64);
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

  dispose(): void {
    if (this._disposed) return;
    this._disposed = true;
    this._fillGeo.dispose();
    this._edgesBase.dispose();
    this._edgesGeo.dispose();
    this._pointsGeo.dispose();
    // Note: globalThis.__semTopGlowTexture is NOT disposed here — the
    // canon F2 cleanup is owned by SemanticTopology.svelte's cleanup return
    // (which disposes + nulls the global handle per the cleanup-contract
    // assertions). Builders are constructed once per component lifetime so
    // the global handle's lifecycle matches the cleanup return's contract.
  }
}
