"""Read-only settings endpoint."""

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from app.config import settings
from app.dependencies.rate_limit import RateLimit
from app.providers.capabilities import (
    effort_support,
    model_label,
    model_version,
    supports_thinking,
)

router = APIRouter(prefix="/api", tags=["settings"])


class ModelTierInfo(BaseModel):
    """Per-tier capability descriptor for the frontend model picker.

    Each entry surfaces the live model ID backing this tier (from
    ``config.py``), a human-readable label, and the effort levels / thinking
    support the model actually accepts. Enables the Navigator to filter
    effort options per phase's selected tier so users can't pick an effort
    that will 400 at request time (e.g. ``xhigh`` on Sonnet).
    """

    tier: str = Field(description='Tier name: "opus", "sonnet", or "haiku".')
    id: str = Field(description="Full model ID (e.g. 'claude-opus-4-7').")
    label: str = Field(description="Human-readable label (e.g. 'Opus 4.7').")
    version: str = Field(description="Version string (e.g. '4.7').")
    supported_efforts: list[str] = Field(
        description=(
            "Effort levels this model accepts. Empty means the effort "
            "parameter has no effect (Haiku)."
        ),
    )
    supports_thinking: bool = Field(
        description="Whether the model accepts the thinking parameter.",
    )


def _build_model_catalog() -> list[ModelTierInfo]:
    """Compose the tier catalog from the active config + capability helpers.

    A new entry here requires (a) adding the tier-name → settings-attr
    mapping and (b) wiring it through preferences. For now the catalog
    covers the three tiers the pipeline already exposes.
    """
    tiers = [
        ("opus", settings.MODEL_OPUS),
        ("sonnet", settings.MODEL_SONNET),
        ("haiku", settings.MODEL_HAIKU),
    ]
    return [
        ModelTierInfo(
            tier=tier,
            id=model_id,
            label=model_label(model_id),
            version=model_version(model_id),
            supported_efforts=effort_support(model_id),
            supports_thinking=supports_thinking(model_id),
        )
        for tier, model_id in tiers
    ]


class ServerSettings(BaseModel):
    max_raw_prompt_chars: int = Field(description="Maximum allowed raw prompt length in characters.")
    max_context_tokens: int = Field(description="Maximum token budget for assembled optimization context.")
    optimize_rate_limit: str = Field(description="Rate limit for POST /api/optimize (limits library format).")
    feedback_rate_limit: str = Field(description="Rate limit for POST /api/feedback.")
    refine_rate_limit: str = Field(description="Rate limit for POST /api/refine.")
    embedding_model: str = Field(description="Sentence-transformers model name for embeddings.")
    trace_retention_days: int = Field(description="Number of days JSONL trace files are retained.")
    database_engine: str = Field(description="Database engine (e.g. 'sqlite', 'postgresql').")
    model_catalog: list[ModelTierInfo] = Field(
        description=(
            "Per-tier model capability catalog. Drives the Navigator model "
            "picker labels and effort-level filtering."
        ),
    )


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
        model_catalog=_build_model_catalog(),
    )
