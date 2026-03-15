"""Read-only settings endpoint."""

from fastapi import APIRouter
from app.config import settings

router = APIRouter(prefix="/api", tags=["settings"])


@router.get("/settings")
async def get_settings():
    return {
        "max_raw_prompt_chars": settings.MAX_RAW_PROMPT_CHARS,
        "max_context_tokens": settings.MAX_CONTEXT_TOKENS,
        "optimize_rate_limit": settings.OPTIMIZE_RATE_LIMIT,
        "feedback_rate_limit": settings.FEEDBACK_RATE_LIMIT,
        "embedding_model": settings.EMBEDDING_MODEL,
        "trace_retention_days": settings.TRACE_RETENTION_DAYS,
    }
