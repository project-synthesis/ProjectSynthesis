"""Compare and merge endpoints for cross-optimization analysis."""

import json
import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from app.database import async_session
from app.dependencies.auth import get_current_user
from app.dependencies.rate_limit import RateLimit
from app.errors import bad_request, not_found
from app.models.optimization import Optimization
from app.schemas.auth import AuthenticatedUser
from app.schemas.compare_models import CompareResponse, MergeAcceptRequest, MergeAcceptResponse
from app.services.cache_service import get_cache
from app.services.compare_service import compute_comparison
from app.services.merge_service import stream_merge
from app.services.settings_service import load_settings

logger = logging.getLogger(__name__)
router = APIRouter(tags=["compare"])


async def _fetch_user_optimization(opt_id: str, user_id: str) -> Optimization:
    """Fetch optimization scoped to user. Does NOT filter deleted_at so trashed items are allowed.
    Returns 404 for missing or wrong-user (information hiding)."""
    async with async_session() as session:
        query = select(Optimization).where(
            Optimization.id == opt_id,
            Optimization.user_id == user_id,
        )
        result = await session.execute(query)
        opt = result.scalar_one_or_none()
        if not opt:
            raise not_found("Optimization not found")
        return opt


def _cache_key(id_a: str, id_b: str) -> str:
    return f"compare:{':'.join(sorted([id_a, id_b]))}"


@router.get("/api/compare")
async def compare_optimizations(
    request: Request,
    a: str = Query(..., description="First optimization ID"),
    b: str = Query(..., description="Second optimization ID"),
    current_user: AuthenticatedUser = Depends(get_current_user),
    _rl: None = Depends(RateLimit(lambda: "10/minute")),
) -> CompareResponse:
    if a == b:
        raise HTTPException(status_code=422, detail="Cannot compare an optimization with itself")
    opt_a = await _fetch_user_optimization(a, current_user.id)
    opt_b = await _fetch_user_optimization(b, current_user.id)
    if not opt_a.optimized_prompt or not opt_b.optimized_prompt:
        raise HTTPException(status_code=422, detail="Cannot compare incomplete optimizations — both must have an optimized prompt")
    if opt_a.overall_score is None or opt_b.overall_score is None:
        raise HTTPException(status_code=422, detail="Cannot compare unscored optimizations — both must have validation scores")

    cache = get_cache()
    key = _cache_key(a, b)
    cached = await cache.get(key)
    if cached:
        return CompareResponse(**cached)

    provider = request.app.state.provider
    result = await compute_comparison(opt_a, opt_b, provider)
    await cache.set(key, result.model_dump(), ttl_seconds=300)
    return result


@router.post("/api/compare/merge")
async def merge_optimizations(
    request: Request,
    body: dict,
    current_user: AuthenticatedUser = Depends(get_current_user),
    _rl: None = Depends(RateLimit(lambda: "5/minute")),
):
    """Stream merged prompt via SSE."""
    id_a = body.get("optimization_id_a")
    id_b = body.get("optimization_id_b")
    if not id_a or not id_b:
        raise bad_request("optimization_id_a and optimization_id_b required")

    cache = get_cache()
    key = _cache_key(id_a, id_b)
    cached = await cache.get(key)
    if cached:
        compare = CompareResponse(**cached)
    else:
        opt_a = await _fetch_user_optimization(id_a, current_user.id)
        opt_b = await _fetch_user_optimization(id_b, current_user.id)
        provider = request.app.state.provider
        compare = await compute_comparison(opt_a, opt_b, provider)

    settings = load_settings()
    model = settings.get("default_model", "auto")
    provider = request.app.state.provider

    async def event_stream():
        try:
            async for chunk in stream_merge(provider, compare, model):
                yield f"data: {json.dumps({'type': 'chunk', 'text': chunk})}\n\n"
            yield f"data: {json.dumps({'type': 'complete'})}\n\n"
        except Exception as e:
            logger.error("Merge stream error: %s", e, exc_info=True)
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.post("/api/compare/merge/accept")
async def accept_merge(
    request: Request,
    body: MergeAcceptRequest,
    current_user: AuthenticatedUser = Depends(get_current_user),
    _rl: None = Depends(RateLimit(lambda: "10/minute")),
) -> MergeAcceptResponse:
    # Validate parents exist and belong to user
    await _fetch_user_optimization(body.optimization_id_a, current_user.id)
    await _fetch_user_optimization(body.optimization_id_b, current_user.id)

    new_id = str(uuid.uuid4())
    async with async_session() as session:
        async with session.begin():
            merged = Optimization(
                id=new_id,
                raw_prompt=body.merged_prompt,
                status="merged",
                user_id=current_user.id,
                merge_parents=json.dumps([body.optimization_id_a, body.optimization_id_b]),
                created_at=datetime.now(timezone.utc),
            )
            session.add(merged)

            # Soft-delete parents in separate try so merge record survives
            try:
                now = datetime.now(timezone.utc)
                for pid in (body.optimization_id_a, body.optimization_id_b):
                    parent = await session.get(Optimization, pid)
                    if parent:
                        parent.deleted_at = now
            except Exception as e:
                logger.error("Failed to soft-delete merge parents: %s", e)

    # Invalidate compare cache
    cache = get_cache()
    await cache.delete(_cache_key(body.optimization_id_a, body.optimization_id_b))

    return MergeAcceptResponse(optimization_id=new_id, status="merged")
