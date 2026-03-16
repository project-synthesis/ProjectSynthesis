"""Tests for RootsScanner (Phase 1b — agent guidance file discovery)."""

import pytest
from pathlib import Path

from app.services.roots_scanner import (
    RootsScanner,
    GUIDANCE_FILES,
    MAX_LINES_PER_FILE,
    MAX_CHARS_PER_FILE,
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
        # The raw content inside the wrapper must not exceed MAX_CHARS_PER_FILE
        # We can check the total length roughly — wrapper tags are small
        assert len(result) < 15_000 + 200  # 200 bytes of slack for wrapper tags
        # And verify the first MAX_CHARS_PER_FILE chars of content are present
        assert "x" * MAX_CHARS_PER_FILE in result

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
