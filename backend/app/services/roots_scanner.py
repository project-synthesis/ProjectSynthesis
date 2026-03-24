"""Scan workspace roots for agent guidance files."""

import hashlib
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
    "GEMINI.md",
    ".clinerules",
    "CONVENTIONS.md",
]

MAX_LINES_PER_FILE = 500
MAX_CHARS_PER_FILE = 10_000

_SKIP_DIRS = {
    "node_modules", ".venv", "__pycache__", ".git", "dist", "build",
    ".next", ".svelte-kit", "target", "vendor", ".tox", "eggs",
    ".mypy_cache", ".ruff_cache", ".pytest_cache", "coverage",
}

MANIFEST_FILES = [
    "package.json",
    "pyproject.toml",
    "requirements.txt",
    "Cargo.toml",
    "go.mod",
]


def discover_project_dirs(root: Path) -> list[Path]:
    """Detect immediate subdirectories containing manifest files."""
    project_dirs: list[Path] = []
    if not root.is_dir():
        return project_dirs
    try:
        children = sorted(root.iterdir())
    except OSError:
        return project_dirs
    for child in children:
        if not child.is_dir():
            continue
        if child.name.startswith(".") or child.name in _SKIP_DIRS:
            continue
        for manifest in MANIFEST_FILES:
            if (child / manifest).is_file():
                project_dirs.append(child)
                break
    return project_dirs


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

    def _collect_file_candidates(
        self, directory: Path, prefix: str | None
    ) -> list[tuple[str, str]]:
        """Return (label, content) pairs for guidance files in directory."""
        candidates: list[tuple[str, str]] = []
        files = self.discover(directory)
        for path in files:
            try:
                content = path.read_text(errors="replace")
            except OSError:
                logger.warning("Failed to read guidance file: %s", path)
                continue
            name = str(path.relative_to(directory))
            label = f"{prefix}/{name}" if prefix else name
            candidates.append((label, content))
        return candidates

    def scan(self, root: Path) -> str | None:
        """Scan *root* and return wrapped guidance content, or None if empty."""
        if not root.exists() or not root.is_dir():
            logger.debug("Root path does not exist or is not a directory: %s", root)
            return None

        # Collect from root (prefix=None → root-level labels)
        all_candidates = self._collect_file_candidates(root, prefix=None)

        # Collect from manifest-detected subdirectories
        for subdir in discover_project_dirs(root):
            prefix = subdir.name
            all_candidates.extend(self._collect_file_candidates(subdir, prefix=prefix))

        if not all_candidates:
            logger.debug("No guidance files found under %s", root)
            return None

        logger.info(
            "Discovered %d guidance file candidate(s) under %s",
            len(all_candidates), root,
        )

        # Deduplicate by content hash (first occurrence wins = root wins)
        seen_hashes: set[str] = set()
        sections: list[str] = []
        total_chars = 0

        for label, content in all_candidates:
            content_hash = hashlib.sha256(content.encode()).hexdigest()
            if content_hash in seen_hashes:
                logger.debug("Skipping duplicate guidance content: %s", label)
                continue
            seen_hashes.add(content_hash)

            # Per-file line cap
            lines = content.split("\n")
            if len(lines) > MAX_LINES_PER_FILE:
                logger.debug(
                    "Truncating %s from %d to %d lines", label, len(lines), MAX_LINES_PER_FILE
                )
                content = "\n".join(lines[:MAX_LINES_PER_FILE])

            # Per-file char cap
            if len(content) > MAX_CHARS_PER_FILE:
                logger.debug(
                    "Truncating %s from %d to %d chars", label, len(content), MAX_CHARS_PER_FILE
                )
                content = content[:MAX_CHARS_PER_FILE]

            # Total budget
            if total_chars + len(content) > self._max_total:
                remaining = self._max_total - total_chars
                if remaining <= 0:
                    logger.warning(
                        "Total guidance budget exhausted (%d chars). Skipping remaining files.",
                        self._max_total,
                    )
                    break
                logger.debug(
                    "Truncating %s to fit total budget (%d remaining chars)", label, remaining
                )
                content = content[:remaining]

            section = (
                f'<untrusted-context source="{label}">\n'
                f"{content}\n"
                f"</untrusted-context>"
            )
            sections.append(section)
            total_chars += len(content)

        if sections:
            logger.info(
                "Roots scanner produced %d section(s), %d total chars from %s",
                len(sections), total_chars, root,
            )
        return "\n\n".join(sections) if sections else None

    def scan_roots(self, roots: list[Path]) -> str | None:
        """Scan multiple workspace roots and concatenate all guidance."""
        all_sections: list[str] = []
        for root in roots:
            result = self.scan(root)
            if result:
                all_sections.append(result)
        return "\n\n".join(all_sections) if all_sections else None
