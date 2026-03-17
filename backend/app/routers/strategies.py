"""Strategy template CRUD — list, read, and update strategy .md files.

Fully adaptive: strategies are discovered from disk. Adding/removing
.md files in prompts/strategies/ is auto-detected.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.config import PROMPTS_DIR
from app.services.strategy_loader import StrategyLoader

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

    Returns name, tagline, and description extracted from YAML frontmatter.
    Auto-discovers: adding/removing .md files changes this list.
    """
    return _loader.list_with_metadata()


@router.get("/strategies/{name}")
async def get_strategy(name: str) -> StrategyDetail:
    """Read the full content of a strategy .md file (including frontmatter)."""
    path = _safe_strategy_path(name)
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"Strategy '{name}' not found")
    content = path.read_text(encoding="utf-8")
    return StrategyDetail(name=name, content=content)


@router.put("/strategies/{name}")
async def update_strategy(name: str, body: StrategyUpdate) -> StrategyDetail:
    """Update a strategy .md file on disk. Hot-reloads on next pipeline call."""
    path = _safe_strategy_path(name)
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"Strategy '{name}' not found")
    try:
        path.write_text(body.content, encoding="utf-8")
    except OSError as exc:
        raise HTTPException(
            status_code=500, detail="Failed to write strategy file: %s" % exc,
        ) from exc
    return StrategyDetail(name=name, content=body.content)
