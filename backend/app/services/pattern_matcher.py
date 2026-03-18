"""On-paste similarity search — matches incoming prompts against pattern family centroids.

Returns the best matching family + meta-patterns if above the suggestion threshold.
"""

from __future__ import annotations

import logging

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
        # Load all families
        result = await db.execute(select(PatternFamily))
        families = result.scalars().all()

        if not families:
            return None

        # Embed the input prompt
        prompt_embedding = await self._embedding.aembed_single(prompt_text)

        # Cosine search against centroids
        centroids = [np.frombuffer(f.centroid_embedding, dtype=np.float32) for f in families]
        matches = EmbeddingService.cosine_search(prompt_embedding, centroids, top_k=1)

        if not matches or matches[0][1] < SUGGESTION_THRESHOLD:
            return None

        idx, similarity = matches[0]
        family = families[idx]

        # Load meta-patterns for this family
        meta_result = await db.execute(
            select(MetaPattern)
            .where(MetaPattern.family_id == family.id)
            .order_by(MetaPattern.source_count.desc())
        )
        meta_patterns = meta_result.scalars().all()

        return {
            "family": {
                "id": family.id,
                "intent_label": family.intent_label,
                "domain": family.domain,
                "task_type": family.task_type,
                "usage_count": family.usage_count,
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
