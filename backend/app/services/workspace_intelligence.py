"""Zero-config workspace analysis — detects project type, tech stack, and guidance files.

Copyright 2025-2026 Project Synthesis contributors.
"""

import json
import logging
import time
from pathlib import Path

from app.services.roots_scanner import RootsScanner, discover_project_dirs

logger = logging.getLogger(__name__)

# Manifest -> framework detection rules
_PYTHON_PACKAGES = {
    "fastapi": "FastAPI",
    "django": "Django",
    "flask": "Flask",
    "sqlalchemy": "SQLAlchemy",
    "aiosqlite": "aiosqlite",
    "pytest": "pytest",
    "anthropic": "Anthropic SDK",
}
_NODE_PACKAGES = {
    "svelte": "Svelte",
    "@sveltejs/kit": "SvelteKit",
    "react": "React",
    "next": "Next.js",
    "vue": "Vue",
    "nuxt": "Nuxt",
    "tailwindcss": "Tailwind CSS",
    "express": "Express",
    "fastify": "Fastify",
    "angular": "Angular",
}


_WORKSPACE_CACHE_TTL = 300  # 5 minutes — _detect_stack is cheap (manifest reads only)


class WorkspaceIntelligence:
    """Scan workspace roots to build a compact project profile."""

    def __init__(self) -> None:
        self._cache: dict[frozenset[str], tuple[str, float]] = {}
        self._scanner = RootsScanner()

    def analyze(self, roots: list[Path]) -> str | None:
        """Analyze workspace roots and return a formatted profile, or None."""
        if not roots:
            return None
        cache_key = frozenset(str(r) for r in roots)
        cached = self._cache.get(cache_key)
        if cached is not None:
            profile, cached_at = cached
            if time.monotonic() - cached_at < _WORKSPACE_CACHE_TTL:
                logger.debug("Workspace profile cache hit for %d roots", len(roots))
                return profile
            logger.debug("Workspace profile cache expired for %d roots", len(roots))

        logger.debug("Analyzing %d workspace roots: %s", len(roots), [str(r) for r in roots])
        guidance = self._scanner.scan_roots(roots)
        stack = self._detect_stack(roots)
        profile = self._build_profile(stack, guidance)

        if profile:
            self._cache[cache_key] = (profile, time.monotonic())
            logger.info(
                "Workspace profile built: %d chars, languages=%s, frameworks=%s",
                len(profile), stack["languages"], stack["frameworks"],
            )
        else:
            logger.debug("No workspace profile generated — no languages, frameworks, or guidance found")
        return profile

    def invalidate(self) -> None:
        """Clear the profile cache."""
        count = len(self._cache)
        self._cache.clear()
        logger.debug("Workspace profile cache invalidated (%d entries cleared)", count)

    def _detect_stack(self, roots: list[Path]) -> dict:
        """Scan manifest files across all roots and their project subdirectories."""
        languages: set[str] = set()
        frameworks: set[str] = set()
        tools: set[str] = set()

        # Build expanded root list: original roots + manifest-detected subdirs
        all_dirs: list[Path] = []
        for root in roots:
            all_dirs.append(root)
            all_dirs.extend(discover_project_dirs(root))

        for root in all_dirs:
            if not root.is_dir():
                continue

            # Python — requirements.txt
            req = root / "requirements.txt"
            if req.is_file():
                languages.add("Python")
                try:
                    content = req.read_text()
                    for pkg, label in _PYTHON_PACKAGES.items():
                        if pkg in content.lower():
                            frameworks.add(label)
                except OSError:
                    pass

            # Python — pyproject.toml
            pyproject = root / "pyproject.toml"
            if pyproject.is_file():
                languages.add("Python")
                try:
                    content = pyproject.read_text()
                    if "py3" in content or "python" in content.lower():
                        for line in content.split("\n"):
                            if "target-version" in line:
                                tools.add(
                                    f"Ruff ({line.split('=')[-1].strip().strip('\"')})"
                                )
                except OSError:
                    pass

            # Node.js — package.json
            pkg_json = root / "package.json"
            if pkg_json.is_file():
                languages.add("JavaScript/TypeScript")
                try:
                    data = json.loads(pkg_json.read_text())
                    all_deps = {
                        **data.get("dependencies", {}),
                        **data.get("devDependencies", {}),
                    }
                    for pkg, label in _NODE_PACKAGES.items():
                        if pkg in all_deps:
                            frameworks.add(label)
                except (OSError, json.JSONDecodeError):
                    pass

            # TypeScript
            if (root / "tsconfig.json").is_file():
                languages.add("TypeScript")

            # Rust
            if (root / "Cargo.toml").is_file():
                languages.add("Rust")

            # Go
            if (root / "go.mod").is_file():
                languages.add("Go")

            # Docker
            if (root / "docker-compose.yml").is_file() or (
                root / "Dockerfile"
            ).is_file():
                tools.add("Docker")

        return {
            "languages": sorted(languages),
            "frameworks": sorted(frameworks),
            "tools": sorted(tools),
        }

    def _build_profile(self, stack: dict, guidance: str | None) -> str | None:
        """Format stack detection + guidance into a <workspace-profile> block."""
        if not stack["languages"] and not stack["frameworks"] and not guidance:
            return None

        parts = ["<workspace-profile>"]

        if stack["languages"]:
            parts.append(f"Languages: {', '.join(stack['languages'])}")
        if stack["frameworks"]:
            parts.append(f"Frameworks: {', '.join(stack['frameworks'])}")
        if stack["tools"]:
            parts.append(f"Tools: {', '.join(stack['tools'])}")

        if guidance:
            parts.append("")
            parts.append("<guidance-files>")
            parts.append(guidance)
            parts.append("</guidance-files>")

        parts.append("</workspace-profile>")
        return "\n".join(parts)
