import { describe, it, expect } from 'vitest';
import { buildSceneData, assignLodVisibility, type SceneNode } from './TopologyData';
import type { TaxonomyNode } from '$lib/api/taxonomy';

function makeNode(overrides: Partial<TaxonomyNode> = {}): TaxonomyNode {
  return {
    id: 'node-1',
    parent_id: null,
    label: 'Test',
    state: 'confirmed',
    persistence: 0.8,
    coherence: 0.9,
    separation: 0.85,
    stability: 0.7,
    member_count: 10,
    usage_count: 5,
    color_hex: '#a855f7',
    umap_x: 1.0,
    umap_y: 2.0,
    umap_z: 3.0,
    children: [],
    families: [],
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

  it('uses fallback position when UMAP coords are null', () => {
    const tree = [makeNode({ umap_x: null, umap_y: null, umap_z: null })];
    const result = buildSceneData(tree);
    // Should have deterministic fallback, not NaN
    expect(Number.isFinite(result.nodes[0].position[0])).toBe(true);
  });
});

describe('assignLodVisibility', () => {
  it('hides low-persistence nodes at far LOD', () => {
    const nodes: SceneNode[] = [
      { id: 'a', position: [0,0,0], color: '#fff', size: 1, persistence: 0.9, state: 'confirmed', label: 'A', visible: true },
      { id: 'b', position: [1,1,1], color: '#fff', size: 1, persistence: 0.2, state: 'confirmed', label: 'B', visible: true },
    ];
    assignLodVisibility(nodes, 'far');
    expect(nodes[0].visible).toBe(true);
    expect(nodes[1].visible).toBe(false);
  });

  it('shows all nodes at near LOD', () => {
    const nodes: SceneNode[] = [
      { id: 'a', position: [0,0,0], color: '#fff', size: 1, persistence: 0.1, state: 'confirmed', label: 'A', visible: false },
    ];
    assignLodVisibility(nodes, 'near');
    expect(nodes[0].visible).toBe(true);
  });
});
