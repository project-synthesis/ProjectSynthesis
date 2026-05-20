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
_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_EXAMPLES_RE = re.compile(
    r"\n## Examples \(top \d+ from probe .*?(?=\n## |\Z)",
    re.DOTALL,
)


class SeedAgentPromoter:
    """Reads completed topic_probe RunRows and writes seed-agent .md files."""

    def __init__(self, *, write_queue: WriteQueue, prompts_dir: Path = PROMPTS_DIR) -> None:
        self._write_queue = write_queue
        self._seed_agents_dir = prompts_dir / "seed-agents"

    async def maybe_promote(self, run_id: str) -> PromotionResult:
        """Promote a completed topic_probe RunRow to a seed-agent file if eligible.

        Gate order (each returns PromotionResult on first failure):
        1. RunRow exists
        2. mode == 'topic_probe'
        3. status == 'completed'
        4. aggregate.mean_overall >= AUTO_PROMOTE_THRESHOLD
        5. topic_probe_meta.suggested_agent_name is non-empty
        6. suggested_agent_name matches _SLUG_RE
        7. topic_probe_meta.promoted_at not already set

        On all gates passing: render .md, atomic-write, stamp source row.

        Spec: ``docs/superpowers/specs/2026-05-19-v0.4.29-t3.2-t3.5-design.md`` §3.1.
        """
        async with _database_mod.async_session_factory() as db:
            row = await db.get(RunRow, run_id)

        if row is None:
            return PromotionResult(run_id=run_id, written=False, skipped_reason="run_not_found")
        if row.mode != "topic_probe":
            return PromotionResult(run_id=run_id, written=False, skipped_reason="wrong_mode")
        if row.status != "completed":
            return PromotionResult(run_id=run_id, written=False, skipped_reason="not_completed")

        aggregate = row.aggregate or {}
        mean_overall = float(aggregate.get("mean_overall", 0.0))
        if mean_overall < AUTO_PROMOTE_THRESHOLD:
            return PromotionResult(run_id=run_id, written=False, skipped_reason="below_threshold")

        meta = row.topic_probe_meta or {}
        agent_name_raw = meta.get("suggested_agent_name") or ""
        agent_name = agent_name_raw.strip()
        if not agent_name:
            return PromotionResult(run_id=run_id, written=False, skipped_reason="no_agent_name")
        if not _SLUG_RE.match(agent_name):
            return PromotionResult(run_id=run_id, written=False, skipped_reason="invalid_slug")

        if meta.get("promoted_at"):
            return PromotionResult(run_id=run_id, written=False, skipped_reason="already_promoted")

        # All gates pass. Render + write + stamp.
        top_k = self._compute_top_k_prompts(row, AUTO_PROMOTE_TOP_K)
        timestamp = datetime.now(UTC).isoformat()
        rendered = self._render_seed_agent_md(
            agent_name=agent_name,
            topic=row.topic or agent_name,
            source_run_id=run_id,
            top_k=top_k,
            timestamp=timestamp,
        )
        target_path = self._seed_agents_dir / f"{agent_name}.md"
        target_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._atomic_write(target_path, rendered)
        except OSError as exc:
            logger.warning("Promoter write failed for %s: %s", target_path, exc)
            return PromotionResult(
                run_id=run_id, written=False, skipped_reason="write_failed",
            )

        await self._stamp_promoted(run_id=run_id, agent_name=agent_name)

        return PromotionResult(
            run_id=run_id,
            written=True,
            agent_name=agent_name,
            path=str(target_path),
            examples_count=len(top_k),
        )

    async def refresh(self, agent_name: str) -> RefreshResult:
        """Re-render Examples section of an existing seed-agent file.

        Algorithm per spec §3.1 (11 steps):
        1. Resolve file path; if missing → file_not_found.
        2. Read + match frontmatter regex; if no match → no_source_run.
        3. Parse frontmatter into dict (line-by-line key: value).
        4. Extract promoted_from_run_id; if missing/empty → no_source_run.
        5. Load source RunRow; if None → source_run_deleted.
        6. Recompute top-K.
        7. Update last_refreshed_at; preserve all other frontmatter keys.
        8. Replace Examples section via regex; append defensively if absent.
        9. Reassemble file with canonical-order frontmatter render.
        10. Atomic write.
        11. Return RefreshResult.

        Spec: ``docs/superpowers/specs/2026-05-19-v0.4.29-t3.2-t3.5-design.md`` §3.1.
        """
        # Step 1
        slug_path = self._seed_agents_dir / f"{agent_name}.md"
        if not slug_path.exists():
            return RefreshResult(
                agent_name=agent_name, refreshed=False,
                skipped_reason="file_not_found",
            )

        # Step 2 — read + parse frontmatter
        content = slug_path.read_text(encoding="utf-8")
        match = _FRONTMATTER_RE.match(content)
        if not match:
            return RefreshResult(
                agent_name=agent_name, refreshed=False,
                skipped_reason="no_source_run",
            )
        frontmatter_raw = match.group(1)
        body = content[match.end():]

        # Step 3 — parse frontmatter into dict
        fm_dict: dict[str, str] = {}
        for raw_line in frontmatter_raw.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            key, _, value = line.partition(":")
            if not value:
                continue
            fm_dict[key.strip().lower()] = value.strip()

        # Step 4 — extract source_run_id
        source_run_id = fm_dict.get("promoted_from_run_id", "").strip()
        if not source_run_id:
            return RefreshResult(
                agent_name=agent_name, refreshed=False,
                skipped_reason="no_source_run",
            )

        # Step 5 — load source RunRow
        async with _database_mod.async_session_factory() as db:
            row = await db.get(RunRow, source_run_id)
        if row is None:
            return RefreshResult(
                agent_name=agent_name, refreshed=False,
                source_run_id=source_run_id,
                skipped_reason="source_run_deleted",
            )

        # Step 6 — recompute top-K
        top_k = self._compute_top_k_prompts(row, AUTO_PROMOTE_TOP_K)
        timestamp = datetime.now(UTC).isoformat()

        # Step 7 — update last_refreshed_at, preserve all other keys
        fm_dict["last_refreshed_at"] = timestamp

        # Step 8 — replace Examples section in body
        new_section = self._render_examples_section(top_k, source_run_id, timestamp)
        body_normalized = body.rstrip("\n") + "\n"
        if _EXAMPLES_RE.search(body_normalized):
            new_body = _EXAMPLES_RE.sub("\n" + new_section, body_normalized, count=1)
        else:
            # Defensive: operator deleted the heading — append at end
            new_body = body_normalized.rstrip("\n") + "\n\n" + new_section + "\n"

        # Step 9 — reassemble file
        new_frontmatter = self._render_frontmatter(fm_dict)
        new_content = new_frontmatter + new_body

        # Step 10 — atomic write
        try:
            self._atomic_write(slug_path, new_content)
        except OSError as exc:
            logger.warning("Refresh write failed for %s: %s", slug_path, exc)
            return RefreshResult(
                agent_name=agent_name, refreshed=False,
                source_run_id=source_run_id,
                skipped_reason="write_failed",
            )

        # Step 11 — return result
        return RefreshResult(
            agent_name=agent_name,
            refreshed=True,
            examples_count=len(top_k),
            source_run_id=source_run_id,
        )

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

    def _render_seed_agent_md(
        self,
        *,
        agent_name: str,
        topic: str,
        source_run_id: str,
        top_k: list[dict[str, Any]],
        timestamp: str,
    ) -> str:
        """Render the full seed-agent .md content per spec §3.4."""
        examples_section = self._render_examples_section(top_k, source_run_id, timestamp)
        return (
            "---\n"
            f"name: {agent_name}\n"
            f"description: Auto-promoted from probe {source_run_id} on topic '{topic}'\n"
            "task_types: general\n"
            "phase_context: build\n"
            "prompts_per_run: 5\n"
            "enabled: true\n"
            f"promoted_from_run_id: {source_run_id}\n"
            f"promoted_at: {timestamp}\n"
            f"last_refreshed_at: {timestamp}\n"
            "---\n\n"
            f"You are a prompt generation agent for {topic}.\n\n"
            "Generate prompts a developer would ask while working on this topic. "
            "Each prompt should be self-contained, realistic, and cover a different aspect.\n\n"
            f"{examples_section}\n\n"
            "Produce 5 similar prompts.\n"
        )

    async def _stamp_promoted(self, *, run_id: str, agent_name: str) -> None:
        """Atomic UPDATE on topic_probe_meta JSON column via WriteQueue.

        JSON-column mutation contract (spec §3.1): SQLAlchemy's default JSON
        column does NOT deep-watch nested mutations. The pattern below builds
        a NEW dict (dict() copy), mutates the copy, then reassigns the
        attribute — that ATTRIBUTE-LEVEL reassignment is what SQLAlchemy
        detects. In-place mutation (row.topic_probe_meta['k'] = v) would
        NOT flush.
        """
        now_iso = datetime.now(UTC).isoformat()

        async def _work(write_db: AsyncSession) -> None:
            row = await write_db.get(RunRow, run_id)
            if row is None:
                return
            meta = dict(row.topic_probe_meta or {})  # NEW dict instance
            meta["promoted_at"] = now_iso
            meta["promoted_to"] = agent_name
            row.topic_probe_meta = meta  # attribute-level reassignment
            await write_db.commit()

        await self._write_queue.submit(
            _work,
            timeout=10,
            operation_label="seed_agent_promoter.stamp_promoted",
        )

    def _render_frontmatter(self, fm_dict: dict[str, str]) -> str:
        """Render frontmatter dict with canonical key order; custom keys lexical.

        Canonical order: name, description, task_types, phase_context,
        prompts_per_run, enabled, promoted_from_run_id, promoted_at,
        last_refreshed_at. Other keys appended in sorted order.
        """
        canonical = [
            "name", "description", "task_types", "phase_context",
            "prompts_per_run", "enabled", "promoted_from_run_id",
            "promoted_at", "last_refreshed_at",
        ]
        lines = ["---"]
        used = set()
        for key in canonical:
            if key in fm_dict:
                lines.append(f"{key}: {fm_dict[key]}")
                used.add(key)
        for key in sorted(set(fm_dict) - used):
            lines.append(f"{key}: {fm_dict[key]}")
        lines.append("---")
        lines.append("")
        return "\n".join(lines) + "\n"
