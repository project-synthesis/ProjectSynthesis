/**
 * Cluster API client — unified tree, detail, stats, match, templates, recluster.
 *
 * Replaces the separate patterns.ts and taxonomy.ts API clients.
 * All endpoints hit /api/clusters/*.
 */
import { apiFetch } from './client';

// -- Types --

export interface ClusterNode {
  id: string;
  parent_id: string | null;
  label: string;
  state: string;
  domain: string;
  task_type: string;
  persistence: number | null;
  coherence: number | null;
  separation: number | null;
  stability: number | null;
  member_count: number;
  usage_count: number;
  avg_score: number | null;
  color_hex: string | null;
  umap_x: number | null;
  umap_y: number | null;
  umap_z: number | null;
  preferred_strategy: string | null;
  output_coherence: number | null;
  blend_w_raw: number | null;
  blend_w_optimized: number | null;
  blend_w_transform: number | null;
  split_failures: number;
  meta_pattern_count: number;
  template_count: number;   // count of proven templates forked from this cluster
  created_at: string | null;
  // Only populated by getClusterDetail
  children?: ClusterNode[];
  breadcrumb?: string[];
}

export interface MetaPatternItem {
  id: string;
  pattern_text: string;
  source_count: number;
}

export interface LinkedOptimization {
  id: string;
  trace_id: string;
  raw_prompt: string;
  intent_label: string | null;
  overall_score: number | null;
  strategy_used: string | null;
  created_at: string | null;
}

export interface ClusterDetail {
  id: string;
  parent_id: string | null;
  label: string;
  state: string;
  domain: string;
  task_type: string;
  member_count: number;
  usage_count: number;
  avg_score: number | null;
  coherence: number | null;
  separation: number | null;
  output_coherence: number | null;
  blend_w_raw: number | null;
  blend_w_optimized: number | null;
  blend_w_transform: number | null;
  split_failures: number;
  preferred_strategy: string | null;
  promoted_at: string | null;
  project_ids: string[];  // ADR-005
  member_counts_by_project: Record<string, number>;  // ADR-005
  meta_patterns: MetaPatternItem[];
  optimizations: LinkedOptimization[];
  children: ClusterNode[] | null;
  breadcrumb: string[] | null;
}

export interface ClusterStats {
  q_system: number | null;
  q_coherence: number | null;
  q_separation: number | null;
  q_coverage: number | null;
  q_dbcv: number | null;
  q_health: number | null;
  q_health_coherence_w: number | null;
  q_health_separation_w: number | null;
  q_health_weights: { w_c: number; w_s: number; w_v: number; w_d: number } | null;
  q_health_total_members: number | null;
  q_health_cluster_count: number | null;
  total_clusters: number;
  nodes: {
    active: number;
    candidate: number;
    mature: number;
    template: number;
    archived: number;
    max_depth: number;
    leaf_count: number;
  } | null;
  last_warm_path: string | null;
  last_cold_path: string | null;
  warm_path_age: number | null;
  q_history: Array<{
    timestamp: string | null;
    q_system: number | null;
    operations: number;
  }> | null;
  q_sparkline: number[] | null;
  q_trend: number;
  q_current: number | null;
  q_min: number | null;
  q_max: number | null;
  q_point_count: number;
}

export interface ClusterMatchResponse {
  match: {
    cluster: {
      id: string;
      label: string;
      domain: string;
      member_count: number;
    };
    meta_patterns: MetaPatternItem[];
    similarity: number;
  } | null;
}

export interface SimilarityEdge {
  from_id: string;
  to_id: string;
  similarity: number;
}

export interface SimilarityEdgesResponse {
  edges: SimilarityEdge[];
}

export interface InjectionEdge {
  source_id: string; // cluster that provided patterns
  target_id: string; // cluster the optimization was assigned to
  weight: number;    // number of injection events
}

export interface InjectionEdgesResponse {
  edges: InjectionEdge[];
}

export interface ReclusterResult {
  status: 'completed' | 'rejected' | 'skipped';
  reason?: string;
  snapshot_id?: string;
  q_system?: number | null;
  q_before?: number | null;
  q_after?: number | null;
  accepted?: boolean;
  nodes_created?: number;
  nodes_updated?: number;
  umap_fitted?: boolean;
}

// -- API functions --

export async function getClusterTree(minPersistence?: number): Promise<ClusterNode[]> {
  const qs = minPersistence != null ? `?min_persistence=${minPersistence}` : '';
  const resp = await apiFetch<{ nodes: ClusterNode[] }>(`/clusters/tree${qs}`);
  return resp.nodes;
}

export const getClusterStats = () =>
  apiFetch<ClusterStats>('/clusters/stats');

export const getClusterDetail = (clusterId: string) =>
  apiFetch<ClusterDetail>(`/clusters/${encodeURIComponent(clusterId)}`);

export async function getClusterTemplates(params?: { offset?: number; limit?: number }): Promise<{
  total: number;
  count: number;
  offset: number;
  has_more: boolean;
  next_offset: number | null;
  items: ClusterNode[];
}> {
  const search = new URLSearchParams();
  if (params?.offset != null) search.set('offset', String(params.offset));
  if (params?.limit != null) search.set('limit', String(params.limit));
  const qs = search.toString();
  return apiFetch(`/clusters/templates${qs ? '?' + qs : ''}`);
}

export const matchPattern = (prompt_text: string, signal?: AbortSignal) =>
  apiFetch<ClusterMatchResponse>('/clusters/match', {
    method: 'POST',
    body: JSON.stringify({ prompt_text }),
    signal,
  });

export const updateCluster = (clusterId: string, updates: { intent_label?: string; state?: string }) =>
  apiFetch<{ id: string; intent_label: string; domain: string; state: string }>(`/clusters/${encodeURIComponent(clusterId)}`, {
    method: 'PATCH',
    body: JSON.stringify(updates),
  });

export const triggerRecluster = () =>
  apiFetch<ReclusterResult>('/clusters/recluster', {
    method: 'POST',
    body: JSON.stringify({}),
  });

export async function getClusterSimilarityEdges(
  threshold?: number,
  maxEdges?: number,
): Promise<SimilarityEdge[]> {
  const params = new URLSearchParams();
  if (threshold != null) params.set('threshold', String(threshold));
  if (maxEdges != null) params.set('max_edges', String(maxEdges));
  const qs = params.toString();
  const resp = await apiFetch<SimilarityEdgesResponse>(`/clusters/similarity-edges${qs ? '?' + qs : ''}`);
  return resp.edges;
}

export async function getClusterInjectionEdges(): Promise<InjectionEdge[]> {
  const resp = await apiFetch<InjectionEdgesResponse>('/clusters/injection-edges');
  return resp.edges;
}

// -- Activity API --

export interface TaxonomyActivityEvent {
  ts: string;
  path: string;
  op: string;
  decision: string;
  cluster_id: string | null;
  optimization_id: string | null;
  duration_ms: number | null;
  context: Record<string, unknown>;
}

export interface ActivityResponse {
  events: TaxonomyActivityEvent[];
  total_in_buffer: number;
  oldest_ts: string | null;
}

export interface ActivityHistoryResponse {
  events: TaxonomyActivityEvent[];
  total: number;
  has_more: boolean;
}

export async function getClusterActivity(params?: {
  limit?: number;
  path?: string;
  op?: string;
  errors_only?: boolean;
}): Promise<ActivityResponse> {
  const search = new URLSearchParams();
  if (params?.limit != null) search.set('limit', String(params.limit));
  if (params?.path) search.set('path', params.path);
  if (params?.op) search.set('op', params.op);
  if (params?.errors_only) search.set('errors_only', 'true');
  const qs = search.toString();
  return apiFetch<ActivityResponse>(`/clusters/activity${qs ? '?' + qs : ''}`);
}

export async function getClusterActivityHistory(params: {
  date: string;
  limit?: number;
  offset?: number;
}): Promise<ActivityHistoryResponse> {
  const search = new URLSearchParams({ date: params.date });
  if (params.limit != null) search.set('limit', String(params.limit));
  if (params.offset != null) search.set('offset', String(params.offset));
  return apiFetch<ActivityHistoryResponse>(`/clusters/activity/history?${search.toString()}`);
}

