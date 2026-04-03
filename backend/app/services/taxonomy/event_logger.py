"""TaxonomyEventLogger — structured decision tracing for taxonomy engine.

Dual-writes to:
  1. Daily JSONL files in data/taxonomy_events/ (persistence)
  2. In-memory ring buffer (real-time reads via API)

Optionally publishes to the EventBus for SSE streaming.
"""

import json
import logging
from collections import deque
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_instance: "TaxonomyEventLogger | None" = None


def get_event_logger() -> "TaxonomyEventLogger":
    """Return the process-wide TaxonomyEventLogger (set during lifespan)."""
    if _instance is None:
        raise RuntimeError("TaxonomyEventLogger not initialized — call set_event_logger() first")
    return _instance


def set_event_logger(inst: "TaxonomyEventLogger") -> None:
    global _instance
    _instance = inst


def reset_event_logger() -> None:
    """Clear the process singleton (test cleanup only)."""
    global _instance
    _instance = None


# ---------------------------------------------------------------------------
# Logger class
# ---------------------------------------------------------------------------


class TaxonomyEventLogger:
    """Structured decision event logger for taxonomy hot/warm/cold paths."""

    def __init__(
        self,
        events_dir: str | Path = "data/taxonomy_events",
        publish_to_bus: bool = True,
        cross_process: bool = False,
        buffer_size: int = 500,
    ) -> None:
        self._events_dir = Path(events_dir)
        self._events_dir.mkdir(parents=True, exist_ok=True)
        self._publish_to_bus = publish_to_bus
        self._cross_process = cross_process
        self._buffer: deque[dict[str, Any]] = deque(maxlen=buffer_size)

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def log_decision(
        self,
        *,
        path: str,
        op: str,
        decision: str,
        cluster_id: str | None = None,
        optimization_id: str | None = None,
        duration_ms: int | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        """Log a taxonomy decision event.

        Args:
            path: "hot", "warm", or "cold".
            op: Operation type (assign, split, merge, retire, phase, refit, etc.).
            decision: Outcome (merge_into, create_new, accepted, rejected, etc.).
            cluster_id: Affected cluster ID (nullable).
            optimization_id: Triggering optimization ID (nullable).
            duration_ms: Wall-clock time in ms (nullable).
            context: Operation-specific decision context dict.
        """
        event: dict[str, Any] = {
            "ts": datetime.now(UTC).isoformat(),
            "path": path,
            "op": op,
            "decision": decision,
        }
        if cluster_id is not None:
            event["cluster_id"] = cluster_id
        if optimization_id is not None:
            event["optimization_id"] = optimization_id
        if duration_ms is not None:
            event["duration_ms"] = duration_ms
        if context:
            event["context"] = context

        # 1. Append to ring buffer
        self._buffer.append(event)

        # 2. Append to daily JSONL file
        try:
            daily_file = self._daily_file()
            with daily_file.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
        except OSError as exc:
            logger.warning("Failed to write taxonomy event to JSONL: %s", exc)

        # 3. Publish for SSE delivery
        if self._publish_to_bus:
            try:
                from app.services.event_bus import event_bus
                event_bus.publish("taxonomy_activity", event)
            except Exception:
                pass  # Non-fatal
        elif self._cross_process:
            # MCP server process: forward via HTTP to backend's event bus.
            try:
                import asyncio
                loop = asyncio.get_running_loop()
                from app.services.event_notification import notify_event_bus
                loop.create_task(notify_event_bus("taxonomy_activity", event))
                logger.debug("Cross-process event queued: %s/%s", event.get("op"), event.get("decision"))
            except RuntimeError:
                logger.debug("Cross-process skip: no running event loop")
            except Exception as _cp_exc:
                logger.debug("Cross-process notification failed: %s", _cp_exc)

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def get_recent(
        self,
        limit: int = 50,
        path: str | None = None,
        op: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return recent events from ring buffer (newest first)."""
        events = list(self._buffer)
        if path:
            events = [e for e in events if e.get("path") == path]
        if op:
            events = [e for e in events if e.get("op") == op]
        events.reverse()  # newest first
        return events[:limit]

    def get_history(
        self,
        date: str,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Read events from a specific day's JSONL file."""
        filepath = self._events_dir / f"decisions-{date}.jsonl"
        if not filepath.exists():
            return []

        events: list[dict[str, Any]] = []
        for line in filepath.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return events[offset : offset + limit]

    # ------------------------------------------------------------------
    # Maintenance
    # ------------------------------------------------------------------

    def rotate(self, retention_days: int = 30) -> int:
        """Delete JSONL event files older than retention_days."""
        cutoff = datetime.now(UTC) - timedelta(days=retention_days)
        deleted = 0
        for filepath in self._events_dir.glob("decisions-*.jsonl"):
            try:
                date_str = filepath.stem.replace("decisions-", "")
                file_date = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=UTC)
                if file_date < cutoff:
                    filepath.unlink()
                    deleted += 1
                    logger.info("Deleted old taxonomy event file: %s", filepath.name)
            except (ValueError, OSError) as exc:
                logger.warning("Could not process event file %s: %s", filepath.name, exc)
        return deleted

    @property
    def buffer_size(self) -> int:
        """Current number of events in ring buffer."""
        return len(self._buffer)

    @property
    def oldest_ts(self) -> str | None:
        """Timestamp of oldest event in buffer, or None if empty."""
        return self._buffer[0]["ts"] if self._buffer else None

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _daily_file(self) -> Path:
        date_str = datetime.now(UTC).strftime("%Y-%m-%d")
        return self._events_dir / f"decisions-{date_str}.jsonl"
