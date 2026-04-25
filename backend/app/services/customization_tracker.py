"""Track user-modified prompt files for safe self-update.

Hardening goal: when ``init.sh update`` / ``POST /api/update/apply`` runs
``git checkout`` to a new release tag, prompt files that the user has
edited via ``PUT /api/strategies/{name}`` must NOT be silently overwritten.

This module records every API edit into a small JSON registry under
``data/`` (gitignored, survives restart) so:

  1. ``GET /api/update/preflight`` can list user customizations BEFORE
     the user clicks "Update & Restart" — visible warning, not a
     surprise.
  2. ``update_service.apply_update`` knows which dirty files came from
     the API and can ``git stash push -m synthesis-update`` them
     automatically before the checkout, then ``git stash pop`` them
     back afterward. Stash conflicts surface to the frontend via the
     ``update_complete`` event with a ``stash_pop_conflicts`` field
     so the user has a clear recovery path.
  3. Operators can audit who edited what when, by source
     (api / manual / unknown), via
     ``CustomizationTracker.list_modifications()``.

Schema (data/.user_customizations.json)::

    {
      "version": 1,
      "files": {
        "prompts/strategies/chain-of-thought.md": {
          "current_sha": "<first-16 hex of sha256(content)>",
          "modified_at": "2026-04-25T22:00:00+00:00",
          "source": "api"
        }
      }
    }

The registry is **best-effort**: a missing or corrupt file degrades to
empty (no edits known). The actual data-loss safeguard is the auto-stash
in ``update_service`` — this registry is for visibility and source
attribution only.

Copyright 2025-2026 Project Synthesis contributors.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)

REGISTRY_FILE = "data/.user_customizations.json"
SCHEMA_VERSION = 1

EditSource = Literal["api", "manual", "unknown"]


class CustomizationTracker:
    """Singleton-style tracker of user-modified prompt files.

    Methods are synchronous + cheap (single JSON file ≤ a few KB).
    Caller is responsible for passing canonical relative paths
    (``prompts/strategies/foo.md``, NOT absolute paths or paths with
    ``..``).
    """

    def __init__(self, root: Path | None = None) -> None:
        self._root = root or Path.cwd()
        self._registry_path = self._root / REGISTRY_FILE
        self._cache: dict | None = None

    # -- internal -----------------------------------------------------

    def _load(self) -> dict:
        """Load (or initialize) the registry, with cache."""
        if self._cache is not None:
            return self._cache
        if not self._registry_path.exists():
            self._cache = {"version": SCHEMA_VERSION, "files": {}}
            return self._cache
        try:
            data = json.loads(self._registry_path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("registry is not a JSON object")
            if data.get("version") != SCHEMA_VERSION:
                logger.warning(
                    "Customization registry version mismatch (%s != %s) — "
                    "starting fresh", data.get("version"), SCHEMA_VERSION,
                )
                self._cache = {"version": SCHEMA_VERSION, "files": {}}
                return self._cache
            files = data.get("files")
            if not isinstance(files, dict):
                files = {}
            self._cache = {"version": SCHEMA_VERSION, "files": files}
            return self._cache
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            logger.warning(
                "Failed to load customization registry %s: %s — starting fresh",
                self._registry_path, exc,
            )
            self._cache = {"version": SCHEMA_VERSION, "files": {}}
            return self._cache

    def _save(self) -> None:
        """Atomic-write the registry to disk."""
        if self._cache is None:
            return
        self._registry_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._registry_path.with_suffix(".json.tmp")
        try:
            tmp.write_text(
                json.dumps(self._cache, indent=2, sort_keys=True),
                encoding="utf-8",
            )
            tmp.replace(self._registry_path)
        except OSError as exc:
            logger.warning("Failed to persist customization registry: %s", exc)
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass

    # -- public API ---------------------------------------------------

    def record_edit(
        self,
        rel_path: str,
        content: str,
        source: EditSource = "api",
    ) -> None:
        """Record that *rel_path* has been user-edited.

        Args:
            rel_path: Repo-relative path (forward slashes), e.g.
                ``prompts/strategies/chain-of-thought.md``.
            content: Final file content (used to compute sha for
                future drift detection).
            source: How the edit arrived. ``"api"`` = via REST endpoint
                (PUT /api/strategies/{name}). ``"manual"`` = explicit
                operator opt-in (e.g., a future endpoint to flag
                hand-edited files). ``"unknown"`` = scan-time discovery.
        """
        if not rel_path:
            return
        registry = self._load()
        sha = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
        registry["files"][rel_path] = {
            "current_sha": sha,
            "modified_at": datetime.now(timezone.utc).isoformat(),
            "source": source,
        }
        self._save()
        logger.info(
            "Customization recorded: %s (sha=%s, source=%s)",
            rel_path, sha, source,
        )

    def is_modified(self, rel_path: str) -> bool:
        """True if *rel_path* is in the registry."""
        return rel_path in self._load()["files"]

    def list_modifications(self) -> dict[str, dict]:
        """Return a defensive copy of the registry's files map."""
        return {k: dict(v) for k, v in self._load()["files"].items()}

    def clear_entry(self, rel_path: str) -> None:
        """Remove *rel_path* from the registry.

        Called after a successful update where the file's edits were
        either preserved (via stash pop) or explicitly discarded.
        Operators may also call this manually after running
        ``git checkout HEAD prompts/strategies/foo.md`` to restore the
        stock version.
        """
        registry = self._load()
        if rel_path in registry["files"]:
            del registry["files"][rel_path]
            self._save()
            logger.info("Customization cleared: %s", rel_path)

    def reset(self) -> None:
        """Drop the entire registry (testing helper)."""
        self._cache = {"version": SCHEMA_VERSION, "files": {}}
        self._save()


# ---------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------

_tracker: CustomizationTracker | None = None


def get_tracker() -> CustomizationTracker:
    """Return the process-singleton ``CustomizationTracker``."""
    global _tracker  # noqa: PLW0603
    if _tracker is None:
        _tracker = CustomizationTracker()
    return _tracker


def set_tracker(tracker: CustomizationTracker | None) -> None:
    """Override the singleton (used by tests + lifespan rebinding)."""
    global _tracker  # noqa: PLW0603
    _tracker = tracker


__all__ = [
    "CustomizationTracker",
    "EditSource",
    "REGISTRY_FILE",
    "SCHEMA_VERSION",
    "get_tracker",
    "set_tracker",
]
