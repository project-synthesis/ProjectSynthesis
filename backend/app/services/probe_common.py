"""v0.4.17 P2 — Cross-cutting utilities + ContextVar for the probe service.

Canonical home for symbols that don't belong to any single probe-pipeline
phase: the ``current_probe_id`` ContextVar (used for cross-module event
correlation), helper functions used during grounding/running/reporting
(``_apply_scope_filter``, ``_truncate``, ``_commit_with_retry``,
``_stub_dimension_scores``).

This module is a leaf: it has no inter-module dependencies on the other
v0.4.17 P2 split modules (``probe_phases``, ``probe_phase_5``).

ContextVar identity is preserved across the split: ``probe_service.py``
re-imports ``current_probe_id`` from this module so the legacy
``from app.services.probe_service import current_probe_id`` keeps working
(Python re-import is a name binding -- same object, same ContextVar
instance).
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
# current_probe_id retained as alias of the SAME object -- preserves the
# `legacy.current_probe_id is common.current_probe_id` identity test in
# tests/test_probe_service_module_split_v0_4_17.py.
# C4<->C7 dependency resolution -- declare ContextVar where it is SET (here).
# C7's probe_event_correlation.py re-exports + adds inject_probe_id helper.
current_run_id: ContextVar[str | None] = ContextVar(
    "current_run_id", default=None,
)
current_probe_id = current_run_id  # backward-compat alias


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
    "current_probe_id",
    "_apply_scope_filter",
    "_truncate",
    "_commit_with_retry",
    "_stub_dimension_scores",
]
