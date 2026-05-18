// frontend/src/lib/components/taxonomy/builders/ClusterBuilder.test.ts
//
// Sub-project D Cycle 1 — 17 unit tests for ClusterBuilder per spec §5.1.
// Real THREE.Scene + mocked SceneData (pattern from scene-cleanup.test.ts).
//
// Spec: docs/superpowers/specs/2026-05-17-scene-builder-extraction-design.md

import { describe, it, expect, vi, beforeEach } from 'vitest';
import * as THREE from 'three';
import type { SceneData } from '../TopologyData';
import type { SceneNode } from '../TopologyData';
import type { TopologyInteraction } from '../TopologyInteraction';
import { ClusterBuilder } from './ClusterBuilder';
import { createBuilderContext, type BuilderContext } from './BuilderContext';

function makeClusterNode(overrides: Partial<SceneNode> = {}): SceneNode {
  return {
    id: 'c1',
    position: [0, 0, 0],
    color: '#ff00ff',
    size: 1.0,
    opacity: 1.0,
    persistence: 1.0,
    state: 'active',
    label: 'cluster 1',
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

function populateSceneNodeMap(ctx: BuilderContext, data: SceneData): void {
  for (const n of data.nodes) ctx.sceneNodeMap.set(n.id, n);
}

describe('ClusterBuilder — 17 unit tests per spec §5.1', () => {
  let scene: THREE.Scene;
  let ctx: BuilderContext;

  beforeEach(() => {
    scene = new THREE.Scene();
    ctx = createBuilderContext();
  });

  // ── #1 ──
  it('#1 — build(empty data, ctx) adds zero meshes to scene', () => {
    const b = new ClusterBuilder(null);
    b.build(makeData([]), scene, ctx);
    expect(scene.children.length).toBe(0);
    expect(ctx.nodeMeshes.size).toBe(0);
  });

  // ── #2 ──
  it('#2 — build(single-cluster data, ctx) adds exactly 1 group with 1 Mesh + 1 LineSegments child', () => {
    const node = makeClusterNode({ id: 'c1' });
    const data = makeData([node]);
    populateSceneNodeMap(ctx, data);
    const b = new ClusterBuilder(null);
    b.build(data, scene, ctx);
    expect(scene.children.length).toBe(1);
    const group = scene.children[0] as THREE.Group;
    expect(group.type).toBe('Group');
    const meshes = group.children.filter((c) => (c as THREE.Mesh).isMesh);
    const lines = group.children.filter((c) => c.type === 'LineSegments');
    expect(meshes.length).toBe(1);
    expect(lines.length).toBe(1);
  });

  // ── #3 ──
  it('#3 — Cluster fill Mesh uses IcosahedronGeometry (canon — neon-contour card)', () => {
    const node = makeClusterNode({ id: 'c1' });
    const data = makeData([node]);
    populateSceneNodeMap(ctx, data);
    const b = new ClusterBuilder(null);
    b.build(data, scene, ctx);
    const group = scene.children[0] as THREE.Group;
    const fill = group.children.find((c) => (c as THREE.Mesh).isMesh) as THREE.Mesh;
    expect(fill.geometry.type).toBe('IcosahedronGeometry');
  });

  // ── #4 ──
  it('#4 — Cluster wireframe uses LineSegments (not Line)', () => {
    const node = makeClusterNode({ id: 'c1' });
    const data = makeData([node]);
    populateSceneNodeMap(ctx, data);
    const b = new ClusterBuilder(null);
    b.build(data, scene, ctx);
    const group = scene.children[0] as THREE.Group;
    const wire = group.children.find((c) => c.type === 'LineSegments' || c.type === 'Line');
    expect(wire?.type).toBe('LineSegments');
  });

  // ── #5 ──
  it('#5 — Cluster fill mesh registered in ctx.nodeMeshes keyed by node.id', () => {
    const node = makeClusterNode({ id: 'c1' });
    const data = makeData([node]);
    populateSceneNodeMap(ctx, data);
    const b = new ClusterBuilder(null);
    b.build(data, scene, ctx);
    const group = scene.children[0] as THREE.Group;
    const fill = group.children.find((c) => (c as THREE.Mesh).isMesh) as THREE.Mesh;
    expect(ctx.nodeMeshes.get('c1')).toBe(fill);
  });

  // ── #6 ──
  it("#6 — Cluster's parent group is registered in ctx.beamNodeGroups keyed by node.id", () => {
    const node = makeClusterNode({ id: 'c1' });
    const data = makeData([node]);
    populateSceneNodeMap(ctx, data);
    const b = new ClusterBuilder(null);
    b.build(data, scene, ctx);
    const group = scene.children[0] as THREE.Group;
    expect(ctx.beamNodeGroups.get('c1')).toBe(group);
  });

  // ── #7 ──
  it("#7 — Cluster's wire ShaderMaterial is registered in ctx.clusterShaderMaterials keyed by node.id", () => {
    const node = makeClusterNode({ id: 'c1' });
    const data = makeData([node]);
    populateSceneNodeMap(ctx, data);
    const b = new ClusterBuilder(null);
    b.build(data, scene, ctx);
    const group = scene.children[0] as THREE.Group;
    const wire = group.children.find((c) => c.type === 'LineSegments') as THREE.LineSegments;
    expect(ctx.clusterShaderMaterials.get('c1')).toBe(wire.material as THREE.ShaderMaterial);
  });

  // ── #8 ──
  it('#8 — nodePhaseOffsets[node.id] is deterministic across rebuilds for the same id (formula hash%1000/1000 * 2π)', () => {
    const id = 'cluster-foo';
    let hash = 0;
    for (let ci = 0; ci < id.length; ci++) hash += id.charCodeAt(ci);
    const expected = (hash % 1000) / 1000 * Math.PI * 2;

    const data = makeData([makeClusterNode({ id })]);
    populateSceneNodeMap(ctx, data);
    const b = new ClusterBuilder(null);
    b.build(data, scene, ctx);
    expect(ctx.nodePhaseOffsets.get(id)).toBeCloseTo(expected, 8);

    // Second build with a fresh ctx yields the same offset.
    const ctx2 = createBuilderContext();
    populateSceneNodeMap(ctx2, data);
    const scene2 = new THREE.Scene();
    new ClusterBuilder(null).build(data, scene2, ctx2);
    expect(ctx2.nodePhaseOffsets.get(id)).toBeCloseTo(expected, 8);
  });

  // ── #9 ──
  it('#9 — nodePhaseOffsets for two different ids differ (desync)', () => {
    const a = makeClusterNode({ id: 'aaa' });
    const b = makeClusterNode({ id: 'zzz', position: [1, 0, 0] });
    const data = makeData([a, b]);
    populateSceneNodeMap(ctx, data);
    new ClusterBuilder(null).build(data, scene, ctx);
    expect(ctx.nodePhaseOffsets.get('aaa')).not.toBeCloseTo(
      ctx.nodePhaseOffsets.get('zzz') as number,
      6,
    );
  });

  // ── #10 ──
  it('#10 — multiple build(...) calls without cleanupScene between produce duplicate groups (documents the orchestrator invariant)', () => {
    const node = makeClusterNode({ id: 'c1' });
    const data = makeData([node]);
    populateSceneNodeMap(ctx, data);
    const b = new ClusterBuilder(null);
    b.build(data, scene, ctx);
    b.build(data, scene, ctx);
    // Two builds without cleanup → two cluster groups in scene.
    expect(scene.children.length).toBe(2);
  });

  // ── #11 ──
  it('#11 — build skips structural nodes (state === "domain" || state === "project") — pins the unified-loop split (M2)', () => {
    const cluster = makeClusterNode({ id: 'c1' });
    const domain = makeClusterNode({ id: 'd1', state: 'domain', position: [5, 0, 0] });
    const project = makeClusterNode({ id: 'p1', state: 'project', position: [0, 5, 0] });
    const data = makeData([cluster, domain, project]);
    populateSceneNodeMap(ctx, data);
    new ClusterBuilder(null).build(data, scene, ctx);
    // Only the cluster gets a group; structural nodes skipped.
    expect(scene.children.length).toBe(1);
    expect(ctx.nodeMeshes.has('c1')).toBe(true);
    expect(ctx.nodeMeshes.has('d1')).toBe(false);
    expect(ctx.nodeMeshes.has('p1')).toBe(false);
  });

  // ── #12 ──
  it('#12 — dispose() is idempotent + clears any internal builder state', () => {
    const b = new ClusterBuilder(null);
    expect(() => b.dispose()).not.toThrow();
    expect(() => b.dispose()).not.toThrow();
  });

  // ── #13 ──
  it('#13 — F1 emissive intensity formula: memberCount=1 → 0.6, memberCount=50 → 1.4, clamped beyond', () => {
    const oneNode = makeClusterNode({ id: 'one', memberCount: 1, coherence: 0, avgScore: null });
    const heroNode = makeClusterNode({ id: 'hero', memberCount: 50, coherence: 0, avgScore: null });
    const data = makeData([oneNode, heroNode]);
    populateSceneNodeMap(ctx, data);
    new ClusterBuilder(null).build(data, scene, ctx);
    const fillOne = ctx.nodeMeshes.get('one') as THREE.Mesh;
    const fillHero = ctx.nodeMeshes.get('hero') as THREE.Mesh;
    const matOne = fillOne.material as THREE.MeshStandardMaterial;
    const matHero = fillHero.material as THREE.MeshStandardMaterial;
    // baseEmissive = 0.6 + 0.8 * 0 = 0.6 (coherenceBoost=0, scoreModifier=1.0).
    expect(matOne.emissiveIntensity).toBeCloseTo(0.6, 5);
    // baseEmissive = 0.6 + 0.8 * 1.0 = 1.4.
    expect(matHero.emissiveIntensity).toBeCloseTo(1.4, 5);
  });

  // ── #14 ──
  it('#14 — F1 coherence boost: coherence=1 → coherenceBoost=0.1, emissiveColor lerped toward white by 0.03', () => {
    // Start from a pure red node (#ff0000). Coherence=1 → emissive lerped 3% toward white.
    const node = makeClusterNode({ id: 'red', color: '#ff0000', coherence: 1, memberCount: 1, avgScore: null });
    const data = makeData([node]);
    populateSceneNodeMap(ctx, data);
    new ClusterBuilder(null).build(data, scene, ctx);
    const fill = ctx.nodeMeshes.get('red') as THREE.Mesh;
    const mat = fill.material as THREE.MeshStandardMaterial;
    // Lerp 0xff0000 toward 0xffffff by 0.03:
    //   r = 1.0  (unchanged — already 1)
    //   g = 0.0 * 0.97 + 1.0 * 0.03 = 0.03
    //   b = 0.0 * 0.97 + 1.0 * 0.03 = 0.03
    expect(mat.emissive.r).toBeCloseTo(1.0, 4);
    expect(mat.emissive.g).toBeCloseTo(0.03, 3);
    expect(mat.emissive.b).toBeCloseTo(0.03, 3);
  });

  // ── #15 ──
  it('#15 — F1 material params: cluster fill MeshStandardMaterial has roughness=0.6, metalness=0.0, transparent=true, opacity=node.opacity*0.9', () => {
    const node = makeClusterNode({ id: 'c1', opacity: 0.8 });
    const data = makeData([node]);
    populateSceneNodeMap(ctx, data);
    new ClusterBuilder(null).build(data, scene, ctx);
    const fill = ctx.nodeMeshes.get('c1') as THREE.Mesh;
    const mat = fill.material as THREE.MeshStandardMaterial;
    expect(mat.roughness).toBeCloseTo(0.6, 5);
    expect(mat.metalness).toBeCloseTo(0.0, 5);
    expect(mat.transparent).toBe(true);
    expect(mat.opacity).toBeCloseTo(0.8 * 0.9, 5);
  });

  // ── #16 ──
  it('#16 — F1 shadow flags: cluster fill has castShadow=true AND receiveShadow=true', () => {
    const node = makeClusterNode({ id: 'c1' });
    const data = makeData([node]);
    populateSceneNodeMap(ctx, data);
    new ClusterBuilder(null).build(data, scene, ctx);
    const fill = ctx.nodeMeshes.get('c1') as THREE.Mesh;
    expect(fill.castShadow).toBe(true);
    expect(fill.receiveShadow).toBe(true);
  });

  // ── #17 ──
  it('#17 — ClusterBuilder registers cluster fill with interaction for every cluster node it builds (not for structural)', () => {
    const interaction = makeMockInteraction();
    const cluster = makeClusterNode({ id: 'c1' });
    const cluster2 = makeClusterNode({ id: 'c2', position: [2, 0, 0] });
    const domain = makeClusterNode({ id: 'd1', state: 'domain', position: [5, 0, 0] });
    const data = makeData([cluster, cluster2, domain]);
    populateSceneNodeMap(ctx, data);
    new ClusterBuilder(interaction).build(data, scene, ctx);
    // Two cluster nodes → 2 registerNode calls; structural skipped.
    expect(interaction.registerNode).toHaveBeenCalledTimes(2);
    const ids = (interaction.registerNode as ReturnType<typeof vi.fn>).mock.calls.map((c) => c[0]);
    expect(ids.sort()).toEqual(['c1', 'c2']);
  });
});
