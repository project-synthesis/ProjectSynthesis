// frontend/src/lib/components/taxonomy/builders/DomainBuilder.test.ts
//
// Sub-project D Cycle 2 — 15 unit tests for DomainBuilder per spec §5.1.
// Real THREE.Scene + mocked SceneData.
//
// Spec: docs/superpowers/specs/2026-05-17-scene-builder-extraction-design.md

import { describe, it, expect, vi, beforeEach } from 'vitest';
import * as THREE from 'three';
import type { SceneData, SceneNode } from '../TopologyData';
import type { TopologyInteraction } from '../TopologyInteraction';
import { DomainBuilder } from './DomainBuilder';
import { createBuilderContext, type BuilderContext } from './BuilderContext';

function makeStructuralNode(overrides: Partial<SceneNode> = {}): SceneNode {
  return {
    id: 'd1',
    position: [0, 0, 0],
    color: '#00ffaa',
    size: 1.5,
    opacity: 1.0,
    persistence: 1.0,
    state: 'domain',
    label: 'domain 1',
    visible: true,
    coherence: 0.5,
    avgScore: null,
    domain: 'backend',
    memberCount: 8,
    isSubDomain: false,
    template_count: 0,
    ...overrides,
  } as SceneNode;
}

function makeData(nodes: SceneNode[] = []): SceneData {
  return { nodes, edges: [] } as SceneData;
}

function makeMockInteraction(): TopologyInteraction & {
  registerNode: ReturnType<typeof vi.fn>;
} {
  return {
    registerNode: vi.fn(),
    clear: vi.fn(),
    dispose: vi.fn(),
  } as unknown as TopologyInteraction & { registerNode: ReturnType<typeof vi.fn> };
}

function populateMap(ctx: BuilderContext, data: SceneData): void {
  for (const n of data.nodes) ctx.sceneNodeMap.set(n.id, n);
}

describe('DomainBuilder — 15 unit tests per spec §5.1', () => {
  let scene: THREE.Scene;
  let ctx: BuilderContext;

  beforeEach(() => {
    scene = new THREE.Scene();
    ctx = createBuilderContext();
  });

  // ── #1 ──
  it('#1 — build(empty) adds zero meshes', () => {
    const b = new DomainBuilder(null);
    b.build(makeData([]), scene, ctx);
    expect(scene.children.length).toBe(0);
    expect(ctx.nodeMeshes.size).toBe(0);
    expect(ctx.domainGroups.length).toBe(0);
  });

  // ── #2 ──
  it('#2 — build(single-domain) adds 1 group with fill Mesh + EdgesGeometry LineSegments + vertex Points', () => {
    const node = makeStructuralNode({ id: 'd1' });
    const data = makeData([node]);
    populateMap(ctx, data);
    new DomainBuilder(null).build(data, scene, ctx);
    expect(scene.children.length).toBe(1);
    const group = scene.children[0] as THREE.Group;
    const meshes = group.children.filter((c) => (c as THREE.Mesh).isMesh);
    const lines = group.children.filter((c) => c.type === 'LineSegments');
    const points = group.children.filter((c) => c.type === 'Points');
    expect(meshes.length).toBe(1);
    expect(lines.length).toBe(1);
    expect(points.length).toBe(1);
  });

  // ── #3 ──
  it('#3 — Fill mesh uses DodecahedronGeometry', () => {
    const node = makeStructuralNode({ id: 'd1' });
    const data = makeData([node]);
    populateMap(ctx, data);
    new DomainBuilder(null).build(data, scene, ctx);
    const group = scene.children[0] as THREE.Group;
    const fill = group.children.find((c) => (c as THREE.Mesh).isMesh) as THREE.Mesh;
    expect(fill.geometry.type).toBe('DodecahedronGeometry');
  });

  // ── #4 ──
  it('#4 — Edges use EdgesGeometry (clean pentagonal outlines)', () => {
    const node = makeStructuralNode({ id: 'd1' });
    const data = makeData([node]);
    populateMap(ctx, data);
    new DomainBuilder(null).build(data, scene, ctx);
    const group = scene.children[0] as THREE.Group;
    const edges = group.children.find((c) => c.type === 'LineSegments') as THREE.LineSegments;
    expect(edges.geometry.type).toBe('EdgesGeometry');
  });

  // ── #5 ──
  it('#5 — Vertex points use THREE.Points with the canonical glow CanvasTexture (F2)', () => {
    const node = makeStructuralNode({ id: 'd1' });
    const data = makeData([node]);
    populateMap(ctx, data);
    new DomainBuilder(null).build(data, scene, ctx);
    const group = scene.children[0] as THREE.Group;
    const points = group.children.find((c) => c.type === 'Points') as THREE.Points;
    expect(points.type).toBe('Points');
    // PointsMaterial.map may be undefined in JSDOM (no canvas context); the
    // material existence is the load-bearing assertion — texture-truthy path
    // is exercised in INT tests with a stubbed canvas.
    const mat = points.material as THREE.PointsMaterial;
    expect(mat).toBeInstanceOf(THREE.PointsMaterial);
  });

  // ── #6 ──
  it('#6 — userData.isStructural = true on the domain group', () => {
    const node = makeStructuralNode({ id: 'd1' });
    const data = makeData([node]);
    populateMap(ctx, data);
    new DomainBuilder(null).build(data, scene, ctx);
    const group = scene.children[0] as THREE.Group;
    expect(group.userData.isStructural).toBe(true);
  });

  // ── #7 ──
  it('#7 — ctx.domainGroups contains the new group', () => {
    const node = makeStructuralNode({ id: 'd1' });
    const data = makeData([node]);
    populateMap(ctx, data);
    new DomainBuilder(null).build(data, scene, ctx);
    expect(ctx.domainGroups.length).toBe(1);
    expect(ctx.domainGroups[0]).toBe(scene.children[0]);
  });

  // ── #8 ──
  it('#8 — ctx.nodeMeshes[domain.id] is the fill mesh; nodePhaseOffsets follows hash formula', () => {
    const id = 'domain-foo';
    let hash = 0;
    for (let ci = 0; ci < id.length; ci++) hash += id.charCodeAt(ci);
    const expectedPhase = (hash % 1000) / 1000 * Math.PI * 2;

    const node = makeStructuralNode({ id });
    const data = makeData([node]);
    populateMap(ctx, data);
    new DomainBuilder(null).build(data, scene, ctx);
    const group = scene.children[0] as THREE.Group;
    const fill = group.children.find((c) => (c as THREE.Mesh).isMesh) as THREE.Mesh;
    expect(ctx.nodeMeshes.get(id)).toBe(fill);
    expect(ctx.nodePhaseOffsets.get(id)).toBeCloseTo(expectedPhase, 8);
  });

  // ── #9 ──
  it('#9 — Per-domain nodePhaseOffsets populated for both state=domain AND state=project (M2)', () => {
    const dom = makeStructuralNode({ id: 'd1', state: 'domain' });
    const proj = makeStructuralNode({ id: 'p1', state: 'project', position: [3, 0, 0] });
    const data = makeData([dom, proj]);
    populateMap(ctx, data);
    new DomainBuilder(null).build(data, scene, ctx);
    expect(ctx.nodePhaseOffsets.has('d1')).toBe(true);
    expect(ctx.nodePhaseOffsets.has('p1')).toBe(true);
  });

  // ── #10 ──
  it('#10 — build skips non-structural nodes (state !== domain && state !== project) — M2 split', () => {
    const dom = makeStructuralNode({ id: 'd1' });
    const cluster = makeStructuralNode({ id: 'c1', state: 'active', position: [3, 0, 0] });
    const data = makeData([dom, cluster]);
    populateMap(ctx, data);
    new DomainBuilder(null).build(data, scene, ctx);
    expect(ctx.nodeMeshes.has('d1')).toBe(true);
    expect(ctx.nodeMeshes.has('c1')).toBe(false);
    expect(scene.children.length).toBe(1);
  });

  // ── #11 ──
  it('#11 — PointsMaterial uses AdditiveBlending (canon F2)', () => {
    const node = makeStructuralNode({ id: 'd1' });
    const data = makeData([node]);
    populateMap(ctx, data);
    new DomainBuilder(null).build(data, scene, ctx);
    const group = scene.children[0] as THREE.Group;
    const points = group.children.find((c) => c.type === 'Points') as THREE.Points;
    const mat = points.material as THREE.PointsMaterial;
    expect(mat.blending).toBe(THREE.AdditiveBlending);
  });

  // ── #12 ──
  it('#12 — dispose() idempotent', () => {
    const b = new DomainBuilder(null);
    expect(() => b.dispose()).not.toThrow();
    expect(() => b.dispose()).not.toThrow();
  });

  // ── #13 ──
  it('#13 — Edge-uniform ownership: build(single-domain) pushes domain-edge uniforms with uTime IUniform', () => {
    const node = makeStructuralNode({ id: 'd1' });
    const data = makeData([node]);
    populateMap(ctx, data);
    new DomainBuilder(null).build(data, scene, ctx);
    expect(ctx.edgeUniforms.length).toBe(1);
    const u = ctx.edgeUniforms[0];
    expect(u).toBeDefined();
    expect(u.uTime).toBeDefined();
    // Confirm shape — IUniform has a `.value` field.
    expect((u.uTime as THREE.IUniform).value !== undefined).toBe(true);
  });

  // ── #14 ──
  it('#14 — F1 structural emissive intensity = 0.4 (coherenceBoost = 0 for structural)', () => {
    const node = makeStructuralNode({ id: 'd1', coherence: 1 });
    const data = makeData([node]);
    populateMap(ctx, data);
    new DomainBuilder(null).build(data, scene, ctx);
    const fill = ctx.nodeMeshes.get('d1') as THREE.Mesh;
    const mat = fill.material as THREE.MeshStandardMaterial;
    // Structural baseEmissive = 0.4; coherenceBoost = 0 (no boost on structural);
    // no scoreModifier path (avgScore=null gives 1.0).
    // Final = 0.4 * (1+0) * 1.0 = 0.4.
    expect(mat.emissiveIntensity).toBeCloseTo(0.4, 5);
  });

  // ── #15 ──
  it('#15 — DomainBuilder registers domain fill with interaction for every structural node', () => {
    const interaction = makeMockInteraction();
    const dom = makeStructuralNode({ id: 'd1' });
    const proj = makeStructuralNode({ id: 'p1', state: 'project', position: [3, 0, 0] });
    const cluster = makeStructuralNode({ id: 'c1', state: 'active', position: [6, 0, 0] });
    const data = makeData([dom, proj, cluster]);
    populateMap(ctx, data);
    new DomainBuilder(interaction).build(data, scene, ctx);
    expect(interaction.registerNode).toHaveBeenCalledTimes(2);
    const ids = (interaction.registerNode as ReturnType<typeof vi.fn>).mock.calls.map((c) => c[0]);
    expect(ids.sort()).toEqual(['d1', 'p1']);
  });
});
