"""v0.4.17 P2 — Reporting helpers for Phase 5 of the probe pipeline.

These functions render the final probe report and resolve follow-up
recommendations from the per-prompt scoring results. They are pure --
no DB access, no network -- and operate over in-memory ProbePromptResult /
ProbeAggregate data structures.

This module is a leaf: it has no inter-module dependencies on the other
v0.4.17 P2 split modules (``probe_common``, ``probe_phases``).
"""
from __future__ import annotations

from datetime import datetime

from app.schemas.probes import (
    ProbeAggregate,
    ProbePromptResult,
    ProbeRunRequest,
    ProbeTaxonomyDelta,
)


def _resolve_followups(
    delta: ProbeTaxonomyDelta,
    agg: ProbeAggregate,
) -> list[str]:
    """Deterministic recommended-followups rule set per AC-C4-5.

    4 rules: single-cluster + no sub-domain emergence; F5 false-premise fires;
    P0a rejected candidate domains; mean below 1-sigma floor.
    """
    out: list[str] = []
    # Rule 1: single-cluster + no sub-domain emergence
    if not delta.sub_domains_created:
        single_member = [
            c for c in delta.clusters_created
            if str(c.get("member_count", "0")) == "1"
        ]
        if single_member:
            label = single_member[0].get("label", "(unnamed)")
            out.append(
                f"Cluster '{label}' has only 1 member; ~3 more topic-related "
                f"prompts could promote a sub-domain (per v0.4.11 P0a "
                f">=2-cluster floor)."
            )
    # Rule 2: false-premise flag fired
    if agg.f5_flag_fires > 0:
        out.append(
            f"{agg.f5_flag_fires} prompts triggered the "
            f"`possible_false_premise` flag -- review topic framing for "
            f"accuracy."
        )
    # Rule 3: P0a rejected candidate domains
    if delta.proposal_rejected_min_source_clusters > 0:
        out.append(
            f"{delta.proposal_rejected_min_source_clusters} candidate "
            f"domain(s) were rejected (insufficient cluster evidence); "
            f"re-probe similar topics to accumulate sibling clusters."
        )
    # Rule 4: mean below 1-sigma floor
    if agg.mean_overall is not None and agg.mean_overall < 6.9:
        out.append(
            "Mean below 1-sigma floor -- review codebase grounding quality "
            "(`relevant_files` count) and topic specificity."
        )
    return out


def _render_final_report(
    request: ProbeRunRequest,
    probe_id: str,
    started_at: datetime,
    completed_at: datetime,
    prompt_results: list[ProbePromptResult],
    agg: ProbeAggregate,
    delta: ProbeTaxonomyDelta,
    commit_sha: str | None,
    rate_limit_meta: dict | None = None,
) -> str:
    """Render the 5-section final report markdown per AC-C4-5.

    When ``rate_limit_meta`` is supplied (one or more prompts hit a provider
    rate limit during phase 3), an extra "Rate-limited" section is rendered
    near the top with the provider name + reset time so the user sees what
    happened without scrolling through the score distribution. Format
    matches the structured ``ProbeRateLimitedEvent`` SSE payload so the
    final report and live event are coherent.
    """
    top_3 = sorted(
        (r for r in prompt_results if r.overall_score is not None),
        key=lambda r: (-(r.overall_score or 0.0), r.prompt_idx),
    )[:3]

    lines: list[str] = []
    lines.append(f"# Topic Probe Run Report -- `{probe_id}`")
    lines.append("")
    lines.append(f"**Topic:** {request.topic}")
    lines.append(f"**Scope:** {request.scope or '**/*'}")
    lines.append(f"**Intent hint:** {request.intent_hint or 'explore'}")
    lines.append("")

    if rate_limit_meta is not None:
        _prov = rate_limit_meta.get("provider") or "?"
        _reset = rate_limit_meta.get("reset_at_iso") or "?"
        _wait = rate_limit_meta.get("estimated_wait_seconds")
        lines.append("## Rate-limited")
        lines.append(
            f"This probe was rate-limited by **{_prov}** mid-batch. "
            f"Provider limit resets at **{_reset}** UTC"
            + (f" (~{_wait}s from when the limit hit)" if _wait else "")
            + "."
        )
        lines.append(
            "Re-run the same probe topic after that time to fill in the "
            "remaining prompts. Already-completed prompts (below) are "
            "persisted and visible in `/api/optimizations`."
        )
        lines.append("")

    lines.append("## Top 3 Prompts (by overall score)")
    if not top_3:
        lines.append("_(no completed prompts)_")
    else:
        for i, r in enumerate(top_3, 1):
            lines.append(
                f"{i}. **score {r.overall_score:.2f}** -- {r.prompt_text}"
            )
    lines.append("")

    lines.append("## Score Distribution")
    lines.append(f"- mean: {agg.mean_overall}")
    lines.append(
        f"- p5 / p50 / p95: {agg.p5_overall} / {agg.p50_overall} / "
        f"{agg.p95_overall}"
    )
    lines.append(f"- completed: {agg.completed_count}")
    lines.append(f"- failed: {agg.failed_count}")
    lines.append("")

    lines.append("## Taxonomy Delta")
    lines.append(
        f"- domains_created: {delta.domains_created or '_none_'}"
    )
    lines.append(
        f"- sub_domains_created: {delta.sub_domains_created or '_none_'}"
    )
    if delta.clusters_created:
        lines.append("- clusters_created:")
        for c in delta.clusters_created:
            cid = str(c.get("id", "?"))[:8]
            lines.append(f"  - `{cid}` -- {c.get('label', '?')}")
    if delta.clusters_split:
        lines.append("- clusters_split:")
        for c in delta.clusters_split:
            lines.append(f"  - `{str(c.get('id', '?'))[:8]}` -> split")
    lines.append(
        f"- proposal_rejected_min_source_clusters: "
        f"{delta.proposal_rejected_min_source_clusters}"
    )
    lines.append("")

    lines.append("## Recommended Follow-ups")
    followups = _resolve_followups(delta, agg)
    if not followups:
        lines.append(
            "_No structural follow-ups detected -- probe ran cleanly._"
        )
    else:
        for f in followups:
            lines.append(f"- {f}")
    lines.append("")

    lines.append("## Run Metadata")
    lines.append(f"- commit_sha: `{commit_sha or 'unknown'}`")
    lines.append(f"- started_at: {started_at.isoformat()}")
    lines.append(f"- completed_at: {completed_at.isoformat()}")
    lines.append(
        f"- prompts_generated: "
        f"{agg.completed_count + agg.failed_count}"
    )
    lines.append(
        f"- scoring_formula_version: {agg.scoring_formula_version}"
    )
    return "\n".join(lines)


__all__ = [
    "_resolve_followups",
    "_render_final_report",
]
