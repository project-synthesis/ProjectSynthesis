"""Tests for WorkspaceIntelligence (workspace analysis + tech stack detection)."""

import json
from pathlib import Path
from unittest.mock import patch

from app.services.workspace_intelligence import WorkspaceIntelligence

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_file(root: Path, rel: str, content: str) -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# 1. test_detect_python_project
# ---------------------------------------------------------------------------

class TestDetectPythonProject:
    def test_detect_python_project(self, tmp_path: Path):
        """requirements.txt with fastapi+sqlalchemy detected correctly."""
        _make_file(tmp_path, "requirements.txt", "fastapi\nsqlalchemy\naiosqlite\n")

        intel = WorkspaceIntelligence()
        profile = intel.analyze([tmp_path])

        assert profile is not None
        assert "Python" in profile
        assert "FastAPI" in profile
        assert "SQLAlchemy" in profile


# ---------------------------------------------------------------------------
# 2. test_detect_node_project
# ---------------------------------------------------------------------------

class TestDetectNodeProject:
    def test_detect_node_project(self, tmp_path: Path):
        """package.json with svelte+tailwindcss detected correctly."""
        pkg = {
            "devDependencies": {
                "svelte": "5.0",
                "tailwindcss": "4.0",
            }
        }
        _make_file(tmp_path, "package.json", json.dumps(pkg))

        intel = WorkspaceIntelligence()
        profile = intel.analyze([tmp_path])

        assert profile is not None
        assert "JavaScript/TypeScript" in profile
        assert "Svelte" in profile
        assert "Tailwind CSS" in profile


# ---------------------------------------------------------------------------
# 3. test_detect_multi_stack
# ---------------------------------------------------------------------------

class TestDetectMultiStack:
    def test_detect_multi_stack(self, tmp_path: Path):
        """Both requirements.txt and package.json — both stacks detected."""
        _make_file(tmp_path, "requirements.txt", "fastapi\npytest\n")
        pkg = {"dependencies": {"@sveltejs/kit": "2.0", "svelte": "5.0"}}
        _make_file(tmp_path, "package.json", json.dumps(pkg))

        intel = WorkspaceIntelligence()
        profile = intel.analyze([tmp_path])

        assert profile is not None
        assert "Python" in profile
        assert "JavaScript/TypeScript" in profile
        assert "FastAPI" in profile
        assert "SvelteKit" in profile


# ---------------------------------------------------------------------------
# 4. test_includes_guidance_files
# ---------------------------------------------------------------------------

class TestIncludesGuidanceFiles:
    def test_includes_guidance_files(self, tmp_path: Path):
        """CLAUDE.md content appears in the profile output."""
        _make_file(tmp_path, "CLAUDE.md", "# Project Guidance\nUse async patterns.")
        _make_file(tmp_path, "requirements.txt", "fastapi\n")

        intel = WorkspaceIntelligence()
        profile = intel.analyze([tmp_path])

        assert profile is not None
        assert "Project Guidance" in profile
        assert "Use async patterns" in profile
        assert "<workspace-profile>" in profile
        assert "</workspace-profile>" in profile


# ---------------------------------------------------------------------------
# 5. test_caches_by_roots
# ---------------------------------------------------------------------------

class TestCachesByRoots:
    def test_caches_by_roots(self, tmp_path: Path):
        """Second call with same roots returns cached result (no re-scan)."""
        _make_file(tmp_path, "requirements.txt", "fastapi\n")

        intel = WorkspaceIntelligence()

        # First call — populates cache
        profile1 = intel.analyze([tmp_path])
        assert profile1 is not None

        # Patch _detect_stack to verify it's NOT called again
        with patch.object(intel, "_detect_stack", wraps=intel._detect_stack) as mock_detect:
            profile2 = intel.analyze([tmp_path])
            mock_detect.assert_not_called()

        assert profile1 == profile2


# ---------------------------------------------------------------------------
# 6. test_empty_roots
# ---------------------------------------------------------------------------

class TestEmptyRoots:
    def test_empty_roots(self):
        """Empty root list returns None."""
        intel = WorkspaceIntelligence()
        assert intel.analyze([]) is None
