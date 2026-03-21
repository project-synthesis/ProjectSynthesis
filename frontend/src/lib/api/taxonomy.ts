/**
 * Taxonomy API client — typed wrappers for /api/taxonomy/* endpoints.
 */
import { apiFetch } from './client';

// -- Response types --

export interface TaxonomyNode {
  id: string;
  parent_id: string | null;
  label: string | null;
  state: 'confirmed' | 'candidate' | 'retired';
  persistence: number | null;
  coherence: number | null;
  separation: number | null;
  stability: number | null;
  member_count: number;
  usage_count: number;
  color_hex: string | null;
  umap_x: number | null;
  umap_y: number | null;
  umap_z: number | null;
  created_at: string | null;
  // Only populated by get_node — get_tree returns a flat list
  children?: TaxonomyNode[];
  breadcrumb?: string[];
  family_count?: number;
}

export interface TaxonomyStats {
  q_system: number | null;
  q_coherence: number | null;
  q_separation: number | null;
  q_coverage: number | null;
  q_dbcv: number | null;
  total_families: number;
  nodes: {
    confirmed: number;
    candidate: number;
    retired: number;
    max_depth: number;
    leaf_count: number;
  };
  last_warm_path: string | null;
  last_cold_path: string | null;
  warm_path_age: number | null;
  q_history: Array<{
    timestamp: string | null;
    q_system: number | null;
    operations: number;
  }>;
  q_sparkline: number[];
}

export interface ReclusterResult {
  status: 'completed' | 'skipped';
  reason?: string;
  snapshot_id?: string;
  q_system?: number | null;
  nodes_created?: number | null;
  nodes_updated?: number | null;
  umap_fitted?: boolean | null;
}

// -- API functions --

export const getTaxonomyTree = async (minPersistence?: number): Promise<TaxonomyNode[]> => {
  const qs = minPersistence != null ? `?min_persistence=${minPersistence}` : '';
  // Backend returns { nodes: [...] } — unwrap to flat array
  const resp = await apiFetch<{ nodes: TaxonomyNode[] }>(`/taxonomy/tree${qs}`);
  return resp.nodes;
};

export const getTaxonomyNode = (nodeId: string) =>
  apiFetch<TaxonomyNode>(`/taxonomy/node/${encodeURIComponent(nodeId)}`);

export const getTaxonomyStats = () =>
  apiFetch<TaxonomyStats>('/taxonomy/stats');

export const triggerRecluster = (minPersistence?: number) =>
  apiFetch<ReclusterResult>('/taxonomy/recluster', {
    method: 'POST',
    body: JSON.stringify(minPersistence != null ? { min_persistence: minPersistence } : {}),
  });
