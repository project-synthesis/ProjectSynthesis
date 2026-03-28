/**
 * Transform API taxonomy tree data into scene-graph-ready structures.
 *
 * Pure functions — no Three.js dependency (just typed arrays and interfaces).
 * The renderer consumes SceneData to build Three.js objects.
 */
import type { ClusterNode } from '$lib/api/clusters';
import type { LODTier } from './TopologyRenderer';
import { taxonomyColor, stateColor } from '$lib/utils/colors';

/** Extract primary domain from "primary: qualifier" format. */
function parsePrimaryDomain(domain: string | null): string {
  if (!domain) return 'general';
  const idx = domain.indexOf(':');
  return idx >= 0 ? domain.substring(0, idx).trim().toLowerCase() : domain.toLowerCase();
}

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

/** Size multiplier by lifecycle state — domain hub nodes are 2x, templates 1.5x, mature 1.2x. */
function stateSizeMultiplier(state: string): number {
  if (state === 'domain') return 2.0;
  if (state === 'template') return 1.5;
  if (state === 'mature') return 1.2;
  return 1.0;
}

/** Color by lifecycle state — templates use neon-cyan override. */
function stateNodeColor(state: string, oklabColor: string | null): string {
  if (state === 'template') return stateColor('template');
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

// LOD persistence thresholds: nodes below these are hidden.
// Default persistence is 0.5 (from hot-path cluster creation), so FAR
// threshold must be <= 0.5 to show new clusters before cold-path runs.
const LOD_THRESHOLDS: Record<LODTier, number> = {
  far: 0.4,
  mid: 0.2,
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
    // Position: UMAP coords scaled to scene units, or hash-based fallback.
    // UMAP outputs ~[-1, 1]; multiply by 10 for comfortable spacing.
    const UMAP_SCALE = 10;
    const x = node.umap_x != null ? node.umap_x * UMAP_SCALE : hashFloat(node.id, 0) * 20 - 10;
    const y = node.umap_y != null ? node.umap_y * UMAP_SCALE : hashFloat(node.id, 1) * 20 - 10;
    const z = node.umap_z != null ? node.umap_z * UMAP_SCALE : hashFloat(node.id, 2) * 20 - 10;

    // Size: blend member_count and usage_count, clamped
    const raw = Math.log2(Math.max(1, node.member_count + node.usage_count * 0.5));
    const size = Math.max(0.6, Math.min(3.0, raw * 0.5));

    nodes.push({
      id: node.id,
      position: [x, y, z],
      color: stateNodeColor(node.state, node.domain ?? node.color_hex),
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

  // Same-domain edges — connect nodes that share a domain keyword
  // (visual grouping for related clusters)
  for (let i = 0; i < nodes.length; i++) {
    for (let j = i + 1; j < nodes.length; j++) {
      const a = flatNodes[i];
      const b = flatNodes[j];
      const domA = parsePrimaryDomain(a.domain);
      const domB = parsePrimaryDomain(b.domain);
      if (domA !== 'general' && domB !== 'general' && domA === domB) {
        edges.push({ from: nodes[i].id, to: nodes[j].id, type: 'similarity' });
      }
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
