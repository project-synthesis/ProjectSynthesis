"""Strategy file discovery and loading.

Strategy files are static Markdown in prompts/strategies/.
Their full text is injected as {{strategy_instructions}} in optimize.md / refine.md.
"""

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class StrategyLoader:
    """Discovers and loads strategy files from the strategies directory."""

    def __init__(self, strategies_dir: Path) -> None:
        self.strategies_dir = strategies_dir

    def list_strategies(self) -> list[str]:
        """Return sorted list of available strategy names (without .md extension)."""
        if not self.strategies_dir.exists():
            return []
        return sorted(p.stem for p in self.strategies_dir.glob("*.md"))

    def load(self, name: str) -> str:
        """Load a strategy file by name (without .md extension)."""
        path = self.strategies_dir / f"{name}.md"
        if not path.exists():
            available = self.list_strategies()
            raise FileNotFoundError(
                "Strategy '%s' not found at %s. Available strategies: %s"
                % (name, path, ", ".join(available) if available else "none")
            )
        content = path.read_text()
        logger.debug("Loaded strategy %s (%d chars)", name, len(content))
        return content

    def format_available(self) -> str:
        """Format available strategies as a bullet list for the analyzer prompt."""
        strategies = self.list_strategies()
        if not strategies:
            return "No strategies available."
        return "\n".join(f"- {s}" for s in strategies)

    def validate(self) -> None:
        """Verify strategies directory is non-empty. Raises RuntimeError if empty."""
        strategies = self.list_strategies()
        if not strategies:
            raise RuntimeError(
                f"No strategy files found in {self.strategies_dir}. At least one .md file is required."
            )
        logger.info("Strategy validation passed: %d strategies available", len(strategies))
