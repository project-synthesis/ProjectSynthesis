"""Pattern knowledge graph endpoints — graph, families, match, search."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
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
    prompt_text: str = Field(
        ..., min_length=10,
        description="Prompt text to match against existing pattern families.",
    )


class PatternMatchResponse(BaseModel):
    match: dict | None = Field(
        default=None,
        description="Matched pattern family with meta-patterns and similarity score, or null if no match.",
    )


class FamilyItem(BaseModel):
    id: str = Field(description="Pattern family ID.")
    intent_label: str = Field(description="Human-readable intent label.")
    domain: str = Field(description="Domain category (backend, frontend, database, etc.).")
    task_type: str = Field(description="Task type classification.")
    usage_count: int = Field(description="Number of times patterns from this family were applied.")
    member_count: int = Field(description="Number of optimizations in this family.")
    avg_score: float | None = Field(default=None, description="Average overall score of family members.")
    created_at: str | None = Field(default=None, description="ISO 8601 creation timestamp.")


class FamilyListResponse(BaseModel):
    total: int = Field(description="Total number of matching families.")
    count: int = Field(description="Number of families in this page.")
    offset: int = Field(description="Current pagination offset.")
    has_more: bool = Field(description="Whether more pages exist.")
    next_offset: int | None = Field(default=None, description="Offset for the next page, or null.")
    items: list[FamilyItem] = Field(description="Pattern family items for this page.")


class RenameRequest(BaseModel):
    intent_label: str = Field(
        ..., min_length=1, max_length=100,
        description="New intent label for the pattern family.",
    )


class RenameResponse(BaseModel):
    id: str = Field(description="Pattern family ID.")
    intent_label: str = Field(description="Updated intent label.")


@router.get("/graph")
async def get_graph(
    family_id: str | None = Query(default=None, description="Optional family ID to show subtree."),
    db: AsyncSession = Depends(get_db),
) -> dict:
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
) -> PatternMatchResponse:
    """Similarity check for auto-suggestion on paste."""
    try:
        result = await _matcher_service.match(db, body.prompt_text)
        if result is None:
            return PatternMatchResponse(match=None)
        return PatternMatchResponse(match=result)
    except Exception as exc:
        logger.error("Pattern match failed: %s", exc, exc_info=True)
        raise HTTPException(500, "Pattern matching failed") from exc


@router.get("/families")
async def list_families(
    offset: int = Query(0, ge=0, description="Pagination offset."),
    limit: int = Query(50, ge=1, le=500, description="Items per page (1-500)."),
    domain: str | None = Query(default=None, description="Filter by domain category."),
    db: AsyncSession = Depends(get_db),
) -> FamilyListResponse:
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

    items = [
        FamilyItem(
            id=f.id,
            intent_label=f.intent_label,
            domain=f.domain,
            task_type=f.task_type,
            usage_count=f.usage_count,
            member_count=f.member_count,
            avg_score=f.avg_score,
            created_at=f.created_at.isoformat() if f.created_at else None,
        )
        for f in families
    ]

    return FamilyListResponse(
        total=total,
        count=len(families),
        offset=offset,
        has_more=offset + len(families) < total,
        next_offset=offset + len(families) if offset + len(families) < total else None,
        items=items,
    )


@router.get("/families/{family_id}")
async def get_family(
    family_id: str,
    db: AsyncSession = Depends(get_db),
) -> dict:
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
) -> RenameResponse:
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
    return RenameResponse(id=family.id, intent_label=family.intent_label)


@router.get("/search")
async def search_patterns(
    q: str = Query(description="Semantic search query."),
    top_k: int = Query(5, ge=1, le=20, description="Maximum results to return (1-20)."),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Semantic search across families and meta-patterns."""
    if not q.strip():
        raise HTTPException(400, "Query cannot be empty")
    try:
        return await _graph_service.search_patterns(db, q, top_k=min(top_k, 20))
    except Exception as exc:
        logger.error("Pattern search failed for q='%s': %s", q[:50], exc, exc_info=True)
        raise HTTPException(500, "Pattern search failed") from exc


@router.get("/stats")
async def get_stats(db: AsyncSession = Depends(get_db)) -> dict:
    """Summary statistics for the knowledge graph."""
    try:
        return await _graph_service.get_stats(db)
    except Exception as exc:
        logger.error("Failed to get pattern stats: %s", exc, exc_info=True)
        raise HTTPException(500, "Failed to load pattern statistics") from exc
