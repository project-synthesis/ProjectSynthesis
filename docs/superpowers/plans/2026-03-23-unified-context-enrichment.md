# Unified Context Enrichment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Unify context resolution across all routing tiers so passthrough gets analysis, codebase context, applied patterns, and task-specific adaptation — all without LLM calls.

**Architecture:** A single `ContextEnrichmentService` replaces 5 scattered context resolution sites. Passthrough enrichment uses a zero-LLM `HeuristicAnalyzer` for classification, augmented `RepoIndexService` for codebase context, and the existing taxonomy engine for pattern injection. Enhanced `RootsScanner` adds manifest-aware subdirectory discovery.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy async, numpy, sentence-transformers (all-MiniLM-L6-v2), pytest

**Spec:** `docs/superpowers/specs/2026-03-23-unified-context-enrichment-design.md`

**Test command:** `cd backend && source .venv/bin/activate && pytest tests/ -v`

**Single test:** `cd backend && source .venv/bin/activate && pytest tests/<file>::<class>::<test> -v`

---

## File Map

### New Files
| File | Responsibility |
|---|---|
| `backend/app/services/heuristic_analyzer.py` | Zero-LLM task classification, weakness detection, strategy recommendation |
| `backend/app/services/context_enrichment.py` | Unified enrichment orchestrator — single entry point for all tiers |
| `backend/tests/test_heuristic_analyzer.py` | Unit tests for heuristic classification + weakness detection |
| `backend/tests/test_context_enrichment.py` | Integration tests for enrichment dispatch per tier |

### Modified Files
| File | Change |
|---|---|
| `backend/app/services/roots_scanner.py` | `discover_project_dirs()`, expanded guidance list, content dedup |
| `backend/app/services/workspace_intelligence.py` | Use `discover_project_dirs()` from scanner |
| `backend/app/services/repo_index_service.py` | Structured outlines, composite embeddings, `query_curated_context()` |
| `backend/app/services/passthrough.py` | Accept `analysis_summary`, `codebase_context`, `applied_patterns` |
| `backend/app/routers/optimize.py` | Replace inline context resolution with `enrich()` |
| `backend/app/tools/optimize.py` | Replace inline context resolution with `enrich()` |
| `backend/app/tools/prepare.py` | Replace inline context resolution with `enrich()`, wire `repo_full_name` |
| `backend/app/tools/refine.py` | Replace `resolve_workspace_guidance()` import with `get_context_service()` |
| `backend/app/tools/_shared.py` | Add `get_context_service()`/`set_context_service()`, remove `resolve_workspace_guidance()` |
| `backend/app/config.py` | Add INDEX_* settings |
| `prompts/passthrough.md` | Add `{{analysis_summary}}`, `{{applied_patterns}}` sections |
| `prompts/manifest.json` | Declare new optional variables |
| `backend/tests/test_roots_scanner.py` | Tests for subdirectory discovery and dedup |
| `backend/tests/test_repo_index_service.py` | Tests for structured outlines and curated retrieval |
| `backend/tests/test_passthrough.py` | Tests for new passthrough parameters |

---

## Task 1: Enhanced RootsScanner — Subdirectory Discovery & Dedup

**Files:**
- Modify: `backend/app/services/roots_scanner.py`
- Test: `backend/tests/test_roots_scanner.py`

- [ ] **Step 1: Write failing tests for discover_project_dirs**

Add to `backend/tests/test_roots_scanner.py`:

```python
from app.services.roots_scanner import discover_project_dirs, GUIDANCE_FILES

class TestDiscoverProjectDirs:
    def test_finds_subdirs_with_manifests(self, tmp_path):
        """Subdirectories with package.json or pyproject.toml are detected."""
        _make_file(tmp_path, "backend/pyproject.toml", "[tool.ruff]")
        _make_file(tmp_path, "frontend/package.json", '{"name": "app"}')
        _make_file(tmp_path, "docs/readme.md", "# docs")  # No manifest

        dirs = discover_project_dirs(tmp_path)
        names = [d.name for d in dirs]
        assert "backend" in names
        assert "frontend" in names
        assert "docs" not in names

    def test_skips_ignored_dirs(self, tmp_path):
        """node_modules, .venv, __pycache__ etc. are skipped even with manifests."""
        _make_file(tmp_path, "node_modules/package.json", '{}')
        _make_file(tmp_path, ".venv/pyproject.toml", "[tool]")
        _make_file(tmp_path, "__pycache__/pyproject.toml", "[tool]")

        dirs = discover_project_dirs(tmp_path)
        assert dirs == []

    def test_empty_root(self, tmp_path):
        dirs = discover_project_dirs(tmp_path)
        assert dirs == []

    def test_nonexistent_root(self):
        from pathlib import Path
        dirs = discover_project_dirs(Path("/nonexistent/path"))
        assert dirs == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && source .venv/bin/activate && pytest tests/test_roots_scanner.py::TestDiscoverProjectDirs -v`
Expected: FAIL — `discover_project_dirs` not importable

- [ ] **Step 3: Implement discover_project_dirs and expand GUIDANCE_FILES**

In `backend/app/services/roots_scanner.py`, add after the imports:

```python
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
```

Expand `GUIDANCE_FILES`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && source .venv/bin/activate && pytest tests/test_roots_scanner.py::TestDiscoverProjectDirs -v`
Expected: PASS

- [ ] **Step 5: Write failing tests for subdirectory scanning with dedup**

Add to `backend/tests/test_roots_scanner.py`:

```python
import hashlib

class TestSubdirScanning:
    def test_scans_root_and_subdirs(self, tmp_path):
        """Scan root + manifest-detected subdirectories."""
        _make_file(tmp_path, "CLAUDE.md", "root guidance")
        _make_file(tmp_path, "backend/pyproject.toml", "[tool]")
        _make_file(tmp_path, "backend/CLAUDE.md", "backend guidance")

        scanner = RootsScanner()
        result = scanner.scan(tmp_path)

        assert result is not None
        assert "root guidance" in result
        assert "backend guidance" in result

    def test_deduplicates_identical_files(self, tmp_path):
        """Identical content in root and subdir is included only once."""
        same_content = "identical guidance content"
        _make_file(tmp_path, "CLAUDE.md", same_content)
        _make_file(tmp_path, "backend/pyproject.toml", "[tool]")
        _make_file(tmp_path, "backend/CLAUDE.md", same_content)

        scanner = RootsScanner()
        result = scanner.scan(tmp_path)

        assert result is not None
        # Content appears only once (root wins)
        assert result.count(same_content) == 1

    def test_new_guidance_files_discovered(self, tmp_path):
        """GEMINI.md, .clinerules, CONVENTIONS.md are now discovered."""
        _make_file(tmp_path, "GEMINI.md", "gemini rules")
        _make_file(tmp_path, ".clinerules", "cline rules")
        _make_file(tmp_path, "CONVENTIONS.md", "conventions")

        scanner = RootsScanner()
        found = scanner.discover(tmp_path)
        names = [p.name for p in found]

        assert "GEMINI.md" in names
        assert ".clinerules" in names
        assert "CONVENTIONS.md" in names
```

- [ ] **Step 6: Run tests to verify they fail**

Run: `cd backend && source .venv/bin/activate && pytest tests/test_roots_scanner.py::TestSubdirScanning -v`
Expected: FAIL — subdirectory scanning not implemented yet

- [ ] **Step 7: Implement subdirectory scanning and content dedup in RootsScanner.scan()**

Modify `RootsScanner.scan()` method to:
1. Scan root for guidance files (existing behavior)
2. Call `discover_project_dirs(root)` to find subdirectories
3. Scan each subdirectory for guidance files
4. Deduplicate by SHA256 content hash (root copy wins)
5. Respect total budget

Add a helper method `_collect_sections()` and modify `scan()`:

```python
import hashlib

def scan(self, root: Path) -> str | None:
    if not root.exists() or not root.is_dir():
        return None

    # Collect from root
    all_candidates = self._collect_file_candidates(root, prefix=None)

    # Collect from manifest-detected subdirectories
    for subdir in discover_project_dirs(root):
        prefix = subdir.name
        all_candidates.extend(self._collect_file_candidates(subdir, prefix=prefix))

    if not all_candidates:
        return None

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

        # Per-file caps
        lines = content.split("\n")
        if len(lines) > MAX_LINES_PER_FILE:
            content = "\n".join(lines[:MAX_LINES_PER_FILE])
        if len(content) > MAX_CHARS_PER_FILE:
            content = content[:MAX_CHARS_PER_FILE]

        # Total budget
        if total_chars + len(content) > self._max_total:
            remaining = self._max_total - total_chars
            if remaining <= 0:
                break
            content = content[:remaining]

        section = (
            f'<untrusted-context source="{label}">\n'
            f"{content}\n"
            f"</untrusted-context>"
        )
        sections.append(section)
        total_chars += len(content)

    return "\n\n".join(sections) if sections else None


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
            continue
        if path.parent.name == ".github":
            name = f".github/{path.name}"
        else:
            name = path.name
        label = f"{prefix}/{name}" if prefix else name
        candidates.append((label, content))
    return candidates
```

- [ ] **Step 8: Run all roots_scanner tests**

Run: `cd backend && source .venv/bin/activate && pytest tests/test_roots_scanner.py -v`
Expected: ALL PASS

- [ ] **Step 9: Commit**

```bash
git add backend/app/services/roots_scanner.py backend/tests/test_roots_scanner.py
git commit -m "feat: enhanced RootsScanner with subdirectory discovery and content dedup"
```

---

## Task 2: Update WorkspaceIntelligence to Use discover_project_dirs

**Files:**
- Modify: `backend/app/services/workspace_intelligence.py`
- Test: `backend/tests/test_workspace_intelligence.py`

- [ ] **Step 1: Write failing test for subdirectory stack detection**

Add to `backend/tests/test_workspace_intelligence.py`:

```python
class TestSubdirStackDetection:
    def test_detects_stack_in_subdirectories(self, tmp_path):
        """Stack detection scans manifest-detected subdirectories too."""
        # Root has no manifests, but backend/ and frontend/ do
        (tmp_path / "backend").mkdir()
        (tmp_path / "backend" / "requirements.txt").write_text("fastapi\nsqlalchemy\n")
        (tmp_path / "frontend").mkdir()
        (tmp_path / "frontend" / "package.json").write_text(
            '{"dependencies": {"svelte": "4.0.0", "tailwindcss": "4.0.0"}}'
        )

        wi = WorkspaceIntelligence()
        result = wi.analyze([tmp_path])

        assert result is not None
        assert "Python" in result
        assert "FastAPI" in result
        assert "Svelte" in result
        assert "Tailwind CSS" in result
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && source .venv/bin/activate && pytest tests/test_workspace_intelligence.py::TestSubdirStackDetection -v`
Expected: FAIL — subdirectories not scanned for stack

- [ ] **Step 3: Update _detect_stack to use discover_project_dirs**

In `backend/app/services/workspace_intelligence.py`, import and use `discover_project_dirs`:

```python
from app.services.roots_scanner import RootsScanner, discover_project_dirs
```

Modify `_detect_stack` to scan subdirectories:

```python
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
        # ... existing detection logic (unchanged) ...
```

- [ ] **Step 4: Run tests**

Run: `cd backend && source .venv/bin/activate && pytest tests/test_workspace_intelligence.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/workspace_intelligence.py backend/tests/test_workspace_intelligence.py
git commit -m "feat: WorkspaceIntelligence uses discover_project_dirs for subdir scanning"
```

---

## Task 3: Heuristic Analyzer — Keyword Classification & Weakness Detection

**Files:**
- Create: `backend/app/services/heuristic_analyzer.py`
- Create: `backend/tests/test_heuristic_analyzer.py`

- [ ] **Step 1: Write failing tests for task_type classification**

Create `backend/tests/test_heuristic_analyzer.py`:

```python
"""Tests for HeuristicAnalyzer — zero-LLM prompt classification."""

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.services.heuristic_analyzer import HeuristicAnalyzer, HeuristicAnalysis


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    from app.models import Base
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with session_factory() as session:
        yield session
    await engine.dispose()


class TestTaskTypeClassification:
    @pytest.mark.asyncio
    async def test_coding_prompt(self, db):
        analyzer = HeuristicAnalyzer()
        result = await analyzer.analyze(
            "Implement a REST API endpoint for user authentication with JWT tokens",
            db,
        )
        assert result.task_type == "coding"
        assert result.confidence > 0.5

    @pytest.mark.asyncio
    async def test_writing_prompt(self, db):
        analyzer = HeuristicAnalyzer()
        result = await analyzer.analyze(
            "Draft a blog post about the future of artificial intelligence for a general audience",
            db,
        )
        assert result.task_type == "writing"

    @pytest.mark.asyncio
    async def test_analysis_prompt(self, db):
        analyzer = HeuristicAnalyzer()
        result = await analyzer.analyze(
            "Analyze the pros and cons of microservices vs monolithic architecture for our team",
            db,
        )
        assert result.task_type == "analysis"

    @pytest.mark.asyncio
    async def test_general_fallback(self, db):
        analyzer = HeuristicAnalyzer()
        result = await analyzer.analyze(
            "Tell me about the weather today and what I should wear",
            db,
        )
        assert result.task_type == "general"
        assert result.confidence < 0.5


class TestDomainClassification:
    @pytest.mark.asyncio
    async def test_backend_domain(self, db):
        analyzer = HeuristicAnalyzer()
        result = await analyzer.analyze(
            "Refactor the FastAPI middleware to handle CORS headers properly",
            db,
        )
        assert result.domain == "backend"

    @pytest.mark.asyncio
    async def test_frontend_domain(self, db):
        analyzer = HeuristicAnalyzer()
        result = await analyzer.analyze(
            "Build a React component with Tailwind CSS for the dashboard layout",
            db,
        )
        assert result.domain == "frontend"

    @pytest.mark.asyncio
    async def test_database_domain(self, db):
        analyzer = HeuristicAnalyzer()
        result = await analyzer.analyze(
            "Write a SQL migration to add an index on the users table email column",
            db,
        )
        assert result.domain == "database"


class TestWeaknessDetection:
    @pytest.mark.asyncio
    async def test_detects_vague_language(self, db):
        analyzer = HeuristicAnalyzer()
        result = await analyzer.analyze(
            "Make some improvements to various parts of the codebase to make things better",
            db,
        )
        weaknesses = [w.lower() for w in result.weaknesses]
        assert any("vague" in w for w in weaknesses)

    @pytest.mark.asyncio
    async def test_detects_missing_constraints(self, db):
        analyzer = HeuristicAnalyzer()
        result = await analyzer.analyze(
            "Write a function that processes data and returns results",
            db,
        )
        weaknesses = [w.lower() for w in result.weaknesses]
        assert any("constraint" in w or "specificity" in w for w in weaknesses)

    @pytest.mark.asyncio
    async def test_detects_strengths(self, db):
        analyzer = HeuristicAnalyzer()
        result = await analyzer.analyze(
            "Implement a Python async function `fetch_user(user_id: int) -> User` "
            "that queries PostgreSQL via SQLAlchemy, returns 404 if not found, "
            "and includes retry logic with exponential backoff (max 3 retries).",
            db,
        )
        assert len(result.strengths) > 0
        strengths = [s.lower() for s in result.strengths]
        assert any("specific" in s or "technical" in s or "constraint" in s for s in strengths)


class TestStrategyRecommendation:
    @pytest.mark.asyncio
    async def test_coding_gets_structured_output(self, db):
        analyzer = HeuristicAnalyzer()
        result = await analyzer.analyze(
            "Implement a REST API endpoint for user registration",
            db,
        )
        assert result.recommended_strategy == "structured-output"

    @pytest.mark.asyncio
    async def test_writing_gets_role_playing(self, db):
        analyzer = HeuristicAnalyzer()
        result = await analyzer.analyze(
            "Draft a blog article about machine learning trends",
            db,
        )
        assert result.recommended_strategy == "role-playing"

    @pytest.mark.asyncio
    async def test_analysis_gets_chain_of_thought(self, db):
        analyzer = HeuristicAnalyzer()
        result = await analyzer.analyze(
            "Evaluate the trade-offs between REST and GraphQL for our API",
            db,
        )
        assert result.recommended_strategy == "chain-of-thought"


class TestIntentLabel:
    @pytest.mark.asyncio
    async def test_generates_label(self, db):
        analyzer = HeuristicAnalyzer()
        result = await analyzer.analyze(
            "Refactor the authentication module to use OAuth2",
            db,
        )
        assert isinstance(result.intent_label, str)
        assert len(result.intent_label) > 0
        assert len(result.intent_label.split()) <= 8  # Not too long


class TestAnalysisDataclass:
    @pytest.mark.asyncio
    async def test_returns_frozen_dataclass(self, db):
        analyzer = HeuristicAnalyzer()
        result = await analyzer.analyze("Implement a sorting algorithm", db)
        assert isinstance(result, HeuristicAnalysis)
        # Frozen — cannot reassign
        with pytest.raises(AttributeError):
            result.task_type = "writing"

    @pytest.mark.asyncio
    async def test_format_analysis_summary(self, db):
        analyzer = HeuristicAnalyzer()
        result = await analyzer.analyze(
            "Implement a REST API for user management with JWT auth",
            db,
        )
        summary = result.format_summary()
        assert "Task type:" in summary
        assert "Domain:" in summary
        assert isinstance(summary, str)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && source .venv/bin/activate && pytest tests/test_heuristic_analyzer.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement HeuristicAnalysis dataclass**

Create `backend/app/services/heuristic_analyzer.py` with the dataclass and signal dictionaries:

```python
"""Zero-LLM heuristic prompt analyzer.

Classifies task_type, domain, detects weaknesses/strengths, and recommends
a strategy — all without any LLM calls. Designed for passthrough tier
enrichment where we cannot call external models.

Copyright 2025-2026 Project Synthesis contributors.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class HeuristicAnalysis:
    """Result of heuristic prompt analysis."""

    task_type: str       # coding | writing | analysis | creative | data | system | general
    domain: str          # backend | frontend | database | devops | security | fullstack | general
    intent_label: str    # 3-6 word phrase
    weaknesses: list[str] = field(default_factory=list)
    strengths: list[str] = field(default_factory=list)
    recommended_strategy: str = "auto"
    confidence: float = 0.0

    def format_summary(self) -> str:
        """Format analysis as a human-readable string for template injection."""
        parts = [
            f"Task type: {self.task_type}",
            f"Domain: {self.domain}",
        ]
        if self.weaknesses:
            parts.append("Weaknesses:")
            for w in self.weaknesses:
                parts.append(f"- {w}")
        if self.strengths:
            parts.append("Strengths:")
            for s in self.strengths:
                parts.append(f"- {s}")
        parts.append(
            f"Recommended strategy: {self.recommended_strategy}"
            f" (confidence: {self.confidence:.2f})"
        )
        return "\n".join(parts)
```

- [ ] **Step 4: Implement keyword classifier**

Add signal dictionaries and the core `analyze()` method to `heuristic_analyzer.py`:

```python
# --- Weighted keyword signals (case-insensitive matching) ---

_TASK_TYPE_SIGNALS: dict[str, list[tuple[str, float]]] = {
    "coding": [
        ("implement", 1.0), ("refactor", 1.0), ("debug", 0.9),
        ("function", 0.7), ("api", 0.8), ("endpoint", 0.8),
        ("bug", 0.9), ("test", 0.7), ("deploy", 0.6),
        ("class", 0.6), ("module", 0.6), ("code", 0.5),
        ("fix", 0.6), ("build", 0.5), ("migrate", 0.7),
    ],
    "writing": [
        ("write", 0.5), ("draft", 0.9), ("blog", 1.0),
        ("article", 1.0), ("essay", 1.0), ("copy", 0.8),
        ("tone", 0.7), ("audience", 0.6), ("narrative", 0.8),
        ("publish", 0.7), ("editorial", 0.9),
    ],
    "analysis": [
        ("analyze", 1.0), ("compare", 0.9), ("evaluate", 0.9),
        ("review", 0.7), ("assess", 0.9), ("critique", 0.8),
        ("pros and cons", 0.9), ("trade-off", 0.8), ("tradeoff", 0.8),
        ("investigate", 0.7), ("examine", 0.7),
    ],
    "creative": [
        ("brainstorm", 1.0), ("imagine", 0.9), ("story", 1.0),
        ("generate ideas", 0.9), ("creative", 0.8), ("invent", 0.9),
        ("design", 0.5), ("concept", 0.6),
    ],
    "data": [
        ("dataset", 0.9), ("etl", 1.0), ("transform", 0.6),
        ("schema", 0.7), ("aggregate", 0.8), ("visualization", 0.7),
        ("csv", 0.8), ("dataframe", 0.9), ("pandas", 0.9),
    ],
    "system": [
        ("system prompt", 1.0), ("agent", 0.7), ("workflow", 0.6),
        ("automate", 0.8), ("orchestrate", 0.9), ("configure", 0.7),
        ("infrastructure", 0.7), ("prompt engineer", 0.9),
    ],
}

_DOMAIN_SIGNALS: dict[str, list[tuple[str, float]]] = {
    "backend": [
        ("api", 0.7), ("endpoint", 0.9), ("server", 0.8),
        ("middleware", 0.9), ("fastapi", 1.0), ("django", 1.0),
        ("flask", 1.0), ("authentication", 0.7), ("route", 0.6),
    ],
    "frontend": [
        ("react", 1.0), ("svelte", 1.0), ("component", 0.8),
        ("css", 0.9), ("ui", 0.8), ("layout", 0.7),
        ("responsive", 0.8), ("tailwind", 0.9), ("vue", 1.0),
    ],
    "database": [
        ("sql", 1.0), ("migration", 0.9), ("schema", 0.8),
        ("query", 0.7), ("index", 0.5), ("postgresql", 1.0),
        ("sqlite", 1.0), ("orm", 0.8), ("table", 0.6),
    ],
    "devops": [
        ("docker", 1.0), ("ci/cd", 1.0), ("kubernetes", 1.0),
        ("terraform", 1.0), ("nginx", 0.9), ("monitoring", 0.7),
        ("deploy", 0.7), ("pipeline", 0.5),
    ],
    "security": [
        ("encryption", 1.0), ("vulnerability", 1.0), ("cors", 0.9),
        ("jwt", 0.9), ("oauth", 0.9), ("sanitize", 0.8),
        ("injection", 0.9), ("xss", 1.0), ("csrf", 1.0),
    ],
}


def _classify_domain(prompt_lower: str, scored: dict[str, float]) -> str:
    """Classify domain with fullstack promotion when both backend + frontend score high."""
    if not scored:
        return "general"
    best = max(scored, key=scored.get)
    # Promote to fullstack when both backend and frontend score significantly
    if (
        scored.get("backend", 0) >= 1.5
        and scored.get("frontend", 0) >= 1.5
    ):
        return "fullstack"
    return best if scored[best] >= 1.0 else "general"


_DEFAULT_STRATEGY_MAP: dict[str, str] = {
    "coding": "structured-output",
    "writing": "role-playing",
    "analysis": "chain-of-thought",
    "creative": "role-playing",
    "data": "structured-output",
    "system": "meta-prompting",
    "general": "auto",
}

# Vague quantifier patterns
_VAGUE_PATTERNS = re.compile(
    r"\b(some|various|many|a few|several|certain|stuff|things|better|improve)\b",
    re.IGNORECASE,
)

# Code block detection
_CODE_BLOCK_RE = re.compile(r"```[\s\S]*?```")

# Constraint/requirement keywords
_CONSTRAINT_KEYWORDS = {
    "must", "should", "require", "constraint", "limit", "maximum",
    "minimum", "exactly", "no more than", "at least", "ensure",
}

# Success criteria keywords
_OUTCOME_KEYWORDS = {
    "return", "output", "produce", "result", "generate", "create",
    "should return", "expected", "format",
}
```

- [ ] **Step 5: Implement the analyze method**

Add the `HeuristicAnalyzer` class:

```python
class HeuristicAnalyzer:
    """Zero-LLM prompt classifier and weakness detector."""

    async def analyze(
        self, raw_prompt: str, db: AsyncSession,
    ) -> HeuristicAnalysis:
        """Classify prompt and detect weaknesses without any LLM calls."""
        try:
            return await self._analyze_inner(raw_prompt, db)
        except Exception:
            logger.exception("Heuristic analysis failed — returning general fallback")
            return HeuristicAnalysis(
                task_type="general", domain="general",
                intent_label="general optimization",
                confidence=0.0,
            )

    async def _analyze_inner(
        self, raw_prompt: str, db: AsyncSession,
    ) -> HeuristicAnalysis:
        prompt_lower = raw_prompt.lower()
        words = prompt_lower.split()
        first_sentence = prompt_lower.split(".")[0] if "." in prompt_lower else prompt_lower

        # Layer 1: Keyword classification
        task_type, task_confidence = self._classify(
            prompt_lower, first_sentence, _TASK_TYPE_SIGNALS,
        )
        # Domain classification with fullstack promotion
        domain_scores: dict[str, float] = {}
        for category, keywords in _DOMAIN_SIGNALS.items():
            domain_scores[category] = self._score_category(
                prompt_lower, first_sentence, keywords,
            )
        domain = _classify_domain(prompt_lower, domain_scores)
        domain_confidence = min(1.0, max(domain_scores.values())) if domain_scores else 0.0

        # Layer 2: Structural signals
        has_code_blocks = bool(_CODE_BLOCK_RE.search(raw_prompt))
        has_lists = bool(re.search(r"^\s*[-*]\s", raw_prompt, re.MULTILINE))
        is_question = first_sentence.strip().startswith(("what", "how", "why", "when", "which", "is", "are", "can", "does"))
        is_imperative = len(words) > 0 and not is_question

        # Boost coding confidence if code blocks present
        if has_code_blocks and task_type != "coding":
            coding_score = self._score_category(prompt_lower, first_sentence, _TASK_TYPE_SIGNALS.get("coding", []))
            if coding_score > task_confidence * 0.7:
                task_type = "coding"
                task_confidence = max(task_confidence, coding_score)

        # Layer 3: Weakness detection
        weaknesses = self._detect_weaknesses(raw_prompt, prompt_lower, words, task_type)
        strengths = self._detect_strengths(raw_prompt, prompt_lower, words, has_code_blocks, has_lists)

        # Layer 4: Strategy from adaptation tracker
        strategy = await self._select_strategy(db, task_type)

        # Layer 5: Intent label
        intent_label = self._generate_intent_label(raw_prompt, task_type, domain)

        # Combine confidence
        confidence = min(1.0, (task_confidence + domain_confidence) / 2)
        if task_type == "general":
            confidence = min(confidence, 0.3)

        return HeuristicAnalysis(
            task_type=task_type,
            domain=domain,
            intent_label=intent_label,
            weaknesses=weaknesses,
            strengths=strengths,
            recommended_strategy=strategy,
            confidence=round(confidence, 2),
        )

    def _classify(
        self, prompt_lower: str, first_sentence: str,
        signals: dict[str, list[tuple[str, float]]],
    ) -> tuple[str, float]:
        """Score all categories and return (best_category, confidence)."""
        scores: dict[str, float] = {}
        for category, keywords in signals.items():
            scores[category] = self._score_category(
                prompt_lower, first_sentence, keywords,
            )
        if not scores or max(scores.values()) == 0:
            return "general", 0.0
        best = max(scores, key=scores.get)  # type: ignore[arg-type]
        return best, min(1.0, scores[best])

    @staticmethod
    def _score_category(
        prompt_lower: str, first_sentence: str,
        keywords: list[tuple[str, float]],
    ) -> float:
        """Score a category by weighted keyword presence with positional boost."""
        score = 0.0
        for keyword, weight in keywords:
            kw = keyword.lower()
            if kw in prompt_lower:
                # 2x boost if keyword appears in first sentence
                multiplier = 2.0 if kw in first_sentence else 1.0
                score += weight * multiplier
        return score

    def _detect_weaknesses(
        self, raw_prompt: str, prompt_lower: str,
        words: list[str], task_type: str,
    ) -> list[str]:
        weaknesses: list[str] = []
        word_count = len(words)

        # Vague language
        vague_matches = _VAGUE_PATTERNS.findall(prompt_lower)
        if len(vague_matches) >= 2:
            weaknesses.append("vague language reduces precision")

        # Missing constraints
        has_constraints = any(kw in prompt_lower for kw in _CONSTRAINT_KEYWORDS)
        if not has_constraints and word_count > 10:
            weaknesses.append("lacks constraints — no boundaries for the output")

        # Missing outcome
        has_outcome = any(kw in prompt_lower for kw in _OUTCOME_KEYWORDS)
        if not has_outcome and word_count > 15:
            weaknesses.append("no measurable outcome defined")

        # Too short for complex task
        if task_type in ("coding", "data", "system") and word_count < 15:
            weaknesses.append("prompt underspecified for task complexity")

        # No examples
        has_examples = "example" in prompt_lower or "e.g." in prompt_lower or "```" in raw_prompt
        if not has_examples and word_count > 20:
            weaknesses.append("no examples to anchor expected output")

        # Broad scope
        if any(w in prompt_lower for w in ("everything", "all aspects", "every part")):
            weaknesses.append("scope too broad — consider narrowing focus")

        # Missing technical context for coding
        if task_type == "coding":
            tech_terms = {"python", "javascript", "typescript", "rust", "go", "java",
                          "react", "svelte", "fastapi", "django", "flask", "sql"}
            if not any(t in prompt_lower for t in tech_terms):
                weaknesses.append("insufficient technical context — no language or framework specified")

        return weaknesses

    def _detect_strengths(
        self, raw_prompt: str, prompt_lower: str,
        words: list[str], has_code_blocks: bool, has_lists: bool,
    ) -> list[str]:
        strengths: list[str] = []

        if has_code_blocks:
            strengths.append("includes concrete code examples")
        if has_lists:
            strengths.append("well-organized prompt structure")

        has_constraints = any(kw in prompt_lower for kw in _CONSTRAINT_KEYWORDS)
        if has_constraints:
            strengths.append("clear constraints defined")

        # Specific technologies mentioned
        tech_count = sum(1 for t in (
            "python", "javascript", "typescript", "react", "svelte",
            "fastapi", "django", "sql", "docker", "kubernetes",
        ) if t in prompt_lower)
        if tech_count >= 2:
            strengths.append("specific technical context provided")

        has_outcome = any(kw in prompt_lower for kw in _OUTCOME_KEYWORDS)
        if has_outcome:
            strengths.append("measurable outcome specified")

        return strengths

    async def _select_strategy(
        self, db: AsyncSession, task_type: str,
    ) -> str:
        """Select strategy: historical learning → adaptation → static fallback."""
        # Try historical learning first
        learned = await self._learn_from_history(db, task_type)
        if learned:
            return learned

        # Try adaptation tracker (use get_affinities — no get_best_strategy method)
        try:
            from app.services.adaptation_tracker import AdaptationTracker
            tracker = AdaptationTracker(db)
            affinities = await tracker.get_affinities(task_type)
            blocked = await tracker.get_blocked_strategies(task_type)
            if affinities:
                # Pick strategy with highest approval rate, excluding blocked
                candidates = {
                    k: v for k, v in affinities.items()
                    if k not in blocked and v.get("approval_rate", 0) > 0.6
                }
                if candidates:
                    best_key = max(candidates, key=lambda k: candidates[k].get("approval_rate", 0))
                    return best_key
        except Exception:
            logger.debug("Adaptation tracker unavailable")

        # Static fallback
        return _DEFAULT_STRATEGY_MAP.get(task_type, "auto")

    async def _learn_from_history(
        self, db: AsyncSession, task_type: str,
    ) -> str | None:
        """Query historical strategy performance for this task_type."""
        try:
            from app.models import Optimization
            result = await db.execute(
                select(
                    Optimization.strategy_used,
                    func.avg(Optimization.overall_score).label("avg_score"),
                    func.count().label("count"),
                )
                .where(
                    Optimization.task_type == task_type,
                    Optimization.status == "completed",
                    Optimization.overall_score.isnot(None),
                    Optimization.scoring_mode.notin_(["heuristic", "hybrid_passthrough"]),
                )
                .group_by(Optimization.strategy_used)
                .having(func.count() >= 3)
            )
            rows = result.all()
            if not rows:
                return None
            best = max(rows, key=lambda r: r.avg_score)
            if best.avg_score >= 6.0:
                return best.strategy_used
        except Exception:
            logger.debug("Historical learning query failed", exc_info=True)
        return None

    def _generate_intent_label(
        self, raw_prompt: str, task_type: str, domain: str,
    ) -> str:
        """Generate a short 3-6 word intent label."""
        first_verb = self._extract_first_verb(raw_prompt)
        if domain != "general":
            label = f"{first_verb} {domain} {task_type}"
        else:
            label = f"{first_verb} {task_type} task"
        # Cap at 6 words
        words = label.split()[:6]
        return " ".join(words)

    @staticmethod
    def _extract_first_verb(text: str) -> str:
        """Extract the first likely verb from the prompt."""
        common_verbs = {
            "implement", "create", "build", "write", "design", "refactor",
            "fix", "add", "remove", "update", "migrate", "deploy", "test",
            "analyze", "review", "evaluate", "compare", "draft", "generate",
            "configure", "optimize", "debug", "integrate", "setup", "improve",
        }
        words = text.lower().split()
        for word in words[:10]:  # Check first 10 words
            cleaned = re.sub(r"[^a-z]", "", word)
            if cleaned in common_verbs:
                return cleaned
        return "optimize"  # Safe fallback
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd backend && source .venv/bin/activate && pytest tests/test_heuristic_analyzer.py -v`
Expected: ALL PASS

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/heuristic_analyzer.py backend/tests/test_heuristic_analyzer.py
git commit -m "feat: HeuristicAnalyzer — zero-LLM classification and weakness detection"
```

---

## Task 4: Augmented RepoIndexService — Structured Outlines & Curated Retrieval

**Files:**
- Modify: `backend/app/services/repo_index_service.py`
- Modify: `backend/app/config.py`
- Test: `backend/tests/test_repo_index_service.py`

- [ ] **Step 1: Add config settings**

In `backend/app/config.py`, add after `EXPLORE_TOTAL_LINE_BUDGET`:

```python
    # --- Index Enrichment ---
    INDEX_OUTLINE_MAX_CHARS: int = Field(
        default=500, description="Maximum characters per file outline in RepoIndexService.",
    )
    INDEX_CURATED_MAX_CHARS: int = Field(
        default=8000, description="Maximum characters for curated codebase context in passthrough.",
    )
    INDEX_CURATED_MIN_SIMILARITY: float = Field(
        default=0.3, description="Minimum cosine similarity threshold for curated retrieval.",
    )
    INDEX_CURATED_MAX_PER_DIR: int = Field(
        default=3, description="Maximum files per directory in curated retrieval (diversity cap).",
    )
    INDEX_DOMAIN_BOOST: float = Field(
        default=1.3, description="Similarity multiplier for files matching the detected domain.",
    )
```

- [ ] **Step 2: Write failing tests for structured outline extraction**

Add to `backend/tests/test_repo_index_service.py`:

```python
from app.services.repo_index_service import (
    _extract_structured_outline,
    CuratedCodebaseContext,
)

class TestStructuredOutlines:
    def test_python_outline_extracts_signatures(self):
        content = '''"""User authentication service."""

import logging
from pathlib import Path

class AuthService:
    """Handles JWT token creation and validation."""

    def create_token(self, user_id: int) -> str:
        """Create a new JWT token."""
        pass

    async def validate_token(self, token: str) -> dict:
        """Validate and decode a JWT token."""
        pass

def helper_function(x: int) -> bool:
    return x > 0
'''
        outline = _extract_structured_outline("auth.py", content)
        assert outline.file_type == "python"
        assert "AuthService" in outline.structural_summary
        assert "create_token" in outline.structural_summary
        assert "validate_token" in outline.structural_summary
        assert outline.doc_summary is not None
        assert "authentication" in outline.doc_summary.lower()
        assert len(outline.structural_summary) <= 500

    def test_typescript_outline_extracts_exports(self):
        content = '''/** API client for backend communication. */

export interface User {
  id: string;
  name: string;
}

export async function fetchUser(id: string): Promise<User> {
  return await fetch(`/api/users/${id}`).then(r => r.json());
}

export class ApiClient {
  constructor(private baseUrl: string) {}
}
'''
        outline = _extract_structured_outline("client.ts", content)
        assert outline.file_type == "typescript"
        assert "User" in outline.structural_summary
        assert "fetchUser" in outline.structural_summary
        assert "ApiClient" in outline.structural_summary

    def test_markdown_outline_extracts_headings(self):
        content = '''# Project Setup

## Installation

Follow these steps to install...

## Configuration

Set the following environment variables...

## Usage

Run the application with...
'''
        outline = _extract_structured_outline("README.md", content)
        assert outline.file_type == "docs"
        assert "Project Setup" in outline.structural_summary
        assert "Installation" in outline.structural_summary
        assert "Configuration" in outline.structural_summary

    def test_config_outline_extracts_keys(self):
        content = '{"name": "my-app", "version": "1.0.0", "scripts": {"dev": "vite"}, "dependencies": {}}'
        outline = _extract_structured_outline("package.json", content)
        assert outline.file_type == "config"

    def test_generic_fallback(self):
        content = "some content\nwith lines\nclass Foo:\n    pass\ndef bar():\n    pass"
        outline = _extract_structured_outline("unknown.xyz", content)
        assert outline.file_type == "other"

    def test_outline_capped_at_max_chars(self):
        long_content = "\n".join(f"def func_{i}(x): pass" for i in range(200))
        outline = _extract_structured_outline("big.py", long_content)
        assert len(outline.structural_summary) <= 500
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd backend && source .venv/bin/activate && pytest tests/test_repo_index_service.py::TestStructuredOutlines -v`
Expected: FAIL — `_extract_structured_outline` not importable

- [ ] **Step 4: Implement structured outline extraction**

In `backend/app/services/repo_index_service.py`, first add `import re` and `from typing import Callable` to the existing imports at the top of the file. Then add the `FileOutline` dataclass and extraction functions:

```python
import re
from dataclasses import dataclass
from typing import Callable
from app.config import settings

@dataclass
class FileOutline:
    file_path: str
    file_type: str
    structural_summary: str
    imports_summary: str | None = None
    doc_summary: str | None = None
    size_lines: int = 0
    size_bytes: int = 0

@dataclass
class CuratedCodebaseContext:
    context_text: str
    files_included: int
    total_files_indexed: int
    index_freshness: str
    top_relevance_score: float


def _extract_structured_outline(path: str, content: str) -> FileOutline:
    """Extract a structured outline from file content based on file type."""
    ext = path[path.rfind("."):].lower() if "." in path else ""
    lines = content.splitlines()
    size_lines = len(lines)
    size_bytes = len(content.encode("utf-8", errors="replace"))
    max_chars = settings.INDEX_OUTLINE_MAX_CHARS

    extractor = _OUTLINE_EXTRACTORS.get(ext, _extract_generic_outline)
    outline = extractor(path, content, lines)
    outline.size_lines = size_lines
    outline.size_bytes = size_bytes

    # Enforce max chars
    if len(outline.structural_summary) > max_chars:
        outline.structural_summary = outline.structural_summary[:max_chars]
    return outline


def _extract_python_outline(
    path: str, content: str, lines: list[str],
) -> FileOutline:
    doc = _extract_docstring(lines)
    sigs = [
        ln.rstrip()
        for ln in lines
        if re.match(r"^(class |(?:async )?def )\w+", ln)
    ][:15]
    return FileOutline(
        file_path=path, file_type="python",
        structural_summary="\n".join(sigs),
        doc_summary=doc,
    )


def _extract_typescript_outline(
    path: str, content: str, lines: list[str],
) -> FileOutline:
    doc = None
    if content.startswith("/**"):
        end = content.find("*/")
        if end != -1:
            doc = content[3:end].strip().split("\n")[0].strip(" *")
    sigs = [
        ln.rstrip()
        for ln in lines
        if re.match(
            r"^export\s+(interface|type|function|async function|class|const)\s+\w+",
            ln,
        )
    ][:15]
    return FileOutline(
        file_path=path, file_type="typescript",
        structural_summary="\n".join(sigs),
        doc_summary=doc,
    )


def _extract_markdown_outline(
    path: str, content: str, lines: list[str],
) -> FileOutline:
    headings = [
        ln.rstrip() for ln in lines if re.match(r"^#{1,2}\s+", ln)
    ][:10]
    first_para = ""
    for ln in lines:
        if ln.strip() and not ln.startswith("#"):
            first_para = ln.strip()
            break
    summary = "\n".join(headings)
    return FileOutline(
        file_path=path, file_type="docs",
        structural_summary=summary,
        doc_summary=first_para[:200] if first_para else None,
    )


def _extract_config_outline(
    path: str, content: str, lines: list[str],
) -> FileOutline:
    preview = "\n".join(lines[:15])
    return FileOutline(
        file_path=path, file_type="config",
        structural_summary=preview,
    )


def _extract_sql_outline(
    path: str, content: str, lines: list[str],
) -> FileOutline:
    stmts = [
        ln.rstrip()
        for ln in lines
        if re.match(r"^(CREATE\s+(TABLE|INDEX|FUNCTION|VIEW))", ln, re.IGNORECASE)
    ][:15]
    return FileOutline(
        file_path=path, file_type="sql",
        structural_summary="\n".join(stmts),
    )


def _extract_svelte_outline(
    path: str, content: str, lines: list[str],
) -> FileOutline:
    exports = [
        ln.rstrip()
        for ln in lines
        if re.match(r"^\s*export\s+(let|const|function)\s+", ln)
    ][:10]
    component_name = path.rsplit("/", 1)[-1].replace(".svelte", "")
    summary = f"Component: {component_name}\n" + "\n".join(exports)
    return FileOutline(
        file_path=path, file_type="svelte",
        structural_summary=summary,
    )


def _extract_generic_outline(
    path: str, content: str, lines: list[str],
) -> FileOutline:
    sigs = [
        ln.rstrip()
        for ln in lines
        if re.match(r"^(class |def |function |export )", ln)
    ][:15]
    if not sigs:
        non_empty = [ln for ln in lines if ln.strip()][:20]
        sigs = non_empty
    return FileOutline(
        file_path=path, file_type="other",
        structural_summary="\n".join(sigs),
    )


def _extract_docstring(lines: list[str]) -> str | None:
    """Extract first paragraph of a Python module docstring."""
    in_doc = False
    doc_lines: list[str] = []
    for ln in lines[:30]:
        stripped = ln.strip()
        if not in_doc and stripped.startswith('"""'):
            in_doc = True
            content = stripped[3:]
            if content.endswith('"""') and len(content) > 3:
                return content[:-3].strip()
            if content:
                doc_lines.append(content)
            continue
        if in_doc:
            if '"""' in stripped:
                before = stripped.split('"""')[0].strip()
                if before:
                    doc_lines.append(before)
                break
            if stripped == "" and doc_lines:
                break  # First paragraph only
            doc_lines.append(stripped)
    return " ".join(doc_lines).strip() or None


# NOTE: `import re` must be added to the top of repo_index_service.py alongside
# existing imports (not here). It is used by the extractor functions below.

_OUTLINE_EXTRACTORS: dict[str, Callable[[str, str, list[str]], FileOutline]] = {
    ".py": _extract_python_outline,
    ".ts": _extract_typescript_outline,
    ".js": _extract_typescript_outline,
    ".tsx": _extract_typescript_outline,
    ".jsx": _extract_typescript_outline,
    ".svelte": _extract_svelte_outline,
    ".json": _extract_config_outline,
    ".yaml": _extract_config_outline,
    ".yml": _extract_config_outline,
    ".toml": _extract_config_outline,
    ".md": _extract_markdown_outline,
    ".sql": _extract_sql_outline,
}
```

- [ ] **Step 5: Run outline tests**

Run: `cd backend && source .venv/bin/activate && pytest tests/test_repo_index_service.py::TestStructuredOutlines -v`
Expected: ALL PASS

- [ ] **Step 6: Write failing tests for query_curated_context**

Add to `backend/tests/test_repo_index_service.py`:

```python
import numpy as np
import pytest

class TestCuratedRetrieval:
    @pytest.mark.asyncio
    async def test_returns_none_when_no_index(self, db_session):
        from unittest.mock import AsyncMock
        from app.services.embedding_service import EmbeddingService
        from app.services.github_client import GitHubClient

        gc = AsyncMock(spec=GitHubClient)
        es = AsyncMock(spec=EmbeddingService)

        svc = RepoIndexService(db_session, gc, es)
        result = await svc.query_curated_context("owner/repo", "main", "query")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_curated_context_with_indexed_data(self, db_session):
        from unittest.mock import AsyncMock
        from app.models import RepoFileIndex, RepoIndexMeta
        from app.services.embedding_service import EmbeddingService
        from app.services.github_client import GitHubClient

        # Setup: insert mock index data
        meta = RepoIndexMeta(
            repo_full_name="owner/repo", branch="main",
            status="ready", file_count=2, head_sha="abc123",
        )
        db_session.add(meta)

        vec1 = np.random.randn(384).astype(np.float32)
        vec2 = np.random.randn(384).astype(np.float32)
        db_session.add(RepoFileIndex(
            repo_full_name="owner/repo", branch="main",
            file_path="backend/app/auth.py", file_sha="a1",
            outline="class AuthService:\n  def login(self):",
            embedding=vec1.tobytes(),
        ))
        db_session.add(RepoFileIndex(
            repo_full_name="owner/repo", branch="main",
            file_path="backend/app/models.py", file_sha="a2",
            outline="class User:\n  id: int",
            embedding=vec2.tobytes(),
        ))
        await db_session.commit()

        gc = AsyncMock(spec=GitHubClient)
        es = AsyncMock(spec=EmbeddingService)
        query_vec = np.random.randn(384).astype(np.float32)
        es.aembed_single.return_value = query_vec
        es.cosine_search.return_value = [(0, 0.85), (1, 0.72)]

        svc = RepoIndexService(db_session, gc, es)
        result = await svc.query_curated_context("owner/repo", "main", "authentication")

        assert result is not None
        assert result.files_included > 0
        assert result.top_relevance_score > 0.0
        assert "auth.py" in result.context_text

    @pytest.mark.asyncio
    async def test_domain_boosting(self, db_session):
        from unittest.mock import AsyncMock
        from app.models import RepoFileIndex, RepoIndexMeta
        from app.services.embedding_service import EmbeddingService
        from app.services.github_client import GitHubClient

        meta = RepoIndexMeta(
            repo_full_name="owner/repo", branch="main",
            status="ready", file_count=2, head_sha="abc123",
        )
        db_session.add(meta)

        vec = np.ones(384, dtype=np.float32) * 0.5
        db_session.add(RepoFileIndex(
            repo_full_name="owner/repo", branch="main",
            file_path="backend/app/service.py", file_sha="a1",
            outline="class Service:", embedding=vec.tobytes(),
        ))
        db_session.add(RepoFileIndex(
            repo_full_name="owner/repo", branch="main",
            file_path="frontend/src/App.svelte", file_sha="a2",
            outline="<script>export let name</script>", embedding=vec.tobytes(),
        ))
        await db_session.commit()

        gc = AsyncMock(spec=GitHubClient)
        es = AsyncMock(spec=EmbeddingService)
        es.aembed_single.return_value = vec
        # Both files have same base score
        es.cosine_search.return_value = [(0, 0.5), (1, 0.5)]

        svc = RepoIndexService(db_session, gc, es)
        # Domain=backend should boost backend/app/service.py
        result = await svc.query_curated_context(
            "owner/repo", "main", "query", domain="backend",
        )
        assert result is not None
        assert "service.py" in result.context_text
```

- [ ] **Step 7: Run tests to verify they fail**

Run: `cd backend && source .venv/bin/activate && pytest tests/test_repo_index_service.py::TestCuratedRetrieval -v`
Expected: FAIL

- [ ] **Step 8: Implement query_curated_context and update build_index**

Add `query_curated_context()` to `RepoIndexService` and update `build_index` to use structured outlines and composite embeddings. See spec Section 4c for the retrieval pipeline: semantic search → relevance filtering → domain boosting → diversity selection → budget packing.

Update `_extract_outline` calls in `build_index` to use `_extract_structured_outline`. Update embedding text to use `_build_embedding_text`.

```python
def _build_embedding_text(path: str, outline: FileOutline) -> str:
    """Combine path + structural info for richer embedding."""
    parts = [path]
    if outline.doc_summary:
        parts.append(outline.doc_summary)
    if outline.structural_summary:
        parts.append(outline.structural_summary)
    return " | ".join(parts)[:1000]

_DOMAIN_PATH_PATTERNS: dict[str, list[str]] = {
    "backend": ["backend/", "server/", "api/", "app/"],
    "frontend": ["frontend/", "src/components/", "src/lib/"],
    "database": ["models/", "migrations/", "alembic/"],
    "devops": ["docker", "ci/", ".github/workflows/"],
    "security": ["auth", "security/", "middleware/"],
}
```

Add `query_curated_context` to the class:

```python
async def query_curated_context(
    self,
    repo_full_name: str,
    branch: str,
    query: str,
    task_type: str | None = None,
    domain: str | None = None,
    max_chars: int | None = None,
) -> CuratedCodebaseContext | None:
    """Curated retrieval: semantic search + domain boost + diversity + budget packing."""
    effective_max = max_chars or settings.INDEX_CURATED_MAX_CHARS
    min_sim = settings.INDEX_CURATED_MIN_SIMILARITY
    max_per_dir = settings.INDEX_CURATED_MAX_PER_DIR
    domain_boost = settings.INDEX_DOMAIN_BOOST

    # Fetch all indexed files
    result = await self._db.execute(
        select(RepoFileIndex).where(
            RepoFileIndex.repo_full_name == repo_full_name,
            RepoFileIndex.branch == branch,
            RepoFileIndex.embedding.isnot(None),
        )
    )
    rows = result.scalars().all()
    if not rows:
        return None

    # Semantic search
    query_vec = await self._es.aembed_single(query)
    corpus_vecs = [np.frombuffer(r.embedding, dtype=np.float32) for r in rows]
    ranked = self._es.cosine_search(query_vec, corpus_vecs, top_k=len(rows))

    # Relevance filtering + domain boosting
    boosted: list[tuple[int, float]] = []
    domain_patterns = _DOMAIN_PATH_PATTERNS.get(domain or "", [])
    for idx, score in ranked:
        if score < min_sim:
            continue
        effective_score = score
        if domain_patterns:
            path_lower = rows[idx].file_path.lower()
            if any(pat in path_lower for pat in domain_patterns):
                effective_score *= domain_boost
        boosted.append((idx, effective_score))

    # Re-sort by boosted score
    boosted.sort(key=lambda x: x[1], reverse=True)

    # Diversity selection: max N per directory
    dir_counts: dict[str, int] = {}
    selected: list[tuple[int, float]] = []
    for idx, score in boosted:
        path = rows[idx].file_path
        directory = path.rsplit("/", 1)[0] if "/" in path else ""
        if dir_counts.get(directory, 0) >= max_per_dir:
            continue
        dir_counts[directory] = dir_counts.get(directory, 0) + 1
        selected.append((idx, score))

    if not selected:
        return None

    # Budget packing
    parts: list[str] = []
    total_chars = 0
    files_included = 0
    top_score = selected[0][1] if selected else 0.0

    for idx, score in selected:
        row = rows[idx]
        entry = f"## {row.file_path} (relevance: {score:.2f})\n{row.outline or ''}"
        if total_chars + len(entry) > effective_max:
            break
        parts.append(entry)
        total_chars += len(entry)
        files_included += 1

    if not parts:
        return None

    # Freshness: "fresh" (SHA exists), "stale" (meta but no SHA), "unknown" (no meta)
    meta = await self.get_index_status(repo_full_name, branch)
    if meta and meta.head_sha:
        freshness = "fresh"
    elif meta:
        freshness = "stale"
    else:
        freshness = "unknown"

    return CuratedCodebaseContext(
        context_text="\n\n".join(parts),
        files_included=files_included,
        total_files_indexed=len(rows),
        index_freshness=freshness,
        top_relevance_score=top_score,
    )
```

- [ ] **Step 9: Run all repo index tests**

Run: `cd backend && source .venv/bin/activate && pytest tests/test_repo_index_service.py -v`
Expected: ALL PASS

- [ ] **Step 10: Commit**

```bash
git add backend/app/services/repo_index_service.py backend/app/config.py backend/tests/test_repo_index_service.py
git commit -m "feat: RepoIndexService structured outlines and curated retrieval"
```

---

## Task 5: Update Passthrough Template & Assembly

**Files:**
- Modify: `prompts/passthrough.md`
- Modify: `prompts/manifest.json`
- Modify: `backend/app/services/passthrough.py`
- Test: `backend/tests/test_passthrough.py`

- [ ] **Step 1: Write failing test for new passthrough parameters**

Add to `backend/tests/test_passthrough.py`:

```python
class TestEnrichedPassthrough:
    def test_assembles_with_analysis_summary(self, tmp_path):
        """Analysis summary from heuristic analyzer is injected."""
        _setup_prompts(tmp_path)
        assembled, strategy = assemble_passthrough_prompt(
            prompts_dir=tmp_path,
            raw_prompt="Build a REST API with authentication",
            analysis_summary="Task type: coding\nDomain: backend\nWeaknesses:\n- lacks constraints",
        )
        assert "Task type: coding" in assembled
        assert "lacks constraints" in assembled

    def test_assembles_with_applied_patterns(self, tmp_path):
        """Applied patterns from taxonomy engine are injected."""
        _setup_prompts(tmp_path)
        assembled, strategy = assemble_passthrough_prompt(
            prompts_dir=tmp_path,
            raw_prompt="Build a REST API with authentication",
            applied_patterns="- Use dependency injection for service layer\n- Validate all inputs with Pydantic",
        )
        assert "dependency injection" in assembled
        assert "Pydantic" in assembled

    def test_assembles_with_codebase_context(self, tmp_path):
        """Curated index context is injected into codebase_context slot."""
        _setup_prompts(tmp_path)
        assembled, strategy = assemble_passthrough_prompt(
            prompts_dir=tmp_path,
            raw_prompt="Refactor the auth service",
            codebase_context="## backend/app/auth.py (relevance: 0.87)\nclass AuthService:",
        )
        assert "auth.py" in assembled
        assert "relevance: 0.87" in assembled
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && source .venv/bin/activate && pytest tests/test_passthrough.py::TestEnrichedPassthrough -v`
Expected: FAIL — `analysis_summary` not accepted

- [ ] **Step 3: Update passthrough.md template**

Replace `prompts/passthrough.md` with:

```markdown
<user-prompt>
{{raw_prompt}}
</user-prompt>

<analysis>
{{analysis_summary}}
</analysis>

<strategy>
{{strategy_instructions}}
</strategy>

<codebase-context>
{{codebase_guidance}}
{{codebase_context}}
</codebase-context>

<proven-patterns>
{{applied_patterns}}
</proven-patterns>

<adaptation>
{{adaptation_state}}
</adaptation>

<scoring-rubric>
{{scoring_rubric_excerpt}}
</scoring-rubric>

## Instructions

You are an expert prompt engineer. Optimize the user's prompt above, then score both the original and your optimized version.

**Use the analysis above** (if provided) to understand the task type, domain, weaknesses, and recommended strategy. Focus your optimization on addressing the identified weaknesses.

**Apply proven patterns** (if provided) — these are techniques that have worked well for similar prompts in the past.

**Optimization guidelines:**
- Preserve the original intent completely
- Add structure, constraints, and specificity
- Remove filler and redundancy
- Apply the strategy above (if provided)

**Output format for the optimized prompt:**
Always structure the optimized prompt using markdown `##` headers to delineate sections (e.g. `## Task`, `## Requirements`, `## Constraints`, `## Output`). Use bullet lists (`-`) for enumerations, numbered lists (`1.`) for sequential steps, and fenced code blocks for signatures, examples, and schemas. This ensures consistent rendering regardless of which strategy was applied.

**Scoring guidelines:**
Score both prompts on 5 dimensions (1-10 each):
- **clarity** — How unambiguous is the prompt?
- **specificity** — How many constraints and details?
- **structure** — How well-organized?
- **faithfulness** — Does the optimized preserve intent? (Original always 5.0)
- **conciseness** — Is every word necessary?

Return a JSON object with this exact structure:

```json
{
  "optimized_prompt": "The full optimized prompt text...",
  "changes_summary": "Brief description of what changed and why...",
  "task_type": "coding|writing|analysis|creative|data|system|general",
  "strategy_used": "The strategy name you applied",
  "scores": {
    "clarity": 7.5,
    "specificity": 8.0,
    "structure": 7.0,
    "faithfulness": 9.0,
    "conciseness": 7.5
  }
}
```

Scores should evaluate the OPTIMIZED prompt only (1.0-10.0 scale, decimals encouraged). Use the scoring rubric above for calibration.
```

- [ ] **Step 4: Update manifest.json**

Update the `passthrough.md` entry in `prompts/manifest.json`:

```json
"passthrough.md": {"required": ["raw_prompt", "scoring_rubric_excerpt"], "optional": ["strategy_instructions", "codebase_guidance", "codebase_context", "adaptation_state", "analysis_summary", "applied_patterns"]}
```

- [ ] **Step 5: Update assemble_passthrough_prompt**

In `backend/app/services/passthrough.py`, add new parameters:

```python
def assemble_passthrough_prompt(
    prompts_dir: Path,
    raw_prompt: str,
    strategy_name: str | None = None,
    codebase_guidance: str | None = None,
    codebase_context: str | None = None,
    adaptation_state: str | None = None,
    analysis_summary: str | None = None,
    applied_patterns: str | None = None,
) -> tuple[str, str]:
    # ... existing strategy/rubric resolution ...

    assembled = loader.render("passthrough.md", {
        "raw_prompt": raw_prompt,
        "strategy_instructions": strategy_instructions,
        "scoring_rubric_excerpt": scoring_excerpt,
        "codebase_guidance": codebase_guidance,
        "codebase_context": codebase_context,
        "adaptation_state": adaptation_state,
        "analysis_summary": analysis_summary,
        "applied_patterns": applied_patterns,
    })

    return assembled, resolved_name
```

- [ ] **Step 6: Run tests**

Run: `cd backend && source .venv/bin/activate && pytest tests/test_passthrough.py -v`
Expected: ALL PASS

- [ ] **Step 7: Commit**

```bash
git add prompts/passthrough.md prompts/manifest.json backend/app/services/passthrough.py backend/tests/test_passthrough.py
git commit -m "feat: passthrough template accepts analysis, codebase context, and patterns"
```

---

## Task 6: Unified ContextEnrichmentService

**Files:**
- Create: `backend/app/services/context_enrichment.py`
- Create: `backend/tests/test_context_enrichment.py`
- Modify: `backend/app/tools/_shared.py`

- [ ] **Step 1: Write failing tests**

Create `backend/tests/test_context_enrichment.py`:

```python
"""Tests for ContextEnrichmentService."""

import pytest
import pytest_asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.context_enrichment import ContextEnrichmentService, EnrichedContext


@pytest_asyncio.fixture
async def db():
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    from app.models import Base
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with factory() as session:
        yield session
    await engine.dispose()


class TestEnrichPassthrough:
    @pytest.mark.asyncio
    async def test_passthrough_runs_heuristic_analysis(self, db, tmp_path):
        service = _build_service(tmp_path)
        result = await service.enrich(
            raw_prompt="Implement a REST API endpoint for user login",
            tier="passthrough", db=db,
        )
        assert isinstance(result, EnrichedContext)
        assert result.analysis is not None
        assert result.analysis.task_type == "coding"
        assert result.context_sources["heuristic_analysis"] is True

    @pytest.mark.asyncio
    async def test_passthrough_gets_adaptation(self, db, tmp_path):
        service = _build_service(tmp_path)
        result = await service.enrich(
            raw_prompt="Implement a REST API endpoint for user login",
            tier="passthrough", db=db,
        )
        # Adaptation state is resolved (may be None if no data, but key exists)
        assert "adaptation" in result.context_sources


class TestEnrichInternal:
    @pytest.mark.asyncio
    async def test_internal_skips_heuristic_analysis(self, db, tmp_path):
        service = _build_service(tmp_path)
        result = await service.enrich(
            raw_prompt="Implement a REST API endpoint for user login",
            tier="internal", db=db,
        )
        assert result.analysis is None
        assert result.context_sources["heuristic_analysis"] is False

    @pytest.mark.asyncio
    async def test_internal_skips_curated_index(self, db, tmp_path):
        service = _build_service(tmp_path)
        result = await service.enrich(
            raw_prompt="Implement a REST API endpoint for user login",
            tier="internal", db=db,
            repo_full_name="owner/repo",
        )
        # Internal tier doesn't use curated index (pipeline does explore)
        assert result.codebase_context is None


class TestEnrichWorkspaceGuidance:
    @pytest.mark.asyncio
    async def test_workspace_path_resolves_guidance(self, db, tmp_path):
        # Create a workspace with CLAUDE.md
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        (workspace / "CLAUDE.md").write_text("# Project Guidance\nUse async everywhere.")

        service = _build_service(tmp_path)
        result = await service.enrich(
            raw_prompt="Implement a REST API endpoint for user login",
            tier="passthrough", db=db,
            workspace_path=str(workspace),
        )
        assert result.workspace_guidance is not None
        assert "async everywhere" in result.workspace_guidance


class TestEnrichGracefulDegradation:
    @pytest.mark.asyncio
    async def test_all_none_still_returns_valid_context(self, db, tmp_path):
        service = _build_service(tmp_path)
        result = await service.enrich(
            raw_prompt="Tell me about the weather",
            tier="passthrough", db=db,
        )
        assert isinstance(result, EnrichedContext)
        assert result.raw_prompt == "Tell me about the weather"


def _build_service(tmp_path: Path) -> ContextEnrichmentService:
    from app.services.heuristic_analyzer import HeuristicAnalyzer
    from app.services.workspace_intelligence import WorkspaceIntelligence
    mock_es = AsyncMock()
    mock_gc = AsyncMock()
    return ContextEnrichmentService(
        prompts_dir=tmp_path,
        data_dir=tmp_path,
        workspace_intel=WorkspaceIntelligence(),
        embedding_service=mock_es,
        heuristic_analyzer=HeuristicAnalyzer(),
        github_client=mock_gc,
    )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && source .venv/bin/activate && pytest tests/test_context_enrichment.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement ContextEnrichmentService**

Create `backend/app/services/context_enrichment.py`:

```python
"""Unified context enrichment service for all routing tiers.

Single entry point replacing 5 scattered context resolution sites.
Each tier calls enrich() and receives an EnrichedContext with all
resolved layers — workspace guidance, codebase context, adaptation,
applied patterns, and (for passthrough) heuristic analysis.

Copyright 2025-2026 Project Synthesis contributors.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.heuristic_analyzer import HeuristicAnalysis, HeuristicAnalyzer
from app.services.workspace_intelligence import WorkspaceIntelligence

if TYPE_CHECKING:
    from mcp.server.fastmcp import Context
    from app.services.embedding_service import EmbeddingService
    from app.services.github_client import GitHubClient

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EnrichedContext:
    """All resolved context layers for an optimization request."""

    raw_prompt: str
    workspace_guidance: str | None = None
    codebase_context: str | None = None
    adaptation_state: str | None = None
    applied_patterns: str | None = None
    analysis: HeuristicAnalysis | None = None
    context_sources: dict[str, bool] = field(default_factory=dict)


class ContextEnrichmentService:
    """Unified enrichment orchestrator for all routing tiers."""

    def __init__(
        self,
        prompts_dir: Path,
        data_dir: Path,
        workspace_intel: WorkspaceIntelligence,
        embedding_service: Any,          # EmbeddingService
        heuristic_analyzer: HeuristicAnalyzer,
        github_client: Any,              # GitHubClient
        taxonomy_engine: Any | None = None,
    ) -> None:
        self._prompts_dir = prompts_dir
        self._data_dir = data_dir
        self._workspace_intel = workspace_intel
        self._embedding_service = embedding_service
        self._heuristic_analyzer = heuristic_analyzer
        self._github_client = github_client
        self._taxonomy_engine = taxonomy_engine

    async def enrich(
        self,
        raw_prompt: str,
        tier: str,
        db: AsyncSession,
        workspace_path: str | None = None,
        mcp_ctx: Any | None = None,
        repo_full_name: str | None = None,
        repo_branch: str | None = None,
        applied_pattern_ids: list[str] | None = None,
        preferences_snapshot: dict | None = None,
    ) -> EnrichedContext:
        """Resolve all context layers for the given tier."""

        # 1. Workspace guidance — ALL tiers, same path
        guidance = await self._resolve_workspace_guidance(mcp_ctx, workspace_path)

        # 2. Analysis — tier-dependent
        analysis: HeuristicAnalysis | None = None
        task_type: str | None = None
        if tier == "passthrough":
            try:
                analysis = await self._heuristic_analyzer.analyze(raw_prompt, db)
                task_type = analysis.task_type
            except Exception:
                logger.exception("Heuristic analysis failed")
                analysis = HeuristicAnalysis(
                    task_type="general", domain="general",
                    intent_label="general optimization", confidence=0.0,
                )
                task_type = "general"

        # 3. Codebase context — tier-dependent
        codebase_context: str | None = None
        if tier == "passthrough" and repo_full_name:
            branch = repo_branch or "main"
            codebase_context = await self._query_index_context(
                repo_full_name, branch, raw_prompt,
                task_type, analysis.domain if analysis else None, db,
            )

        # 4. Adaptation state — ALL tiers, task_type-aware
        effective_task_type = task_type or "general"
        adaptation = await self._resolve_adaptation(db, effective_task_type)

        # 5. Applied patterns — ALL tiers
        patterns = await self._resolve_patterns(
            raw_prompt, applied_pattern_ids, db,
        )

        # 6. Context sources audit
        sources = {
            "workspace_guidance": guidance is not None,
            "codebase_context": codebase_context is not None,
            "adaptation": adaptation is not None,
            "applied_patterns": patterns is not None,
            "heuristic_analysis": analysis is not None,
        }

        return EnrichedContext(
            raw_prompt=raw_prompt,
            workspace_guidance=guidance,
            codebase_context=codebase_context,
            adaptation_state=adaptation,
            applied_patterns=patterns,
            analysis=analysis,
            context_sources=sources,
        )

    async def _resolve_workspace_guidance(
        self, mcp_ctx: Any | None, workspace_path: str | None,
    ) -> str | None:
        """Resolve workspace guidance via MCP roots or filesystem path."""
        roots: list[Path] = []

        if mcp_ctx:
            try:
                roots_result = await mcp_ctx.session.list_roots()
                for root in roots_result.roots:
                    uri = str(root.uri)
                    if uri.startswith("file://"):
                        roots.append(Path(uri.removeprefix("file://")))
                if roots:
                    logger.debug("Resolved %d workspace roots via MCP", len(roots))
            except Exception:
                logger.debug("MCP roots/list unavailable")

        if not roots and workspace_path:
            wp = Path(workspace_path)
            if wp.is_dir():
                roots = [wp]

        if not roots:
            return None

        return self._workspace_intel.analyze(roots)

    async def _query_index_context(
        self,
        repo_full_name: str,
        branch: str,
        raw_prompt: str,
        task_type: str | None,
        domain: str | None,
        db: AsyncSession,
    ) -> str | None:
        """Query pre-built index for curated codebase context."""
        try:
            from app.services.repo_index_service import RepoIndexService
            svc = RepoIndexService(db, self._github_client, self._embedding_service)
            result = await svc.query_curated_context(
                repo_full_name, branch, raw_prompt,
                task_type=task_type, domain=domain,
            )
            if result:
                return result.context_text
        except Exception:
            logger.debug("Curated index retrieval failed", exc_info=True)
        return None

    async def _resolve_adaptation(
        self, db: AsyncSession, task_type: str,
    ) -> str | None:
        """Resolve adaptation state for the given task type."""
        try:
            from app.services.adaptation_tracker import AdaptationTracker
            tracker = AdaptationTracker(db)
            return await tracker.render_adaptation_state(task_type)
        except Exception:
            logger.debug("Adaptation state unavailable", exc_info=True)
        return None

    async def _resolve_patterns(
        self,
        raw_prompt: str,
        applied_pattern_ids: list[str] | None,
        db: AsyncSession,
    ) -> str | None:
        """Resolve applied meta-patterns via taxonomy engine or explicit IDs."""
        try:
            if applied_pattern_ids:
                from app.models import MetaPattern
                from sqlalchemy import select
                result = await db.execute(
                    select(MetaPattern).where(MetaPattern.id.in_(applied_pattern_ids))
                )
                patterns = result.scalars().all()
                if patterns:
                    return "\n".join(f"- {p.pattern_text}" for p in patterns)

            # Auto-inject from taxonomy engine via match_prompt()
            if self._taxonomy_engine and self._embedding_service:
                try:
                    from app.services.taxonomy.matching import match_prompt
                    match = await match_prompt(
                        raw_prompt, db, self._embedding_service,
                    )
                    if match and match.meta_patterns:
                        return "\n".join(
                            f"- {p.pattern_text}"
                            for p in match.meta_patterns[:3]
                            if p.pattern_text
                        )
                except Exception:
                    logger.debug("Taxonomy pattern search failed", exc_info=True)
        except Exception:
            logger.debug("Pattern resolution failed", exc_info=True)
        return None
```

- [ ] **Step 4: Add get/set helpers to _shared.py**

In `backend/app/tools/_shared.py`, add:

```python
from app.services.context_enrichment import ContextEnrichmentService

_context_service: ContextEnrichmentService | None = None

def set_context_service(svc: ContextEnrichmentService | None) -> None:
    global _context_service
    _context_service = svc

def get_context_service() -> ContextEnrichmentService:
    if _context_service is None:
        raise ValueError("Context enrichment service not initialized")
    return _context_service
```

Add to `__all__`.

- [ ] **Step 5: Run tests**

Run: `cd backend && source .venv/bin/activate && pytest tests/test_context_enrichment.py -v`
Expected: ALL PASS

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/context_enrichment.py backend/tests/test_context_enrichment.py backend/app/tools/_shared.py
git commit -m "feat: ContextEnrichmentService — unified enrichment for all tiers"
```

---

## Task 7: Wire Call Sites — Replace Inline Context Resolution

**Files:**
- Modify: `backend/app/routers/optimize.py`
- Modify: `backend/app/tools/optimize.py`
- Modify: `backend/app/tools/prepare.py`
- Modify: `backend/app/tools/refine.py`
- Modify: `backend/app/tools/_shared.py` (remove `resolve_workspace_guidance`)

This task replaces the inline context resolution in all 5 call sites with `ContextEnrichmentService.enrich()`. Each call site follows the same pattern:

```python
enrichment = await context_service.enrich(
    raw_prompt=..., tier=decision.tier, db=...,
    workspace_path=..., mcp_ctx=..., repo_full_name=...,
    applied_pattern_ids=..., preferences_snapshot=...,
)
```

Then pass `enrichment.workspace_guidance`, `enrichment.adaptation_state`, `enrichment.applied_patterns`, etc. to the pipeline or passthrough assembler.

- [ ] **Step 1: Update routers/optimize.py — main optimize endpoint**

Replace the inline scanner/adaptation calls in `POST /api/optimize` with a single `enrich()` call. For the passthrough path, also pass `enrichment.analysis.format_summary()` and `enrichment.codebase_context` to `assemble_passthrough_prompt()`. Populate the pending `Optimization` record with `enrichment.analysis.task_type/domain/intent_label` instead of hardcoded "general". Set `context_sources=enrichment.context_sources`.

- [ ] **Step 2: Update routers/optimize.py — passthrough_prepare endpoint**

Same pattern for `POST /api/optimize/passthrough`.

- [ ] **Step 3: Update tools/optimize.py — MCP optimize handler**

Replace `resolve_workspace_guidance()` call with `enrich()`. For passthrough branch, pass enriched analysis/context/patterns to `assemble_passthrough_prompt()`. For sampling/internal branches, pass `enrichment.workspace_guidance`, `enrichment.adaptation_state`, and `enrichment.applied_patterns`.

- [ ] **Step 4: Update tools/prepare.py — MCP prepare handler**

Replace `resolve_workspace_guidance()` and inline adaptation resolution with `enrich()`. Wire `repo_full_name` (currently dead parameter). Pass all enrichment layers to `assemble_passthrough_prompt()`.

- [ ] **Step 4.5: Update tools/refine.py — MCP refine handler**

Replace `resolve_workspace_guidance` import (line 21) with `get_context_service` from `_shared`. Replace the `resolve_workspace_guidance(ctx, workspace_path)` call with `get_context_service().enrich()` — refine only needs `enrichment.workspace_guidance` and `enrichment.adaptation_state` from the result.

- [ ] **Step 5: Remove resolve_workspace_guidance from _shared.py**

Delete the `resolve_workspace_guidance` function from `backend/app/tools/_shared.py`. Remove from `__all__`. The enrichment service now owns this logic.

- [ ] **Step 6: Run full test suite**

Run: `cd backend && source .venv/bin/activate && pytest tests/ -v --timeout=60`
Expected: ALL PASS (some tests may need mock adjustments for the new enrichment service dependency)

- [ ] **Step 7: Fix any broken tests**

Update mocks in `test_mcp_synthesis_optimize.py`, `test_mcp_refine.py`, `test_passthrough.py`, and `test_routers.py` to account for the new `ContextEnrichmentService` dependency.

- [ ] **Step 8: Commit**

```bash
git add backend/app/routers/optimize.py backend/app/tools/optimize.py backend/app/tools/prepare.py backend/app/tools/refine.py backend/app/tools/_shared.py backend/tests/
git commit -m "feat: wire all call sites to unified ContextEnrichmentService"
```

---

## Task 8: Initialize ContextEnrichmentService at Startup

**Files:**
- Modify: `backend/app/main.py` (FastAPI lifespan)
- Modify: `backend/app/mcp_server.py` (MCP lifespan)

- [ ] **Step 1: Add to FastAPI lifespan**

In `backend/app/main.py`'s lifespan, after creating the routing manager:

```python
from app.services.context_enrichment import ContextEnrichmentService
from app.services.heuristic_analyzer import HeuristicAnalyzer
from app.services.workspace_intelligence import WorkspaceIntelligence
from app.services.embedding_service import EmbeddingService
from app.services.github_client import GitHubClient

context_service = ContextEnrichmentService(
    prompts_dir=PROMPTS_DIR,
    data_dir=DATA_DIR,
    workspace_intel=WorkspaceIntelligence(),
    embedding_service=EmbeddingService(),
    heuristic_analyzer=HeuristicAnalyzer(),
    github_client=GitHubClient(),
    taxonomy_engine=taxonomy_engine,
)
app.state.context_service = context_service
```

- [ ] **Step 2: Add to MCP lifespan**

In `backend/app/mcp_server.py`'s lifespan:

```python
from app.services.context_enrichment import ContextEnrichmentService
from app.services.heuristic_analyzer import HeuristicAnalyzer
from app.tools._shared import set_context_service

context_service = ContextEnrichmentService(
    prompts_dir=PROMPTS_DIR,
    data_dir=DATA_DIR,
    workspace_intel=workspace_intel,
    embedding_service=embedding_service,
    heuristic_analyzer=HeuristicAnalyzer(),
    github_client=github_client,
    taxonomy_engine=taxonomy_engine,
)
set_context_service(context_service)
```

- [ ] **Step 3: Verify server starts**

Run: `cd /home/drei/my_project/builder/claude-quickstarts/autonomous-coding/generations/PromptForge_v2 && ./init.sh restart && sleep 3 && ./init.sh status`
Expected: All services running

- [ ] **Step 4: Commit**

```bash
git add backend/app/main.py backend/app/mcp_server.py
git commit -m "feat: initialize ContextEnrichmentService at startup"
```

---

## Task 9: End-to-End Verification & Changelog

**Files:**
- Test: run full suite
- Modify: `docs/CHANGELOG.md`

- [ ] **Step 1: Run full test suite**

Run: `cd backend && source .venv/bin/activate && pytest tests/ -v --timeout=60 2>&1 | tail -30`
Expected: ALL PASS

- [ ] **Step 2: Add DB persistence verification tests**

Add to `backend/tests/test_context_enrichment.py`:

```python
@pytest.mark.asyncio
async def test_passthrough_enrichment_populates_db_fields(db_session):
    """Verify heuristic analysis fields would persist correctly to Optimization."""
    svc = _build_service()
    enrichment = await svc.enrich(
        raw_prompt="Implement a FastAPI REST endpoint with JWT authentication",
        tier="passthrough", db=db_session,
    )
    # Heuristic analysis should classify — not default to "general"
    assert enrichment.analysis is not None
    assert enrichment.analysis.task_type == "coding"
    assert enrichment.analysis.domain in ("backend", "fullstack")
    assert enrichment.analysis.intent_label != ""

    # context_sources should track what was resolved
    assert "workspace_guidance" in enrichment.context_sources or "analysis" in enrichment.context_sources

    # Simulate DB persistence: external LLM values take precedence
    opt_task_type = "writing"  # External LLM override
    effective = opt_task_type or enrichment.analysis.task_type or "general"
    assert effective == "writing"  # External wins

    # Fallback: no external override → heuristic value
    opt_task_type_none = None
    effective2 = opt_task_type_none or enrichment.analysis.task_type or "general"
    assert effective2 == "coding"  # Heuristic fills in
```

- [ ] **Step 3: Verify passthrough enrichment manually**

Start servers and test via curl:
```bash
curl -X POST http://localhost:8000/api/optimize \
  -H 'Content-Type: application/json' \
  -d '{"prompt": "Implement a FastAPI endpoint for user registration with email validation and password hashing"}'
```

If tier is passthrough, the response should show analysis_summary and enriched context in the assembled prompt.

- [ ] **Step 4: Update CHANGELOG.md**

Add under `## Unreleased`:

```markdown
### Added
- Unified `ContextEnrichmentService` replacing 5 scattered context resolution call sites
- `HeuristicAnalyzer` for zero-LLM passthrough classification (task_type, domain, weaknesses, strengths, strategy recommendation)
- Enhanced `RootsScanner` with manifest-aware subdirectory discovery and content deduplication
- Augmented `RepoIndexService` with structured file outlines (type-aware extraction) and curated retrieval (`query_curated_context`)
- Passthrough tier now receives analysis summary, codebase context from pre-built index, applied meta-patterns, and task-specific adaptation state
- New guidance files discovered: GEMINI.md, .clinerules, CONVENTIONS.md
- New config settings: INDEX_OUTLINE_MAX_CHARS, INDEX_CURATED_MAX_CHARS, INDEX_CURATED_MIN_SIMILARITY, INDEX_CURATED_MAX_PER_DIR, INDEX_DOMAIN_BOOST

### Changed
- REST optimize endpoint now uses `WorkspaceIntelligence` (previously bypassed it with raw `RootsScanner`)
- Passthrough optimizations persist `task_type`, `domain`, `intent_label`, and `context_sources` from heuristic analysis (previously hardcoded "general")
- `WorkspaceIntelligence._detect_stack()` uses `discover_project_dirs()` for subdir scanning
- `passthrough.md` template expanded with analysis, codebase context, and patterns sections

### Removed
- `resolve_workspace_guidance()` from `tools/_shared.py` (moved into ContextEnrichmentService)
```

- [ ] **Step 5: Commit**

```bash
git add docs/CHANGELOG.md
git commit -m "docs: add context enrichment changelog entries"
```
