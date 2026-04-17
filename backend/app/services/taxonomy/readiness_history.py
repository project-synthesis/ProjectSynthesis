"""Readiness history persistence — JSONL snapshots per warm cycle.

One file per UTC day under ``data/readiness_history/``.  Each row is a
``ReadinessSnapshot`` serialized via ``model_dump(mode='json')``.  Designed
to mirror ``data/taxonomy_events/`` conventions: sync ``path.open("a")``
append (no aiofiles dependency), daily rotation, zero-migration storage.
Mirrors the pattern in ``backend/app/services/taxonomy/event_logger.py``
(``log_decision`` uses sync ``daily_file.open("a")``) — we wrap the call
in ``asyncio.to_thread`` so warm-path Phase 5 never blocks on disk I/O.

The writer is fire-and-forget (``record_snapshot`` swallows IO errors and
logs them) so warm-path Phase 5 is never blocked by disk hiccups.

Copyright 2025-2026 Project Synthesis contributors.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import DATA_DIR
from app.schemas.sub_domain_readiness import (
    DomainReadinessReport,
    ReadinessSnapshot,
)
from app.services.taxonomy._constants import (
    READINESS_HISTORY_DIR_NAME,
)

__all__ = ["record_snapshot"]

logger = logging.getLogger(__name__)


def _resolve_dir(base_dir: Path | None = None) -> Path:
    if base_dir is not None:
        return base_dir
    return DATA_DIR / READINESS_HISTORY_DIR_NAME


def _file_for_day(day: datetime, target_dir: Path) -> Path:
    """Return the JSONL path inside ``target_dir`` for ``day`` (UTC date).

    ``target_dir`` is the already-resolved directory — pass the result of
    ``_resolve_dir()`` once per call rather than re-resolving here.

    Naive datetimes are assumed-UTC; aware datetimes are converted to UTC
    before the date is extracted so daily rotation never drifts with the
    caller's local timezone.
    """
    if day.tzinfo is not None:
        day = day.astimezone(timezone.utc)
    return target_dir / f"snapshots-{day.strftime('%Y-%m-%d')}.jsonl"


def _snapshot_from_report(report: DomainReadinessReport) -> ReadinessSnapshot:
    return ReadinessSnapshot(
        ts=report.computed_at,
        domain_id=report.domain_id,
        domain_label=report.domain_label,
        consistency=report.stability.consistency,
        dissolution_risk=report.stability.dissolution_risk,
        stability_tier=report.stability.tier,
        emergence_tier=report.emergence.tier,
        top_candidate_gap=report.emergence.gap_to_threshold,
        member_count=report.stability.member_count,
        total_opts=report.stability.total_opts,
    )


def _write_jsonl_row_sync(path: Path, payload: dict[str, Any]) -> None:
    """Sync append writer — mirrors ``TaxonomyEventLogger._daily_file`` usage."""
    with path.open("a", encoding="utf-8") as fp:
        fp.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")


async def record_snapshot(
    report: DomainReadinessReport,
    *,
    base_dir: Path | None = None,
) -> None:
    """Append one snapshot row to today's JSONL file.

    Errors are logged and swallowed — readiness history is observability,
    never load-bearing for warm-path correctness.

    Uses sync ``open("a")`` offloaded via ``asyncio.to_thread`` to match
    the pattern in ``app.services.taxonomy.event_logger.TaxonomyEventLogger.
    log_decision`` (see that module's ``_daily_file().open("a", ...)`` call).
    """
    snap = _snapshot_from_report(report)
    target_dir = _resolve_dir(base_dir)
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        path = _file_for_day(snap.ts, target_dir)
        await asyncio.to_thread(
            _write_jsonl_row_sync, path, snap.model_dump(mode="json"),
        )
    except OSError as exc:
        logger.warning("readiness snapshot write failed: %s", exc)
