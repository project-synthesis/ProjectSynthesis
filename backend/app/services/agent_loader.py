"""AgentLoader — seed agent file parsing and registry.

Loads agent definitions from prompts/seed-agents/*.md files with
YAML frontmatter. Follows the same pattern as StrategyLoader.
Hot-reloaded via file watcher — reads from disk on each call.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_MAX_FILE_SIZE = 50_000


@dataclass
class SeedAgent:
    """Parsed seed agent definition.

    ``model`` lets an individual agent opt into a specific tier
    (``sonnet|opus|haiku``). ``None`` means "use the orchestrator default"
    (currently Haiku). Resolved by ``seed_orchestrator._resolve_agent_model``.
    """
    name: str
    description: str
    task_types: list[str]
    phase_context: list[str]
    body: str
    prompts_per_run: int = 6
    enabled: bool = True
    model: str | None = None


def _parse_frontmatter(content: str) -> tuple[dict[str, str], str]:
    """Parse YAML-like frontmatter from markdown content."""
    match = _FRONTMATTER_RE.match(content)
    if not match:
        return {}, content
    raw_meta = match.group(1)
    body = content[match.end():]
    meta: dict[str, str] = {}
    for line in raw_meta.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, _, value = line.partition(":")
        if not value:
            continue
        meta[key.strip().lower()] = value.strip()
    return meta, body.strip()


def _parse_list_field(value: str) -> list[str]:
    """Parse a comma-separated frontmatter field into a list."""
    return [v.strip() for v in value.split(",") if v.strip()]


class AgentLoader:
    """Load seed agent definitions from .md files with frontmatter."""

    def __init__(self, agents_dir: Path) -> None:
        self.agents_dir = agents_dir

    def list_agents(self) -> list[str]:
        """Return sorted list of agent names (file stems)."""
        if not self.agents_dir.exists():
            return []
        return sorted(p.stem for p in self.agents_dir.glob("*.md") if p.is_file())

    def load(self, name: str) -> SeedAgent | None:
        """Load a single agent by name. Returns None if not found."""
        path = self.agents_dir / f"{name}.md"
        if not path.exists() or not path.is_file():
            return None
        try:
            content = path.read_text(encoding="utf-8")
            if len(content) > _MAX_FILE_SIZE:
                logger.warning("Agent file too large: %s (%d bytes)", name, len(content))
                return None
            meta, body = _parse_frontmatter(content)
            raw_model = meta.get("model", "").strip().lower()
            model_override = raw_model if raw_model in {"sonnet", "opus", "haiku"} else None
            return SeedAgent(
                name=meta.get("name", name),
                description=meta.get("description", ""),
                task_types=_parse_list_field(meta.get("task_types", "general")),
                phase_context=_parse_list_field(meta.get("phase_context", "build")),
                body=body,
                prompts_per_run=int(meta.get("prompts_per_run", "6")),
                enabled=meta.get("enabled", "true").lower() != "false",
                model=model_override,
            )
        except Exception as exc:
            logger.warning("Failed to load agent '%s': %s", name, exc)
            return None

    def list_enabled(self) -> list[SeedAgent]:
        """Return all enabled agent definitions."""
        agents = []
        for name in self.list_agents():
            agent = self.load(name)
            if agent and agent.enabled:
                agents.append(agent)
        return agents

    def validate(self) -> None:
        """Startup validation. Logs warnings, never raises."""
        agents = self.list_agents()
        if not agents:
            logger.warning("No seed agents found in %s", self.agents_dir)
            return
        for name in agents:
            agent = self.load(name)
            if agent is None:
                logger.warning("Seed agent '%s' failed to load", name)
            elif not agent.description:
                logger.warning("Seed agent '%s' has no description", name)
        logger.info("Loaded %d seed agents from %s", len(agents), self.agents_dir)
