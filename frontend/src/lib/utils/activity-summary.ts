/**
 * Per-op event summary formatter.
 *
 * Returns a compact one-line metric extracted from `event.context` per op.
 * Pre-extraction this lived inline in `ActivityPanel.svelte` (220+ lines)
 * and was unavailable to `DomainLifecycleTimeline.svelte`. Now both
 * surfaces format the same way; the Observatory's lifecycle rows surface
 * a meaningful one-liner instead of just `op + decision`.
 *
 * Pure function — no DOM, no store. Tested directly via unit tests.
 *
 * Format conventions:
 *   - Q values formatted to 3 decimals (`Q 0.512→0.643`).
 *   - Similarity / coherence formatted to 3 decimals (`sim=0.910`).
 *   - Scores formatted to 1 decimal (`score=7.8`).
 *   - Member counts append `m` suffix (`12m`).
 *   - Long strings truncated (60 chars for error_message, 50 for pattern_text).
 *   - Returns "" for unrecognized ops or missing context (Timeline + Panel
 *     gracefully omit the metric column when empty).
 */
import type { TimelineEvent } from './activity-filters';

export function keyMetric(e: TimelineEvent): string {
  const c = e.context;
  if (!c) return '';

  if (e.op === 'assign') {
    if (e.decision === 'merge_into' && typeof c.winner_label === 'string') {
      const candidates = c.candidates as Array<{ effective_score?: number }> | undefined;
      const score = Array.isArray(candidates) && candidates.length > 0 && typeof candidates[0].effective_score === 'number'
        ? `${candidates[0].effective_score.toFixed(3)}`
        : '';
      const members = typeof c.member_count === 'number' ? `[${c.member_count}m]` : '';
      const label = typeof c.prompt_label === 'string' ? `"${c.prompt_label}" ` : '';
      return `${label}→ ${c.winner_label} ${members} ${score}`.trim();
    }
    if (e.decision === 'create_new' && typeof c.new_label === 'string') {
      return `new: ${c.new_label} [${c.prompt_domain ?? ''}]`;
    }
    if (typeof c.winner_label === 'string') {
      return c.winner_label;
    }
    return '';
  }

  if (e.op === 'phase') {
    if (typeof c.q_before === 'number' || typeof c.q_after === 'number') {
      const qb = typeof c.q_before === 'number' ? c.q_before.toFixed(3) : '?';
      const qa = typeof c.q_after === 'number' ? c.q_after.toFixed(3) : '?';
      return `Q ${qb}→${qa}`;
    }
    return '';
  }

  if (e.op === 'refit') {
    if (e.decision === 'cluster_detail') {
      const clusters = Array.isArray(c.clusters) ? c.clusters as Array<{ label: string; member_count: number }> : [];
      const top = clusters.slice(0, 3).map((cl) => `${cl.label}(${cl.member_count})`).join(', ');
      return top || `${c.total_optimizations ?? '?'} optimizations`;
    }
    return `Q ${(c.q_before as number)?.toFixed(3) ?? '?'}→${(c.q_after as number)?.toFixed(3) ?? '?'}`;
  }

  if (e.op === 'split') {
    if (e.decision === 'spectral_evaluation') {
      const k = typeof c.best_k === 'number' ? `k=${c.best_k}` : '';
      const sil = typeof c.best_silhouette === 'number' ? `sil=${c.best_silhouette.toFixed(3)}` : '';
      const accepted = c.accepted ? 'accepted' : c.fallback_to_hdbscan ? '→ hdbscan' : 'rejected';
      return `${k} ${sil} ${accepted}`.trim();
    }
    if (typeof c.algorithm === 'string') {
      const algo = `[${c.algorithm}]`;
      const clusters = typeof c.clusters_found === 'number'
        ? `${c.clusters_found} sub-clusters`
        : typeof c.hdbscan_clusters === 'number'
          ? `${c.hdbscan_clusters} sub-clusters`
          : '';
      return `${clusters} ${algo}`.trim();
    }
    return typeof c.hdbscan_clusters === 'number' ? `${c.hdbscan_clusters} sub-clusters` : '';
  }

  if (e.op === 'candidate') {
    if (e.decision === 'candidate_created') {
      const label = typeof c.child_label === 'string' ? c.child_label : '';
      const algo = typeof c.split_algorithm === 'string' ? `[${c.split_algorithm}]` : '';
      return `${label} ${algo}`.trim();
    }
    if (e.decision === 'candidate_promoted') {
      const label = typeof c.cluster_label === 'string' ? c.cluster_label : '';
      const coh = typeof c.coherence === 'number' ? `coh=${c.coherence.toFixed(3)}` : '';
      return `${label} ${coh}`.trim();
    }
    if (e.decision === 'candidate_rejected') {
      const label = typeof c.cluster_label === 'string' ? c.cluster_label : '';
      const coh = typeof c.coherence === 'number' ? `coh=${c.coherence.toFixed(3)}` : '';
      const count = typeof c.member_count === 'number' ? `${c.member_count}m` : '';
      return `${label} ${coh} ${count}`.trim();
    }
    if (e.decision === 'split_fully_reversed') {
      const label = typeof c.parent_label === 'string' ? c.parent_label : '';
      const n = typeof c.candidates_rejected === 'number' ? `${c.candidates_rejected} rejected` : '';
      return `${label} ${n}`.trim();
    }
    return '';
  }

  if (e.op === 'merge') {
    const sim = typeof c.similarity === 'number' ? `sim=${c.similarity.toFixed(3)}` : '';
    const gate = typeof c.gate === 'string' ? ` [${c.gate}]` : '';
    return sim + gate;
  }

  if (e.op === 'score') {
    const overall = typeof c.overall === 'number' ? c.overall.toFixed(1) : '?';
    const label = typeof c.intent_label === 'string' ? c.intent_label : '';
    const divs = Array.isArray(c.divergence) && c.divergence.length > 0 ? ` [${c.divergence.join(',')}]` : '';
    return `${overall} ${label}${divs}`;
  }

  if (e.op === 'extract') {
    return typeof c.meta_patterns_added === 'number' ? `${c.meta_patterns_added} patterns` : '';
  }

  if (e.op === 'retire') {
    if (e.decision === 'dissolved') {
      const label = typeof c.cluster_label === 'string' ? c.cluster_label : '';
      const coh = typeof c.coherence === 'number' ? `coh=${c.coherence.toFixed(3)}` : '';
      const mc = typeof c.member_count === 'number' ? `${c.member_count}m` : '';
      return `${label} ${coh} ${mc} → reassigned`.trim();
    }
    return typeof c.sibling_label === 'string' ? `→ ${c.sibling_label}` : '';
  }

  if (e.op === 'discover') {
    if (e.decision === 'domains_created' || e.decision === 'sub_domains_created') {
      const count = typeof c.count === 'number' ? c.count : 0;
      const names = Array.isArray(c.domains) ? c.domains.slice(0, 3).join(', ')
        : Array.isArray(c.sub_domains) ? c.sub_domains.slice(0, 3).join(', ')
        : '';
      return names ? `${count}: ${names}` : `${count} domains`;
    }
    // R1+R5 — re-eval kept the sub-domain alive: surface raw + shrunk
    // consistency so operators can see how close the dissolution call was.
    if (e.decision === 'sub_domain_reevaluated') {
      const sub = typeof c.sub_domain === 'string' ? c.sub_domain : '';
      const cons = typeof c.consistency_pct === 'number' ? `${c.consistency_pct.toFixed(1)}%` : '?';
      const shrunk = typeof c.shrunk_consistency_pct === 'number' ? c.shrunk_consistency_pct.toFixed(1) : '?';
      const m = typeof c.matching_members === 'number' && typeof c.total_opts === 'number'
        ? ` ${c.matching_members}/${c.total_opts}m` : '';
      return `${sub}${m} cons=${cons} shrunk=${shrunk}%`.trim();
    }
    // R1+R5 — dissolution fired: same payload plus reparent count.
    if (e.decision === 'sub_domain_dissolved') {
      const sub = typeof c.sub_domain === 'string' ? c.sub_domain : '';
      const cons = typeof c.consistency_pct === 'number' ? `${c.consistency_pct.toFixed(1)}%` : '?';
      const shrunk = typeof c.shrunk_consistency_pct === 'number' ? c.shrunk_consistency_pct.toFixed(1) : '?';
      const samples = Array.isArray(c.sample_match_failures) ? c.sample_match_failures.length : 0;
      const reparented = typeof c.clusters_reparented === 'number' ? ` → ${c.clusters_reparented} reparented` : '';
      const samplesNote = samples > 0 ? ` (${samples} sample${samples !== 1 ? 's' : ''})` : '';
      return `${sub} cons=${cons} shrunk=${shrunk}%${samplesNote}${reparented}`.trim();
    }
    // R3 — re-eval skipped due to empty vocab snapshot.
    if (e.decision === 'sub_domain_reevaluation_skipped') {
      const sub = typeof c.sub_domain === 'string' ? c.sub_domain : '';
      const reason = typeof c.reason === 'string' ? c.reason : '';
      const opts = typeof c.total_opts === 'number' ? ` ${c.total_opts}m` : '';
      return `${sub}${opts} skipped — ${reason}`.trim();
    }
    // R6 — operator-triggered rebuild (always emits, including dry-runs).
    if (e.decision === 'sub_domain_rebuild_invoked') {
      const dom = typeof c.domain === 'string' ? c.domain : '';
      const dry = c.dry_run === true ? ' [dry]' : '';
      const thr = typeof c.threshold_used === 'number' ? ` thr=${c.threshold_used.toFixed(2)}` : '';
      const created = typeof c.created_count === 'number' ? c.created_count : 0;
      const skipped = typeof c.skipped_existing_count === 'number' ? c.skipped_existing_count : 0;
      return `${dom}${dry}${thr} +${created} created, ${skipped} skipped`.trim();
    }
    // R7 — vocab regeneration overlap telemetry. Show the Jaccard % so
    // low-overlap regens are immediately visible in the timeline.
    if (e.decision === 'vocab_generated_enriched') {
      const dom = typeof c.domain === 'string' ? c.domain : '';
      const groups = typeof c.groups === 'number' ? `${c.groups} groups` : '';
      const overlap = typeof c.overlap_pct === 'number' ? ` overlap=${c.overlap_pct.toFixed(1)}%` : '';
      return `${dom} ${groups}${overlap}`.trim();
    }
    return typeof c.domain_label === 'string' ? c.domain_label : '';
  }

  if (e.op === 'error') {
    return typeof c.error_message === 'string' ? c.error_message.slice(0, 60) : '';
  }

  if (e.op === 'emerge') {
    return typeof c.domain === 'string' ? c.domain : '';
  }

  if (e.op === 'global_pattern') {
    const action = e.decision ?? '';
    const text = typeof c.pattern_text === 'string' ? c.pattern_text.slice(0, 50) : '';
    const score = typeof c.avg_score === 'number' ? `score=${c.avg_score.toFixed(1)}` : '';
    return `${action}: ${text} ${score}`.trim();
  }

  if (e.op === 'injection_effectiveness') {
    const lift = typeof c.lift === 'number' ? `lift=${c.lift > 0 ? '+' : ''}${c.lift.toFixed(2)}` : '';
    const n = typeof c.injected_n === 'number' ? `(n=${c.injected_n}+${c.baseline_n ?? '?'})` : '';
    return `${lift} ${n}`.trim();
  }

  if (e.op === 'skip') {
    return typeof c.warm_path_age === 'number' ? `age=${c.warm_path_age}` : '';
  }

  if (e.op === 'recovery') {
    if (e.decision === 'scan') {
      const n = typeof c.orphan_count === 'number' ? c.orphan_count : '?';
      return `${n} orphan${n !== 1 ? 's' : ''} found`;
    }
    if (e.decision === 'success') {
      const cid = typeof c.cluster_id === 'string' ? c.cluster_id.slice(0, 8) : '';
      return `recovered → ${cid}`;
    }
    if (e.decision === 'failed') {
      return typeof c.error_message === 'string' ? c.error_message.slice(0, 40) : 'failed';
    }
    return '';
  }

  if (e.op === 'audit') {
    if (typeof c.q_system === 'number' || typeof c.q_baseline === 'number') {
      const qb = typeof c.q_baseline === 'number' ? c.q_baseline.toFixed(3) : '?';
      const qs = typeof c.q_system === 'number' ? c.q_system.toFixed(3) : '?';
      return `Q ${qb}→${qs}`;
    }
    return '';
  }

  if (e.op === 'reconcile') {
    if (e.decision === 'repaired') {
      const fixes = [
        typeof c.member_counts_fixed === 'number' && c.member_counts_fixed > 0
          ? `${c.member_counts_fixed} counts` : '',
        typeof c.coherence_updated === 'number' && c.coherence_updated > 0
          ? `${c.coherence_updated} coh` : '',
        typeof c.scores_reconciled === 'number' && c.scores_reconciled > 0
          ? `${c.scores_reconciled} scores` : '',
      ].filter(Boolean).join(', ');
      return fixes || 'repaired';
    }
    return typeof c.count === 'number' ? `${c.count} zombies` : '';
  }

  if (e.op === 'readiness') {
    const domain = typeof c.domain === 'string' ? c.domain : '';
    const tier = typeof c.tier === 'string' ? c.tier : '';
    if (e.decision === 'sub_domain_readiness_computed') {
      const top = typeof c.top_qualifier === 'string' ? ` top="${c.top_qualifier}"` : '';
      const gap = typeof c.gap_to_threshold === 'number'
        ? ` gap=${(c.gap_to_threshold).toFixed(3)}` : '';
      return `${domain} [${tier}]${top}${gap}`.trim();
    }
    if (e.decision === 'domain_stability_computed') {
      const cons = typeof c.consistency === 'number' ? `cons=${(c.consistency).toFixed(2)}` : '';
      const risk = typeof c.dissolution_risk === 'number'
        ? `risk=${(c.dissolution_risk).toFixed(2)}` : '';
      return `${domain} [${tier}] ${cons} ${risk}`.trim();
    }
    return domain;
  }

  if (e.op === 'seed') {
    if (e.decision === 'seed_agents_complete') {
      return `${c.prompts_after_dedup ?? c.prompts_generated ?? '?'} prompts`;
    }
    if (e.decision === 'seed_prompt_scored') {
      const score = typeof c.overall_score === 'number' ? c.overall_score.toFixed(1) : '?';
      return `${score} ${c.intent_label ?? c.task_type ?? ''}`;
    }
    if (e.decision === 'seed_prompt_failed') {
      return typeof c.error === 'string' ? c.error.slice(0, 40) : 'failed';
    }
    if (e.decision === 'seed_completed') {
      return `${c.prompts_optimized ?? '?'} done, ${c.clusters_created ?? 0} clusters`;
    }
    if (e.decision === 'seed_failed') {
      return typeof c.error_message === 'string' ? c.error_message.slice(0, 40) : 'failed';
    }
    if (e.decision === 'seed_persist_complete') {
      return `${c.rows_inserted ?? '?'} rows`;
    }
    if (e.decision === 'seed_taxonomy_complete') {
      const domains = Array.isArray(c.domains_touched) ? c.domains_touched.length : 0;
      return `${c.clusters_created ?? 0} clusters, ${domains} domains`;
    }
    return '';
  }

  return '';
}
