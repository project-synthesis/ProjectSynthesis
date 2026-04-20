"""Strategy intelligence resolver for context enrichment.

Merges three signal sources into a single strategy advisory:

1. **Score-based rankings** — top/bottom strategies by ``(task_type, domain)``
   from the Optimization table (C1 domain-relaxed fallback when exact scope
   returns nothing).
2. **User feedback affinities** — approval rates per strategy from the
   AdaptationTracker.
3. **Domain vocabulary** — keyword signals from ``DomainSignalLoader``.

Standalone async functions — callable from the enrichment service, the
sampling pipeline, the batch pipeline, and the refinement fallback path.

Extracted from ``context_enrichment.py`` (Phase 3A).  Public API is preserved
via re-exports in ``context_enrichment.py``.

Copyright 2025-2026 Project Synthesis contributors.
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


async def resolve_performance_signals(
    db: AsyncSession,
    task_type: str,
    domain: str,
) -> tuple[str | None, bool]:
    """Resolve performance signals: strategy perf by domain, anti-patterns, domain keywords.

    Standalone function — callable from both the enrichment service (instance method)
    and the sampling pipeline (no instance needed). Cheap signals (~150 tokens) from
    the Optimization table, no LLM calls.

    Returns:
        Tuple of (formatted signals text or None, fallback_used flag).
        When the exact domain+task_type query is empty, falls back to
        task_type-only across all domains (C1 domain-relaxed fallback).
    """
    try:
        from sqlalchemy import func, select

        from app.models import Optimization

        lines: list[str] = []

        # 1. Strategy performance by domain+task_type (top 3)
        _strategy_base = select(
            Optimization.strategy_used,
            func.avg(Optimization.overall_score).label("avg_score"),
            func.count().label("n"),
        ).where(
            Optimization.task_type == task_type,
            Optimization.overall_score.isnot(None),
            Optimization.strategy_used.isnot(None),
        )

        # Exact match first (domain + task_type)
        perf_q = await db.execute(
            _strategy_base.where(Optimization.domain == domain)
            .group_by(Optimization.strategy_used)
            .having(func.count() >= 3)
            .order_by(func.avg(Optimization.overall_score).desc())
            .limit(3)
        )
        top_strategies = perf_q.all()
        strategy_fallback = False

        # C1: Domain-relaxed fallback when exact match returns nothing
        if not top_strategies:
            fallback_q = await db.execute(
                _strategy_base
                .group_by(Optimization.strategy_used)
                .having(func.count() >= 3)
                .order_by(func.avg(Optimization.overall_score).desc())
                .limit(3)
            )
            top_strategies = fallback_q.all()
            if top_strategies:
                strategy_fallback = True
                logger.info(
                    "strategy_intelligence: exact=%s+%s empty, fallback to %s-only (%d strategies)",
                    task_type, domain, task_type, len(top_strategies),
                )

        if top_strategies:
            strat_parts = [
                f"{r.strategy_used} ({r.avg_score:.1f}, n={r.n})"
                for r in top_strategies
            ]
            scope = f"{task_type} (across all domains)" if strategy_fallback else f"{domain}+{task_type}"
            lines.append(f"Top strategies for {scope}: " + ", ".join(strat_parts))

        # 2. Anti-patterns: strategies whose average is below 5.5
        anti_q = await db.execute(
            _strategy_base.where(Optimization.domain == domain)
            .group_by(Optimization.strategy_used)
            .having(func.count() >= 3, func.avg(Optimization.overall_score) < 5.5)
            .order_by(func.avg(Optimization.overall_score).asc())
            .limit(2)
        )
        anti_patterns = anti_q.all()
        anti_fallback = False

        # C1: Anti-pattern fallback (independent of strategy fallback)
        if not anti_patterns:
            anti_fb_q = await db.execute(
                _strategy_base
                .group_by(Optimization.strategy_used)
                .having(func.count() >= 3, func.avg(Optimization.overall_score) < 5.5)
                .order_by(func.avg(Optimization.overall_score).asc())
                .limit(2)
            )
            anti_patterns = anti_fb_q.all()
            if anti_patterns:
                anti_fallback = True

        if anti_patterns:
            scope = f"{task_type} (across all domains)" if anti_fallback else f"{domain}+{task_type}"
            for r in anti_patterns:
                lines.append(
                    f"Avoid: {r.strategy_used} averaged {r.avg_score:.1f} "
                    f"for {scope} (n={r.n})"
                )

        # Unified fallback flag — either strategy or anti-pattern needed the fallback
        fallback_used = strategy_fallback or anti_fallback

        # 3. Domain keywords from DomainSignalLoader singleton
        try:
            from app.services.domain_signal_loader import get_signal_loader
            loader = get_signal_loader()
            if loader:
                domain_signals = loader.signals.get(domain, [])
                if domain_signals:
                    keywords = [kw for kw, _weight in domain_signals[:8]]
                    lines.append(
                        f"Domain vocabulary: {', '.join(keywords)}"
                    )
        except Exception:
            pass  # DomainSignalLoader may not be initialized

        return ("\n".join(lines) if lines else None, fallback_used)
    except Exception:
        logger.debug("Performance signals resolution failed", exc_info=True)
        return None, False


async def resolve_strategy_intelligence(
    db: AsyncSession,
    task_type: str,
    domain: str,
) -> tuple[str | None, bool]:
    """Unified strategy intelligence — merges performance signals + user adaptation feedback.

    Combines domain+task-type strategy performance data with user approval ratings
    into a single strategy advisory.

    Standalone function — callable from the enrichment service, sampling pipeline,
    batch pipeline, and refinement fallback paths.

    Returns:
        Tuple of (formatted strategy intelligence string or None, fallback_used flag).
        The fallback flag indicates whether the domain-relaxed fallback (C1) was used
        for performance signals.
    """
    sections: list[str] = []
    fallback_used = False

    # 1. Score-based strategy rankings + anti-patterns + domain keywords
    perf, fallback_used = await resolve_performance_signals(db, task_type, domain)
    if perf:
        sections.append(perf)

    # 2. Feedback-based affinities from AdaptationTracker
    try:
        from app.services.adaptation_tracker import AdaptationTracker

        tracker = AdaptationTracker(db)
        affinities = await tracker.get_affinities(task_type)

        if affinities:
            aff_lines: list[str] = []
            for strategy, data in sorted(
                affinities.items(),
                key=lambda x: x[1]["approval_rate"],
                reverse=True,
            ):
                total = data["thumbs_up"] + data["thumbs_down"]
                rate = data["approval_rate"]
                aff_lines.append(
                    f"  {strategy}: {rate:.0%} approval ({total} feedbacks)"
                )
            sections.append("User feedback:\n" + "\n".join(aff_lines))

        # 3. Blocked strategies (approval < 0.3 with 5+ feedbacks)
        blocked = await tracker.get_blocked_strategies(task_type)
        if blocked:
            sections.append(
                "Blocked strategies (low approval): "
                + ", ".join(sorted(blocked))
            )
    except Exception:
        logger.debug("Adaptation data resolution failed", exc_info=True)

    return ("\n\n".join(sections) if sections else None, fallback_used)


__all__ = ["resolve_performance_signals", "resolve_strategy_intelligence"]
