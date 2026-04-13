import { describe, it, expect, beforeEach } from 'vitest';
import { buildSceneData, assignLodVisibility, computeHierarchicalOpacity, type SceneNode } from './TopologyData';
import type { ClusterNode } from '$lib/api/clusters';
import { domainStore } from '$lib/stores/domains.svelte';

function makeNode(overrides: Partial<ClusterNode> = {}): ClusterNode {
  return {
    id: 'node-1',
    parent_id: null,
    label: 'Test',
    state: 'active',
    domain: 'backend',
    task_type: 'coding',
    persistence: 0.8,
    coherence: 0.9,
    separation: 0.85,
    stability: 0.7,
    member_count: 10,
    usage_count: 5,
    avg_score: null,
    color_hex: '#b44aff',
    umap_x: 1.0,
    umap_y: 2.0,
    umap_z: 3.0,
    preferred_strategy: null,
    output_coherence: null, blend_w_raw: null, blend_w_optimized: null,
    blend_w_transform: null, split_failures: 0,
    created_at: null,
    children: [],
    ...overrides,
  };
}

describe('buildSceneData', () => {
  beforeEach(() => {
    domainStore._reset();
    // Populate domain store for tests that rely on domain color resolution
    domainStore.domains = [
      { id: 'd1', label: 'backend', color_hex: '#b44aff', member_count: 0, avg_score: null, source: 'seed' },
      { id: 'd2', label: 'frontend', color_hex: '#ff4895', member_count: 0, avg_score: null, source: 'seed' },
      { id: 'd3', label: 'general', color_hex: '#7a7a9e', member_count: 0, avg_score: null, source: 'seed' },
    ];
  });

  it('creates SceneNode for each tree node', () => {
    const tree = [makeNode({ id: 'a' }), makeNode({ id: 'b' })];
    const result = buildSceneData(tree);
    expect(result.nodes).toHaveLength(2);
    expect(result.nodes[0].id).toBe('a');
  });

  it('handles flat list with parent_id references', () => {
    // Backend get_tree returns flat list — children linked via parent_id
    const flat = [
      makeNode({ id: 'parent' }),
      makeNode({ id: 'child', parent_id: 'parent' }),
    ];
    const result = buildSceneData(flat);
    expect(result.nodes).toHaveLength(2);
  });

  it('creates hierarchical edges from parent_id', () => {
    const flat = [
      makeNode({ id: 'parent', domain: 'backend' }),
      makeNode({ id: 'child', parent_id: 'parent', domain: 'frontend' }),
    ];
    const result = buildSceneData(flat);
    // 1 hierarchical edge only (different domains → no similarity edge)
    expect(result.edges).toHaveLength(1);
    expect(result.edges[0]).toMatchObject({ from: 'parent', to: 'child', type: 'hierarchical' });
  });

  it('produces no similarity or injection edges when not passed', () => {
    const flat = [
      makeNode({ id: 'a', domain: 'backend' }),
      makeNode({ id: 'b', domain: 'backend' }),
      makeNode({ id: 'c', domain: 'frontend' }),
    ];
    const result = buildSceneData(flat);
    // Without explicit similarity/injection edge arrays, only hierarchical edges appear
    expect(result.edges).toHaveLength(0);
    expect(result.edges.filter(e => e.type === 'similarity')).toHaveLength(0);
    expect(result.edges.filter(e => e.type === 'injection')).toHaveLength(0);
  });

  it('adds similarity edges when passed and both nodes exist in scene', () => {
    const flat = [
      makeNode({ id: 'a', domain: 'backend' }),
      makeNode({ id: 'b', domain: 'frontend' }),
    ];
    const simEdges = [{ from_id: 'a', to_id: 'b', similarity: 0.75 }];
    const result = buildSceneData(flat, simEdges);
    const sim = result.edges.filter(e => e.type === 'similarity');
    expect(sim).toHaveLength(1);
    expect(sim[0]).toEqual({ from: 'a', to: 'b', type: 'similarity' });
  });

  it('filters similarity edges where a node is missing from scene', () => {
    const flat = [makeNode({ id: 'a' })];
    const simEdges = [{ from_id: 'a', to_id: 'missing', similarity: 0.8 }];
    const result = buildSceneData(flat, simEdges);
    expect(result.edges.filter(e => e.type === 'similarity')).toHaveLength(0);
  });

  it('adds injection edges when passed and both nodes exist in scene', () => {
    const flat = [
      makeNode({ id: 'src' }),
      makeNode({ id: 'tgt' }),
    ];
    const injEdges = [{ source_id: 'src', target_id: 'tgt', weight: 3 }];
    const result = buildSceneData(flat, undefined, injEdges);
    const inj = result.edges.filter(e => e.type === 'injection');
    expect(inj).toHaveLength(1);
    expect(inj[0]).toEqual({ from: 'src', to: 'tgt', type: 'injection' });
  });

  it('filters injection edges where a node is missing from scene', () => {
    const flat = [makeNode({ id: 'src' })];
    const injEdges = [{ source_id: 'src', target_id: 'missing', weight: 1 }];
    const result = buildSceneData(flat, undefined, injEdges);
    expect(result.edges.filter(e => e.type === 'injection')).toHaveLength(0);
  });

  it('defaults persistence to 0.5 when node has null persistence', () => {
    const tree = [makeNode({ persistence: null as unknown as number })];
    const result = buildSceneData(tree);
    expect(result.nodes[0].persistence).toBe(0.5);
  });

  it('uses fallback position when UMAP coords are null', () => {
    const tree = [makeNode({ umap_x: null, umap_y: null, umap_z: null })];
    const result = buildSceneData(tree);
    // Should have deterministic fallback, not NaN
    expect(Number.isFinite(result.nodes[0].position[0])).toBe(true);
  });

  it('converts null label to empty string', () => {
    const tree = [makeNode({ label: null as unknown as string })];
    const result = buildSceneData(tree);
    expect(result.nodes[0].label).toBe('');
  });

  it('uses domain color as fallback when color_hex is null', () => {
    const tree = [makeNode({ color_hex: null, domain: 'backend' })];
    const result = buildSceneData(tree);
    expect(result.nodes[0].color).toBe('#b44aff'); // backend domain color
  });

  it('uses general fallback for unknown domain with null color_hex', () => {
    const tree = [makeNode({ color_hex: null, domain: 'unknown-domain' })];
    const result = buildSceneData(tree);
    expect(result.nodes[0].color).toBe('#7a7a9e'); // FALLBACK_COLOR
  });

  it('handles empty input array', () => {
    const result = buildSceneData([]);
    expect(result.nodes).toHaveLength(0);
    expect(result.edges).toHaveLength(0);
  });
});

describe('assignLodVisibility', () => {
  it('hides low-persistence nodes at far LOD', () => {
    const nodes: SceneNode[] = [
      { id: 'a', position: [0,0,0], color: '#fff', size: 1, opacity: 1, persistence: 0.9, state: 'active', label: 'A', visible: true, coherence: 0.5, avgScore: null, domain: 'general', memberCount: 0, isSubDomain: false, parentDomainLabel: null },
      { id: 'b', position: [1,1,1], color: '#fff', size: 1, opacity: 1, persistence: 0.2, state: 'active', label: 'B', visible: true, coherence: 0.5, avgScore: null, domain: 'general', memberCount: 0, isSubDomain: false, parentDomainLabel: null },
    ];
    assignLodVisibility(nodes, 'far');
    expect(nodes[0].visible).toBe(true);
    expect(nodes[1].visible).toBe(false);
  });

  it('shows all nodes at near LOD', () => {
    const nodes: SceneNode[] = [
      { id: 'a', position: [0,0,0], color: '#fff', size: 1, opacity: 1, persistence: 0.1, state: 'active', label: 'A', visible: false, coherence: 0.5, avgScore: null, domain: 'general', memberCount: 0, isSubDomain: false, parentDomainLabel: null },
    ];
    assignLodVisibility(nodes, 'near');
    expect(nodes[0].visible).toBe(true);
  });

  it('shows nodes with threshold-level persistence at mid LOD', () => {
    const nodes: SceneNode[] = [
      { id: 'a', position: [0,0,0], color: '#fff', size: 1, opacity: 1, persistence: 0.3, state: 'active', label: 'A', visible: false, coherence: 0.5, avgScore: null, domain: 'general', memberCount: 0, isSubDomain: false, parentDomainLabel: null },
      { id: 'b', position: [1,1,1], color: '#fff', size: 1, opacity: 1, persistence: 0.1, state: 'active', label: 'B', visible: false, coherence: 0.5, avgScore: null, domain: 'general', memberCount: 0, isSubDomain: false, parentDomainLabel: null },
    ];
    assignLodVisibility(nodes, 'mid');
    expect(nodes[0].visible).toBe(true);   // 0.3 >= 0.2
    expect(nodes[1].visible).toBe(false);  // 0.1 < 0.2
  });
});

describe('buildSceneData — state-based visual encoding', () => {
  it('template state nodes get size multiplied by 1.3', () => {
    const baseNode = makeNode({ id: 'base', state: 'active', member_count: 4, usage_count: 2 });
    const templateNode = makeNode({ id: 'tmpl', state: 'template', member_count: 4, usage_count: 2 });

    const { nodes } = buildSceneData([baseNode, templateNode]);
    const base = nodes.find(n => n.id === 'base')!;
    const template = nodes.find(n => n.id === 'tmpl')!;

    expect(template.size).toBeCloseTo(base.size * 1.3, 5);
  });

  it('mature state nodes get size multiplied by 1.15', () => {
    const baseNode = makeNode({ id: 'base', state: 'active', member_count: 4, usage_count: 2 });
    const matureNode = makeNode({ id: 'mat', state: 'mature', member_count: 4, usage_count: 2 });

    const { nodes } = buildSceneData([baseNode, matureNode]);
    const base = nodes.find(n => n.id === 'base')!;
    const mature = nodes.find(n => n.id === 'mat')!;

    expect(mature.size).toBeCloseTo(base.size * 1.15, 5);
  });

  it('candidate state nodes get opacity 0.4', () => {
    const { nodes } = buildSceneData([makeNode({ state: 'candidate' })]);
    expect(nodes[0].opacity).toBe(0.4);
  });

  it('active state nodes get opacity 1.0', () => {
    const { nodes } = buildSceneData([makeNode({ state: 'active' })]);
    expect(nodes[0].opacity).toBe(1.0);
  });

  it('template state nodes get color #00e5ff regardless of color_hex', () => {
    const withHex = makeNode({ id: 'a', state: 'template', color_hex: '#b44aff' });
    const withNull = makeNode({ id: 'b', state: 'template', color_hex: null });

    const { nodes } = buildSceneData([withHex, withNull]);
    expect(nodes.find(n => n.id === 'a')!.color).toBe('#00e5ff');
    expect(nodes.find(n => n.id === 'b')!.color).toBe('#00e5ff');
  });

  it('SceneNode always has an opacity field', () => {
    const { nodes } = buildSceneData([makeNode()]);
    expect(nodes[0]).toHaveProperty('opacity');
    expect(typeof nodes[0].opacity).toBe('number');
  });
});

describe('buildSceneData — edge distance', () => {
  it('computes distance on hierarchical edges', () => {
    const parent = makeNode({
      id: 'parent', state: 'domain',
      umap_x: 0, umap_y: 0, umap_z: 0,
    });
    const child = makeNode({
      id: 'child', parent_id: 'parent',
      umap_x: 0.3, umap_y: 0.4, umap_z: 0,
    });
    const { edges } = buildSceneData([parent, child]);
    const hier = edges.find(e => e.type === 'hierarchical')!;
    expect(hier.distance).toBeDefined();
    // UMAP_SCALE=10, so positions are (0,0,0) and (3,4,0), distance = 5
    expect(hier.distance).toBeCloseTo(5.0, 1);
  });

  it('does not set distance on similarity edges', () => {
    const a = makeNode({ id: 'a' });
    const b = makeNode({ id: 'b' });
    const simEdges = [{ from_id: 'a', to_id: 'b', similarity: 0.8 }];
    const { edges } = buildSceneData([a, b], simEdges);
    const sim = edges.find(e => e.type === 'similarity')!;
    expect(sim.distance).toBeUndefined();
  });
});

describe('buildSceneData — quality encoding', () => {
  beforeEach(() => {
    domainStore._reset();
    domainStore.domains = [
      { id: 'd1', label: 'backend', color_hex: '#b44aff', member_count: 0, avg_score: null, source: 'seed' },
    ];
  });

  it('populates coherence from ClusterNode, defaults to 0.5 when null', () => {
    const withCoherence = makeNode({ id: 'a', coherence: 0.9 });
    const withNull = makeNode({ id: 'b', coherence: null });
    const { nodes } = buildSceneData([withCoherence, withNull]);
    expect(nodes.find(n => n.id === 'a')!.coherence).toBe(0.9);
    expect(nodes.find(n => n.id === 'b')!.coherence).toBe(0.5);
  });

  it('populates avgScore from ClusterNode, preserves null', () => {
    const withScore = makeNode({ id: 'a', avg_score: 7.5 });
    const withNull = makeNode({ id: 'b', avg_score: null });
    const { nodes } = buildSceneData([withScore, withNull]);
    expect(nodes.find(n => n.id === 'a')!.avgScore).toBe(7.5);
    expect(nodes.find(n => n.id === 'b')!.avgScore).toBeNull();
  });
});

describe('computeHierarchicalOpacity', () => {
  // Base=1.0, CAP=10. Density is the primary opacity control; depth adds 25% reduction.
  it('returns full opacity for small domains (≤10 children)', () => {
    expect(computeHierarchicalOpacity(1)).toBeCloseTo(1.0);
    expect(computeHierarchicalOpacity(5)).toBeCloseTo(1.0);
    expect(computeHierarchicalOpacity(10)).toBeCloseTo(1.0);
  });

  it('reduces opacity for dense domains', () => {
    expect(computeHierarchicalOpacity(20)).toBeCloseTo(0.5);
    expect(computeHierarchicalOpacity(40)).toBeCloseTo(0.25);
  });

  it('handles zero/negative gracefully', () => {
    expect(computeHierarchicalOpacity(0)).toBeCloseTo(1.0);
    expect(computeHierarchicalOpacity(-1)).toBeCloseTo(1.0);
  });
});
