import { describe, it, expect } from 'vitest';
import { buildSceneData, assignLodVisibility, type SceneNode } from './TopologyData';
import type { ClusterNode } from '$lib/api/clusters';

function makeNode(overrides: Partial<ClusterNode> = {}): ClusterNode {
  return {
    id: 'node-1',
    parent_id: null,
    label: 'Test',
    state: 'confirmed',
    domain: 'backend',
    task_type: 'coding',
    persistence: 0.8,
    coherence: 0.9,
    separation: 0.85,
    stability: 0.7,
    member_count: 10,
    usage_count: 5,
    avg_score: null,
    color_hex: '#a855f7',
    umap_x: 1.0,
    umap_y: 2.0,
    umap_z: 3.0,
    preferred_strategy: null,
    created_at: null,
    children: [],
    ...overrides,
  };
}

describe('buildSceneData', () => {
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
      makeNode({ id: 'parent' }),
      makeNode({ id: 'child', parent_id: 'parent' }),
    ];
    const result = buildSceneData(flat);
    expect(result.edges).toHaveLength(1);
    expect(result.edges[0]).toEqual({ from: 'parent', to: 'child', type: 'hierarchical' });
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

  it('uses fallback color for null color_hex', () => {
    const tree = [makeNode({ color_hex: null })];
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
      { id: 'a', position: [0,0,0], color: '#fff', size: 1, opacity: 1, persistence: 0.9, state: 'confirmed', label: 'A', visible: true },
      { id: 'b', position: [1,1,1], color: '#fff', size: 1, opacity: 1, persistence: 0.2, state: 'confirmed', label: 'B', visible: true },
    ];
    assignLodVisibility(nodes, 'far');
    expect(nodes[0].visible).toBe(true);
    expect(nodes[1].visible).toBe(false);
  });

  it('shows all nodes at near LOD', () => {
    const nodes: SceneNode[] = [
      { id: 'a', position: [0,0,0], color: '#fff', size: 1, opacity: 1, persistence: 0.1, state: 'confirmed', label: 'A', visible: false },
    ];
    assignLodVisibility(nodes, 'near');
    expect(nodes[0].visible).toBe(true);
  });

  it('shows nodes with threshold-level persistence at mid LOD', () => {
    const nodes: SceneNode[] = [
      { id: 'a', position: [0,0,0], color: '#fff', size: 1, opacity: 1, persistence: 0.3, state: 'confirmed', label: 'A', visible: false },
      { id: 'b', position: [1,1,1], color: '#fff', size: 1, opacity: 1, persistence: 0.2, state: 'confirmed', label: 'B', visible: false },
    ];
    assignLodVisibility(nodes, 'mid');
    expect(nodes[0].visible).toBe(true);   // 0.3 >= 0.3
    expect(nodes[1].visible).toBe(false);  // 0.2 < 0.3
  });
});

describe('buildSceneData — state-based visual encoding', () => {
  it('template state nodes get size multiplied by 1.5', () => {
    const baseNode = makeNode({ id: 'base', state: 'active', member_count: 4, usage_count: 2 });
    const templateNode = makeNode({ id: 'tmpl', state: 'template', member_count: 4, usage_count: 2 });

    const { nodes } = buildSceneData([baseNode, templateNode]);
    const base = nodes.find(n => n.id === 'base')!;
    const template = nodes.find(n => n.id === 'tmpl')!;

    expect(template.size).toBeCloseTo(base.size * 1.5, 5);
  });

  it('mature state nodes get size multiplied by 1.2', () => {
    const baseNode = makeNode({ id: 'base', state: 'active', member_count: 4, usage_count: 2 });
    const matureNode = makeNode({ id: 'mat', state: 'mature', member_count: 4, usage_count: 2 });

    const { nodes } = buildSceneData([baseNode, matureNode]);
    const base = nodes.find(n => n.id === 'base')!;
    const mature = nodes.find(n => n.id === 'mat')!;

    expect(mature.size).toBeCloseTo(base.size * 1.2, 5);
  });

  it('candidate state nodes get opacity 0.4', () => {
    const { nodes } = buildSceneData([makeNode({ state: 'candidate' })]);
    expect(nodes[0].opacity).toBe(0.4);
  });

  it('active state nodes get opacity 1.0', () => {
    const { nodes } = buildSceneData([makeNode({ state: 'active' })]);
    expect(nodes[0].opacity).toBe(1.0);
  });

  it('confirmed state nodes get opacity 1.0', () => {
    const { nodes } = buildSceneData([makeNode({ state: 'confirmed' })]);
    expect(nodes[0].opacity).toBe(1.0);
  });

  it('template state nodes get color #00e5ff regardless of color_hex', () => {
    const withHex = makeNode({ id: 'a', state: 'template', color_hex: '#a855f7' });
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
