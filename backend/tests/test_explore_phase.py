"""Tests for the rewritten explore phase (semantic retrieval + single-shot synthesis)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.codebase_explorer import (
    CodebaseContext,
    _deduplicate_files,
    _format_files_for_llm,
    _get_anchor_paths,
    _keyword_fallback,
    _normalize_snippets,
    _normalize_string_list,
)
from app.services.repo_index_service import IndexStatus, RankedFile


class TestAnchorPaths:
    """Test deterministic anchor file detection."""

    def test_finds_readme(self):
        tree = [{"path": "README.md"}, {"path": "src/main.py"}]
        anchors = _get_anchor_paths(tree)
        assert "README.md" in anchors

    def test_finds_manifests(self):
        tree = [
            {"path": "package.json"},
            {"path": "Dockerfile"},
            {"path": "src/index.ts"},
        ]
        anchors = _get_anchor_paths(tree)
        assert "package.json" in anchors
        assert "Dockerfile" in anchors

    def test_ignores_non_anchor_files(self):
        tree = [{"path": "src/utils.py"}, {"path": "tests/test_foo.py"}]
        anchors = _get_anchor_paths(tree)
        assert len(anchors) == 0

    def test_finds_nested_anchor(self):
        """Anchor detection uses filename, not full path."""
        tree = [{"path": "docs/README.md"}]
        anchors = _get_anchor_paths(tree)
        assert "docs/README.md" in anchors


class TestDeduplicateFiles:
    """Test file deduplication and capping."""

    def test_basic_dedup(self):
        ranked = [
            RankedFile(path="src/auth.py", score=0.9),
            RankedFile(path="src/main.py", score=0.8),
        ]
        anchors = ["README.md", "package.json"]
        result = _deduplicate_files(ranked, anchors, cap=10)

        # Anchors first, then ranked
        assert result[0] == "README.md"
        assert result[1] == "package.json"
        assert "src/auth.py" in result
        assert "src/main.py" in result

    def test_dedup_overlap(self):
        """Overlapping files between ranked and anchors are deduplicated."""
        ranked = [
            RankedFile(path="README.md", score=0.9),  # also an anchor
            RankedFile(path="src/auth.py", score=0.8),
        ]
        anchors = ["README.md"]
        result = _deduplicate_files(ranked, anchors, cap=10)

        # README.md should only appear once
        assert result.count("README.md") == 1
        assert len(result) == 2

    def test_cap_enforced(self):
        ranked = [RankedFile(path=f"file_{i}.py", score=0.5) for i in range(50)]
        anchors = ["README.md"]
        result = _deduplicate_files(ranked, anchors, cap=5)
        assert len(result) == 5


class TestKeywordFallback:
    """Test keyword-based file ranking."""

    def test_matches_keywords_in_path(self):
        tree = [
            {"path": "src/auth/middleware.py", "sha": "a", "size_bytes": 100},
            {"path": "src/database.py", "sha": "b", "size_bytes": 100},
            {"path": "tests/test_auth.py", "sha": "c", "size_bytes": 100},
        ]
        results = _keyword_fallback(tree, "authentication middleware handler")

        # auth/middleware should score highest
        assert len(results) > 0
        paths = [r.path for r in results]
        assert "src/auth/middleware.py" in paths

    def test_empty_prompt(self):
        tree = [{"path": "src/main.py", "sha": "a", "size_bytes": 100}]
        results = _keyword_fallback(tree, "")
        assert results == []

    def test_no_matches(self):
        tree = [{"path": "src/main.py", "sha": "a", "size_bytes": 100}]
        results = _keyword_fallback(tree, "authentication")
        assert results == []

    def test_stopwords_filtered(self):
        tree = [
            {"path": "src/the_handler.py", "sha": "a", "size_bytes": 100},
            {"path": "src/auth.py", "sha": "b", "size_bytes": 100},
        ]
        # "the" is a stopword, "auth" is a keyword
        results = _keyword_fallback(tree, "the auth handler")
        paths = [r.path for r in results]
        assert "src/auth.py" in paths


class TestExploreFlow:
    """Test the full explore flow with mocked dependencies."""

    @pytest.mark.asyncio
    async def test_cache_hit_returns_immediately(self):
        """Cached result is returned without any GitHub or LLM calls."""
        from app.services.codebase_explorer import run_explore

        cached_result = {
            "tech_stack": ["Python"],
            "key_files_read": ["main.py"],
            "relevant_snippets": [],
            "observations": ["cached"],
            "grounding_notes": [],
            "coverage_pct": 10,
            "files_read_count": 1,
            "explore_quality": "complete",
        }

        mock_cache = MagicMock()
        mock_cache.get = AsyncMock(return_value=cached_result)

        mock_provider = MagicMock()

        with (
            patch("app.services.codebase_explorer.get_cache", return_value=mock_cache),
            patch("anyio.to_thread.run_sync", new_callable=AsyncMock),  # branch check
        ):
            events = []
            async for event in run_explore(
                provider=mock_provider,
                raw_prompt="test prompt",
                repo_full_name="owner/repo",
                repo_branch="main",
                github_token="fake-token",
            ):
                events.append(event)

        # Should have explore_result event with cached data
        result_events = [e for e in events if e[0] == "explore_result"]
        assert len(result_events) == 1
        assert result_events[0][1] == cached_result

        # LLM should NOT have been called
        mock_provider.complete_json.assert_not_called()

    @pytest.mark.asyncio
    async def test_token_resolution_error(self):
        """Missing token yields a failed explore_result."""
        from app.services.codebase_explorer import run_explore

        mock_provider = MagicMock()

        events = []
        async for event in run_explore(
            provider=mock_provider,
            raw_prompt="test",
            repo_full_name="owner/repo",
            repo_branch="main",
            # No token or session_id provided
        ):
            events.append(event)

        result_events = [e for e in events if e[0] == "explore_result"]
        assert len(result_events) == 1
        assert result_events[0][1]["explore_quality"] == "failed"
        assert result_events[0][1]["explore_failed"] is True

    @pytest.mark.asyncio
    async def test_sse_event_sequence(self):
        """Explore emits the expected SSE event types in order."""
        from app.services.codebase_explorer import run_explore

        mock_provider = MagicMock()
        mock_provider.complete_json = AsyncMock(return_value={
            "tech_stack": ["Python"],
            "key_files_read": ["main.py"],
            "relevant_code_snippets": [],
            "codebase_observations": ["test observation"],
            "prompt_grounding_notes": ["test grounding"],
        })

        mock_tree = [
            {"path": "README.md", "sha": "abc", "size_bytes": 500},
            {"path": "main.py", "sha": "def", "size_bytes": 200},
        ]

        mock_index_status = IndexStatus(status="none")

        with (
            patch("anyio.to_thread.run_sync", new_callable=AsyncMock),
            patch("app.services.codebase_explorer.get_cache", return_value=None),
            patch(
                "app.services.codebase_explorer.get_repo_tree",
                new_callable=AsyncMock,
                return_value=mock_tree,
            ),
            patch(
                "app.services.codebase_explorer.get_repo_index_service"
            ) as mock_idx_svc,
            patch(
                "app.services.codebase_explorer.read_file_content",
                new_callable=AsyncMock,
                return_value="# README\nHello",
            ),
        ):
            mock_idx_svc.return_value.get_index_status = AsyncMock(
                return_value=mock_index_status
            )

            events = []
            async for event in run_explore(
                provider=mock_provider,
                raw_prompt="test prompt",
                repo_full_name="owner/repo",
                repo_branch="main",
                github_token="fake-token",
            ):
                events.append(event)

        event_types = [e[0] for e in events]

        # Should have progress events and final result
        assert "agent_text" in event_types
        assert "tool_call" in event_types
        assert "explore_result" in event_types

        # explore_result should be last
        assert event_types[-1] == "explore_result"

        # The result should have the expected structure
        result = events[-1][1]
        assert "tech_stack" in result
        assert "key_files_read" in result
        assert "observations" in result
        assert "explore_quality" in result


class TestCodebaseContext:
    """Test CodebaseContext dataclass."""

    def test_default_values(self):
        ctx = CodebaseContext()
        assert ctx.repo == ""
        assert ctx.branch == "main"
        assert ctx.tech_stack == []
        assert ctx.explore_quality == "complete"

    def test_custom_values(self):
        ctx = CodebaseContext(
            repo="owner/repo",
            branch="develop",
            tech_stack=["Python", "FastAPI"],
            explore_quality="partial",
        )
        assert ctx.repo == "owner/repo"
        assert ctx.branch == "develop"
        assert len(ctx.tech_stack) == 2


class TestNormalizeStringList:
    """Test _normalize_string_list coercion of LLM output."""

    def test_none_returns_empty(self):
        assert _normalize_string_list(None) == []

    def test_single_string_wraps(self):
        assert _normalize_string_list("hello") == ["hello"]

    def test_list_of_strings_passthrough(self):
        assert _normalize_string_list(["a", "b"]) == ["a", "b"]

    def test_dict_extracts_detail_key(self):
        result = _normalize_string_list([{"detail": "found it"}])
        assert result == ["found it"]

    def test_dict_extracts_text_key(self):
        result = _normalize_string_list([{"text": "note here"}])
        assert result == ["note here"]

    def test_dict_extracts_description_key(self):
        result = _normalize_string_list([{"description": "desc"}])
        assert result == ["desc"]

    def test_dict_priority_order(self):
        """'detail' key takes priority over 'text'."""
        result = _normalize_string_list([{"detail": "first", "text": "second"}])
        assert result == ["first"]

    def test_dict_fallback_joins_values(self):
        result = _normalize_string_list([{"unknown_key": "z", "other": "w"}])
        assert len(result) == 1
        assert "z" in result[0]
        assert "w" in result[0]

    def test_dict_with_empty_values_skipped(self):
        """Dict with all falsy values produces nothing."""
        result = _normalize_string_list([{"a": "", "b": 0, "c": None}])
        assert result == []

    def test_mixed_list(self):
        result = _normalize_string_list(["a", {"detail": "b"}, 42])
        assert result == ["a", "b", "42"]

    def test_nested_list_joins(self):
        result = _normalize_string_list([["hello", "world"]])
        assert result == ["hello world"]

    def test_numeric_input(self):
        assert _normalize_string_list(42) == ["42"]

    def test_empty_list(self):
        assert _normalize_string_list([]) == []


class TestNormalizeSnippets:
    """Test _normalize_snippets coercion of LLM snippet output."""

    def test_none_returns_empty(self):
        assert _normalize_snippets(None) == []

    def test_not_list_returns_empty(self):
        assert _normalize_snippets("not a list") == []
        assert _normalize_snippets(42) == []

    def test_standard_keys_passthrough(self):
        raw = [{"file": "a.py", "lines": "1-5", "context": "code here"}]
        result = _normalize_snippets(raw)
        assert len(result) == 1
        assert result[0] == {"file": "a.py", "lines": "1-5", "context": "code here"}

    def test_alternate_keys_remapped(self):
        raw = [{"path": "b.py", "line_range": "10-20", "description": "desc"}]
        result = _normalize_snippets(raw)
        assert result[0]["file"] == "b.py"
        assert result[0]["lines"] == "10-20"
        assert result[0]["context"] == "desc"

    def test_missing_keys_default(self):
        raw = [{"file": "c.py"}]
        result = _normalize_snippets(raw)
        assert result[0]["lines"] == ""
        assert result[0]["context"] == ""

    def test_bare_string_wrapped(self):
        raw = ["some code snippet"]
        result = _normalize_snippets(raw)
        assert result[0] == {"file": "unknown", "lines": "", "context": "some code snippet"}

    def test_empty_list(self):
        assert _normalize_snippets([]) == []

    def test_mixed_dicts_and_strings(self):
        raw = [
            {"file": "a.py", "context": "code"},
            "bare string",
        ]
        result = _normalize_snippets(raw)
        assert len(result) == 2
        assert result[0]["file"] == "a.py"
        assert result[1]["file"] == "unknown"


class TestFormatFilesForLlm:
    """Test line-numbered file formatting."""

    def test_adds_line_numbers(self):
        contents = {"main.py": "import os\nprint('hello')"}
        result = _format_files_for_llm(contents)
        assert "   1 | import os" in result
        assert "   2 | print('hello')" in result

    def test_file_header_preserved(self):
        contents = {"src/app.py": "x = 1"}
        result = _format_files_for_llm(contents)
        assert "=== src/app.py ===" in result

    def test_empty_lines_get_numbers(self):
        contents = {"f.py": "a\n\nb"}
        result = _format_files_for_llm(contents)
        assert "   1 | a" in result
        assert "   2 | " in result
        assert "   3 | b" in result

    def test_multiple_files(self):
        contents = {"a.py": "x = 1", "b.py": "y = 2"}
        result = _format_files_for_llm(contents)
        assert "=== a.py ===" in result
        assert "=== b.py ===" in result
        assert "   1 | x = 1" in result
        assert "   1 | y = 2" in result


class TestBatchReadFilesTruncation:
    """Test that truncation message warns the LLM not to reference beyond cutoff."""

    @pytest.mark.asyncio
    async def test_truncation_message_warns_against_claims(self):
        """Truncated files warn LLM not to reference lines beyond the cutoff."""
        long_content = "\n".join(f"line {i}" for i in range(1, 101))  # 100 lines

        with patch(
            "app.services.codebase_explorer.read_file_content",
            new_callable=AsyncMock,
            return_value=long_content,
        ):
            from app.services.codebase_explorer import _batch_read_files

            tree = [{"path": "big.py", "sha": "abc"}]
            result = await _batch_read_files("tok", "o/r", tree, ["big.py"], max_lines_per_file=10)

        content = result["big.py"]
        assert "Do NOT reference or make claims about lines beyond 10" in content
        assert "TRUNCATED" in content

    @pytest.mark.asyncio
    async def test_no_truncation_for_short_files(self):
        """Short files are not truncated."""
        short_content = "line 1\nline 2\nline 3"

        with patch(
            "app.services.codebase_explorer.read_file_content",
            new_callable=AsyncMock,
            return_value=short_content,
        ):
            from app.services.codebase_explorer import _batch_read_files

            tree = [{"path": "small.py", "sha": "abc"}]
            result = await _batch_read_files("tok", "o/r", tree, ["small.py"], max_lines_per_file=10)

        content = result["small.py"]
        assert "TRUNCATED" not in content
