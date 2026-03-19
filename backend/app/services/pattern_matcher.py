"""On-paste similarity search — matches incoming prompts against pattern family centroids.

Returns the best matching family + meta-patterns if above the suggestion threshold.
"""

from __future__ import annotations

import logging
import time

import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import MetaPattern, PatternFamily
from app.services.embedding_service import EmbeddingService

logger = logging.getLogger(__name__)

SUGGESTION_THRESHOLD = 0.72


class PatternMatcherService:
    """Matches prompt text against pattern family centroids."""

    def __init__(self, embedding_service: EmbeddingService | None = None) -> None:
        self._embedding = embedding_service or EmbeddingService()

    async def match(self, db: AsyncSession, prompt_text: str) -> dict | None:
        """Find the best matching pattern family for a prompt.

        Returns None if no family matches above SUGGESTION_THRESHOLD.
        Returns dict with family, meta_patterns, and similarity score.
        """
        t0 = time.monotonic()

        # Load all families
        result = await db.execute(select(PatternFamily))
        families = result.scalars().all()

        if not families:
            logger.debug("Pattern match — no families exist yet")
            return None

        # Embed the input prompt
        try:
            prompt_embedding = await self._embedding.aembed_single(prompt_text)
        except Exception as exc:
            logger.warning("Embedding failed for pattern match (non-fatal): %s", exc)
            return None

        # Cosine search against centroids — skip corrupt entries
        valid_families = []
        centroids = []
        for f in families:
            try:
                centroid = np.frombuffer(f.centroid_embedding, dtype=np.float32)
                centroids.append(centroid)
                valid_families.append(f)
            except (ValueError, TypeError) as exc:
                logger.warning(
                    "Skipping family '%s' in match — corrupt centroid: %s",
                    f.intent_label, exc,
                )
                continue

        if not centroids:
            logger.debug("Pattern match — no valid centroids to match against")
            return None

        matches = EmbeddingService.cosine_search(prompt_embedding, centroids, top_k=1)

        if not matches or matches[0][1] < SUGGESTION_THRESHOLD:
            best_score = matches[0][1] if matches else 0.0
            elapsed_ms = (time.monotonic() - t0) * 1000
            logger.debug(
                "Pattern match miss in %.0fms: best_score=%.3f threshold=%.2f families=%d prompt='%s'",
                elapsed_ms, best_score, SUGGESTION_THRESHOLD,
                len(valid_families), prompt_text[:40],
            )
            return None

        idx, similarity = matches[0]
        family = valid_families[idx]

        # Load meta-patterns for this family
        meta_result = await db.execute(
            select(MetaPattern)
            .where(MetaPattern.family_id == family.id)
            .order_by(MetaPattern.source_count.desc())
        )
        meta_patterns = meta_result.scalars().all()

        elapsed_ms = (time.monotonic() - t0) * 1000
        logger.info(
            "Pattern match hit in %.0fms: family='%s' similarity=%.3f "
            "meta_patterns=%d prompt='%s'",
            elapsed_ms,
            family.intent_label,
            similarity,
            len(meta_patterns),
            prompt_text[:40],
        )

        return {
            "family": {
                "id": family.id,
                "intent_label": family.intent_label,
                "domain": family.domain,
                "task_type": family.task_type,
                "usage_count": family.usage_count,
                "member_count": family.member_count,
                "avg_score": family.avg_score,
            },
            "meta_patterns": [
                {
                    "id": mp.id,
                    "pattern_text": mp.pattern_text,
                    "source_count": mp.source_count,
                }
                for mp in meta_patterns
            ],
            "similarity": round(similarity, 3),
        }
