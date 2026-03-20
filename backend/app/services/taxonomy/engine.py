"""TaxonomyEngine — hot path orchestration for the Evolutionary Taxonomy Engine.

Spec Section 2.3, 4.2, 6.4, 7.3, 7.5.

Responsibilities:
  - process_optimization: embed + assign family + extract meta-patterns (hot path)
  - map_domain: embed domain_raw, optional Bayesian blend with applied pattern
    centroids, cosine search over confirmed TaxonomyNode centroids.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

import numpy as np
from pydantic import BaseModel, Field as PydanticField
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import PROMPTS_DIR, settings
from app.models import (
    MetaPattern,
    Optimization,
    OptimizationPattern,
    PatternFamily,
    TaxonomyNode,
)
from app.providers.base import LLMProvider
from app.services.embedding_service import EmbeddingService
from app.services.prompt_loader import PromptLoader

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants — cosine similarity thresholds
# ---------------------------------------------------------------------------

FAMILY_MERGE_THRESHOLD = 0.78
PATTERN_MERGE_THRESHOLD = 0.82
DOMAIN_ALIGNMENT_FLOOR = 0.35

# Valid domain values — must match DomainType in pipeline_contracts.py
_VALID_DOMAINS = frozenset(
    {"backend", "frontend", "database", "devops", "security", "fullstack", "general"}
)


def _sanitize_domain(domain: str) -> str:
    """Normalize domain to a known value. Falls back to 'general' for unknown."""
    return domain if domain in _VALID_DOMAINS else "general"


# ---------------------------------------------------------------------------
# Public data-transfer objects
# ---------------------------------------------------------------------------


@dataclass
class TaxonomyMapping:
    """Result of map_domain — may be fully unmapped (taxonomy_node_id is None)."""

    taxonomy_node_id: str | None
    taxonomy_label: str | None
    taxonomy_breadcrumb: list[str]
    domain_raw: str


@dataclass
class PatternMatch:
    """Result of a pattern similarity search against the knowledge graph."""

    family: PatternFamily | None
    taxonomy_node: TaxonomyNode | None
    meta_patterns: list[MetaPattern]
    similarity: float
    match_level: str  # "family" | "cluster" | "none"


# ---------------------------------------------------------------------------
# Pydantic schema for _extract_meta_patterns structured output
# ---------------------------------------------------------------------------


class _ExtractedPatterns(BaseModel):
    model_config = {"extra": "forbid"}
    patterns: list[str] = PydanticField(
        description=(
            "List of reusable meta-pattern descriptions extracted from the "
            "optimization (max 5)."
        ),
    )


# ---------------------------------------------------------------------------
# TaxonomyEngine
# ---------------------------------------------------------------------------


class TaxonomyEngine:
    """Orchestrates the hot path for the Evolutionary Taxonomy Engine.

    Args:
        embedding_service: EmbeddingService instance (or mock in tests).
        provider: LLM provider used for Haiku calls. None disables LLM steps.
    """

    def __init__(
        self,
        embedding_service: EmbeddingService | None = None,
        provider: LLMProvider | None = None,
    ) -> None:
        self._embedding = embedding_service or EmbeddingService()
        self._provider = provider
        self._prompt_loader = PromptLoader(PROMPTS_DIR)
        # Lock gates concurrent warm-path writes to shared centroid state.
        self._lock: asyncio.Lock = asyncio.Lock()
        # In-memory centroid cache: node_id → ndarray.  Invalidated on writes.
        self._centroid_cache: dict[str, np.ndarray] = {}

    # ------------------------------------------------------------------
    # Public hot-path entry point
    # ------------------------------------------------------------------

    async def process_optimization(
        self,
        optimization_id: str,
        db: AsyncSession,
    ) -> None:
        """Full extraction pipeline for a single completed optimization.

        Steps:
          1. Load optimization — skip if not 'completed'.
          2. Idempotency check via OptimizationPattern 'source' record.
          3. Embed raw_prompt.
          4. Find or create PatternFamily via _assign_family().
          5. Extract meta-patterns via _extract_meta_patterns().
          6. Merge meta-patterns via _merge_meta_pattern().
          7. Write OptimizationPattern join record and commit.

        Args:
            optimization_id: PK of the Optimization row to process.
            db: Async SQLAlchemy session.
        """
        try:
            result = await db.execute(
                select(Optimization).where(Optimization.id == optimization_id)
            )
            opt = result.scalar_one_or_none()

            if not opt or opt.status != "completed":
                logger.debug(
                    "Skipping taxonomy extraction for %s (status=%s)",
                    optimization_id,
                    opt.status if opt else "not_found",
                )
                return

            # Idempotency: skip if a 'source' OptimizationPattern already exists
            existing = await db.execute(
                select(OptimizationPattern).where(
                    OptimizationPattern.optimization_id == optimization_id,
                    OptimizationPattern.relationship == "source",
                )
            )
            if existing.scalar_one_or_none():
                logger.debug(
                    "Skipping taxonomy extraction for %s (already processed)",
                    optimization_id,
                )
                return

            # 1. Embed raw_prompt
            embedding = await self._embedding.aembed_single(opt.raw_prompt)
            opt.embedding = embedding.astype(np.float32).tobytes()

            # 2. Find or create PatternFamily
            # Use the structured `domain` field (already validated by the pipeline)
            # as the canonical domain for family assignment.  `domain_raw` is freeform
            # text that may not map to a valid domain value.
            async with self._lock:
                family = await self._assign_family(
                    db=db,
                    embedding=embedding,
                    intent_label=opt.intent_label or "general",
                    domain=_sanitize_domain(opt.domain or "general"),
                    task_type=opt.task_type or "general",
                    overall_score=opt.overall_score,
                )

            # 3. Extract meta-patterns
            meta_texts = await self._extract_meta_patterns(opt)

            # 4. Merge meta-patterns
            for text in meta_texts:
                await self._merge_meta_pattern(db, family.id, text)

            # 5. Write join record
            join = OptimizationPattern(
                optimization_id=opt.id,
                family_id=family.id,
                relationship="source",
            )
            db.add(join)

            await db.commit()
            logger.debug(
                "Taxonomy extraction complete: opt=%s family='%s' meta_patterns=%d",
                optimization_id,
                family.intent_label,
                len(meta_texts),
            )

        except Exception as exc:
            logger.error(
                "Taxonomy process_optimization failed for %s: %s",
                optimization_id,
                exc,
                exc_info=True,
            )

    # ------------------------------------------------------------------
    # Domain mapping
    # ------------------------------------------------------------------

    async def map_domain(
        self,
        domain_raw: str,
        db: AsyncSession,
        applied_pattern_ids: list[str] | None = None,
    ) -> TaxonomyMapping:
        """Map a free-text domain string to the nearest confirmed TaxonomyNode.

        If applied_pattern_ids are provided, compute a pattern centroid and
        blend 70 % analyzer embedding + 30 % pattern centroid (Bayesian prior).

        Args:
            domain_raw: Raw domain string from the analyzer phase.
            db: Async SQLAlchemy session.
            applied_pattern_ids: Optional list of MetaPattern IDs applied to
                this optimization — used to inject a pattern-based prior.

        Returns:
            TaxonomyMapping.  taxonomy_node_id is None when no confirmed node
            has cosine similarity ≥ DOMAIN_ALIGNMENT_FLOOR.
        """
        # Embed domain_raw
        query_emb = await self._embedding.aembed_single(domain_raw)

        # Optional 70/30 Bayesian blend with pattern centroid
        if applied_pattern_ids:
            pattern_centroid = await self._compute_pattern_centroid(
                db, applied_pattern_ids
            )
            if pattern_centroid is not None:
                # 70 % analyzer, 30 % pattern prior
                blended = 0.7 * query_emb + 0.3 * pattern_centroid
                norm = np.linalg.norm(blended)
                if norm > 0:
                    query_emb = blended / norm

        # Load confirmed TaxonomyNode centroids
        result = await db.execute(
            select(TaxonomyNode).where(TaxonomyNode.state == "confirmed")
        )
        nodes = result.scalars().all()

        if not nodes:
            return TaxonomyMapping(
                taxonomy_node_id=None,
                taxonomy_label=None,
                taxonomy_breadcrumb=[],
                domain_raw=domain_raw,
            )

        # Build centroid list, skip corrupt rows
        valid_nodes: list[TaxonomyNode] = []
        centroids: list[np.ndarray] = []
        for node in nodes:
            try:
                c = np.frombuffer(node.centroid_embedding, dtype=np.float32)
                if c.shape[0] != query_emb.shape[0]:
                    logger.warning(
                        "TaxonomyNode '%s' centroid dim %d != query dim %d — skipped",
                        node.label,
                        c.shape[0],
                        query_emb.shape[0],
                    )
                    continue
                centroids.append(c)
                valid_nodes.append(node)
            except (ValueError, TypeError) as exc:
                logger.warning(
                    "TaxonomyNode '%s' has corrupt centroid: %s — skipped",
                    node.label,
                    exc,
                )

        if not centroids:
            return TaxonomyMapping(
                taxonomy_node_id=None,
                taxonomy_label=None,
                taxonomy_breadcrumb=[],
                domain_raw=domain_raw,
            )

        # Nearest centroid search
        matches = EmbeddingService.cosine_search(query_emb, centroids, top_k=1)
        if not matches:
            return TaxonomyMapping(
                taxonomy_node_id=None,
                taxonomy_label=None,
                taxonomy_breadcrumb=[],
                domain_raw=domain_raw,
            )

        idx, score = matches[0]
        if score < DOMAIN_ALIGNMENT_FLOOR:
            return TaxonomyMapping(
                taxonomy_node_id=None,
                taxonomy_label=None,
                taxonomy_breadcrumb=[],
                domain_raw=domain_raw,
            )

        best_node = valid_nodes[idx]
        breadcrumb = await self._build_breadcrumb(db, best_node)

        return TaxonomyMapping(
            taxonomy_node_id=best_node.id,
            taxonomy_label=best_node.label,
            taxonomy_breadcrumb=breadcrumb,
            domain_raw=domain_raw,
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _assign_family(
        self,
        db: AsyncSession,
        embedding: np.ndarray,
        intent_label: str,
        domain: str,
        task_type: str,
        overall_score: float | None,
    ) -> PatternFamily:
        """Find nearest PatternFamily or create a new one.

        Nearest centroid search with FAMILY_MERGE_THRESHOLD guard and
        cross-domain merge prevention.  Updates centroid as running mean
        ``(old * n + new) / (n+1)`` on merge.

        Args:
            db: Async SQLAlchemy session.
            embedding: Unit-norm embedding of the raw prompt.
            intent_label: Analyzer intent label.
            domain: Sanitized domain string (one of the known domain values).
            task_type: Analyzer task type.
            overall_score: Pipeline overall score (may be None).

        Returns:
            Existing (updated) or newly-created PatternFamily.
        """

        result = await db.execute(select(PatternFamily))
        families = result.scalars().all()

        if families:
            valid_families: list[PatternFamily] = []
            centroids: list[np.ndarray] = []

            for f in families:
                try:
                    c = np.frombuffer(f.centroid_embedding, dtype=np.float32)
                    if c.shape[0] != embedding.shape[0]:
                        logger.warning(
                            "Skipping family '%s' — centroid dim %d != expected %d",
                            f.intent_label,
                            c.shape[0],
                            embedding.shape[0],
                        )
                        continue
                    centroids.append(c)
                    valid_families.append(f)
                except (ValueError, TypeError) as exc:
                    logger.warning(
                        "Skipping family '%s' — corrupt centroid: %s",
                        f.intent_label,
                        exc,
                    )

            if centroids:
                matches = EmbeddingService.cosine_search(embedding, centroids, top_k=1)
                if matches and matches[0][1] >= FAMILY_MERGE_THRESHOLD:
                    idx, score = matches[0]
                    family = valid_families[idx]

                    # Cross-domain merge prevention
                    if family.domain != domain:
                        logger.info(
                            "Cross-domain merge prevented: family '%s' domain=%s != "
                            "incoming domain=%s (cosine=%.3f). Creating new family.",
                            family.intent_label,
                            family.domain,
                            domain,
                            score,
                        )
                        # Fall through to creation
                    else:
                        # Merge: update centroid as running mean
                        old_centroid = np.frombuffer(
                            family.centroid_embedding, dtype=np.float32
                        )
                        new_centroid = (old_centroid * family.member_count + embedding) / (
                            family.member_count + 1
                        )
                        family.centroid_embedding = new_centroid.astype(
                            np.float32
                        ).tobytes()
                        family.member_count += 1

                        # avg_score tracks the running mean over members that
                        # have a score.  Members with overall_score=None are
                        # excluded intentionally — we cannot average with None.
                        # When the first scored member arrives, avg_score is
                        # seeded with that single score.
                        if overall_score is not None and family.avg_score is not None:
                            family.avg_score = round(
                                (
                                    family.avg_score * (family.member_count - 1)
                                    + overall_score
                                )
                                / family.member_count,
                                2,
                            )
                        elif overall_score is not None:
                            family.avg_score = overall_score

                        logger.debug(
                            "Merged into family '%s' (cosine=%.3f, members=%d)",
                            family.intent_label,
                            score,
                            family.member_count,
                        )
                        return family

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
        await db.flush()  # populate ID
        logger.debug(
            "Created new PatternFamily: id=%s label='%s' domain=%s",
            family.id,
            intent_label,
            domain,
        )
        return family

    async def _extract_meta_patterns(self, opt: Optimization) -> list[str]:
        """Call Haiku to extract meta-patterns from a completed optimization.

        Renders extract_patterns.md template, calls provider.complete_parsed()
        with _ExtractedPatterns structured output.  Caps at 5 patterns.
        Returns empty list on any error (non-fatal).

        Args:
            opt: Completed Optimization row with prompt text and metadata.

        Returns:
            List of meta-pattern strings (at most 5).
        """
        if not self._provider:
            logger.debug("No LLM provider — skipping meta-pattern extraction")
            return []

        try:
            template = self._prompt_loader.render(
                "extract_patterns.md",
                {
                    "raw_prompt": opt.raw_prompt[:2000],
                    "optimized_prompt": (opt.optimized_prompt or "")[:2000],
                    "intent_label": opt.intent_label or "general",
                    "domain": opt.domain or "general",
                    "strategy_used": opt.strategy_used or "auto",
                },
            )

            response = await self._provider.complete_parsed(
                model=settings.MODEL_HAIKU,
                system_prompt=(
                    "You are a prompt engineering analyst. "
                    "Extract reusable meta-patterns."
                ),
                user_message=template,
                output_format=_ExtractedPatterns,
            )

            patterns = [
                str(p) for p in response.patterns if isinstance(p, str)
            ][:5]
            logger.debug(
                "Haiku returned %d meta-patterns for opt=%s", len(patterns), opt.id
            )
            return patterns

        except Exception as exc:
            logger.warning(
                "Meta-pattern extraction failed (non-fatal) for opt=%s: %s",
                opt.id,
                exc,
            )
            return []

    async def _merge_meta_pattern(
        self, db: AsyncSession, family_id: str, pattern_text: str
    ) -> bool:
        """Merge a meta-pattern into a family — enrich existing or create new.

        Cosine search against existing MetaPatterns for the family.  If the
        best match is ≥ PATTERN_MERGE_THRESHOLD: increment source_count and
        update text if new version is longer.  Otherwise create a new row.

        Args:
            db: Async SQLAlchemy session.
            family_id: PatternFamily PK.
            pattern_text: Meta-pattern text extracted by Haiku.

        Returns:
            True if merged into existing pattern, False if new pattern created.
        """
        try:
            result = await db.execute(
                select(MetaPattern).where(MetaPattern.family_id == family_id)
            )
            existing = result.scalars().all()

            pattern_embedding = await self._embedding.aembed_single(pattern_text)

            if existing:
                embeddings: list[np.ndarray] = []
                for mp in existing:
                    if mp.embedding:
                        embeddings.append(
                            np.frombuffer(mp.embedding, dtype=np.float32)
                        )
                    else:
                        embeddings.append(
                            np.zeros(self._embedding.dimension, dtype=np.float32)
                        )

                matches = EmbeddingService.cosine_search(
                    pattern_embedding, embeddings, top_k=1
                )
                if matches and matches[0][1] >= PATTERN_MERGE_THRESHOLD:
                    idx, score = matches[0]
                    mp = existing[idx]
                    mp.source_count += 1
                    if len(pattern_text) > len(mp.pattern_text):
                        mp.pattern_text = pattern_text
                        mp.embedding = pattern_embedding.astype(np.float32).tobytes()
                    logger.debug(
                        "Enriched meta-pattern '%s' (cosine=%.3f, count=%d)",
                        mp.pattern_text[:50],
                        score,
                        mp.source_count,
                    )
                    return True

            # No match — create new MetaPattern
            mp = MetaPattern(
                family_id=family_id,
                pattern_text=pattern_text,
                embedding=pattern_embedding.astype(np.float32).tobytes(),
                source_count=1,
            )
            db.add(mp)
            logger.debug(
                "Created new MetaPattern for family=%s: '%s'",
                family_id,
                pattern_text[:50],
            )
            return False

        except Exception as exc:
            logger.error(
                "Failed to merge meta-pattern into family=%s: %s",
                family_id,
                exc,
                exc_info=True,
            )
            return False

    async def _compute_pattern_centroid(
        self, db: AsyncSession, pattern_ids: list[str]
    ) -> np.ndarray | None:
        """Compute mean centroid of TaxonomyNodes linked via MetaPattern → PatternFamily.

        Looks up MetaPatterns by ID, gets their families' taxonomy_node_id,
        loads the corresponding TaxonomyNode centroids, and returns the mean.

        Args:
            db: Async SQLAlchemy session.
            pattern_ids: List of MetaPattern PKs.

        Returns:
            Mean centroid as float32 ndarray, or None if no valid nodes found.
        """
        if not pattern_ids:
            return None

        result = await db.execute(
            select(MetaPattern).where(MetaPattern.id.in_(pattern_ids))
        )
        meta_patterns = result.scalars().all()

        if not meta_patterns:
            return None

        # Collect unique family IDs
        family_ids = list({mp.family_id for mp in meta_patterns if mp.family_id})
        if not family_ids:
            return None

        # Load families to get taxonomy_node_ids
        fam_result = await db.execute(
            select(PatternFamily).where(PatternFamily.id.in_(family_ids))
        )
        families = fam_result.scalars().all()

        node_ids = list(
            {f.taxonomy_node_id for f in families if f.taxonomy_node_id}
        )
        if not node_ids:
            return None

        # Load TaxonomyNodes and collect their centroids
        node_result = await db.execute(
            select(TaxonomyNode).where(TaxonomyNode.id.in_(node_ids))
        )
        nodes = node_result.scalars().all()

        vecs: list[np.ndarray] = []
        for node in nodes:
            try:
                c = np.frombuffer(node.centroid_embedding, dtype=np.float32)
                vecs.append(c)
            except (ValueError, TypeError):
                continue

        if not vecs:
            return None

        mean = np.mean(np.stack(vecs, axis=0), axis=0).astype(np.float32)
        norm = np.linalg.norm(mean)
        if norm > 0:
            mean = mean / norm
        return mean

    async def _build_breadcrumb(
        self, db: AsyncSession, node: TaxonomyNode
    ) -> list[str]:
        """Walk parent_id chain upward and return labels from root to leaf.

        Args:
            db: Async SQLAlchemy session.
            node: The leaf TaxonomyNode to start from.

        Returns:
            List of label strings ordered from root to leaf.
        """
        labels: list[str] = []
        current: TaxonomyNode | None = node
        visited: set[str] = set()  # cycle guard

        while current is not None:
            if current.id in visited:
                logger.warning(
                    "Breadcrumb cycle detected at node '%s' (id=%s) — stopping",
                    current.label,
                    current.id,
                )
                break
            visited.add(current.id)
            labels.append(current.label)

            if current.parent_id is None:
                break

            parent_result = await db.execute(
                select(TaxonomyNode).where(TaxonomyNode.id == current.parent_id)
            )
            current = parent_result.scalar_one_or_none()

        # Reverse so list goes root → leaf
        labels.reverse()
        return labels
