/**
 * Domain readiness API client — fetches live stability + sub-domain emergence
 * reports for every top-level domain.
 *
 * Mirrors `backend/app/schemas/sub_domain_readiness.py` — any contract change
 * must be synchronized here.
 */
import { apiFetch } from './client';

// -- Types --

export type QualifierSource = 'domain_raw' | 'intent_label' | 'tf_idf';
export type EmergenceTier = 'ready' | 'warming' | 'inert';
export type StabilityTier = 'healthy' | 'guarded' | 'critical';
export type EmergenceBlocker =
  | 'no_candidates'
  | 'below_threshold'
  | 'insufficient_members'
  | 'single_cluster'
  | 'none';

export interface QualifierCandidate {
  qualifier: string;
  count: number;
  consistency: number;
  dominant_source: QualifierSource;
  source_breakdown: Record<string, number>;
  cluster_breadth: number;
}

export interface SubDomainEmergenceReport {
  threshold: number;
  threshold_formula: string;
  min_member_count: number;
  total_opts: number;
  top_candidate: QualifierCandidate | null;
  gap_to_threshold: number | null;
  ready: boolean;
  blocked_reason: EmergenceBlocker | null;
  runner_ups: QualifierCandidate[];
  tier: EmergenceTier;
}

export interface DomainStabilityGuards {
  general_protected: boolean;
  has_sub_domain_anchor: boolean;
  age_eligible: boolean;
  above_member_ceiling: boolean;
  consistency_above_floor: boolean;
}

export interface DomainStabilityReport {
  consistency: number;
  dissolution_floor: number;
  hysteresis_creation_threshold: number;
  age_hours: number;
  min_age_hours: number;
  member_count: number;
  member_ceiling: number;
  sub_domain_count: number;
  total_opts: number;
  guards: DomainStabilityGuards;
  tier: StabilityTier;
  dissolution_risk: number;
  would_dissolve: boolean;
}

export interface DomainReadinessReport {
  domain_id: string;
  domain_label: string;
  member_count: number;
  stability: DomainStabilityReport;
  emergence: SubDomainEmergenceReport;
  computed_at: string;
}

// -- API functions --

export const getAllDomainReadiness = (fresh = false) =>
  apiFetch<DomainReadinessReport[]>(
    `/domains/readiness${fresh ? '?fresh=true' : ''}`,
  );

export const getDomainReadiness = (domainId: string, fresh = false) =>
  apiFetch<DomainReadinessReport>(
    `/domains/${encodeURIComponent(domainId)}/readiness${fresh ? '?fresh=true' : ''}`,
  );

// -- History --

export type ReadinessWindow = '24h' | '7d' | '30d';

export interface ReadinessHistoryPoint {
  ts: string;
  consistency: number;
  dissolution_risk: number;
  top_candidate_gap: number | null;
  stability_tier: StabilityTier;
  emergence_tier: EmergenceTier;
  is_bucket_mean: boolean;
}

export interface ReadinessHistoryResponse {
  domain_id: string;
  domain_label: string;
  window: ReadinessWindow;
  bucketed: boolean;
  points: ReadinessHistoryPoint[];
}

export const getDomainReadinessHistory = (
  domainId: string,
  window: ReadinessWindow = '24h',
) =>
  apiFetch<ReadinessHistoryResponse>(
    `/domains/${encodeURIComponent(domainId)}/readiness/history?window=${window}`,
  );
