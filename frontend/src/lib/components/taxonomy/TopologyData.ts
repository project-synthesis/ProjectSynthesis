/**
 * Transform API taxonomy tree data into scene-graph-ready structures.
 *
 * Pure functions — no Three.js dependency (just typed arrays and interfaces).
 * The renderer consumes SceneData to build Three.js objects.
 */
import type { ClusterNode } from '$lib/api/clusters';
import type { LODTier } from './TopologyRenderer';
import { taxonomyColor } from '$lib/utils/colors';

export interface SceneNode {
  id: string;
  position: [number, number, number];
  color: string;
  size: number;
  opacity: number;
  persistence: number;
  state: string;
  label: string;
  visible: boolean;
  parentId?: string;
}

/** Opacity by lifecycle state — candidates are translucent. */
function stateOpacity(state: string): number {
  return state === 'candidate' ? 0.4 : 1.0;
}

/** Size multiplier by lifecycle state — templates and mature nodes are larger. */
function stateSizeMultiplier(state: string): number {
  if (state === 'template') return 1.5;
  if (state === 'mature') return 1.2;
  return 1.0;
}

/** Color by lifecycle state — templates use neon-cyan override. */
function stateNodeColor(state: string, oklabColor: string | null): string {
  if (state === 'template') return '#00e5ff'; // neon-cyan override for templates
  return taxonomyColor(oklabColor); // existing logic handles hex/domain/null
}

export interface SceneEdge {
  from: string;
  to: string;
  type: 'hierarchical' | 'similarity';
}

export interface SceneData {
  nodes: SceneNode[];
  edges: SceneEdge[];
}

// LOD persistence thresholds: nodes below these are hidden
const LOD_THRESHOLDS: Record<LODTier, number> = {
  far: 0.6,
  mid: 0.3,
  near: 0.0,
};

/**
 * Convert flat taxonomy node list into scene-ready nodes and edges.
 * Backend `get_tree` returns a flat list — we build edges from `parent_id`.
 */
export function buildSceneData(flatNodes: ClusterNode[]): SceneData {
  const nodes: SceneNode[] = [];
  const edges: SceneEdge[] = [];

  for (const node of flatNodes) {
    // Deterministic fallback position for nodes without UMAP coords
    const x = node.umap_x ?? hashFloat(node.id, 0) * 20 - 10;
    const y = node.umap_y ?? hashFloat(node.id, 1) * 20 - 10;
    const z = node.umap_z ?? hashFloat(node.id, 2) * 20 - 10;

    // Size: blend member_count and usage_count, clamped
    const raw = Math.log2(Math.max(1, node.member_count + node.usage_count * 0.5));
    const size = Math.max(0.3, Math.min(3.0, raw * 0.5));

    nodes.push({
      id: node.id,
      position: [x, y, z],
      color: stateNodeColor(node.state, node.color_hex),
      size: size * stateSizeMultiplier(node.state),
      opacity: stateOpacity(node.state),
      persistence: node.persistence ?? 0.5,
      state: node.state,
      label: node.label ?? '',
      visible: true,
      parentId: node.parent_id ?? undefined,
    });

    // Hierarchical edges from parent_id
    if (node.parent_id) {
      edges.push({ from: node.parent_id, to: node.id, type: 'hierarchical' });
    }
  }

  return { nodes, edges };
}

/**
 * Update visibility flags based on LOD tier.
 * Mutates nodes in place for performance.
 */
export function assignLodVisibility(nodes: SceneNode[], tier: LODTier): void {
  const threshold = LOD_THRESHOLDS[tier];
  for (const node of nodes) {
    node.visible = node.persistence >= threshold;
  }
}

/** Deterministic float from string hash (0..1). */
function hashFloat(str: string, seed: number): number {
  let h = seed * 2654435761;
  for (let i = 0; i < str.length; i++) {
    h = ((h << 5) - h + str.charCodeAt(i)) | 0;
  }
  return ((h >>> 0) % 10000) / 10000;
}
