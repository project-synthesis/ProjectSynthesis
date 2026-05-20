"""SeedAgentPromoter — auto-promotes high-scoring topic_probe runs to
``prompts/seed-agents/<slug>.md`` and refreshes their few-shot examples.

Spec: ``docs/superpowers/specs/2026-05-19-v0.4.29-t3.2-t3.5-design.md`` §3.
"""
from __future__ import annotations

import logging
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app import database as _database_mod
from app.config import AUTO_PROMOTE_THRESHOLD, AUTO_PROMOTE_TOP_K, PROMPTS_DIR
from app.models import RunRow
from app.schemas.seed_agent_promotion import PromotionResult, RefreshResult
from app.services.write_queue import WriteQueue


logger = logging.getLogger(__name__)

_SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,58}[a-z0-9])?$")


class SeedAgentPromoter:
    """Reads completed topic_probe RunRows and writes seed-agent .md files."""

    def __init__(self, *, write_queue: WriteQueue, prompts_dir: Path = PROMPTS_DIR) -> None:
        self._write_queue = write_queue
        self._seed_agents_dir = prompts_dir / "seed-agents"

    async def maybe_promote(self, run_id: str) -> PromotionResult:
        """Spec §3.1 — auto-promote on completion gate."""
        raise NotImplementedError("RED phase — implement in GREEN")

    async def refresh(self, agent_name: str) -> RefreshResult:
        """Spec §3.1 — re-render Examples section from source probe."""
        raise NotImplementedError("RED phase — implement in GREEN")

    # ----- private helpers (real, not stubs — tests reference them indirectly) -----

    def _compute_top_k_prompts(self, row: RunRow, k: int) -> list[dict[str, Any]]:
        """Top-K prompts ranked by ``overall_score`` desc."""
        prompts = row.prompt_results or []
        scored = [p for p in prompts if p.get("overall_score") is not None]
        ranked = sorted(scored, key=lambda p: float(p["overall_score"]), reverse=True)
        return ranked[:k]

    def _atomic_write(self, path: Path, content: str) -> None:
        """tempfile + ``os.replace`` (POSIX atomic rename)."""
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(content, encoding="utf-8")
        os.replace(tmp, path)

    def _render_examples_section(
        self,
        top_k: list[dict[str, Any]],
        source_run_id: str,
        timestamp: str,
    ) -> str:
        """Render ``## Examples (top N from probe X, refreshed T)`` block."""
        n = len(top_k)
        header = f"## Examples (top {n} from probe {source_run_id}, refreshed {timestamp})"
        if not top_k:
            return f"{header}\n\n(no high-scoring prompts available)"
        lines = [header, ""]
        for i, p in enumerate(top_k, start=1):
            prompt = (p.get("raw_prompt") or p.get("prompt_text") or "").strip().replace("\n", " ")
            score = float(p.get("overall_score", 0.0))
            lines.append(f"{i}. \"{prompt}\" (score {score:.1f})")
        return "\n".join(lines)
