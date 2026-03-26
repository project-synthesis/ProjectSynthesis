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
        profile = self._build_profile(stack, guidance, roots=roots)

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

    def _scan_readme(self, roots: list[Path]) -> str | None:
        """Extract the first 80 lines of README.md for project intent/overview."""
        for root in roots:
            for name in ("README.md", "readme.md", "Readme.md"):
                readme = root / name
                if readme.is_file():
                    try:
                        text = readme.read_text(errors="replace")
                        lines = text.split("\n")[:80]
                        content = "\n".join(lines).strip()
                        if content:
                            logger.debug("README.md found: %d lines from %s", len(lines), readme)
                            return content
                    except Exception:
                        logger.debug("Failed to read %s", readme, exc_info=True)
        return None

    def _scan_entry_points(self, roots: list[Path]) -> str | None:
        """Extract first 40 lines of key entry point files for architecture signals."""
        entry_points = [
            "backend/app/main.py", "app/main.py", "main.py", "src/main.py",
            "src/index.ts", "src/app.ts", "src/index.js", "src/app.js",
            "frontend/src/routes/+layout.svelte", "src/routes/+layout.svelte",
            "manage.py", "wsgi.py", "asgi.py",
        ]
        found = []
        for root in roots:
            for ep in entry_points:
                path = root / ep
                if path.is_file():
                    try:
                        text = path.read_text(errors="replace")
                        lines = text.split("\n")[:40]
                        preview = "\n".join(lines).strip()
                        if preview:
                            rel = str(path.relative_to(root))
                            found.append(f"## {rel}\n{preview}")
                            logger.debug("Entry point: %s (%d lines)", rel, len(lines))
                    except Exception:
                        pass
                if len(found) >= 3:  # Cap at 3 entry points
                    break
        return "\n\n".join(found) if found else None

    def _scan_architecture_docs(self, roots: list[Path]) -> str | None:
        """Scan docs/ or architecture/ for design documents (first 60 lines each)."""
        doc_dirs = ["docs", "doc", "architecture", "design"]
        found = []
        for root in roots:
            for doc_dir in doc_dirs:
                d = root / doc_dir
                if d.is_dir():
                    for md in sorted(d.glob("*.md"))[:3]:  # Top 3 docs
                        try:
                            text = md.read_text(errors="replace")
                            lines = text.split("\n")[:60]
                            preview = "\n".join(lines).strip()
                            if preview:
                                rel = str(md.relative_to(root))
                                found.append(f"## {rel}\n{preview}")
                        except Exception:
                            pass
        return "\n\n".join(found) if found else None

    def _build_profile(self, stack: dict, guidance: str | None, roots: list[Path] | None = None) -> str | None:
        """Format stack detection + guidance + deep context into a <workspace-profile> block."""
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

        # Deep context: README, entry points, architecture docs
        # These provide structural understanding beyond manifest metadata.
        if roots:
            readme = self._scan_readme(roots)
            if readme:
                parts.append("")
                parts.append("<project-readme>")
                parts.append(readme)
                parts.append("</project-readme>")

            entry_points = self._scan_entry_points(roots)
            if entry_points:
                parts.append("")
                parts.append("<entry-points>")
                parts.append(entry_points)
                parts.append("</entry-points>")

            arch_docs = self._scan_architecture_docs(roots)
            if arch_docs:
                parts.append("")
                parts.append("<architecture-docs>")
                parts.append(arch_docs)
                parts.append("</architecture-docs>")

        parts.append("</workspace-profile>")
        return "\n".join(parts)
