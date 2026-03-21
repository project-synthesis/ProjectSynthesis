"""Pattern endpoints — families, match, family detail, family update."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import MetaPattern, Optimization, OptimizationPattern, PatternFamily

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/patterns", tags=["patterns"])


class MatchRequest(BaseModel):
    prompt_text: str = Field(
        ..., min_length=10,
        description="Prompt text to match against existing pattern families.",
    )


class PatternMatchResponse(BaseModel):
    match: dict | None = Field(
        default=None,
        description="Matched pattern with taxonomy context, or null if no match.",
    )
    match_level: str = Field(
        default="none",
        description="Match level: 'family', 'cluster', or 'none'.",
    )
    taxonomy_node_id: str | None = Field(
        default=None,
        description="Taxonomy node ID for the matched cluster.",
    )
    taxonomy_label: str | None = Field(
        default=None,
        description="Generated label for the matched taxonomy node.",
    )
    taxonomy_color: str | None = Field(
        default=None,
        description="OKLab-derived color hex for the matched taxonomy node.",
    )
    taxonomy_breadcrumb: list[str] = Field(
        default_factory=list,
        description="Hierarchy path from root to matched node.",
    )
    similarity: float = Field(
        default=0.0,
        description="Cosine similarity score of the match.",
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


class UpdateFamilyRequest(BaseModel):
    intent_label: str | None = Field(
        default=None, min_length=1, max_length=100,
        description="New intent label for the pattern family.",
    )
    domain: str | None = Field(
        default=None,
        description="New domain for the pattern family (free-text).",
    )


class UpdateFamilyResponse(BaseModel):
    id: str = Field(description="Pattern family ID.")
    intent_label: str = Field(description="Current intent label.")
    domain: str = Field(description="Current domain category.")


@router.post("/match")
async def match_pattern(
    request: Request,
    body: MatchRequest,
    db: AsyncSession = Depends(get_db),
) -> PatternMatchResponse:
    """Hierarchical similarity check for auto-suggestion on paste."""
    try:
        from app.services.taxonomy import TaxonomyEngine

        # Use singleton engine from app.state; fallback for tests
        engine = getattr(request.app.state, "taxonomy_engine", None)
        if not engine:
            from app.services.embedding_service import EmbeddingService
            engine = TaxonomyEngine(embedding_service=EmbeddingService())
        result = await engine.match_prompt(body.prompt_text, db=db)

        if result is None or result.match_level == "none":
            return PatternMatchResponse()

        # Build match dict for backward compatibility
        match_dict: dict = {}
        if result.family:
            match_dict["family"] = {
                "id": result.family.id,
                "intent_label": result.family.intent_label,
                "domain": result.family.domain,
                "member_count": result.family.member_count,
            }
        match_dict["meta_patterns"] = [
            {"id": mp.id, "pattern_text": mp.pattern_text, "source_count": mp.source_count}
            for mp in result.meta_patterns
        ]
        match_dict["similarity"] = result.similarity

        # Taxonomy context — reuse the same engine instance
        taxonomy_node = result.taxonomy_node
        breadcrumb: list[str] = []
        if taxonomy_node:
            breadcrumb = await engine._build_breadcrumb(db, taxonomy_node)

        return PatternMatchResponse(
            match=match_dict,
            match_level=result.match_level,
            taxonomy_node_id=taxonomy_node.id if taxonomy_node else None,
            taxonomy_label=taxonomy_node.label if taxonomy_node else None,
            taxonomy_color=taxonomy_node.color_hex if taxonomy_node else None,
            taxonomy_breadcrumb=breadcrumb,
            similarity=result.similarity,
        )
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

    from sqlalchemy import func

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
    result = await db.execute(
        select(PatternFamily).where(PatternFamily.id == family_id)
    )
    family = result.scalar_one_or_none()
    if not family:
        raise HTTPException(404, "Pattern family not found")

    # Meta-patterns
    meta_result = await db.execute(
        select(MetaPattern)
        .where(MetaPattern.family_id == family_id)
        .order_by(MetaPattern.source_count.desc())
    )
    meta_patterns = meta_result.scalars().all()

    # Linked optimizations (most recent 20)
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
                "trace_id": o.trace_id,
                "raw_prompt": (o.raw_prompt or "")[:100],
                "intent_label": o.intent_label,
                "overall_score": o.overall_score,
                "strategy_used": o.strategy_used,
                "created_at": o.created_at.isoformat() if o.created_at else None,
            }
            for o in optimizations
        ],
    }


@router.patch("/families/{family_id}")
async def update_family(
    family_id: str,
    body: UpdateFamilyRequest,
    db: AsyncSession = Depends(get_db),
) -> UpdateFamilyResponse:
    """Update a pattern family (intent label and/or domain)."""
    if body.intent_label is None and body.domain is None:
        raise HTTPException(422, "At least one of 'intent_label' or 'domain' must be provided")

    result = await db.execute(
        select(PatternFamily).where(PatternFamily.id == family_id)
    )
    family = result.scalar_one_or_none()
    if not family:
        raise HTTPException(404, "Pattern family not found")

    if body.intent_label is not None:
        old_label = family.intent_label
        family.intent_label = body.intent_label
        logger.info(
            "Family renamed: id=%s '%s' → '%s'",
            family_id, old_label, body.intent_label,
        )

    if body.domain is not None:
        old_domain = family.domain
        family.domain = body.domain
        logger.info(
            "Family domain changed: id=%s '%s' → '%s'",
            family_id, old_domain, body.domain,
        )

    await db.commit()

    return UpdateFamilyResponse(id=family.id, intent_label=family.intent_label, domain=family.domain)


