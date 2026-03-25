"""Read-only settings endpoint."""

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from app.config import settings
from app.dependencies.rate_limit import RateLimit

router = APIRouter(prefix="/api", tags=["settings"])


class ServerSettings(BaseModel):
    max_raw_prompt_chars: int = Field(description="Maximum allowed raw prompt length in characters.")
    max_context_tokens: int = Field(description="Maximum token budget for assembled optimization context.")
    optimize_rate_limit: str = Field(description="Rate limit for POST /api/optimize (limits library format).")
    feedback_rate_limit: str = Field(description="Rate limit for POST /api/feedback.")
    refine_rate_limit: str = Field(description="Rate limit for POST /api/refine.")
    embedding_model: str = Field(description="Sentence-transformers model name for embeddings.")
    trace_retention_days: int = Field(description="Number of days JSONL trace files are retained.")
    database_engine: str = Field(description="Database engine (e.g. 'sqlite', 'postgresql').")


@router.get("/settings")
async def get_settings(
    request: Request,
    _rate: None = Depends(RateLimit(lambda: settings.DEFAULT_RATE_LIMIT)),
) -> ServerSettings:
    return ServerSettings(
        max_raw_prompt_chars=settings.MAX_RAW_PROMPT_CHARS,
        max_context_tokens=settings.MAX_CONTEXT_TOKENS,
        optimize_rate_limit=settings.OPTIMIZE_RATE_LIMIT,
        feedback_rate_limit=settings.FEEDBACK_RATE_LIMIT,
        refine_rate_limit=settings.REFINE_RATE_LIMIT,
        embedding_model=settings.EMBEDDING_MODEL,
        trace_retention_days=settings.TRACE_RETENTION_DAYS,
        database_engine=settings.DATABASE_URL.split(":")[0].split("+")[0],
    )
