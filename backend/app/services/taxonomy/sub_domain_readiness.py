"""Domain + sub-domain readiness analytics — live cluster tree analysis.

Exposes two projections of each top-level domain's state:

  * ``DomainStabilityReport``    — how protected is the domain against dissolution
  * ``SubDomainEmergenceReport`` — how close is a qualifier to forming a sub-domain

The emergence side reuses a **pure cascade primitive** (``compute_qualifier_cascade``)
that mirrors the three-source cascade inside ``engine._propose_sub_domains``:

  1. ``domain_raw``    — parsed "primary: qualifier" against organic vocab
  2. ``intent_label``  — keyword hits against ``generated_qualifiers``
  3. ``tf_idf``        — raw_prompt hits against ``signal_keywords``

Extracting the cascade here eliminates drift by construction — both the engine
(in a follow-up refactor) and this service consume the same implementation.

An in-memory TTL cache (30s, keyed by ``(domain_id, member_count)``) keeps repeat
polls cheap.  Passing ``fresh=True`` bypasses the cache.  The cache key naturally
invalidates whenever a new optimization changes the member count.

Copyright 2025-2026 Project Synthesis contributors.
"""

from __future__ import annotations

import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal, TypedDict

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Optimization, PromptCluster
from app.schemas.sub_domain_readiness import (
    DomainReadinessReport,
    DomainStabilityGuards,
    DomainStabilityReport,
    QualifierCandidate,
    SubDomainEmergenceReport,
)
from app.services.taxonomy._constants import (
    DOMAIN_DISSOLUTION_CONSISTENCY_FLOOR,
    DOMAIN_DISSOLUTION_MEMBER_CEILING,
    DOMAIN_DISSOLUTION_MIN_AGE_HOURS,
    EXCLUDED_STRUCTURAL_STATES,
    READINESS_CROSSING_COOLDOWN_SECONDS,
    READINESS_CROSSING_HYSTERESIS_CYCLES,
    SUB_DOMAIN_MIN_CLUSTER_BREADTH,
    SUB_DOMAIN_QUALIFIER_CONSISTENCY_HIGH,
    SUB_DOMAIN_QUALIFIER_CONSISTENCY_LOW,
    SUB_DOMAIN_QUALIFIER_MIN_KEYWORD_HITS,
    SUB_DOMAIN_QUALIFIER_MIN_MEMBERS,
    SUB_DOMAIN_QUALIFIER_SCALE_RATE,
)
from app.services.taxonomy.cluster_meta import read_meta
from app.services.taxonomy.event_logger import get_event_logger
from app.utils.text_cleanup import parse_domain

__all__ = [
    "CascadeResult",
    "TierCrossing",
    "compute_qualifier_cascade",
    "compute_sub_domain_emergence",
    "compute_domain_stability",
    "compute_domain_readiness",
    "compute_all_domain_readiness",
    "clear_cache",
    "clear_tier_history",
]


# Domain-wide hysteresis creation threshold (for UX context only — not evaluated here)
_DOMAIN_CREATION_HYSTERESIS = 0.60
# Tier boundary for sub-domain emergence: qualifier within this gap is "warming"
_WARMING_GAP = 0.10
# Tier boundary for domain stability: "healthy" band (double the dissolution floor)
_DOMAIN_HEALTHY_FLOOR = 0.40

_CACHE_TTL_SECONDS = 30.0
_CACHE_MAX_ENTRIES = 128

_SOURCE_DOMAIN_RAW = "domain_raw"
_SOURCE_INTENT_LABEL = "intent_label"
_SOURCE_TF_IDF = "tf_idf"
_SOURCES = (_SOURCE_DOMAIN_RAW, _SOURCE_INTENT_LABEL, _SOURCE_TF_IDF)


# ---------------------------------------------------------------------------
# Cascade primitive
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CascadeResult:
    """Outcome of the three-source qualifier cascade for one domain.

    Attributes:
        total_opts: Total optimizations scanned across the domain's cluster tree.
        qualifier_counts: qualifier → total hits across all sources.
        source_breakdown: top-level counts per source (may double-count qualifiers).
        per_qualifier_sources: qualifier → per-source hit map.
        qualifier_to_cluster_ids: qualifier → set of distinct cluster ids contributing.
        dynamic_keywords: ordered TF-IDF keyword/weight pairs that were eligible
            for Source 3 matching (length ≥3, weight ≥0.5).  Exposed so callers
            (engine observability) can log the vocab without re-reading meta.
        generated_qualifiers_present: whether the domain has organic Haiku
            qualifier vocabulary in its ``cluster_metadata``.  Feeds the
            ``has_organic_vocab`` field in the engine's signal-scan event.
    """

    total_opts: int
    qualifier_counts: dict[str, int]
    source_breakdown: dict[str, int]
    per_qualifier_sources: dict[str, dict[str, int]]
    qualifier_to_cluster_ids: dict[str, set[str]] = field(default_factory=dict)
    dynamic_keywords: tuple[tuple[str, float], ...] = ()
    generated_qualifiers_present: bool = False

    def dominant_source_for(self, qualifier: str) -> Literal["domain_raw", "intent_label", "tf_idf"]:
        """Return the source that contributed the most hits for ``qualifier``.

        Ties are broken by the canonical source order: domain_raw > intent_label > tf_idf.
        """
        sources = self.per_qualifier_sources.get(qualifier, {})
        best = _SOURCE_DOMAIN_RAW
        best_count = -1
        for src in _SOURCES:
            count = sources.get(src, 0)
            if count > best_count:
                best_count = count
                best = src
        return best  # type: ignore[return-value]


async def _collect_child_cluster_ids(
    db: AsyncSession,
    domain_node: PromptCluster,
    *,
    include_sub_domain_descendants: bool = True,
) -> list[str]:
    """Return ids of all non-structural clusters anchored under the domain.

    When ``include_sub_domain_descendants`` is True, includes clusters under
    existing sub-domain children — matches the engine's behavior of scanning
    the full hierarchy when qualifying.
    """
    parent_ids: list[str] = [domain_node.id]
    if include_sub_domain_descendants:
        sub_q = await db.execute(
            select(PromptCluster.id).where(
                PromptCluster.parent_id == domain_node.id,
                PromptCluster.state == "domain",
            )
        )
        parent_ids.extend(r[0] for r in sub_q.all())

    child_q = await db.execute(
        select(PromptCluster.id).where(
            PromptCluster.parent_id.in_(parent_ids),
            PromptCluster.state.notin_(EXCLUDED_STRUCTURAL_STATES),
        )
    )
    return [r[0] for r in child_q.all()]


async def compute_qualifier_cascade(
    db: AsyncSession,
    domain_node: PromptCluster,
    *,
    meta_node: PromptCluster | None = None,
    include_sub_domain_descendants: bool = True,
) -> CascadeResult:
    """Run the three-source qualifier cascade over a domain's optimizations.

    Pure (read-only) analytics — no state mutation, no side effects beyond
    SELECTs.  Shared primitive consumed by the readiness service **and** the
    warm-path sub-domain discovery/dissolution in
    ``TaxonomyEngine._propose_sub_domains`` / ``_reevaluate_sub_domains`` —
    single implementation, no drift.

    Args:
        db: Async SQLAlchemy session.
        domain_node: Node whose children are scanned for qualifier signals.
        meta_node: Node whose ``cluster_metadata`` (``generated_qualifiers``,
            ``signal_keywords``) supplies the cascade vocabulary.  Defaults to
            ``domain_node``.  Sub-domain re-evaluation passes the parent
            domain here because vocabulary always lives on the parent.
        include_sub_domain_descendants: When True, scans clusters under
            existing sub-domain children as well as direct children — matches
            engine discovery semantics.  Set False for per-sub-domain
            re-evaluation (sub-domains don't nest).
    """
    child_ids = await _collect_child_cluster_ids(
        db, domain_node,
        include_sub_domain_descendants=include_sub_domain_descendants,
    )
    if not child_ids:
        return CascadeResult(
            total_opts=0,
            qualifier_counts={},
            source_breakdown={src: 0 for src in _SOURCES},
            per_qualifier_sources={},
            qualifier_to_cluster_ids={},
            dynamic_keywords=(),
            generated_qualifiers_present=False,
        )

    opt_q = await db.execute(
        select(
            Optimization.domain_raw,
            Optimization.intent_label,
            Optimization.cluster_id,
            Optimization.raw_prompt,
        ).where(Optimization.cluster_id.in_(child_ids))
    )
    opt_rows = opt_q.all()
    total_opts = len(opt_rows)

    meta = read_meta((meta_node or domain_node).cluster_metadata)
    generated_qualifiers: dict[str, list[str]] = {}
    cached_vocab = meta.get("generated_qualifiers")
    if isinstance(cached_vocab, dict):
        generated_qualifiers = cached_vocab

    dynamic_keywords: list[tuple[str, float]] = []
    for item in meta.get("signal_keywords", []) or []:
        try:
            kw, weight = item[0], float(item[1])
        except (IndexError, TypeError, ValueError):
            continue
        if isinstance(kw, str) and len(kw) >= 3 and weight >= 0.5:
            dynamic_keywords.append((kw, weight))

    known_qualifiers: set[str] = set(generated_qualifiers.keys())
    for kw, _ in dynamic_keywords:
        kw_lower = kw.lower()
        known_qualifiers.add(kw_lower)
        known_qualifiers.add(kw_lower.replace(" ", "-"))

    qualifier_counts: Counter[str] = Counter()
    per_qualifier_sources: dict[str, dict[str, int]] = {}
    qualifier_to_cluster_ids: dict[str, set[str]] = {}
    source_breakdown: Counter[str] = Counter()

    # Lazy import to avoid circular dependency
    from app.services.domain_signal_loader import DomainSignalLoader

    def _record(qualifier: str, source: str, cluster_id: str) -> None:
        qualifier_counts[qualifier] += 1
        per_qualifier_sources.setdefault(qualifier, {}).setdefault(source, 0)
        per_qualifier_sources[qualifier][source] += 1
        qualifier_to_cluster_ids.setdefault(qualifier, set()).add(cluster_id)
        source_breakdown[source] += 1

    for domain_raw, intent_label, cluster_id, raw_prompt in opt_rows:
        qualifier: str | None = None

        # Source 1: parse_domain on domain_raw
        if domain_raw:
            _, q = parse_domain(domain_raw)
            if q:
                q_normalized = q.lower().replace(" ", "-")
                if q in known_qualifiers or q_normalized in known_qualifiers:
                    qualifier = q
                    _record(qualifier, _SOURCE_DOMAIN_RAW, cluster_id)

        # Source 2: intent_label vs organic vocab
        if not qualifier and intent_label and generated_qualifiers:
            intent_lower = intent_label.lower()
            best_q, best_hits = DomainSignalLoader.find_best_qualifier(
                intent_lower, generated_qualifiers,
            )
            if best_q and best_hits >= SUB_DOMAIN_QUALIFIER_MIN_KEYWORD_HITS:
                qualifier = best_q
                _record(qualifier, _SOURCE_INTENT_LABEL, cluster_id)

        # Source 3: raw_prompt vs dynamic TF-IDF keywords
        if not qualifier and raw_prompt and dynamic_keywords:
            prompt_lower = raw_prompt.lower()
            intent_lower_s3 = (intent_label or "").lower()
            best_dyn: str | None = None
            best_dyn_weight = 0.0
            dyn_hits = 0
            for kw, weight in dynamic_keywords:
                kw_lower = kw.lower()
                if kw_lower in prompt_lower:
                    dyn_hits += 1
                    effective_weight = weight + (
                        0.5 if kw_lower in intent_lower_s3 else 0.0
                    )
                    if effective_weight > best_dyn_weight:
                        best_dyn_weight = effective_weight
                        best_dyn = kw
            if best_dyn:
                raw_weight = best_dyn_weight - (
                    0.5 if best_dyn.lower() in intent_lower_s3 else 0.0
                )
                min_hits = 1 if raw_weight >= 0.8 else 2
                if dyn_hits >= min_hits:
                    qualifier = best_dyn.lower().replace(" ", "-")
                    _record(qualifier, _SOURCE_TF_IDF, cluster_id)

    return CascadeResult(
        total_opts=total_opts,
        qualifier_counts=dict(qualifier_counts),
        source_breakdown={src: source_breakdown.get(src, 0) for src in _SOURCES},
        per_qualifier_sources=per_qualifier_sources,
        qualifier_to_cluster_ids=qualifier_to_cluster_ids,
        dynamic_keywords=tuple(dynamic_keywords),
        generated_qualifiers_present=bool(generated_qualifiers),
    )


# ---------------------------------------------------------------------------
# Sub-domain emergence
# ---------------------------------------------------------------------------


def _emergence_threshold(total_opts: int) -> float:
    """Return the adaptive consistency threshold for a domain of N opts."""
    return max(
        SUB_DOMAIN_QUALIFIER_CONSISTENCY_LOW,
        SUB_DOMAIN_QUALIFIER_CONSISTENCY_HIGH
        - SUB_DOMAIN_QUALIFIER_SCALE_RATE * total_opts,
    )


def _threshold_formula(total_opts: int, threshold: float) -> str:
    """Render the adaptive threshold formula as a human-readable string."""
    raw = (
        SUB_DOMAIN_QUALIFIER_CONSISTENCY_HIGH
        - SUB_DOMAIN_QUALIFIER_SCALE_RATE * total_opts
    )
    return (
        f"max({SUB_DOMAIN_QUALIFIER_CONSISTENCY_LOW:.2f}, "
        f"{SUB_DOMAIN_QUALIFIER_CONSISTENCY_HIGH:.2f} - "
        f"{SUB_DOMAIN_QUALIFIER_SCALE_RATE:.3f} * {total_opts}) "
        f"= max({SUB_DOMAIN_QUALIFIER_CONSISTENCY_LOW:.2f}, {raw:.3f}) "
        f"= {threshold:.3f}"
    )


def _build_candidate(
    qualifier: str,
    count: int,
    total_opts: int,
    cascade: CascadeResult,
) -> QualifierCandidate:
    sources = cascade.per_qualifier_sources.get(qualifier, {})
    breakdown = {src: sources.get(src, 0) for src in _SOURCES}
    return QualifierCandidate(
        qualifier=qualifier,
        count=count,
        consistency=(count / total_opts) if total_opts else 0.0,
        dominant_source=cascade.dominant_source_for(qualifier),
        source_breakdown=breakdown,
        cluster_breadth=len(cascade.qualifier_to_cluster_ids.get(qualifier, set())),
    )


async def compute_sub_domain_emergence(
    db: AsyncSession,
    domain_node: PromptCluster,
    *,
    cascade: CascadeResult | None = None,
) -> SubDomainEmergenceReport:
    """Return the readiness-to-promote report for a domain.

    Evaluates the top qualifier against:
      1. count >= ``SUB_DOMAIN_QUALIFIER_MIN_MEMBERS``
      2. consistency >= adaptive threshold
      3. cluster_breadth >= ``SUB_DOMAIN_MIN_CLUSTER_BREADTH``
         (prevents single-cluster concentration)

    Ready ⇔ all three.  Blocker records the first check that failed.
    Shares gating constants with ``engine._propose_sub_domains`` so
    the primitive's ``ready`` flag and engine promotion agree by construction.
    """
    cascade = cascade or await compute_qualifier_cascade(db, domain_node)
    total_opts = cascade.total_opts
    threshold = _emergence_threshold(total_opts)
    formula = _threshold_formula(total_opts, threshold)

    counts_sorted = sorted(
        cascade.qualifier_counts.items(), key=lambda kv: (-kv[1], kv[0]),
    )

    if not counts_sorted:
        return SubDomainEmergenceReport(
            threshold=threshold,
            threshold_formula=formula,
            min_member_count=SUB_DOMAIN_QUALIFIER_MIN_MEMBERS,
            total_opts=total_opts,
            top_candidate=None,
            gap_to_threshold=None,
            ready=False,
            blocked_reason="no_candidates",
            runner_ups=[],
            tier="inert",
        )

    top_q, top_count = counts_sorted[0]
    top_candidate = _build_candidate(top_q, top_count, total_opts, cascade)
    runner_ups = [
        _build_candidate(q, c, total_opts, cascade)
        for q, c in counts_sorted[1:6]
    ]

    gap = threshold - top_candidate.consistency  # negative = over threshold

    # Evaluate blockers in priority order
    below_min = top_count < SUB_DOMAIN_QUALIFIER_MIN_MEMBERS
    below_threshold = top_candidate.consistency < threshold
    single_cluster = top_candidate.cluster_breadth < SUB_DOMAIN_MIN_CLUSTER_BREADTH

    ready = not (below_min or below_threshold or single_cluster)

    blocked_reason: (
        Literal["no_candidates", "below_threshold", "insufficient_members", "single_cluster", "none"] | None
    ) = None
    if not ready:
        if below_min:
            blocked_reason = "insufficient_members"
        elif below_threshold:
            blocked_reason = "below_threshold"
        else:
            # single_cluster blocker — threshold + MIN_MEMBERS both satisfied
            blocked_reason = "single_cluster"

    # Tier: ready ▸ warming ▸ inert
    tier: Literal["ready", "warming", "inert"]
    if ready:
        tier = "ready"
    elif (
        not below_min
        and not single_cluster
        and gap <= _WARMING_GAP
    ):
        tier = "warming"
    else:
        tier = "inert"

    return SubDomainEmergenceReport(
        threshold=threshold,
        threshold_formula=formula,
        min_member_count=SUB_DOMAIN_QUALIFIER_MIN_MEMBERS,
        total_opts=total_opts,
        top_candidate=top_candidate,
        gap_to_threshold=gap,
        ready=ready,
        blocked_reason=blocked_reason if not ready else "none",
        runner_ups=runner_ups,
        tier=tier,
    )


# ---------------------------------------------------------------------------
# Domain stability
# ---------------------------------------------------------------------------


def _domain_age_hours(domain_node: PromptCluster) -> float:
    created = domain_node.created_at
    if created is None:
        return 0.0
    if isinstance(created, str):
        try:
            created = datetime.fromisoformat(created)
        except (ValueError, TypeError):
            return 0.0
    if created.tzinfo is not None:
        created = created.replace(tzinfo=None)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    delta = now - created
    return max(0.0, delta.total_seconds() / 3600.0)


async def compute_domain_stability(
    db: AsyncSession,
    domain_node: PromptCluster,
) -> DomainStabilityReport:
    """Return the stability (dissolution-readiness) report for a domain.

    Mirrors ``engine._reevaluate_domains`` guard logic.  Never mutates state.
    """
    label = (domain_node.label or "").lower()
    is_general = label == "general"

    # Guard 2: sub-domain anchor — child clusters with state="domain"
    sub_q = await db.execute(
        select(func.count()).where(
            PromptCluster.parent_id == domain_node.id,
            PromptCluster.state == "domain",
        )
    )
    sub_count = int(sub_q.scalar() or 0)
    has_sub_domain_anchor = sub_count > 0

    # Child cluster count (used for ceiling guard AND member_count report field)
    child_q = await db.execute(
        select(PromptCluster.id).where(
            PromptCluster.parent_id == domain_node.id,
            PromptCluster.state.notin_(EXCLUDED_STRUCTURAL_STATES),
        )
    )
    child_ids = [r[0] for r in child_q.all()]
    member_count = len(child_ids)
    above_member_ceiling = member_count > DOMAIN_DISSOLUTION_MEMBER_CEILING

    # Guard 3: age
    age_hours = _domain_age_hours(domain_node)
    age_eligible = age_hours >= DOMAIN_DISSOLUTION_MIN_AGE_HOURS

    # Guard 4: Source-1 consistency (domain_raw primary label match rate)
    total_opts = 0
    consistency = 0.0
    if child_ids:
        opt_q = await db.execute(
            select(Optimization.domain_raw).where(
                Optimization.cluster_id.in_(child_ids),
            )
        )
        domain_raws = [r[0] for r in opt_q.all()]
        total_opts = len(domain_raws)
        if total_opts > 0:
            matching = 0
            for dr in domain_raws:
                if not dr:
                    continue
                primary, _ = parse_domain(dr)
                if primary == label:
                    matching += 1
            consistency = matching / total_opts

    consistency_above_floor = consistency >= DOMAIN_DISSOLUTION_CONSISTENCY_FLOOR

    guards = DomainStabilityGuards(
        general_protected=is_general,
        has_sub_domain_anchor=has_sub_domain_anchor,
        age_eligible=age_eligible,
        above_member_ceiling=above_member_ceiling,
        consistency_above_floor=consistency_above_floor,
    )

    # Would-dissolve: ALL guards against dissolution currently failing
    would_dissolve = (
        not is_general
        and not has_sub_domain_anchor
        and age_eligible
        and not above_member_ceiling
        and not consistency_above_floor
    )

    # Tier
    stability_tier: Literal["healthy", "guarded", "critical"]
    if is_general or consistency >= _DOMAIN_HEALTHY_FLOOR:
        stability_tier = "healthy"
    elif would_dissolve:
        stability_tier = "critical"
    else:
        stability_tier = "guarded"

    # Dissolution risk: composite [0,1] of failing guards weighted toward
    # would_dissolve outcome. Each of the four non-general-protected guards
    # that is failing contributes 0.25.
    failing = 0
    if not has_sub_domain_anchor:
        failing += 1
    if age_eligible:
        failing += 1
    if not above_member_ceiling:
        failing += 1
    if not consistency_above_floor:
        failing += 1
    dissolution_risk = 0.0 if is_general else failing / 4.0

    return DomainStabilityReport(
        consistency=consistency,
        dissolution_floor=DOMAIN_DISSOLUTION_CONSISTENCY_FLOOR,
        hysteresis_creation_threshold=_DOMAIN_CREATION_HYSTERESIS,
        age_hours=age_hours,
        min_age_hours=DOMAIN_DISSOLUTION_MIN_AGE_HOURS,
        member_count=member_count,
        member_ceiling=DOMAIN_DISSOLUTION_MEMBER_CEILING,
        sub_domain_count=sub_count,
        total_opts=total_opts,
        guards=guards,
        tier=stability_tier,
        dissolution_risk=dissolution_risk,
        would_dissolve=would_dissolve,
    )


# ---------------------------------------------------------------------------
# Unified report + cache
# ---------------------------------------------------------------------------


@dataclass
class _CacheEntry:
    report: DomainReadinessReport
    stored_at: float
    member_count: int


_cache: dict[str, _CacheEntry] = {}


def clear_cache() -> None:
    """Drop all cached readiness reports (test hook + manual invalidation)."""
    _cache.clear()


def _evict_expired(now: float) -> None:
    """Remove expired entries and cap the cache at ``_CACHE_MAX_ENTRIES``."""
    for domain_id, entry in list(_cache.items()):
        if now - entry.stored_at >= _CACHE_TTL_SECONDS:
            _cache.pop(domain_id, None)
    if len(_cache) > _CACHE_MAX_ENTRIES:
        # Evict oldest first
        victims = sorted(_cache.items(), key=lambda kv: kv[1].stored_at)
        for domain_id, _ in victims[: len(_cache) - _CACHE_MAX_ENTRIES]:
            _cache.pop(domain_id, None)


async def _count_domain_opts(db: AsyncSession, domain_node: PromptCluster) -> int:
    """Fast opt-count query used as cache-key input.

    Counts the same opt set that the cascade will later scan, so the key
    changes the instant a new optimization lands in the domain's tree.
    """
    child_ids = await _collect_child_cluster_ids(db, domain_node)
    if not child_ids:
        return 0
    q = await db.execute(
        select(func.count()).where(Optimization.cluster_id.in_(child_ids))
    )
    return int(q.scalar() or 0)


async def compute_domain_readiness(
    db: AsyncSession,
    domain_node: PromptCluster,
    *,
    fresh: bool = False,
) -> DomainReadinessReport:
    """Compose stability + emergence for a domain, with TTL caching.

    Cache is keyed by ``(domain_id, total_opts)``.  Adding an optimization
    (or moving one into/out of the domain's tree) changes the key and forces
    a recompute.  ``fresh=True`` bypasses the cache entirely.
    """
    now = time.monotonic()
    _evict_expired(now)

    current_member_count = await _count_domain_opts(db, domain_node)

    if not fresh:
        entry = _cache.get(domain_node.id)
        if (
            entry is not None
            and entry.member_count == current_member_count
            and (now - entry.stored_at) < _CACHE_TTL_SECONDS
        ):
            return entry.report

    cascade = await compute_qualifier_cascade(db, domain_node)
    emergence = await compute_sub_domain_emergence(db, domain_node, cascade=cascade)
    stability = await compute_domain_stability(db, domain_node)

    report = DomainReadinessReport(
        domain_id=domain_node.id,
        domain_label=domain_node.label,
        member_count=stability.member_count,
        stability=stability,
        emergence=emergence,
        computed_at=datetime.now(timezone.utc),
    )

    _cache[domain_node.id] = _CacheEntry(
        report=report,
        stored_at=now,
        member_count=current_member_count,
    )

    # Observability — debounced at 5s/domain
    _maybe_emit_events(domain_node, report, now)

    return report


async def compute_all_domain_readiness(
    db: AsyncSession,
    *,
    fresh: bool = False,
) -> list[DomainReadinessReport]:
    """Return readiness reports for every top-level (non-sub) domain.

    Top-level domain = ``state="domain"`` AND either no parent (legacy pre-ADR-005)
    or parent is a project node (``state="project"``).  Sub-domains — whose parent
    is also a domain — are excluded.

    Sorted by (critical → healthy on stability tier, then emergence gap
    ascending) so the most action-relevant domains lead.
    """
    # Load candidate domain nodes in one pass, then filter by parent state.
    q = await db.execute(
        select(PromptCluster).where(PromptCluster.state == "domain")
    )
    candidates = list(q.scalars().all())

    # Resolve parent states in a single query (skip nulls → legacy top-level).
    parent_ids = {d.parent_id for d in candidates if d.parent_id is not None}
    parent_state_by_id: dict[str, str] = {}
    if parent_ids:
        parent_q = await db.execute(
            select(PromptCluster.id, PromptCluster.state).where(
                PromptCluster.id.in_(parent_ids)
            )
        )
        parent_state_by_id = {row[0]: row[1] for row in parent_q.all()}

    domains = [
        d
        for d in candidates
        if d.parent_id is None
        or parent_state_by_id.get(d.parent_id) == "project"
    ]
    reports: list[DomainReadinessReport] = []
    for domain in domains:
        reports.append(await compute_domain_readiness(db, domain, fresh=fresh))

    stability_rank = {"critical": 0, "guarded": 1, "healthy": 2}
    reports.sort(
        key=lambda r: (
            stability_rank.get(r.stability.tier, 3),
            (r.emergence.gap_to_threshold if r.emergence.gap_to_threshold is not None else float("inf")),
            r.domain_label,
        )
    )
    return reports


# ---------------------------------------------------------------------------
# Observability (debounced)
# ---------------------------------------------------------------------------


_event_debounce: dict[str, float] = {}
_EVENT_DEBOUNCE_SECONDS = 5.0


class TierCrossing(TypedDict):
    """Stable payload shape for a single tier-axis crossing.

    Consumed by ``_detect_crossings`` and (in Task 4) by ``_publish_crossings``.
    """

    axis: Literal["emergence", "stability"]
    from_tier: str
    to_tier: str


@dataclass
class _TierHistoryEntry:
    """Per-domain rolling state for crossing detection.

    ``stable_*_tier`` is the last tier we considered "settled" (i.e., observed
    at least HYSTERESIS_CYCLES times in a row).  ``pending_*_tier`` and
    ``pending_*_count`` track the streak of a candidate new tier.  A crossing
    fires when ``pending_count`` reaches HYSTERESIS_CYCLES *and* the cooldown
    window has elapsed since the last fire for that axis.
    """

    stable_emergence_tier: str | None = None
    pending_emergence_tier: str | None = None
    pending_emergence_count: int = 0
    last_emergence_fire_at: float = 0.0

    stable_stability_tier: str | None = None
    pending_stability_tier: str | None = None
    pending_stability_count: int = 0
    last_stability_fire_at: float = 0.0


_tier_history: dict[str, _TierHistoryEntry] = {}


def clear_tier_history() -> None:
    """Drop all per-domain crossing history (test hook + manual reset)."""
    _tier_history.clear()


def _process_axis_crossing(
    *,
    entry: _TierHistoryEntry,
    axis: Literal["emergence", "stability"],
    new_tier: str,
    now: float,
) -> TierCrossing | None:
    """Update entry for one axis; return a ``TierCrossing`` if one fires."""
    if axis == "emergence":
        stable_attr = "stable_emergence_tier"
        pending_attr = "pending_emergence_tier"
        count_attr = "pending_emergence_count"
        last_attr = "last_emergence_fire_at"
    else:
        stable_attr = "stable_stability_tier"
        pending_attr = "pending_stability_tier"
        count_attr = "pending_stability_count"
        last_attr = "last_stability_fire_at"

    stable_tier = getattr(entry, stable_attr)
    if stable_tier is None:
        # First observation — record baseline, no crossing.
        setattr(entry, stable_attr, new_tier)
        setattr(entry, pending_attr, None)
        setattr(entry, count_attr, 0)
        return None

    if new_tier == stable_tier:
        # No transition; clear any in-flight pending state.
        setattr(entry, pending_attr, None)
        setattr(entry, count_attr, 0)
        return None

    # Different from stable tier — accumulate pending streak.
    pending = getattr(entry, pending_attr)
    if pending == new_tier:
        setattr(entry, count_attr, getattr(entry, count_attr) + 1)
    else:
        setattr(entry, pending_attr, new_tier)
        setattr(entry, count_attr, 1)

    if getattr(entry, count_attr) < READINESS_CROSSING_HYSTERESIS_CYCLES:
        return None

    # Cooldown: skip if we fired for this axis recently.  Promote stable
    # and clear pending so the cooldown still covers any future re-cross.
    last_fire = getattr(entry, last_attr)
    if last_fire > 0.0 and now - last_fire < READINESS_CROSSING_COOLDOWN_SECONDS:
        setattr(entry, stable_attr, new_tier)
        setattr(entry, pending_attr, None)
        setattr(entry, count_attr, 0)
        return None

    crossing = TierCrossing(axis=axis, from_tier=stable_tier, to_tier=new_tier)
    setattr(entry, stable_attr, new_tier)
    setattr(entry, pending_attr, None)
    setattr(entry, count_attr, 0)
    setattr(entry, last_attr, now)
    return crossing


def _detect_crossings(
    report: DomainReadinessReport,
    *,
    now: float,
) -> list[TierCrossing]:
    """Compare report tiers against history; return crossings that fired.

    Each ``TierCrossing`` has keys ``axis``, ``from_tier``, ``to_tier``.
    The two axes (emergence and stability) are evaluated independently —
    a spike on one axis never resets the hysteresis counter of the other.

    Side effect: mutates ``_tier_history`` for the report's domain.
    """
    entry = _tier_history.setdefault(report.domain_id, _TierHistoryEntry())
    crossings: list[TierCrossing] = []
    for crossing in (
        _process_axis_crossing(
            entry=entry, axis="emergence",
            new_tier=report.emergence.tier, now=now,
        ),
        _process_axis_crossing(
            entry=entry, axis="stability",
            new_tier=report.stability.tier, now=now,
        ),
    ):
        if crossing is not None:
            crossings.append(crossing)
    return crossings


def _maybe_emit_events(
    domain_node: PromptCluster,
    report: DomainReadinessReport,
    now: float,
) -> None:
    """Emit observability events at most once every 5 seconds per domain."""
    last = _event_debounce.get(domain_node.id, 0.0)
    if now - last < _EVENT_DEBOUNCE_SECONDS:
        return
    _event_debounce[domain_node.id] = now

    try:
        logger = get_event_logger()
    except RuntimeError:
        return

    try:
        logger.log_decision(
            path="api",
            op="readiness",
            decision="sub_domain_readiness_computed",
            cluster_id=domain_node.id,
            context={
                "domain": domain_node.label,
                "tier": report.emergence.tier,
                "ready": report.emergence.ready,
                "total_opts": report.emergence.total_opts,
                "threshold": round(report.emergence.threshold, 3),
                "top_qualifier": (
                    report.emergence.top_candidate.qualifier
                    if report.emergence.top_candidate
                    else None
                ),
                "gap_to_threshold": (
                    round(report.emergence.gap_to_threshold, 3)
                    if report.emergence.gap_to_threshold is not None
                    else None
                ),
                "blocked_reason": report.emergence.blocked_reason,
            },
        )
        logger.log_decision(
            path="api",
            op="readiness",
            decision="domain_stability_computed",
            cluster_id=domain_node.id,
            context={
                "domain": domain_node.label,
                "tier": report.stability.tier,
                "would_dissolve": report.stability.would_dissolve,
                "consistency": round(report.stability.consistency, 3),
                "dissolution_risk": round(report.stability.dissolution_risk, 3),
                "member_count": report.stability.member_count,
                "sub_domain_count": report.stability.sub_domain_count,
                "age_hours": round(report.stability.age_hours, 1),
            },
        )
    except RuntimeError:
        pass
