"""Scan workspace roots for agent guidance files."""

import logging
from pathlib import Path

from app.config import settings

logger = logging.getLogger(__name__)

GUIDANCE_FILES = [
    "CLAUDE.md",
    "AGENTS.md",
    ".cursorrules",
    ".github/copilot-instructions.md",
    ".windsurfrules",
]

MAX_LINES_PER_FILE = 500
MAX_CHARS_PER_FILE = 10_000


class RootsScanner:
    def __init__(self, max_total_chars: int | None = None) -> None:
        self._max_total = max_total_chars or settings.MAX_GUIDANCE_CHARS

    def discover(self, root: Path) -> list[Path]:
        """Return ordered list of guidance files that exist under *root*."""
        found = []
        for rel_path in GUIDANCE_FILES:
            path = root / rel_path
            if path.is_file():
                found.append(path)
        return found

    def scan(self, root: Path) -> str | None:
        """Scan *root* and return wrapped guidance content, or None if empty."""
        if not root.exists() or not root.is_dir():
            return None

        files = self.discover(root)
        if not files:
            return None

        sections: list[str] = []
        total_chars = 0

        for path in files:
            try:
                content = path.read_text(errors="replace")
            except OSError:
                logger.warning("Failed to read guidance file: %s", path)
                continue

            # Per-file line cap
            lines = content.split("\n")
            if len(lines) > MAX_LINES_PER_FILE:
                content = "\n".join(lines[:MAX_LINES_PER_FILE])

            # Per-file char cap
            if len(content) > MAX_CHARS_PER_FILE:
                content = content[:MAX_CHARS_PER_FILE]

            # Total budget
            if total_chars + len(content) > self._max_total:
                remaining = self._max_total - total_chars
                if remaining <= 0:
                    break
                content = content[:remaining]

            # Determine source label
            if path.parent.name == ".github":
                name = f".github/{path.name}"
            else:
                name = path.name

            section = (
                f'<untrusted-context source="{name}">\n'
                f"{content}\n"
                f"</untrusted-context>"
            )
            sections.append(section)
            total_chars += len(content)

        return "\n\n".join(sections) if sections else None

    def scan_roots(self, roots: list[Path]) -> str | None:
        """Scan multiple workspace roots and concatenate all guidance."""
        all_sections: list[str] = []
        for root in roots:
            result = self.scan(root)
            if result:
                all_sections.append(result)
        return "\n\n".join(all_sections) if all_sections else None
