"""Strategy template CRUD — list, read, and update strategy .md files.

Fully adaptive: strategies are discovered from disk. Adding/removing
.md files in prompts/strategies/ is auto-detected.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.config import PROMPTS_DIR, settings
from app.dependencies.rate_limit import RateLimit
from app.services.strategy_loader import (
    StrategyLoader,
    _parse_frontmatter,
    validate_frontmatter,
)

logger = logging.getLogger(__name__)

_MAX_STRATEGY_SIZE = 50_000

router = APIRouter(prefix="/api", tags=["strategies"])

_strategies_dir = PROMPTS_DIR / "strategies"
_strategies_dir_resolved = _strategies_dir.resolve()
_loader = StrategyLoader(_strategies_dir)


class StrategyDetail(BaseModel):
    name: str = Field(description="Strategy file name (without .md extension).")
    content: str = Field(description="Full Markdown content including YAML frontmatter.")


class StrategyMetadata(BaseModel):
    name: str = Field(description="Strategy file name (without .md extension).")
    tagline: str | None = Field(default=None, description="Short tagline from YAML frontmatter.")
    description: str | None = Field(default=None, description="One-sentence description from YAML frontmatter.")
    warnings: list[str] = Field(default_factory=list, description="Validation warnings for missing frontmatter fields.")


class StrategyUpdateResponse(BaseModel):
    name: str = Field(description="Strategy file name that was updated.")
    content: str = Field(description="Saved Markdown content including YAML frontmatter.")
    warnings: list[str] = Field(default_factory=list, description="Validation warnings for frontmatter issues.")


class StrategyUpdate(BaseModel):
    content: str = Field(..., min_length=10, description="Markdown content with frontmatter")


def _safe_strategy_path(name: str):
    """Resolve strategy path and guard against path traversal."""
    path = (_strategies_dir / f"{name}.md").resolve()
    if not path.is_relative_to(_strategies_dir_resolved):
        raise HTTPException(status_code=400, detail="Invalid strategy name")
    return path


@router.get("/strategies")
async def list_strategies(
    request: Request,
    _rate: None = Depends(RateLimit(lambda: settings.DEFAULT_RATE_LIMIT)),
) -> list[StrategyMetadata]:
    """List all available strategies with frontmatter metadata.

    Returns name, tagline, description, and validation warnings.
    Auto-discovers: adding/removing .md files changes this list.
    """
    raw = _loader.list_with_metadata()
    return [StrategyMetadata(**item) for item in raw]


@router.get("/strategies/{name}")
async def get_strategy(name: str) -> StrategyDetail:
    """Read the full content of a strategy .md file (including frontmatter)."""
    path = _safe_strategy_path(name)
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"Strategy '{name}' not found")
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        logger.error("Failed to read strategy file '%s': %s", name, exc)
        raise HTTPException(
            status_code=500, detail="Failed to read strategy file.",
        ) from exc
    return StrategyDetail(name=name, content=content)


@router.put("/strategies/{name}")
async def update_strategy(name: str, body: StrategyUpdate, request: Request) -> StrategyUpdateResponse:
    """Update a strategy .md file on disk.

    Validates frontmatter before saving. Returns the saved content
    plus any validation warnings. Hot-reloads on next pipeline call.
    """
    path = _safe_strategy_path(name)
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"Strategy '{name}' not found")

    if len(body.content) > _MAX_STRATEGY_SIZE:
        raise HTTPException(status_code=413, detail="Strategy file exceeds 50KB limit.")

    # Validate frontmatter before writing
    meta, content_body = _parse_frontmatter(body.content)
    fm_warnings = validate_frontmatter(meta, filename=name)

    # Block save if frontmatter is completely missing (no --- delimiters)
    if not body.content.strip().startswith("---"):
        raise HTTPException(
            status_code=422,
            detail=(
                "Strategy files require YAML frontmatter. "
                "Expected format:\n---\ntagline: short-tag\n"
                "description: One-sentence description.\n---\n\n# Strategy content..."
            ),
        )

    # Block save if body is empty after frontmatter
    if not content_body.strip():
        raise HTTPException(
            status_code=422,
            detail="Strategy file has frontmatter but no body content.",
        )

    try:
        path.write_text(body.content, encoding="utf-8")
    except OSError as exc:
        logger.error("Failed to write strategy file '%s': %s", name, exc)
        raise HTTPException(
            status_code=500, detail="Failed to save strategy.",
        ) from exc

    # Audit log
    try:
        from app.database import async_session_factory
        from app.services.audit_logger import log_event

        async with async_session_factory() as audit_db:
            await log_event(
                db=audit_db,
                action="strategy_updated",
                actor_ip=request.client.host if request.client else None,
                detail={"strategy_name": name},
                outcome="success",
            )
    except Exception:
        logger.debug("Audit log write failed", exc_info=True)

    return StrategyUpdateResponse(
        name=name,
        content=body.content,
        warnings=fm_warnings,
    )
