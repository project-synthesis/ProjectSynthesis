"""Strategy template CRUD — list, read, and update strategy .md files.

Fully adaptive: strategies are discovered from disk. Adding/removing
.md files in prompts/strategies/ is auto-detected.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.config import PROMPTS_DIR
from app.services.strategy_loader import (
    StrategyLoader,
    _parse_frontmatter,
    validate_frontmatter,
)

router = APIRouter(prefix="/api", tags=["strategies"])

_strategies_dir = PROMPTS_DIR / "strategies"
_strategies_dir_resolved = _strategies_dir.resolve()
_loader = StrategyLoader(_strategies_dir)


class StrategyDetail(BaseModel):
    name: str
    content: str


class StrategyUpdate(BaseModel):
    content: str = Field(..., min_length=10, description="Markdown content with frontmatter")


def _safe_strategy_path(name: str):
    """Resolve strategy path and guard against path traversal."""
    path = (_strategies_dir / f"{name}.md").resolve()
    if not path.is_relative_to(_strategies_dir_resolved):
        raise HTTPException(status_code=400, detail="Invalid strategy name")
    return path


@router.get("/strategies")
async def list_strategies() -> list[dict]:
    """List all available strategies with frontmatter metadata.

    Returns name, tagline, description, and validation warnings.
    Auto-discovers: adding/removing .md files changes this list.
    """
    return _loader.list_with_metadata()


@router.get("/strategies/{name}")
async def get_strategy(name: str) -> StrategyDetail:
    """Read the full content of a strategy .md file (including frontmatter)."""
    path = _safe_strategy_path(name)
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"Strategy '{name}' not found")
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise HTTPException(
            status_code=500, detail=f"Failed to read strategy file: {exc}",
        ) from exc
    return StrategyDetail(name=name, content=content)


@router.put("/strategies/{name}")
async def update_strategy(name: str, body: StrategyUpdate) -> dict:
    """Update a strategy .md file on disk.

    Validates frontmatter before saving. Returns the saved content
    plus any validation warnings. Hot-reloads on next pipeline call.
    """
    path = _safe_strategy_path(name)
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"Strategy '{name}' not found")

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
        raise HTTPException(
            status_code=500, detail="Failed to write strategy file: %s" % exc,
        ) from exc

    return {
        "name": name,
        "content": body.content,
        "warnings": fm_warnings,
    }
