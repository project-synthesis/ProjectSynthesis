"""Read-only settings endpoint."""

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.config import settings

router = APIRouter(prefix="/api", tags=["settings"])


class ServerSettings(BaseModel):
    max_raw_prompt_chars: int = Field(description="Maximum allowed raw prompt length in characters.")
    max_context_tokens: int = Field(description="Maximum token budget for assembled optimization context.")
    optimize_rate_limit: str = Field(description="Rate limit for POST /api/optimize (limits library format).")
    feedback_rate_limit: str = Field(description="Rate limit for POST /api/feedback.")
    embedding_model: str = Field(description="Sentence-transformers model name for embeddings.")
    trace_retention_days: int = Field(description="Number of days JSONL trace files are retained.")


@router.get("/settings")
async def get_settings() -> ServerSettings:
    return ServerSettings(
        max_raw_prompt_chars=settings.MAX_RAW_PROMPT_CHARS,
        max_context_tokens=settings.MAX_CONTEXT_TOKENS,
        optimize_rate_limit=settings.OPTIMIZE_RATE_LIMIT,
        feedback_rate_limit=settings.FEEDBACK_RATE_LIMIT,
        embedding_model=settings.EMBEDDING_MODEL,
        trace_retention_days=settings.TRACE_RETENTION_DAYS,
    )
