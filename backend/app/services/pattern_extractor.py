"""Post-completion pattern extraction — embeds prompts, clusters into families,
extracts meta-patterns via Haiku LLM call.

Subscribes to 'optimization_created' events on the event bus and runs
extraction as an async background task.
"""

from __future__ import annotations

import logging
import time

import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import PROMPTS_DIR, settings
from app.database import async_session_factory
from app.models import MetaPattern, Optimization, OptimizationPattern, PatternFamily
from app.providers.base import LLMProvider
from app.services.embedding_service import EmbeddingService
from app.services.event_bus import event_bus
from app.services.prompt_loader import PromptLoader

logger = logging.getLogger(__name__)

# Thresholds (cosine similarity)
FAMILY_MERGE_THRESHOLD = 0.78
PATTERN_MERGE_THRESHOLD = 0.82


class PatternExtractorService:
    """Extracts and clusters prompt patterns from completed optimizations."""

    def __init__(
        self,
        embedding_service: EmbeddingService | None = None,
        provider: LLMProvider | None = None,
    ) -> None:
        self._embedding = embedding_service or EmbeddingService()
        self._provider = provider
        self._prompt_loader = PromptLoader(PROMPTS_DIR)

    async def process(self, optimization_id: str) -> None:
        """Full extraction pipeline for a single optimization.

        1. Embed the raw prompt
        2. Find or create a pattern family
        3. Extract meta-patterns via Haiku
        4. Merge meta-patterns into the family
        5. Write join records
        6. Publish pattern_updated event
        """
        t0 = time.monotonic()
        logger.info("Pattern extraction started: opt=%s", optimization_id)
        try:
            async with async_session_factory() as db:
                result = await db.execute(
                    select(Optimization).where(Optimization.id == optimization_id)
                )
                opt = result.scalar_one_or_none()
                if not opt or opt.status != "completed":
                    logger.debug(
                        "Skipping pattern extraction for %s (status=%s)",
                        optimization_id,
                        opt.status if opt else "not_found",
                    )
                    return

                # Idempotency: skip if already extracted
                existing = await db.execute(
                    select(OptimizationPattern).where(
                        OptimizationPattern.optimization_id == optimization_id,
                        OptimizationPattern.relationship == "source",
                    )
                )
                if existing.scalar_one_or_none():
                    logger.debug(
                        "Skipping pattern extraction for %s (already processed)",
                        optimization_id,
                    )
                    return

                # 1. Embed the raw prompt
                t_embed = time.monotonic()
                embedding = await self._embedding.aembed_single(opt.raw_prompt)
                opt.embedding = embedding.astype(np.float32).tobytes()
                logger.debug(
                    "Prompt embedded in %.1fms (opt=%s, %d chars)",
                    (time.monotonic() - t_embed) * 1000,
                    optimization_id,
                    len(opt.raw_prompt),
                )

                # 2. Find or create family
                family = await self._find_or_create_family(
                    db,
                    embedding=embedding,
                    intent_label=opt.intent_label or "general",
                    domain=opt.domain or "general",
                    task_type=opt.task_type or "general",
                    overall_score=opt.overall_score,
                )

                # 3. Extract meta-patterns via Haiku
                t_extract = time.monotonic()
                meta_texts = await self._extract_meta_patterns(opt)
                logger.debug(
                    "Meta-pattern extraction took %.1fms (opt=%s, %d patterns)",
                    (time.monotonic() - t_extract) * 1000,
                    optimization_id,
                    len(meta_texts),
                )

                # 4. Merge meta-patterns
                merged_count = 0
                created_count = 0
                for text in meta_texts:
                    was_merged = await self._merge_meta_pattern(db, family.id, text)
                    if was_merged:
                        merged_count += 1
                    else:
                        created_count += 1

                # 5. Write join record
                join = OptimizationPattern(
                    optimization_id=opt.id,
                    family_id=family.id,
                    relationship="source",
                )
                db.add(join)

                await db.commit()

                # 6. Publish event
                event_bus.publish(
                    "pattern_updated",
                    {
                        "family_id": family.id,
                        "intent_label": family.intent_label,
                        "domain": family.domain,
                        "optimization_id": opt.id,
                    },
                )

                elapsed_ms = (time.monotonic() - t0) * 1000
                logger.info(
                    "Pattern extraction complete in %.0fms: opt=%s family='%s' "
                    "meta_patterns=%d (merged=%d, created=%d)",
                    elapsed_ms,
                    optimization_id,
                    family.intent_label,
                    len(meta_texts),
                    merged_count,
                    created_count,
                )

        except Exception as exc:
            elapsed_ms = (time.monotonic() - t0) * 1000
            logger.error(
                "Pattern extraction failed after %.0fms for %s: %s",
                elapsed_ms,
                optimization_id,
                exc,
                exc_info=True,
            )

    async def _find_or_create_family(
        self,
        db: AsyncSession,
        embedding: np.ndarray,
        intent_label: str,
        domain: str,
        task_type: str,
        overall_score: float | None,
    ) -> PatternFamily:
        """Find best matching family or create a new one."""
        result = await db.execute(select(PatternFamily))
        families = result.scalars().all()

        if families:
            # Build centroid matrix and search — skip families with corrupt embeddings
            valid_families = []
            centroids = []
            for f in families:
                try:
                    centroid = np.frombuffer(f.centroid_embedding, dtype=np.float32)
                    if centroid.shape[0] != embedding.shape[0]:
                        logger.warning(
                            "Skipping family '%s' — centroid dimension %d != expected %d",
                            f.intent_label, centroid.shape[0], embedding.shape[0],
                        )
                        continue
                    centroids.append(centroid)
                    valid_families.append(f)
                except (ValueError, TypeError) as exc:
                    logger.warning(
                        "Skipping family '%s' — corrupt centroid: %s", f.intent_label, exc,
                    )
                    continue

            if centroids:
                matches = EmbeddingService.cosine_search(embedding, centroids, top_k=1)

                if matches and matches[0][1] >= FAMILY_MERGE_THRESHOLD:
                    idx, score = matches[0]
                    family = valid_families[idx]
                    logger.info(
                        "Merging into family '%s' (cosine=%.3f, members=%d→%d)",
                        family.intent_label,
                        score,
                        family.member_count,
                        family.member_count + 1,
                    )

                    # Update centroid as running mean
                    old_centroid = np.frombuffer(
                        family.centroid_embedding, dtype=np.float32
                    )
                    new_centroid = self._update_centroid(
                        old_centroid, embedding, family.member_count
                    )
                    family.centroid_embedding = new_centroid.astype(
                        np.float32
                    ).tobytes()
                    family.member_count += 1

                    # Update avg_score
                    if overall_score is not None and family.avg_score is not None:
                        total = (
                            family.avg_score * (family.member_count - 1) + overall_score
                        )
                        family.avg_score = round(total / family.member_count, 2)
                    elif overall_score is not None:
                        family.avg_score = overall_score

                    return family
                else:
                    best_score = matches[0][1] if matches else 0.0
                    logger.debug(
                        "No family match above threshold %.2f (best=%.3f across %d families)",
                        FAMILY_MERGE_THRESHOLD, best_score, len(valid_families),
                    )

        # No match — create new family
        family = PatternFamily(
            intent_label=intent_label,
            domain=domain,
            task_type=task_type,
            centroid_embedding=embedding.astype(np.float32).tobytes(),
            member_count=1,
            usage_count=0,
            avg_score=overall_score,
        )
        db.add(family)
        await db.flush()  # get ID
        logger.info(
            "Created new pattern family: id=%s label='%s' domain=%s task_type=%s (total=%d)",
            family.id, intent_label, domain, task_type, len(families) + 1,
        )
        return family

    @staticmethod
    def _update_centroid(
        old: np.ndarray, new: np.ndarray, member_count: int
    ) -> np.ndarray:
        """Running mean: (old * n + new) / (n + 1)."""
        return (old * member_count + new) / (member_count + 1)

    async def _extract_meta_patterns(self, opt: Optimization) -> list[str]:
        """Call Haiku to extract meta-patterns from a completed optimization."""
        try:
            template = self._prompt_loader.render(
                "extract_patterns.md",
                {
                    "raw_prompt": opt.raw_prompt[:2000],  # cap input size
                    "optimized_prompt": (opt.optimized_prompt or "")[:2000],
                    "intent_label": opt.intent_label or "general",
                    "domain": opt.domain or "general",
                    "strategy_used": opt.strategy_used or "auto",
                },
            )

            from pydantic import BaseModel as PydanticBaseModel

            class ExtractedPatterns(PydanticBaseModel):
                model_config = {"extra": "forbid"}
                patterns: list[str]

            if not self._provider:
                logger.warning("No LLM provider available for meta-pattern extraction")
                return []

            logger.debug(
                "Calling %s for meta-pattern extraction (opt=%s, model=%s)",
                self._provider.name, opt.id, settings.MODEL_HAIKU,
            )
            response = await self._provider.complete_parsed(
                model=settings.MODEL_HAIKU,
                system_prompt="You are a prompt engineering analyst. Extract reusable meta-patterns.",
                user_message=template,
                output_format=ExtractedPatterns,
            )

            # Filter and cap at 5
            patterns = [str(p) for p in response.patterns if isinstance(p, str)][:5]
            logger.debug(
                "Haiku returned %d meta-patterns for opt=%s", len(patterns), opt.id,
            )
            return patterns

        except Exception as exc:
            logger.warning(
                "Meta-pattern extraction failed (non-fatal) for opt=%s: %s",
                opt.id, exc,
            )
            return []

    async def _merge_meta_pattern(
        self, db: AsyncSession, family_id: str, pattern_text: str
    ) -> bool:
        """Merge a meta-pattern into a family — enrich existing or create new.

        Returns True if merged into existing, False if created new.
        """
        try:
            result = await db.execute(
                select(MetaPattern).where(MetaPattern.family_id == family_id)
            )
            existing = result.scalars().all()

            pattern_embedding = await self._embedding.aembed_single(pattern_text)

            if existing:
                # Check similarity against existing patterns
                embeddings = []
                for mp in existing:
                    if mp.embedding:
                        embeddings.append(
                            np.frombuffer(mp.embedding, dtype=np.float32)
                        )
                    else:
                        embeddings.append(np.zeros(384, dtype=np.float32))

                matches = EmbeddingService.cosine_search(
                    pattern_embedding, embeddings, top_k=1
                )
                if matches and matches[0][1] >= PATTERN_MERGE_THRESHOLD:
                    idx, score = matches[0]
                    mp = existing[idx]
                    mp.source_count += 1
                    # Update text if new version is longer (richer)
                    if len(pattern_text) > len(mp.pattern_text):
                        mp.pattern_text = pattern_text
                        mp.embedding = pattern_embedding.astype(
                            np.float32
                        ).tobytes()
                    logger.debug(
                        "Enriched meta-pattern '%s' (cosine=%.3f, count=%d)",
                        mp.pattern_text[:50],
                        score,
                        mp.source_count,
                    )
                    return True

            # No match — create new
            mp = MetaPattern(
                family_id=family_id,
                pattern_text=pattern_text,
                embedding=pattern_embedding.astype(np.float32).tobytes(),
                source_count=1,
            )
            db.add(mp)
            logger.debug(
                "Created new meta-pattern for family=%s: '%s'",
                family_id, pattern_text[:50],
            )
            return False

        except Exception as exc:
            logger.error(
                "Failed to merge meta-pattern into family=%s: %s",
                family_id, exc, exc_info=True,
            )
            return False
