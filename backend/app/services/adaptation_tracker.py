"""Adaptation tracker — counter-based strategy affinity tracking from user feedback."""

import json
import logging
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import PROMPTS_DIR
from app.models import StrategyAffinity
from app.services.prompt_loader import PromptLoader
from app.services.strategy_loader import StrategyLoader

logger = logging.getLogger(__name__)


class AdaptationTracker:
    """Tracks strategy affinities from user feedback using simple counters.

    Each (task_type, strategy) pair maintains thumbs_up and thumbs_down counts
    along with a recomputed approval_rate after every update.
    """

    def __init__(self, session: AsyncSession, prompts_dir: Path = PROMPTS_DIR) -> None:
        self._session = session
        self._loader = PromptLoader(prompts_dir)

    async def update_affinity(self, task_type: str, strategy: str, rating: str) -> None:
        """Increment thumbs_up or thumbs_down for the task_type+strategy pair.

        Creates a new row if none exists. Recomputes approval_rate after update.
        Commits the session.

        Args:
            task_type: Task classification (e.g. "generation", "classification").
            strategy: Strategy name (e.g. "chain_of_thought", "few_shot").
            rating: Either "thumbs_up" or "thumbs_down".
        """
        stmt = select(StrategyAffinity).where(
            StrategyAffinity.task_type == task_type,
            StrategyAffinity.strategy == strategy,
        )
        result = await self._session.execute(stmt)
        row = result.scalar_one_or_none()

        if row is None:
            row = StrategyAffinity(
                task_type=task_type,
                strategy=strategy,
                thumbs_up=0,
                thumbs_down=0,
                approval_rate=0.0,
            )
            self._session.add(row)

        if rating == "thumbs_up":
            row.thumbs_up = (row.thumbs_up or 0) + 1
        elif rating == "thumbs_down":
            row.thumbs_down = (row.thumbs_down or 0) + 1
        else:
            raise ValueError(f"Invalid rating '{rating}': must be 'thumbs_up' or 'thumbs_down'")

        total = row.thumbs_up + row.thumbs_down
        row.approval_rate = row.thumbs_up / total if total > 0 else 0.0

        await self._session.commit()

        logger.info(
            "Affinity updated: task_type=%s strategy=%s rating=%s approval_rate=%.2f total=%d",
            task_type, strategy, rating, row.approval_rate, total,
        )

    async def get_affinities(self, task_type: str) -> dict[str, dict]:
        """Return strategy affinity data for the given task_type.

        Returns:
            Dict mapping strategy name to {thumbs_up, thumbs_down, approval_rate}.
            Returns {} if no data exists for the task_type.
        """
        stmt = select(StrategyAffinity).where(StrategyAffinity.task_type == task_type)
        result = await self._session.execute(stmt)
        rows = result.scalars().all()

        return {
            row.strategy: {
                "thumbs_up": row.thumbs_up,
                "thumbs_down": row.thumbs_down,
                "approval_rate": row.approval_rate,
            }
            for row in rows
        }

    # Minimum total feedbacks before a strategy can be blocked
    _MIN_FEEDBACK_FOR_GATE = 5
    # Approval rate below which a strategy is blocked for a task type
    _BLOCK_THRESHOLD = 0.3

    async def get_blocked_strategies(self, task_type: str) -> set[str]:
        """Return strategy names with approval_rate < 0.3 and ≥5 total feedbacks.

        These strategies have been consistently rated poorly by users for the
        given task_type and should be excluded from the analyzer's available list.
        """
        affinities = await self.get_affinities(task_type)
        blocked: set[str] = set()
        for strategy, data in affinities.items():
            total = data["thumbs_up"] + data["thumbs_down"]
            if total >= self._MIN_FEEDBACK_FOR_GATE and data["approval_rate"] < self._BLOCK_THRESHOLD:
                blocked.add(strategy)
                logger.info(
                    "Strategy '%s' blocked for task_type='%s': approval_rate=%.2f (%d feedbacks)",
                    strategy, task_type, data["approval_rate"], total,
                )
        return blocked

    async def render_adaptation_state(self, task_type: str) -> str | None:
        """Render the adaptation.md template with affinities for the given task_type.

        Returns:
            Rendered template string, or None if no affinity data exists for task_type.
        """
        affinities = await self.get_affinities(task_type)
        if not affinities:
            return None

        affinities_json = json.dumps({task_type: affinities}, indent=2)
        return self._loader.render("adaptation.md", {"task_type_affinities": affinities_json})

    async def check_degenerate(self, task_type: str, strategy: str) -> bool:
        """Return True if 10+ feedbacks exist and >90% are the same rating.

        A degenerate signal means user feedback is so one-sided that further
        adaptation for this pair offers diminishing value.

        Args:
            task_type: Task classification.
            strategy: Strategy name.

        Returns:
            True if the feedback distribution is degenerate, False otherwise.
        """
        stmt = select(StrategyAffinity).where(
            StrategyAffinity.task_type == task_type,
            StrategyAffinity.strategy == strategy,
        )
        result = await self._session.execute(stmt)
        row = result.scalar_one_or_none()

        if row is None:
            return False

        total = (row.thumbs_up or 0) + (row.thumbs_down or 0)
        if total < 10:
            return False

        dominant = max(row.thumbs_up or 0, row.thumbs_down or 0)
        is_degenerate = (dominant / total) > 0.90
        if is_degenerate:
            logger.warning(
                "Degenerate feedback pattern detected: task_type=%s strategy=%s "
                "dominant=%d/%d (%.0f%%)",
                task_type, strategy, dominant, total, dominant / total * 100,
            )
        return is_degenerate

    async def cleanup_orphaned_affinities(self) -> int:
        """Remove StrategyAffinity rows for strategies no longer on disk.

        Returns the number of rows deleted.
        """
        loader = StrategyLoader(self._loader.prompts_dir / "strategies")
        available = set(loader.list_strategies())
        if not available:
            # No strategies on disk — skip cleanup to avoid wiping everything
            return 0

        result = await self._session.execute(select(StrategyAffinity))
        rows = result.scalars().all()

        orphaned_ids = [
            row.id for row in rows if row.strategy not in available
        ]
        if not orphaned_ids:
            return 0

        await self._session.execute(
            delete(StrategyAffinity).where(StrategyAffinity.id.in_(orphaned_ids))
        )
        await self._session.commit()

        logger.info(
            "Cleaned up %d orphaned strategy affinity rows (strategies no longer on disk)",
            len(orphaned_ids),
        )
        return len(orphaned_ids)
