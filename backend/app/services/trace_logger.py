"""TraceLogger — writes per-phase JSONL trace entries to data/traces/."""

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class TraceLogger:
    """Append-only JSONL trace logger.

    Each call to ``log_phase`` appends a single JSON line to a daily file
    ``<traces_dir>/traces-YYYY-MM-DD.jsonl``.  ``read_trace`` scans all
    ``.jsonl`` files in the directory and returns every entry whose
    ``trace_id`` matches the requested value.
    """

    def __init__(self, traces_dir: str | Path = "data/traces") -> None:
        self.traces_dir = Path(traces_dir)
        self.traces_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def log_phase(
        self,
        trace_id: str,
        phase: str,
        duration_ms: int,
        tokens_in: int,
        tokens_out: int,
        model: str,
        provider: str,
        result: dict[str, Any] | None = None,
        *,
        status: str = "ok",
    ) -> None:
        """Append one trace entry to today's JSONL file.

        *status* indicates the outcome of the phase: ``"ok"`` (default),
        ``"error"``, or ``"skipped"``.
        """
        entry: dict[str, Any] = {
            "trace_id": trace_id,
            "phase": phase,
            "status": status,
            "duration_ms": duration_ms,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "model": model,
            "provider": provider,
            "timestamp": datetime.now(UTC).isoformat(),
        }
        if result is not None:
            entry["result"] = result

        daily_file = self._daily_file()
        with daily_file.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def read_trace(self, trace_id: str) -> list[dict[str, Any]]:
        """Return all entries for *trace_id* across all daily files, in order."""
        matches: list[dict[str, Any]] = []
        for path in sorted(self.traces_dir.glob("traces-*.jsonl")):
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError as jde:
                    logger.warning(
                        "Malformed JSONL line in %s (skipping): %s",
                        path.name, jde,
                    )
                    continue
                if entry.get("trace_id") == trace_id:
                    matches.append(entry)
        return matches

    def rotate(self, retention_days: int = 30) -> int:
        """Delete JSONL trace files older than retention_days. Returns count deleted."""
        from datetime import timedelta

        cutoff = datetime.now(UTC) - timedelta(days=retention_days)
        deleted = 0
        for path in self.traces_dir.glob("*.jsonl"):
            # Parse date from filename: traces-YYYY-MM-DD.jsonl
            try:
                date_str = path.stem.replace("traces-", "")
                file_date = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=UTC)
                if file_date < cutoff:
                    path.unlink()
                    deleted += 1
                    logger.info("Deleted old trace file: %s", path.name)
            except (ValueError, OSError) as exc:
                logger.warning("Could not process trace file %s: %s", path.name, exc)
        return deleted

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _daily_file(self) -> Path:
        date_str = datetime.now(UTC).strftime("%Y-%m-%d")
        return self.traces_dir / f"traces-{date_str}.jsonl"
