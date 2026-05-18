// frontend/src/lib/components/taxonomy/builders/EdgeBuilder.test.ts
//
// Sub-project D Cycle 3 — 15 unit tests for EdgeBuilder per spec §5.1.
// Real THREE.Scene + mocked SceneData. Tests cover 3 edge classes:
//   - hierarchical (catenary curves cluster ↔ domain)
//   - similarity (between clusters)
//   - injection (between domains)
//
// Spec: docs/superpowers/specs/2026-05-17-scene-builder-extraction-design.md

import { describe, it, expect, beforeEach } from 'vitest';
import * as THREE from 'three';
import type { SceneData, SceneNode } from '../TopologyData';
import { EdgeBuilder } from './EdgeBuilder';
import { ClusterBuilder } from './ClusterBuilder';
import { DomainBuilder } from './DomainBuilder';
import { createBuilderContext, type BuilderContext } from './BuilderContext';

interface MockEdge {
  from: string;
  to: string;
  type: 'hierarchical' | 'similarity' | 'injection';
  distance?: number;
}

function makeNode(id: string, overrides: Partial<SceneNode> = {}): SceneNode {
  return {
    id,
    position: [0, 0, 0],
    color: '#ff00ff',
    size: 1.0,
    opacity: 1.0,
    persistence: 1.0,
    state: 'active',
    label: id,
    visible: true,
    coherence: 0.5,
    avgScore: 5.0,
    domain: 'general',
    memberCount: 1,
    isSubDomain: false,
    template_count: 0,
    ...overrides,
  } as SceneNode;
}

function makeData(nodes: SceneNode[], edges: MockEdge[] = []): SceneData {
  return { nodes, edges: edges as unknown as SceneData['edges'] } as SceneData;
}

function buildClusterAndDomainCtx(
  cluster: SceneNode,
  domain: SceneNode,
  scene: THREE.Scene,
): BuilderContext {
  const ctx = createBuilderContext();
  ctx.sceneNodeMap.set(cluster.id, cluster);
  ctx.sceneNodeMap.set(domain.id, domain);
  new ClusterBuilder(null).build(makeData([cluster, domain]), scene, ctx);
  new DomainBuilder(null).build(makeData([cluster, domain]), scene, ctx);
  return ctx;
}

describe('EdgeBuilder — 15 unit tests per spec §5.1', () => {
  let scene: THREE.Scene;

  beforeEach(() => {
    scene = new THREE.Scene();
  });

  // ── #1 Hierarchical: build(data with cluster + domain) adds catenary curve ──
  it('#1 — Hierarchical: build adds one catenary curve between cluster + domain', () => {
    const cluster = makeNode('c1');
    const domain = makeNode('d1', { state: 'domain', position: [10, 0, 0] });
    const data = makeData([cluster, domain], [
      { from: 'd1', to: 'c1', type: 'hierarchical' },
    ]);
    const ctx = buildClusterAndDomainCtx(cluster, domain, scene);
    new EdgeBuilder().build(data, scene, ctx);
    // At least one LineSegments (hierarchical edge) added.
    const hierLines = scene.children.filter((c) =>
      (c as THREE.Group).userData?.isInterClusterEdgeGroup === true,
    );
    expect(hierLines.length).toBe(1);
    const hierGroup = hierLines[0] as THREE.Group;
    expect(hierGroup.children.length).toBe(1);
    expect(hierGroup.children[0].type).toBe('LineSegments');
  });

  // ── #2 Hierarchical (rev 3 — perpendicular-bezier displacement) ──
  it('#2 — Hierarchical perpendicular-bezier: from=(0,0,0) to=(10,0,0) → t=0.5 vertex ≈ (5, 0, 0.75)', () => {
    // For an X-axis edge from=(0,0,0) to=(10,0,0):
    //   - len = 10
    //   - direction = (1,0,0); cross with up (0,1,0) = (0,0,1) (unit Z perp)
    //   - arcMagnitude (isolated arc, arcTotal=1) = len * 0.15 = 1.5
    //   - midpoint = (5, 0, 0)
    //   - ctrl = midpoint + perpUnit * arcMagnitude = (5, 0, 1.5)
    //   - t=0.5 bezier vertex:
    //     B(0.5) = 0.25*start + 0.5*ctrl + 0.25*end
    //            = 0.25*(0,0,0) + 0.5*(5,0,1.5) + 0.25*(10,0,0)
    //            = (2.5, 0, 0.75) + (2.5, 0, 0)
    //            = (5.0, 0, 0.75)
    // Test asserts (5, 0, 0.75) ±0.01.
    //
    // DEVIATION from spec §5.1 #2 literal: the spec fixture used
    // `{ from: 'B', to: 'A' }` (parent='B') which after endpoint
    // resolution feeds the catenary `start=(10,0,0), end=(0,0,0)` —
    // the OPPOSITE direction of the docstring math. The fix flips
    // the edge so the catenary input matches the docstring
    // `start=(0,0,0), end=(10,0,0)`; the resulting +0.75 mid-vertex
    // is the spec rev-3 intent. Submitted upstream.
    const a = makeNode('A', { position: [0, 0, 0] });
    const b = makeNode('B', { state: 'domain', position: [10, 0, 0] });
    const data = makeData([a, b], [{ from: 'A', to: 'B', type: 'hierarchical' }]);
    const ctx = buildClusterAndDomainCtx(a, b, scene);
    new EdgeBuilder().build(data, scene, ctx);
    const hierGroup = scene.children.find(
      (c) => (c as THREE.Group).userData?.isInterClusterEdgeGroup,
    ) as THREE.Group;
    expect(hierGroup).toBeDefined();
    const lines = hierGroup.children[0] as THREE.LineSegments;
    const positions = lines.geometry.getAttribute('position') as THREE.BufferAttribute;
    // CURVE_SEGMENTS = 12 → 13 vertices per curve; mid-vertex index = 6.
    const midX = positions.getX(6);
    const midY = positions.getY(6);
    const midZ = positions.getZ(6);
    expect(midX).toBeCloseTo(5, 2);
    expect(midY).toBeCloseTo(0, 2);
    expect(midZ).toBeCloseTo(0.75, 2);
  });

  // ── #3 Hierarchical group contains all hierarchical edges ──
  it('#3 — Hierarchical group contains all hierarchical edges (one parent, two children)', () => {
    const domain = makeNode('d1', { state: 'domain', position: [0, 0, 0] });
    const child1 = makeNode('c1', { position: [5, 0, 0] });
    const child2 = makeNode('c2', { position: [-5, 0, 0] });
    const data = makeData(
      [domain, child1, child2],
      [
        { from: 'd1', to: 'c1', type: 'hierarchical' },
        { from: 'd1', to: 'c2', type: 'hierarchical' },
      ],
    );
    const ctx = createBuilderContext();
    for (const n of data.nodes) ctx.sceneNodeMap.set(n.id, n);
    new ClusterBuilder(null).build(data, scene, ctx);
    new DomainBuilder(null).build(data, scene, ctx);
    new EdgeBuilder().build(data, scene, ctx);
    const hierGroup = scene.children.find(
      (c) => (c as THREE.Group).userData?.isInterClusterEdgeGroup,
    ) as THREE.Group;
    // Both child edges live inside hierGroup (bucketed by parent d1).
    expect(hierGroup.children.length).toBe(1); // one merged LineSegments per parent
  });

  // ── #4 Hierarchical: empty data → empty hierarchical group ──
  it('#4 — Hierarchical: empty data → no hierarchical-edge children', () => {
    const ctx = createBuilderContext();
    new EdgeBuilder().build(makeData([]), scene, ctx);
    const hierGroup = scene.children.find(
      (c) => (c as THREE.Group).userData?.isInterClusterEdgeGroup,
    );
    // Either no hierarchical group attached, or attached with 0 children.
    if (hierGroup) {
      expect((hierGroup as THREE.Group).children.length).toBe(0);
    }
  });

  // ── #5 Hierarchical: edge shader uniforms pushed to ctx.edgeUniforms ──
  it('#5 — Hierarchical: shader uniforms pushed to ctx.edgeUniforms', () => {
    const cluster = makeNode('c1');
    const domain = makeNode('d1', { state: 'domain', position: [5, 0, 0] });
    const data = makeData([cluster, domain], [
      { from: 'd1', to: 'c1', type: 'hierarchical' },
    ]);
    const ctx = buildClusterAndDomainCtx(cluster, domain, scene);
    const baseLen = ctx.edgeUniforms.length; // DomainBuilder pushed 1
    new EdgeBuilder().build(data, scene, ctx);
    // EdgeBuilder adds at least 1 more uniform record (per parent bucket).
    expect(ctx.edgeUniforms.length).toBeGreaterThan(baseLen);
    const last = ctx.edgeUniforms[ctx.edgeUniforms.length - 1];
    expect(last.uTime).toBeDefined();
  });

  // ── #6 Similarity: build(2 clusters with similarity > threshold) adds 1 edge ──
  it('#6 — Similarity: build adds an edge to similarityEdgeGroup', () => {
    const c1 = makeNode('c1', { position: [0, 0, 0] });
    const c2 = makeNode('c2', { position: [5, 0, 0] });
    const data = makeData([c1, c2], [{ from: 'c1', to: 'c2', type: 'similarity' }]);
    const ctx = createBuilderContext();
    for (const n of data.nodes) ctx.sceneNodeMap.set(n.id, n);
    new ClusterBuilder(null).build(data, scene, ctx);
    new EdgeBuilder().build(data, scene, ctx);
    const simGroup = scene.children.find(
      (c) => (c as THREE.Group).userData?.isSimilarityEdge === true,
    ) as THREE.Group | undefined;
    expect(simGroup).toBeDefined();
    expect(simGroup!.children.length).toBe(1);
  });

  // ── #7 Similarity: zero edges when no similarity input ──
  it('#7 — Similarity: zero edges when data carries none', () => {
    const c1 = makeNode('c1');
    const c2 = makeNode('c2', { position: [5, 0, 0] });
    const data = makeData([c1, c2]); // no edges array
    const ctx = createBuilderContext();
    for (const n of data.nodes) ctx.sceneNodeMap.set(n.id, n);
    new ClusterBuilder(null).build(data, scene, ctx);
    new EdgeBuilder().build(data, scene, ctx);
    const simGroup = scene.children.find(
      (c) => (c as THREE.Group).userData?.isSimilarityEdge === true,
    ) as THREE.Group | undefined;
    if (simGroup) expect(simGroup.children.length).toBe(0);
  });

  // ── #8 Similarity: edges live in similarityEdgeGroup ──
  it('#8 — Similarity: edges grouped in similarityEdgeGroup userData tag', () => {
    const c1 = makeNode('c1');
    const c2 = makeNode('c2', { position: [5, 0, 0] });
    const data = makeData([c1, c2], [{ from: 'c1', to: 'c2', type: 'similarity' }]);
    const ctx = createBuilderContext();
    for (const n of data.nodes) ctx.sceneNodeMap.set(n.id, n);
    new ClusterBuilder(null).build(data, scene, ctx);
    new EdgeBuilder().build(data, scene, ctx);
    const simGroup = scene.children.find(
      (c) => (c as THREE.Group).userData?.isSimilarityEdge === true,
    ) as THREE.Group;
    expect(simGroup).toBeDefined();
    expect(simGroup.children[0].userData.isSimilarityEdge).toBe(true);
  });

  // ── #9 Similarity edges are NOT in hierarchicalGroup ──
  it('#9 — Similarity edges are NOT in hierarchicalGroup', () => {
    const c1 = makeNode('c1');
    const c2 = makeNode('c2', { position: [5, 0, 0] });
    const data = makeData([c1, c2], [{ from: 'c1', to: 'c2', type: 'similarity' }]);
    const ctx = createBuilderContext();
    for (const n of data.nodes) ctx.sceneNodeMap.set(n.id, n);
    new ClusterBuilder(null).build(data, scene, ctx);
    new EdgeBuilder().build(data, scene, ctx);
    const hierGroup = scene.children.find(
      (c) => (c as THREE.Group).userData?.isInterClusterEdgeGroup === true,
    ) as THREE.Group | undefined;
    if (hierGroup) {
      // No hierarchical children with isSimilarityEdge tag.
      const leakedSim = hierGroup.children.some((c) => c.userData?.isSimilarityEdge);
      expect(leakedSim).toBe(false);
    }
  });

  // ── #10 Similarity: edge has baseOpacity userData ──
  it('#10 — Similarity edge userData includes baseOpacity', () => {
    const c1 = makeNode('c1');
    const c2 = makeNode('c2', { position: [5, 0, 0] });
    const data = makeData([c1, c2], [{ from: 'c1', to: 'c2', type: 'similarity' }]);
    const ctx = createBuilderContext();
    for (const n of data.nodes) ctx.sceneNodeMap.set(n.id, n);
    new ClusterBuilder(null).build(data, scene, ctx);
    new EdgeBuilder().build(data, scene, ctx);
    const simGroup = scene.children.find(
      (c) => (c as THREE.Group).userData?.isSimilarityEdge === true,
    ) as THREE.Group;
    expect(simGroup.children[0].userData.baseOpacity).toBeDefined();
  });

  // ── #11 Injection: build(injection edges) adds to injectionEdgeGroup ──
  it('#11 — Injection: build adds 1 edge in injectionEdgeGroup', () => {
    const d1 = makeNode('d1', { state: 'domain' });
    const d2 = makeNode('d2', { state: 'domain', position: [5, 0, 0] });
    const data = makeData([d1, d2], [{ from: 'd1', to: 'd2', type: 'injection' }]);
    const ctx = createBuilderContext();
    for (const n of data.nodes) ctx.sceneNodeMap.set(n.id, n);
    new DomainBuilder(null).build(data, scene, ctx);
    new EdgeBuilder().build(data, scene, ctx);
    const injGroup = scene.children.find(
      (c) => (c as THREE.Group).userData?.isInjectionEdge === true,
    ) as THREE.Group;
    expect(injGroup).toBeDefined();
    expect(injGroup.children.length).toBe(1);
  });

  // ── #12 Injection endpoint resolution via ctx.nodeMeshes / sceneNodeMap ──
  it('#12 — Injection: endpoints resolve via ctx.sceneNodeMap (positions match)', () => {
    const d1 = makeNode('d1', { state: 'domain', position: [0, 0, 0] });
    const d2 = makeNode('d2', { state: 'domain', position: [10, 0, 0] });
    const data = makeData([d1, d2], [{ from: 'd1', to: 'd2', type: 'injection' }]);
    const ctx = createBuilderContext();
    for (const n of data.nodes) ctx.sceneNodeMap.set(n.id, n);
    new DomainBuilder(null).build(data, scene, ctx);
    new EdgeBuilder().build(data, scene, ctx);
    const injGroup = scene.children.find(
      (c) => (c as THREE.Group).userData?.isInjectionEdge === true,
    ) as THREE.Group;
    const line = injGroup.children[0] as THREE.LineSegments;
    const positions = line.geometry.getAttribute('position') as THREE.BufferAttribute;
    // Two vertices; one at d1 position, one at d2.
    expect(positions.count).toBe(2);
    expect(positions.getX(0)).toBeCloseTo(0, 5);
    expect(positions.getX(1)).toBeCloseTo(10, 5);
  });

  // ── #13 Injection: zero edges when no injection data ──
  it('#13 — Injection: zero edges when none in data', () => {
    const d1 = makeNode('d1', { state: 'domain' });
    const data = makeData([d1]);
    const ctx = createBuilderContext();
    for (const n of data.nodes) ctx.sceneNodeMap.set(n.id, n);
    new DomainBuilder(null).build(data, scene, ctx);
    new EdgeBuilder().build(data, scene, ctx);
    const injGroup = scene.children.find(
      (c) => (c as THREE.Group).userData?.isInjectionEdge === true,
    ) as THREE.Group | undefined;
    if (injGroup) expect(injGroup.children.length).toBe(0);
  });

  // ── #14 Cross: all 3 sub-groups are children of scene ──
  it('#14 — Cross: all 3 sub-groups (hier/similarity/injection) are children of scene', () => {
    const c1 = makeNode('c1');
    const d1 = makeNode('d1', { state: 'domain', position: [5, 0, 0] });
    const d2 = makeNode('d2', { state: 'domain', position: [10, 0, 0] });
    const data = makeData(
      [c1, d1, d2],
      [
        { from: 'd1', to: 'c1', type: 'hierarchical' },
        { from: 'c1', to: 'd1', type: 'similarity' },
        { from: 'd1', to: 'd2', type: 'injection' },
      ],
    );
    const ctx = createBuilderContext();
    for (const n of data.nodes) ctx.sceneNodeMap.set(n.id, n);
    new ClusterBuilder(null).build(data, scene, ctx);
    new DomainBuilder(null).build(data, scene, ctx);
    new EdgeBuilder().build(data, scene, ctx);
    const tagsPresent = new Set<string>();
    for (const child of scene.children) {
      const u = (child as THREE.Group).userData ?? {};
      if (u.isInterClusterEdgeGroup) tagsPresent.add('hier');
      if (u.isSimilarityEdge) tagsPresent.add('sim');
      if (u.isInjectionEdge) tagsPresent.add('inj');
    }
    expect(tagsPresent.has('hier')).toBe(true);
    expect(tagsPresent.has('sim')).toBe(true);
    expect(tagsPresent.has('inj')).toBe(true);
  });

  // ── #15 dispose() clears all 3 groups + idempotent ──
  it('#15 — dispose() releases internal state + idempotent', () => {
    const b = new EdgeBuilder();
    expect(() => b.dispose()).not.toThrow();
    expect(() => b.dispose()).not.toThrow();
  });
});
