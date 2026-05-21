"""v0.4.17 P2 — Cross-cutting utilities + ContextVar for the probe service.

Canonical home for the ``current_run_id`` ContextVar (used for
cross-module event correlation) and helper functions used during
grounding/running/reporting (``_apply_scope_filter``, ``_truncate``,
``_commit_with_retry``, ``_stub_dimension_scores``).

This module is a leaf: it has no inter-module dependencies on the other
v0.4.17 P2 split modules (``probe_phases``, ``probe_phase_5``).

History: the v0.4.18 Foundation P3 rename introduced a backward-compat
``current_probe_id`` alias of the same ContextVar object. The alias was
retired in v0.4.34 (16 release cycles later — well beyond the "2+ release
cycles" migration window). ``current_run_id`` is now the only name.
"""
from __future__ import annotations

import asyncio
import fnmatch
import logging
from contextvars import ContextVar

from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.pipeline_contracts import DimensionScores

logger = logging.getLogger(__name__)

# Foundation P3 (v0.4.18): canonical ContextVar renamed to current_run_id.
# The v0.4.18 rename introduced ``current_probe_id`` as a backward-compat
# alias "for 2+ release cycles"; the alias was retired in v0.4.34 (16
# release cycles later — migration window closed).
# C4<->C7 dependency resolution -- declare ContextVar where it is SET (here).
# C7's probe_event_correlation.py imports + adds the inject_probe_id helper.
current_run_id: ContextVar[str | None] = ContextVar(
    "current_run_id", default=None,
)


def _apply_scope_filter(files: list[str], scope: str) -> list[str]:
    """Post-retrieval glob filter.

    ``RepoIndexQuery.query_curated_context`` has no scope parameter, so the
    probe applies the filter here at the boundary.
    """
    if scope == "**/*" or not scope:
        return files
    return [f for f in files if fnmatch.fnmatch(f, scope)]


def _truncate(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 3] + "..."


async def _commit_with_retry(
    db: AsyncSession,
    *,
    max_attempts: int = 5,
    probe_id: str = "",
) -> None:
    """Commit with exponential backoff on SQLite "database is locked".

    The canonical batch path has just committed N Optimization INSERTs +
    OptimizationPattern joins + cluster updates immediately before. The
    warm-path engine runs in the same process and may hold writers
    concurrently. Under SQLite WAL the final ProbeRun UPDATE can hit
    transient lock contention even with busy_timeout=30s. Retrying with
    backoff (0.5s, 1s, 2s, 4s, 8s -- max ~15s) catches the window
    without losing the terminal-state write.

    Raises the underlying error after ``max_attempts`` so the
    orchestrator's top-level except handler still marks the row failed.
    """
    import sqlalchemy.exc as _sa_exc

    delay = 0.5
    last_exc: Exception | None = None
    for attempt in range(max_attempts):
        try:
            await db.commit()
            if attempt > 0:
                logger.info(
                    "probe %s commit succeeded on attempt %d",
                    probe_id, attempt + 1,
                )
            return
        except _sa_exc.OperationalError as exc:
            last_exc = exc
            if "database is locked" not in str(exc):
                raise
            logger.warning(
                "probe %s commit hit lock (attempt %d/%d); backing off %.1fs",
                probe_id, attempt + 1, max_attempts, delay,
            )
            try:
                await db.rollback()
            except Exception:
                pass
            await asyncio.sleep(delay)
            delay = min(delay * 2, 8.0)
    if last_exc is not None:
        raise last_exc


def _stub_dimension_scores() -> DimensionScores:
    """Per-prompt deterministic baseline scores.

    Tier 1 ProbeService synthesizes per-prompt results in-memory rather than
    calling the full pipeline (which has heavy provider/loader dependencies
    not present in unit tests). The dimension values are intentionally
    asymmetric so analysis-vs-default weight differences surface in the
    aggregate (AC-C4-6).

    Default-weights overall: 6.80; analysis-weights overall: 7.30.
    """
    return DimensionScores(
        clarity=9.0,
        specificity=9.0,
        structure=8.0,
        faithfulness=4.0,
        conciseness=4.0,
    )


__all__ = [
    "current_run_id",
    "_apply_scope_filter",
    "_truncate",
    "_commit_with_retry",
    "_stub_dimension_scores",
]
