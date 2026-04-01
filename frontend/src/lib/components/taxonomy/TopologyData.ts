/**
 * Transform API taxonomy tree data into scene-graph-ready structures.
 *
 * Pure functions — no Three.js dependency (just typed arrays and interfaces).
 * The renderer consumes SceneData to build Three.js objects.
 */
import type { ClusterNode, SimilarityEdge, InjectionEdge } from '$lib/api/clusters';
import type { LODTier } from './TopologyRenderer';
import { taxonomyColor, stateColor } from '$lib/utils/colors';
import { parsePrimaryDomain } from '$lib/utils/formatting';

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
  coherence: number;      // [0, 1] → wireframe brightness
  avgScore: number | null; // [1, 10] → color saturation
}

/** Opacity by lifecycle state — candidates are translucent. */
function stateOpacity(state: string): number {
  return state === 'candidate' ? 0.4 : 1.0;
}

/** Size multiplier by lifecycle state.
 * Domain nodes already aggregate children's members for their base size,
 * so the multiplier is modest (1.6x) — just enough to be visually dominant
 * without overwhelming the graph at scale. */
function stateSizeMultiplier(state: string): number {
  if (state === 'domain') return 1.6;
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
  type: 'hierarchical' | 'similarity' | 'injection';
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
export function buildSceneData(flatNodes: ClusterNode[], similarityEdges?: SimilarityEdge[], injectionEdges?: InjectionEdge[]): SceneData {
  const nodes: SceneNode[] = [];
  const edges: SceneEdge[] = [];

  // Pre-compute aggregate member count per domain node (sum of children's members).
  // Domain nodes' own member_count is child-cluster count, not optimization count,
  // so a domain with 1 child cluster of 25 members would render tiny without this.
  const domainChildMembers = new Map<string, number>();
  for (const node of flatNodes) {
    if (node.state === 'domain') {
      const childSum = flatNodes
        .filter(n => n.parent_id === node.id && n.state !== 'domain')
        .reduce((sum, n) => sum + n.member_count + n.usage_count * 0.5, 0);
      domainChildMembers.set(node.id, childSum);
    }
  }

  for (const node of flatNodes) {
    // Position: UMAP coords scaled to scene units, or hash-based fallback.
    // UMAP outputs ~[-1, 1]; multiply by 10 for comfortable spacing.
    const UMAP_SCALE = 10;
    const x = node.umap_x != null ? node.umap_x * UMAP_SCALE : hashFloat(node.id, 0) * 20 - 10;
    const y = node.umap_y != null ? node.umap_y * UMAP_SCALE : hashFloat(node.id, 1) * 20 - 10;
    const z = node.umap_z != null ? node.umap_z * UMAP_SCALE : hashFloat(node.id, 2) * 20 - 10;

    // Size: domain nodes aggregate children's members; clusters use their own.
    const memberInput = node.state === 'domain'
      ? domainChildMembers.get(node.id) ?? 1
      : node.member_count + node.usage_count * 0.5;
    const raw = Math.log2(Math.max(1, memberInput));
    let size = Math.max(0.6, Math.min(3.0, raw * 0.5));

    // GENERAL domain: vary size by score to reduce uniform gray blob appearance
    const primaryDomain = parsePrimaryDomain(node.domain);
    if (primaryDomain === 'general' && node.state !== 'domain' && node.avg_score != null) {
      const scoreBonus = (node.avg_score - 5) * 0.1; // +/-0.5 range centered on score 5
      size = Math.max(0.6, Math.min(3.0, size + scoreBonus));
    }

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
      coherence: node.coherence ?? 0.5,
      avgScore: node.avg_score ?? null,
    });

    // Hierarchical edges from parent_id
    if (node.parent_id) {
      edges.push({ from: node.parent_id, to: node.id, type: 'hierarchical' });
    }
  }

  // Optional edge layers — build node lookup once for both
  const nodeIdSet = (similarityEdges || injectionEdges)
    ? new Set(nodes.map(n => n.id))
    : null;

  // Similarity edges from embedding index pairwise similarities
  if (similarityEdges && nodeIdSet) {
    for (const edge of similarityEdges) {
      if (nodeIdSet.has(edge.from_id) && nodeIdSet.has(edge.to_id)) {
        edges.push({ from: edge.from_id, to: edge.to_id, type: 'similarity' });
      }
    }
  }

  // Injection provenance edges (directed: source cluster → target cluster)
  if (injectionEdges && nodeIdSet) {
    for (const edge of injectionEdges) {
      if (nodeIdSet.has(edge.source_id) && nodeIdSet.has(edge.target_id)) {
        edges.push({ from: edge.source_id, to: edge.target_id, type: 'injection' });
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
