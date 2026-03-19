"""Pattern knowledge graph endpoints — graph, families, match, search."""

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import PatternFamily
from app.services.knowledge_graph import KnowledgeGraphService
from app.services.pattern_matcher import PatternMatcherService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/patterns", tags=["patterns"])

_graph_service = KnowledgeGraphService()
_matcher_service = PatternMatcherService()


class MatchRequest(BaseModel):
    prompt_text: str = Field(..., min_length=10)


class RenameRequest(BaseModel):
    intent_label: str = Field(..., min_length=1, max_length=100)


@router.get("/graph")
async def get_graph(
    family_id: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    """Full mindmap graph data or subtree for a specific family."""
    try:
        return await _graph_service.get_graph(db, family_id=family_id)
    except Exception as exc:
        logger.error("Failed to build graph: %s", exc, exc_info=True)
        raise HTTPException(500, "Failed to build knowledge graph") from exc


@router.post("/match")
async def match_pattern(
    body: MatchRequest,
    db: AsyncSession = Depends(get_db),
):
    """Similarity check for auto-suggestion on paste."""
    try:
        result = await _matcher_service.match(db, body.prompt_text)
        if result is None:
            return {"match": None}
        return {"match": result}
    except Exception as exc:
        logger.error("Pattern match failed: %s", exc, exc_info=True)
        raise HTTPException(500, "Pattern matching failed") from exc


@router.get("/families")
async def list_families(
    offset: int = 0,
    limit: int = 50,
    domain: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    """List all pattern families with pagination."""
    offset = max(offset, 0)
    limit = min(max(limit, 1), 500)

    from sqlalchemy import func, select

    query = select(PatternFamily).order_by(PatternFamily.usage_count.desc())
    count_query = select(func.count(PatternFamily.id))

    if domain:
        query = query.where(PatternFamily.domain == domain)
        count_query = count_query.where(PatternFamily.domain == domain)

    total = (await db.execute(count_query)).scalar() or 0
    result = await db.execute(query.offset(offset).limit(limit))
    families = result.scalars().all()

    logger.debug(
        "Listed families: total=%d returned=%d offset=%d domain=%s",
        total, len(families), offset, domain,
    )

    return {
        "total": total,
        "count": len(families),
        "offset": offset,
        "has_more": offset + len(families) < total,
        "next_offset": offset + len(families) if offset + len(families) < total else None,
        "items": [
            {
                "id": f.id,
                "intent_label": f.intent_label,
                "domain": f.domain,
                "task_type": f.task_type,
                "usage_count": f.usage_count,
                "member_count": f.member_count,
                "avg_score": f.avg_score,
                "created_at": f.created_at.isoformat() if f.created_at else None,
            }
            for f in families
        ],
    }


@router.get("/families/{family_id}")
async def get_family(
    family_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Family detail with meta-patterns and linked optimizations."""
    try:
        detail = await _graph_service.get_family_detail(db, family_id)
    except Exception as exc:
        logger.error("Failed to load family detail id=%s: %s", family_id, exc, exc_info=True)
        raise HTTPException(500, "Failed to load family detail") from exc
    if not detail:
        raise HTTPException(404, "Pattern family not found")
    return detail


@router.patch("/families/{family_id}")
async def rename_family(
    family_id: str,
    body: RenameRequest,
    db: AsyncSession = Depends(get_db),
):
    """Rename a pattern family (user label override)."""
    from sqlalchemy import select

    result = await db.execute(
        select(PatternFamily).where(PatternFamily.id == family_id)
    )
    family = result.scalar_one_or_none()
    if not family:
        raise HTTPException(404, "Pattern family not found")

    old_label = family.intent_label
    family.intent_label = body.intent_label
    await db.commit()

    logger.info(
        "Family renamed: id=%s '%s' → '%s'",
        family_id, old_label, body.intent_label,
    )
    return {"id": family.id, "intent_label": family.intent_label}


@router.get("/search")
async def search_patterns(
    q: str,
    top_k: int = 5,
    db: AsyncSession = Depends(get_db),
):
    """Semantic search across families and meta-patterns."""
    if not q.strip():
        raise HTTPException(400, "Query cannot be empty")
    try:
        return await _graph_service.search_patterns(db, q, top_k=min(top_k, 20))
    except Exception as exc:
        logger.error("Pattern search failed for q='%s': %s", q[:50], exc, exc_info=True)
        raise HTTPException(500, "Pattern search failed") from exc


@router.get("/stats")
async def get_stats(db: AsyncSession = Depends(get_db)):
    """Summary statistics for the knowledge graph."""
    try:
        return await _graph_service.get_stats(db)
    except Exception as exc:
        logger.error("Failed to get pattern stats: %s", exc, exc_info=True)
        raise HTTPException(500, "Failed to load pattern statistics") from exc
