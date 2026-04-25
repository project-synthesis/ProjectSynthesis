/**
 * Observatory API client — typed wrapper around the taxonomy observatory
 * endpoints. Backed by `backend/app/routers/taxonomy_insights.py::get_pattern_density`.
 */

import { apiFetch } from './client';

export type ObservatoryPeriod = '24h' | '7d' | '30d';

export interface PatternDensityRow {
  domain_id: string;
  domain_label: string;
  cluster_count: number;
  meta_pattern_count: number;
  meta_pattern_avg_score: number | null;
  global_pattern_count: number;
  cross_cluster_injection_rate: number;
  period_start: string;
  period_end: string;
}

export interface PatternDensityResponse {
  rows: PatternDensityRow[];
  total_domains: number;
  total_meta_patterns: number;
  total_global_patterns: number;
}

export async function fetchPatternDensity(
  period: ObservatoryPeriod,
): Promise<PatternDensityResponse> {
  return apiFetch<PatternDensityResponse>(`/taxonomy/pattern-density?period=${period}`);
}
