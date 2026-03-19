"""Stateless helper for all ``mcp_session.json`` operations.

Encapsulates read/write/update/delete and staleness logic so callers in
``mcp_server.py`` and ``health.py`` don't duplicate raw JSON I/O.

Copyright 2025-2026 Project Synthesis contributors.
"""

from __future__ import annotations

import json as _json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.config import MCP_ACTIVITY_STALENESS_SECONDS, MCP_CAPABILITY_STALENESS_MINUTES

logger = logging.getLogger(__name__)

_FILENAME = "mcp_session.json"


class MCPSessionFile:
    """All operations on ``mcp_session.json`` go through this class."""

    def __init__(self, data_dir: Path) -> None:
        self._path = data_dir / _FILENAME

    # ------------------------------------------------------------------
    # Core I/O
    # ------------------------------------------------------------------

    def read(self) -> dict | None:
        """Read and parse the session file. Returns ``None`` on missing/corrupt."""
        try:
            if not self._path.exists():
                return None
            return _json.loads(self._path.read_text(encoding="utf-8"))
        except Exception:
            logger.debug("MCPSessionFile.read: could not read %s", self._path, exc_info=True)
            return None

    def write(self, data: dict) -> None:
        """JSON write (overwrites existing file)."""
        self._path.write_text(_json.dumps(data), encoding="utf-8")

    def write_session(
        self,
        sampling_capable: bool,
        *,
        sse_streams: int | None = None,
    ) -> None:
        """Write a complete session record with auto-generated timestamps.

        This is the primary write path — 4 call sites construct the same
        ``{sampling_capable, written_at, last_activity}`` dict, and this
        method eliminates that duplication.
        """
        now = datetime.now(timezone.utc).isoformat()
        data: dict = {
            "sampling_capable": sampling_capable,
            "written_at": now,
            "last_activity": now,
        }
        if sse_streams is not None:
            data["sse_streams"] = sse_streams
        self.write(data)

    def update(self, **fields: object) -> dict | None:
        """Read-modify-write. Returns ``None`` if no file exists."""
        data = self.read()
        if data is None:
            return None
        data.update(fields)
        self.write(data)
        return data

    def delete(self) -> bool:
        """Unlink the file. Returns ``True`` if a file was actually removed."""
        try:
            self._path.unlink()
            return True
        except FileNotFoundError:
            return False
        except Exception:
            logger.debug("MCPSessionFile.delete: could not remove %s", self._path, exc_info=True)
            return False

    # ------------------------------------------------------------------
    # Staleness / freshness helpers
    # ------------------------------------------------------------------

    @staticmethod
    def is_capability_fresh(data: dict) -> bool:
        """``True`` if ``written_at`` is within ``MCP_CAPABILITY_STALENESS_MINUTES``."""
        try:
            written_at = datetime.fromisoformat(data["written_at"])
            return (
                datetime.now(timezone.utc) - written_at
                <= timedelta(minutes=MCP_CAPABILITY_STALENESS_MINUTES)
            )
        except (KeyError, ValueError):
            return False

    @staticmethod
    def is_activity_stale(data: dict) -> bool:
        """``True`` if ``last_activity`` exceeds ``MCP_ACTIVITY_STALENESS_SECONDS``."""
        try:
            last_activity = datetime.fromisoformat(data["last_activity"])
            return (
                datetime.now(timezone.utc) - last_activity
            ).total_seconds() > MCP_ACTIVITY_STALENESS_SECONDS
        except (KeyError, ValueError):
            return False

    def should_skip_downgrade(self) -> bool:
        """``True`` if the file has a fresh ``sampling_capable=True`` (don't overwrite with False)."""
        data = self.read()
        if data is None:
            return False
        return data.get("sampling_capable") is True and self.is_capability_fresh(data)

    def detect_disconnect(self, data: dict) -> bool:
        """Determine if the MCP client has disconnected.

        Disconnect detection uses two signals in priority order:

        1. **SSE stream count** (instant): ``sse_streams == 0`` means the
           last SSE stream closed — the client just disconnected.
        2. **Activity staleness** (legacy fallback): if ``sse_streams`` is
           absent (old file format), fall back to checking whether
           ``last_activity`` exceeds the staleness window.

        An active SSE stream (``sse_streams > 0``) proves the client is
        connected even when no POSTs are happening (idle stream).
        """
        sse_streams = data.get("sse_streams")
        # Active streams → definitely connected
        if sse_streams is not None and sse_streams > 0:
            return False
        if sse_streams == 0:
            return True
        # Legacy file without sse_streams — fall back to activity staleness
        return self.is_activity_stale(data)
