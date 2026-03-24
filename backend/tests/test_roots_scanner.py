"""Tests for RootsScanner (Phase 1b — agent guidance file discovery)."""

from pathlib import Path

from app.services.roots_scanner import (
    MAX_CHARS_PER_FILE,
    MAX_LINES_PER_FILE,
    RootsScanner,
    discover_project_dirs,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_file(root: Path, rel: str, content: str) -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# 1. test_discovers_guidance_files
# ---------------------------------------------------------------------------

class TestDiscover:
    def test_discovers_guidance_files(self, tmp_path: Path):
        _make_file(tmp_path, "CLAUDE.md", "# guidance")
        _make_file(tmp_path, "AGENTS.md", "# agents")
        _make_file(tmp_path, ".cursorrules", "rules here")
        _make_file(tmp_path, "main.py", "# not a guidance file")

        scanner = RootsScanner()
        found = scanner.discover(tmp_path)
        names = [p.name for p in found]

        assert "CLAUDE.md" in names
        assert "AGENTS.md" in names
        assert ".cursorrules" in names
        assert "main.py" not in names

    def test_discovers_only_existing_files(self, tmp_path: Path):
        _make_file(tmp_path, "CLAUDE.md", "# guidance")
        # AGENTS.md and others do NOT exist

        scanner = RootsScanner()
        found = scanner.discover(tmp_path)
        assert len(found) == 1
        assert found[0].name == "CLAUDE.md"


# ---------------------------------------------------------------------------
# 2. test_reads_and_concatenates
# ---------------------------------------------------------------------------

class TestScan:
    def test_reads_and_concatenates(self, tmp_path: Path):
        _make_file(tmp_path, "CLAUDE.md", "claude content")
        _make_file(tmp_path, "AGENTS.md", "agents content")

        scanner = RootsScanner()
        result = scanner.scan(tmp_path)

        assert result is not None
        assert "claude content" in result
        assert "agents content" in result

    # -----------------------------------------------------------------------
    # 3. test_per_file_cap_lines
    # -----------------------------------------------------------------------

    def test_per_file_cap_lines(self, tmp_path: Path):
        lines = "\n".join(f"line {i}" for i in range(600))  # 600 lines
        _make_file(tmp_path, "CLAUDE.md", lines)

        scanner = RootsScanner()
        result = scanner.scan(tmp_path)

        assert result is not None
        # Reconstruct what should be in the output — only first 500 lines
        expected_last = f"line {MAX_LINES_PER_FILE - 1}"
        unexpected = f"line {MAX_LINES_PER_FILE}"  # line 500 should be cut
        assert expected_last in result
        assert unexpected not in result

    # -----------------------------------------------------------------------
    # 4. test_per_file_cap_chars
    # -----------------------------------------------------------------------

    def test_per_file_cap_chars(self, tmp_path: Path):
        big_content = "x" * 15_000  # 15K chars > 10K limit
        _make_file(tmp_path, "CLAUDE.md", big_content)

        scanner = RootsScanner()
        result = scanner.scan(tmp_path)

        assert result is not None
        # Truncation must happen at exactly MAX_CHARS_PER_FILE
        assert "x" * MAX_CHARS_PER_FILE in result
        assert "x" * (MAX_CHARS_PER_FILE + 1) not in result

    # -----------------------------------------------------------------------
    # 5. test_wraps_in_untrusted_context
    # -----------------------------------------------------------------------

    def test_wraps_in_untrusted_context(self, tmp_path: Path):
        _make_file(tmp_path, "CLAUDE.md", "hello")
        _make_file(tmp_path, "AGENTS.md", "world")

        scanner = RootsScanner()
        result = scanner.scan(tmp_path)

        assert result is not None
        assert '<untrusted-context source="CLAUDE.md">' in result
        assert '<untrusted-context source="AGENTS.md">' in result
        assert "</untrusted-context>" in result

    # -----------------------------------------------------------------------
    # 6. test_empty_workspace
    # -----------------------------------------------------------------------

    def test_empty_workspace(self, tmp_path: Path):
        scanner = RootsScanner()
        result = scanner.scan(tmp_path)
        assert result is None

    # -----------------------------------------------------------------------
    # 7. test_nonexistent_path
    # -----------------------------------------------------------------------

    def test_nonexistent_path(self, tmp_path: Path):
        nonexistent = tmp_path / "does_not_exist"
        scanner = RootsScanner()
        result = scanner.scan(nonexistent)
        assert result is None

    # -----------------------------------------------------------------------
    # 8. test_github_copilot_instructions
    # -----------------------------------------------------------------------

    def test_github_copilot_instructions(self, tmp_path: Path):
        _make_file(tmp_path, ".github/copilot-instructions.md", "copilot rules")

        scanner = RootsScanner()
        found = scanner.discover(tmp_path)
        names = [p.name for p in found]
        assert "copilot-instructions.md" in names

        result = scanner.scan(tmp_path)
        assert result is not None
        assert "copilot rules" in result
        assert '<untrusted-context source=".github/copilot-instructions.md">' in result

    # -----------------------------------------------------------------------
    # 9. test_windsurfrules
    # -----------------------------------------------------------------------

    def test_windsurfrules(self, tmp_path: Path):
        _make_file(tmp_path, ".windsurfrules", "windsurf config")

        scanner = RootsScanner()
        found = scanner.discover(tmp_path)
        names = [p.name for p in found]
        assert ".windsurfrules" in names

        result = scanner.scan(tmp_path)
        assert result is not None
        assert "windsurf config" in result
        assert '<untrusted-context source=".windsurfrules">' in result

    # -----------------------------------------------------------------------
    # 10. test_total_output_capped
    # -----------------------------------------------------------------------

    def test_total_output_capped(self, tmp_path: Path):
        max_total = 5_000
        # Each file at 4K — two of them would exceed the 5K total cap
        _make_file(tmp_path, "CLAUDE.md", "A" * 4_000)
        _make_file(tmp_path, "AGENTS.md", "B" * 4_000)

        scanner = RootsScanner(max_total_chars=max_total)
        result = scanner.scan(tmp_path)

        assert result is not None
        # The combined raw content must not exceed max_total
        # Extract content between tags (rough check via total length)
        # Wrapper tags add some overhead; allow generous slack
        assert len(result) <= max_total + 200

    # -----------------------------------------------------------------------
    # scan_roots — multiple roots
    # -----------------------------------------------------------------------

    def test_scan_roots_aggregates(self, tmp_path: Path):
        root1 = tmp_path / "proj1"
        root2 = tmp_path / "proj2"
        root1.mkdir()
        root2.mkdir()
        _make_file(root1, "CLAUDE.md", "from root1")
        _make_file(root2, "AGENTS.md", "from root2")

        scanner = RootsScanner()
        result = scanner.scan_roots([root1, root2])

        assert result is not None
        assert "from root1" in result
        assert "from root2" in result

    def test_scan_roots_all_empty_returns_none(self, tmp_path: Path):
        root1 = tmp_path / "empty1"
        root2 = tmp_path / "empty2"
        root1.mkdir()
        root2.mkdir()

        scanner = RootsScanner()
        result = scanner.scan_roots([root1, root2])
        assert result is None


# ---------------------------------------------------------------------------
# TestDiscoverProjectDirs
# ---------------------------------------------------------------------------

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
        dirs = discover_project_dirs(Path("/nonexistent/path"))
        assert dirs == []


# ---------------------------------------------------------------------------
# TestSubdirScanning
# ---------------------------------------------------------------------------

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
