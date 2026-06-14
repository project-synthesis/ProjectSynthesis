"""Strategy intelligence resolver for context enrichment.

Merges three signal sources into a single strategy advisory:

1. **Score-based rankings** — top/bottom strategies by ``(task_type, domain)``
   from the Optimization table (C1 domain-relaxed fallback when exact scope
   returns nothing).
2. **User feedback affinities** — approval rates per strategy from the
   AdaptationTracker.
3. **Domain vocabulary** — keyword signals from ``DomainSignalLoader``.

v0.4.40 (Tier 2): the formatted-string ``resolve_strategy_intelligence`` now
delegates to ``resolve_strategy_intelligence_structured`` for the
ranking/blocked computation so the live ContextPanel preview surfaces the
same data as the prompt-injected advisory. Formatted-output is byte-identical
pre/post the split (parity test in ``tests/test_preview_enrichment.py``).

Copyright 2025-2026 Project Synthesis contributors.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StrategyRanking:
    """A single strategy ranked by performance or feedback signal."""

    name: str
    score: float  # 0.0-1.0 for feedback approval_rate, raw avg_score (1-10) for performance
    source: Literal["performance", "feedback"]


@dataclass(frozen=True)
class StructuredStrategyIntel:
    """Structured form of strategy intelligence — consumed by both the
    formatted prompt-injection advisory and the Tier 2 live ContextPanel
    preview endpoint."""

    top_strategies: list[StrategyRanking]
    blocked_strategies: list[str]
    domain_relaxed_fallback: bool


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


async def resolve_strategy_intelligence_structured(
    db: AsyncSession,
    task_type: str,
    domain: str,
) -> StructuredStrategyIntel:
    """Return a structured form of strategy intelligence for UI consumption.

    Consumed by the Tier 2 live ContextPanel preview endpoint AND
    delegated to internally by ``resolve_strategy_intelligence`` so the
    formatted prompt-injection advisory cannot drift from the panel view.

    Returns:
        StructuredStrategyIntel with:
          - top_strategies: ≤3 StrategyRanking entries, sorted by score desc.
            Pulled from the same Optimization table query as the formatted
            helper. The ``source`` field reads "performance" because the
            top-strategies entry-point uses score-based rankings only;
            feedback approvals contribute to ``blocked_strategies`` and
            the formatted helper but are not surfaced as ranked entries
            (avoids ambiguity between rank-by-score and rank-by-approval).
          - blocked_strategies: sorted alphabetically.
          - domain_relaxed_fallback: True iff resolve_performance_signals
            returned fallback_used=True (mirror exactly; blocked path
            never moves this flag — task-type-only by construction).
    """
    try:
        from sqlalchemy import func, select

        from app.models import Optimization

        _strategy_base = select(
            Optimization.strategy_used,
            func.avg(Optimization.overall_score).label("avg_score"),
            func.count().label("n"),
        ).where(
            Optimization.task_type == task_type,
            Optimization.overall_score.isnot(None),
            Optimization.strategy_used.isnot(None),
        )
        perf_q = await db.execute(
            _strategy_base.where(Optimization.domain == domain)
            .group_by(Optimization.strategy_used)
            .having(func.count() >= 3)
            .order_by(func.avg(Optimization.overall_score).desc())
            .limit(3)
        )
        rows = perf_q.all()
        domain_relaxed_fallback = False
        if not rows:
            fallback_q = await db.execute(
                _strategy_base
                .group_by(Optimization.strategy_used)
                .having(func.count() >= 3)
                .order_by(func.avg(Optimization.overall_score).desc())
                .limit(3)
            )
            rows = fallback_q.all()
            if rows:
                domain_relaxed_fallback = True

        top: list[StrategyRanking] = [
            StrategyRanking(name=r.strategy_used, score=float(r.avg_score), source="performance")
            for r in rows
        ]
        top.sort(key=lambda r: r.score, reverse=True)
        top = top[:3]
    except Exception:
        logger.debug("structured strategy ranking failed", exc_info=True)
        top = []
        domain_relaxed_fallback = False

    blocked: list[str] = []
    try:
        from app.services.adaptation_tracker import AdaptationTracker

        tracker = AdaptationTracker(db)
        blocked_set = await tracker.get_blocked_strategies(task_type)
        blocked = sorted(blocked_set)
    except Exception:
        logger.debug("structured blocked resolution failed", exc_info=True)

    return StructuredStrategyIntel(
        top_strategies=top,
        blocked_strategies=blocked,
        domain_relaxed_fallback=domain_relaxed_fallback,
    )


async def resolve_strategy_intelligence(
    db: AsyncSession,
    task_type: str,
    domain: str,
) -> tuple[str | None, bool]:
    """Unified strategy intelligence — merges performance signals + user adaptation feedback.

    v0.4.40: delegates blocked-set resolution to the structured helper for
    zero-drift parity with the Tier 2 ContextPanel preview endpoint. The
    formatted-string output is byte-identical pre/post the split (locked by
    ``tests/test_preview_enrichment.py::TestStrategyIntelligenceParity``).
    """
    sections: list[str] = []
    fallback_used = False

    # 1. Score-based strategy rankings + anti-patterns + domain keywords (formatted)
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
    except Exception:
        logger.debug("Adaptation data resolution failed", exc_info=True)

    # 3. Blocked strategies — sourced from the structured helper so the panel
    # and the prompt-injection advisory cannot drift.
    try:
        structured = await resolve_strategy_intelligence_structured(db, task_type, domain)
        if structured.blocked_strategies:
            sections.append(
                "Blocked strategies (low approval): "
                + ", ".join(structured.blocked_strategies)
            )
    except Exception:
        logger.debug("Structured blocked resolution failed", exc_info=True)

    return ("\n\n".join(sections) if sections else None, fallback_used)


__all__ = [
    "StrategyRanking",
    "StructuredStrategyIntel",
    "resolve_performance_signals",
    "resolve_strategy_intelligence",
    "resolve_strategy_intelligence_structured",
]
