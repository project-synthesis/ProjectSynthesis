"""ErrorLogger — structured exception logging to daily JSONL files.

Captures unhandled exceptions and explicit pipeline errors into
``data/errors/errors-YYYY-MM-DD.jsonl`` with 30-day retention.
Follows the same daily-rotation pattern as ``trace_logger.py`` and
``taxonomy/event_logger.py``.

Copyright 2025-2026 Project Synthesis contributors.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_instance: "ErrorLogger | None" = None


def get_error_logger() -> "ErrorLogger":
    """Return the process-wide ErrorLogger (set during lifespan)."""
    if _instance is None:
        raise RuntimeError("ErrorLogger not initialized — call set_error_logger() first")
    return _instance


def set_error_logger(inst: "ErrorLogger") -> None:
    global _instance  # noqa: PLW0603
    _instance = inst


# ---------------------------------------------------------------------------
# Logger class
# ---------------------------------------------------------------------------


class ErrorLogger:
    """Append-only JSONL error logger with daily rotation and retention."""

    def __init__(self, errors_dir: str | Path = "data/errors") -> None:
        self._errors_dir = Path(errors_dir)
        self._errors_dir.mkdir(parents=True, exist_ok=True)

    def log_error(
        self,
        *,
        service: str,
        level: str = "error",
        module: str = "unknown",
        error_type: str = "Exception",
        message: str = "",
        traceback: str | None = None,
        request_context: dict[str, Any] | None = None,
    ) -> None:
        """Append one error entry to today's JSONL file.

        Args:
            service: Originating service (``backend``, ``mcp``, ``pipeline``).
            level: Severity (``error``, ``warning``, ``critical``).
            module: Python module path (e.g. ``app.services.pipeline``).
            error_type: Exception class name (e.g. ``ValueError``).
            message: Human-readable error description.
            traceback: Full traceback string (optional).
            request_context: HTTP request metadata — method, URL, client IP
                (optional, only available for request-scoped errors).
        """
        entry: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "service": service,
            "level": level,
            "module": module,
            "error_type": error_type,
            "message": message,
        }
        if traceback:
            entry["traceback"] = traceback
        if request_context:
            entry["request_context"] = request_context

        try:
            daily_file = self._daily_file()
            with daily_file.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
        except OSError as exc:
            logger.warning("Failed to write error entry to JSONL: %s", exc)

    def read_errors(
        self,
        date: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Read error entries from a specific day's JSONL file.

        Args:
            date: Date string ``YYYY-MM-DD``. Defaults to today.
            limit: Max entries to return.
            offset: Skip first N entries.
        """
        if date is None:
            date = datetime.now(UTC).strftime("%Y-%m-%d")
        filepath = self._errors_dir / f"errors-{date}.jsonl"
        if not filepath.exists():
            return []

        entries: list[dict[str, Any]] = []
        for line in filepath.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return entries[offset : offset + limit]

    def rotate(self, retention_days: int = 30) -> int:
        """Delete error JSONL files older than retention_days. Returns count deleted."""
        cutoff = datetime.now(UTC) - timedelta(days=retention_days)
        deleted = 0
        for filepath in self._errors_dir.glob("errors-*.jsonl"):
            try:
                date_str = filepath.stem.replace("errors-", "")
                file_date = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=UTC)
                if file_date < cutoff:
                    filepath.unlink()
                    deleted += 1
                    logger.info("Deleted old error log file: %s", filepath.name)
            except (ValueError, OSError) as exc:
                logger.warning("Could not process error file %s: %s", filepath.name, exc)
        return deleted

    def _daily_file(self) -> Path:
        date_str = datetime.now(UTC).strftime("%Y-%m-%d")
        return self._errors_dir / f"errors-{date_str}.jsonl"
