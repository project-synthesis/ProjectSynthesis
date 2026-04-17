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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import ValidationError

from app.config import DATA_DIR
from app.schemas.sub_domain_readiness import (
    DomainReadinessReport,
    ReadinessHistoryPoint,
    ReadinessHistoryResponse,
    ReadinessSnapshot,
)
from app.services.taxonomy._constants import (
    READINESS_HISTORY_BUCKET_THRESHOLD_DAYS,
    READINESS_HISTORY_DIR_NAME,
    READINESS_HISTORY_RETENTION_DAYS,
)

__all__ = ["record_snapshot", "query_history", "prune_old_snapshots"]

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


HistoryWindow = Literal["24h", "7d", "30d"]

_WINDOW_DAYS: dict[HistoryWindow, int] = {"24h": 1, "7d": 7, "30d": 30}


def _iter_files_for_window(
    *, days: int, base_dir: Path | None = None,
) -> list[Path]:
    """Return existing snapshot files spanning the last N days (inclusive)."""
    target_dir = _resolve_dir(base_dir)
    if not target_dir.exists():
        return []
    today = datetime.now(timezone.utc)
    files: list[Path] = []
    for day_offset in range(days + 1):  # +1 → catch midnight rollover
        day = today - timedelta(days=day_offset)
        path = _file_for_day(day, target_dir)
        if path.exists():
            files.append(path)
    return files


def _bucket_key_hour(ts: datetime) -> datetime:
    """Floor ``ts`` to the enclosing UTC hour — the bucket key for aggregation."""
    return ts.replace(minute=0, second=0, microsecond=0)


def _bucket_points(snapshots: list[ReadinessSnapshot]) -> list[ReadinessHistoryPoint]:
    """Collapse hourly buckets of snapshots into single aggregate points.

    Aggregation semantics (documented for downstream consumers):

    * ``consistency``, ``dissolution_risk``, ``top_candidate_gap`` → arithmetic
      mean across the bucket (``top_candidate_gap`` skips ``None`` entries;
      returns ``None`` only when every snapshot in the bucket lacks a gap).
    * ``stability_tier`` / ``emergence_tier`` → **tier of the LATEST snapshot
      in the bucket** (by ``ts``), not a tier derived from the mean.  This
      preserves the most recent classification without duplicating the upstream
      tier-threshold logic in ``compute_domain_stability()`` /
      ``compute_sub_domain_emergence()``.  UI renderers that need a
      mean-derived tier should recompute locally from the returned means.
    * Output is ordered newest-bucket first to match ``_raw_points``.
    """
    buckets: dict[datetime, list[ReadinessSnapshot]] = {}
    for s in snapshots:
        buckets.setdefault(_bucket_key_hour(s.ts), []).append(s)

    points: list[ReadinessHistoryPoint] = []
    for bucket_ts, items in buckets.items():
        items_sorted = sorted(items, key=lambda x: x.ts)
        latest = items_sorted[-1]
        avg_consistency = sum(s.consistency for s in items) / len(items)
        avg_risk = sum(s.dissolution_risk for s in items) / len(items)
        gaps = [s.top_candidate_gap for s in items if s.top_candidate_gap is not None]
        avg_gap = (sum(gaps) / len(gaps)) if gaps else None
        points.append(
            ReadinessHistoryPoint(
                ts=bucket_ts,
                consistency=avg_consistency,
                dissolution_risk=avg_risk,
                top_candidate_gap=avg_gap,
                stability_tier=latest.stability_tier,
                emergence_tier=latest.emergence_tier,
                is_bucket_mean=True,
            )
        )
    return sorted(points, key=lambda p: p.ts, reverse=True)


def _raw_points(snapshots: list[ReadinessSnapshot]) -> list[ReadinessHistoryPoint]:
    """Project snapshots to history points in newest-first order, no aggregation."""
    return [
        ReadinessHistoryPoint(
            ts=s.ts,
            consistency=s.consistency,
            dissolution_risk=s.dissolution_risk,
            top_candidate_gap=s.top_candidate_gap,
            stability_tier=s.stability_tier,
            emergence_tier=s.emergence_tier,
            is_bucket_mean=False,
        )
        for s in sorted(snapshots, key=lambda x: x.ts, reverse=True)
    ]


async def query_history(
    *,
    domain_id: str,
    domain_label: str,
    window: HistoryWindow,
    base_dir: Path | None = None,
) -> ReadinessHistoryResponse:
    """Read snapshots for ``domain_id`` over ``window`` (24h|7d|30d).

    For windows >= READINESS_HISTORY_BUCKET_THRESHOLD_DAYS, snapshots are
    aggregated into hourly bucket means to bound payload size.
    """
    if window not in _WINDOW_DAYS:
        raise ValueError(f"invalid window: {window!r} (use 24h|7d|30d)")
    days = _WINDOW_DAYS[window]
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    snapshots: list[ReadinessSnapshot] = []
    for path in _iter_files_for_window(days=days, base_dir=base_dir):
        try:
            with path.open("r", encoding="utf-8") as fp:
                for line in fp:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if row.get("domain_id") != domain_id:
                        continue
                    try:
                        snap = ReadinessSnapshot.model_validate(row)
                    except ValidationError:
                        # Stale schema row (older snapshot layout or manual edit).
                        # Skip silently — observability never blocks on history gaps.
                        continue
                    if snap.ts < cutoff:
                        continue
                    snapshots.append(snap)
        except OSError as exc:
            logger.warning("readiness history read failed for %s: %s", path, exc)

    bucket = days >= READINESS_HISTORY_BUCKET_THRESHOLD_DAYS
    points = _bucket_points(snapshots) if bucket else _raw_points(snapshots)
    return ReadinessHistoryResponse(
        domain_id=domain_id,
        domain_label=domain_label,
        window=window,
        bucketed=bucket,
        points=points,
    )


def prune_old_snapshots(*, base_dir: Path | None = None) -> int:
    """Delete snapshot files older than ``READINESS_HISTORY_RETENTION_DAYS``.

    Scans ``target_dir`` for ``snapshots-YYYY-MM-DD.jsonl`` files, parses
    the date from each filename (the only authoritative timestamp — file
    mtime can drift on restores/rsync), and unlinks entries whose day is
    strictly before the retention cutoff.  A file whose day equals the
    cutoff is kept (boundary-inclusive retention, matching the 30-day
    rotation convention used by ``data/taxonomy_events/``).

    Failure modes — never raise, always return a count:

    * Missing ``target_dir`` → returns 0.
    * Filenames that don't match the date pattern → skipped silently
      (``ValueError`` from ``strptime``).
    * ``OSError`` on ``unlink`` (e.g. permission denied, concurrent
      deletion) → logged at WARNING, iteration continues.

    Safe to run concurrently with ``record_snapshot``: ``unlink`` is
    atomic, and the writer re-creates the file on next append via
    ``open("a")``.
    """
    target_dir = _resolve_dir(base_dir)
    if not target_dir.exists():
        return 0
    cutoff = datetime.now(timezone.utc) - timedelta(
        days=READINESS_HISTORY_RETENTION_DAYS,
    )
    removed = 0
    for path in target_dir.glob("snapshots-*.jsonl"):
        try:
            date_part = path.stem.replace("snapshots-", "")
            file_day = datetime.strptime(date_part, "%Y-%m-%d").replace(
                tzinfo=timezone.utc,
            )
        except ValueError:
            continue
        if file_day < cutoff:
            try:
                path.unlink()
                removed += 1
            except OSError as exc:
                logger.warning("prune failed for %s: %s", path, exc)
    return removed
