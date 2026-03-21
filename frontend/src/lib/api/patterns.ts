/**
 * Pattern knowledge graph API client.
 */
import { apiFetch } from './client';

export interface PatternFamily {
  id: string;
  intent_label: string;
  domain: string;
  task_type: string;
  usage_count: number;
  member_count: number;
  avg_score: number | null;
  created_at: string | null;
}

export interface MetaPatternItem {
  id: string;
  pattern_text: string;
  source_count: number;
}

export interface PatternMatch {
  family: PatternFamily;
  meta_patterns: MetaPatternItem[];
  similarity: number;
  // Taxonomy context (from TaxonomyEngine.match_prompt)
  match_level: 'family' | 'cluster' | null;
  taxonomy_node_id: string | null;
  taxonomy_label: string | null;
  taxonomy_color: string | null;
  taxonomy_breadcrumb: string[] | null;
}

export interface GraphEdge {
  from: string;
  to: string;
  weight: number;
}

export interface GraphFamily extends PatternFamily {
  meta_patterns: MetaPatternItem[];
}

export interface PatternGraph {
  center: { total_families: number; total_patterns: number; total_optimizations: number };
  families: GraphFamily[];
  edges: GraphEdge[];
}

export interface FamilyDetail extends PatternFamily {
  updated_at: string | null;
  meta_patterns: MetaPatternItem[];
  optimizations: { id: string; trace_id: string; raw_prompt: string; intent_label: string | null; overall_score: number | null; strategy_used: string | null; created_at: string | null }[];
}

export const matchPattern = (prompt_text: string) =>
  apiFetch<{ match: PatternMatch | null }>('/patterns/match', {
    method: 'POST',
    body: JSON.stringify({ prompt_text }),
  });

export const getPatternGraph = (familyId?: string) => {
  const qs = familyId ? `?family_id=${encodeURIComponent(familyId)}` : '';
  return apiFetch<PatternGraph>(`/patterns/graph${qs}`);
};

export const listFamilies = (params?: { offset?: number; limit?: number; domain?: string }) => {
  const search = new URLSearchParams();
  if (params?.offset != null) search.set('offset', String(params.offset));
  if (params?.limit != null) search.set('limit', String(params.limit));
  if (params?.domain) search.set('domain', params.domain);
  const qs = search.toString();
  return apiFetch<{ total: number; count: number; offset: number; has_more: boolean; next_offset: number | null; items: PatternFamily[] }>(
    `/patterns/families${qs ? '?' + qs : ''}`
  );
};

export const getFamilyDetail = (familyId: string) =>
  apiFetch<FamilyDetail>(`/patterns/families/${familyId}`);

export const updateFamily = (familyId: string, updates: { intent_label?: string; domain?: string }) =>
  apiFetch<{ id: string; intent_label: string; domain: string }>(`/patterns/families/${familyId}`, {
    method: 'PATCH',
    body: JSON.stringify(updates),
  });

/** @deprecated Use updateFamily instead */
export const renameFamily = (familyId: string, intent_label: string) =>
  updateFamily(familyId, { intent_label });

export interface SearchResult {
  type: string;
  id: string;
  label: string;
  score: number;
  domain?: string;
  family_id?: string;
}

export const searchPatterns = (q: string, topK = 5) =>
  apiFetch<SearchResult[]>(
    `/patterns/search?q=${encodeURIComponent(q)}&top_k=${topK}`
  );

export const getPatternStats = () =>
  apiFetch<{ total_families: number; total_patterns: number; total_optimizations: number; domain_distribution: Record<string, number> }>(
    '/patterns/stats'
  );
