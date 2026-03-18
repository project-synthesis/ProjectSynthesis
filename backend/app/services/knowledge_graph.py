"""Knowledge graph service — in-memory cache, graph computation, semantic search.

Provides the data structure for the radial mindmap frontend visualization.
"""

from __future__ import annotations

import logging

import numpy as np
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import MetaPattern, Optimization, OptimizationPattern, PatternFamily
from app.services.embedding_service import EmbeddingService

logger = logging.getLogger(__name__)

EDGE_THRESHOLD = 0.55


class KnowledgeGraphService:
    """Builds and queries the pattern knowledge graph."""

    def __init__(self, embedding_service: EmbeddingService | None = None) -> None:
        self._embedding = embedding_service or EmbeddingService()

    async def get_graph(self, db: AsyncSession, family_id: str | None = None) -> dict:
        """Build the full graph structure for the radial mindmap."""
        # Load families
        query = select(PatternFamily)
        if family_id:
            query = query.where(PatternFamily.id == family_id)
        result = await db.execute(query)
        families = result.scalars().all()

        if not families:
            return {"center": {"total_families": 0, "total_patterns": 0, "total_optimizations": 0}, "families": [], "edges": []}

        # Load meta-patterns for all families
        family_ids = [f.id for f in families]
        meta_result = await db.execute(
            select(MetaPattern).where(MetaPattern.family_id.in_(family_ids))
        )
        all_meta = meta_result.scalars().all()
        meta_by_family: dict[str, list] = {}
        for mp in all_meta:
            meta_by_family.setdefault(mp.family_id, []).append(mp)

        # Count total optimizations linked
        opt_count_result = await db.execute(
            select(func.count(func.distinct(OptimizationPattern.optimization_id)))
            .where(OptimizationPattern.family_id.in_(family_ids))
        )
        total_optimizations = opt_count_result.scalar() or 0

        # Build family nodes
        family_nodes = []
        for f in families:
            meta_patterns = meta_by_family.get(f.id, [])
            family_nodes.append({
                "id": f.id,
                "intent_label": f.intent_label,
                "domain": f.domain,
                "task_type": f.task_type,
                "usage_count": f.usage_count,
                "member_count": f.member_count,
                "avg_score": f.avg_score,
                "meta_patterns": [
                    {"id": mp.id, "pattern_text": mp.pattern_text, "source_count": mp.source_count}
                    for mp in sorted(meta_patterns, key=lambda m: m.source_count, reverse=True)
                ],
            })

        # Compute edges
        edges = self._compute_edges(families) if len(families) > 1 else []

        return {
            "center": {
                "total_families": len(families),
                "total_patterns": len(all_meta),
                "total_optimizations": total_optimizations,
            },
            "families": family_nodes,
            "edges": edges,
        }

    def _compute_edges(self, families: list[PatternFamily]) -> list[dict]:
        """Compute cross-family edges based on centroid similarity."""
        edges = []
        centroids = []
        for f in families:
            centroids.append(np.frombuffer(f.centroid_embedding, dtype=np.float32))

        for i in range(len(families)):
            for j in range(i + 1, len(families)):
                # Cosine similarity
                norm_i = np.linalg.norm(centroids[i]) + 1e-9
                norm_j = np.linalg.norm(centroids[j]) + 1e-9
                sim = float(np.dot(centroids[i], centroids[j]) / (norm_i * norm_j))

                if sim >= EDGE_THRESHOLD:
                    edges.append({
                        "from": families[i].id,
                        "to": families[j].id,
                        "shared_patterns": 0,
                        "weight": round(sim, 3),
                    })

        return edges

    async def search_patterns(
        self, db: AsyncSession, query: str, top_k: int = 5
    ) -> list[dict]:
        """Semantic search across all families and meta-patterns."""
        query_embedding = await self._embedding.aembed_single(query)

        # Search families
        result = await db.execute(select(PatternFamily))
        families = result.scalars().all()

        if not families:
            return []

        centroids = [np.frombuffer(f.centroid_embedding, dtype=np.float32) for f in families]
        matches = EmbeddingService.cosine_search(query_embedding, centroids, top_k=top_k)

        # Also search meta-patterns
        meta_result = await db.execute(select(MetaPattern).where(MetaPattern.embedding.isnot(None)))
        all_meta = meta_result.scalars().all()

        results = []
        for idx, score in matches:
            f = families[idx]
            results.append({
                "type": "family",
                "id": f.id,
                "label": f.intent_label,
                "domain": f.domain,
                "score": round(score, 3),
            })

        if all_meta:
            meta_embeddings = [np.frombuffer(mp.embedding, dtype=np.float32) for mp in all_meta]
            meta_matches = EmbeddingService.cosine_search(query_embedding, meta_embeddings, top_k=top_k)
            for idx, score in meta_matches:
                mp = all_meta[idx]
                results.append({
                    "type": "meta_pattern",
                    "id": mp.id,
                    "label": mp.pattern_text,
                    "family_id": mp.family_id,
                    "score": round(score, 3),
                })

        # Sort by score, return top_k
        results.sort(key=lambda r: r["score"], reverse=True)
        return results[:top_k]

    async def get_family_detail(self, db: AsyncSession, family_id: str) -> dict | None:
        """Get detailed view of a single family."""
        result = await db.execute(
            select(PatternFamily).where(PatternFamily.id == family_id)
        )
        family = result.scalar_one_or_none()
        if not family:
            return None

        # Meta-patterns
        meta_result = await db.execute(
            select(MetaPattern)
            .where(MetaPattern.family_id == family_id)
            .order_by(MetaPattern.source_count.desc())
        )
        meta_patterns = meta_result.scalars().all()

        # Linked optimizations
        opt_result = await db.execute(
            select(Optimization)
            .join(OptimizationPattern, OptimizationPattern.optimization_id == Optimization.id)
            .where(OptimizationPattern.family_id == family_id)
            .order_by(Optimization.created_at.desc())
            .limit(20)
        )
        optimizations = opt_result.scalars().all()

        return {
            "id": family.id,
            "intent_label": family.intent_label,
            "domain": family.domain,
            "task_type": family.task_type,
            "usage_count": family.usage_count,
            "member_count": family.member_count,
            "avg_score": family.avg_score,
            "created_at": family.created_at.isoformat() if family.created_at else None,
            "updated_at": family.updated_at.isoformat() if family.updated_at else None,
            "meta_patterns": [
                {"id": mp.id, "pattern_text": mp.pattern_text, "source_count": mp.source_count}
                for mp in meta_patterns
            ],
            "optimizations": [
                {
                    "id": o.id,
                    "raw_prompt": (o.raw_prompt or "")[:100],
                    "overall_score": o.overall_score,
                    "strategy_used": o.strategy_used,
                    "created_at": o.created_at.isoformat() if o.created_at else None,
                }
                for o in optimizations
            ],
        }

    async def get_stats(self, db: AsyncSession) -> dict:
        """Summary statistics for the knowledge graph."""
        fam_count = (await db.execute(select(func.count(PatternFamily.id)))).scalar() or 0
        meta_count = (await db.execute(select(func.count(MetaPattern.id)))).scalar() or 0
        opt_count = (await db.execute(
            select(func.count(func.distinct(OptimizationPattern.optimization_id)))
        )).scalar() or 0

        # Domain distribution
        domain_result = await db.execute(
            select(PatternFamily.domain, func.count(PatternFamily.id))
            .group_by(PatternFamily.domain)
        )
        domain_dist = {row[0]: row[1] for row in domain_result}

        return {
            "total_families": fam_count,
            "total_patterns": meta_count,
            "total_optimizations": opt_count,
            "domain_distribution": domain_dist,
        }
