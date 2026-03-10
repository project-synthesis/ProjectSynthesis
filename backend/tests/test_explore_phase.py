"""Tests for the rewritten explore phase (semantic retrieval + single-shot synthesis)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.codebase_explorer import (
    CodebaseContext,
    _extract_prompt_referenced_files,
    _format_files_for_llm,
    _get_anchor_paths,
    _keyword_fallback,
    _merge_file_lists,
    _normalize_snippets,
    _normalize_string_list,
    _validate_explore_output,
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
    """Test file deduplication and capping (now via _merge_file_lists)."""

    def test_basic_dedup(self):
        ranked = [
            RankedFile(path="src/auth.py", score=0.9),
            RankedFile(path="src/main.py", score=0.8),
        ]
        anchors = ["README.md", "package.json"]
        result = _merge_file_lists([], anchors, ranked, cap=10)

        # Anchors first, then ranked
        assert result[0] == "README.md"
        assert result[1] == "package.json"
        assert "src/auth.py" in result
        assert "src/main.py" in result

    def test_dedup_overlap(self):
        ranked = [
            RankedFile(path="README.md", score=0.9),
            RankedFile(path="src/auth.py", score=0.8),
        ]
        anchors = ["README.md"]
        result = _merge_file_lists([], anchors, ranked, cap=10)

        assert result.count("README.md") == 1
        assert len(result) == 2

    def test_cap_enforced(self):
        ranked = [RankedFile(path=f"file_{i}.py", score=0.5) for i in range(50)]
        anchors = ["README.md"]
        result = _merge_file_lists([], anchors, ranked, cap=5)
        assert len(result) == 5


class TestMergeFileLists:
    """Test 3-tier priority file merge."""

    def test_priority_order(self):
        result = _merge_file_lists(
            prompt_referenced=["pipeline.py"],
            anchors=["README.md"],
            semantic_ranked=["utils.py"],
            cap=10,
        )
        assert result[0] == "pipeline.py"
        assert result[1] == "README.md"
        assert result[2] == "utils.py"

    def test_dedup_across_tiers(self):
        result = _merge_file_lists(
            prompt_referenced=["README.md"],
            anchors=["README.md", "Dockerfile"],
            semantic_ranked=["README.md", "utils.py"],
            cap=10,
        )
        assert result.count("README.md") == 1
        assert len(result) == 3

    def test_cap_trims_semantic_first(self):
        result = _merge_file_lists(
            prompt_referenced=["a.py", "b.py"],
            anchors=["README.md"],
            semantic_ranked=["c.py", "d.py", "e.py"],
            cap=4,
        )
        assert len(result) == 4
        assert "a.py" in result
        assert "b.py" in result
        assert "README.md" in result

    def test_prompt_referenced_never_trimmed(self):
        result = _merge_file_lists(
            prompt_referenced=["a.py", "b.py", "c.py"],
            anchors=["README.md"],
            semantic_ranked=["d.py"],
            cap=3,
        )
        assert len(result) == 3
        assert all(f in result for f in ["a.py", "b.py", "c.py"])


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


class TestDynamicBudget:
    """Test dynamic line budget calculation."""

    def test_few_files_get_max_lines(self):
        """With few files, each gets the max lines per file."""
        from app.config import settings
        file_count = 10
        max_lines = min(
            settings.EXPLORE_MAX_LINES_PER_FILE,
            settings.EXPLORE_TOTAL_LINE_BUDGET // max(1, file_count),
        )
        assert max_lines == settings.EXPLORE_MAX_LINES_PER_FILE  # 500 < 15000/10=1500

    def test_many_files_get_budget_share(self):
        """With many files, lines per file is budget/count."""
        from app.config import settings
        file_count = 40
        max_lines = min(
            settings.EXPLORE_MAX_LINES_PER_FILE,
            settings.EXPLORE_TOTAL_LINE_BUDGET // max(1, file_count),
        )
        assert max_lines == 375  # 15000/40 = 375 < 500

    def test_zero_files_no_crash(self):
        """Zero files doesn't divide by zero."""
        from app.config import settings
        max_lines = min(
            settings.EXPLORE_MAX_LINES_PER_FILE,
            settings.EXPLORE_TOTAL_LINE_BUDGET // max(1, 0),
        )
        assert max_lines == settings.EXPLORE_MAX_LINES_PER_FILE


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


class TestExtractPromptReferencedFiles:
    """Test tree-validated prompt file extraction."""

    def _tree(self, paths):
        return [{"path": p, "sha": "abc", "size_bytes": 100} for p in paths]

    def test_exact_path_match(self):
        tree = self._tree(["backend/app/services/pipeline.py", "README.md"])
        prompt = "Audit backend/app/services/pipeline.py for handoff issues"
        result = _extract_prompt_referenced_files(prompt, tree)
        assert "backend/app/services/pipeline.py" in result

    def test_filename_match(self):
        tree = self._tree(["src/pipeline.py", "tests/test_pipeline.py"])
        prompt = "Check pipeline.py for bugs"
        result = _extract_prompt_referenced_files(prompt, tree)
        assert "src/pipeline.py" in result

    def test_ambiguous_filename_skipped(self):
        tree = self._tree([f"pkg{i}/index.ts" for i in range(5)])
        prompt = "Fix index.ts"
        result = _extract_prompt_referenced_files(prompt, tree)
        assert result == []  # >3 matches, skipped

    def test_url_excluded(self):
        tree = self._tree(["src/config.py"])
        prompt = "See https://example.com/config.py for details"
        result = _extract_prompt_referenced_files(prompt, tree)
        # config.py should NOT match from a URL context
        assert result == []

    def test_backslash_normalized(self):
        tree = self._tree(["src/app/main.py"])
        prompt = r"Check src\app\main.py"
        result = _extract_prompt_referenced_files(prompt, tree)
        assert "src/app/main.py" in result

    def test_module_stem_match(self):
        tree = self._tree(["backend/app/services/optimizer.py", "README.md"])
        prompt = "How does the optimizer handle secondary frameworks?"
        result = _extract_prompt_referenced_files(prompt, tree)
        assert "backend/app/services/optimizer.py" in result

    def test_no_matches_returns_empty(self):
        tree = self._tree(["src/main.py"])
        prompt = "What is the meaning of life?"
        result = _extract_prompt_referenced_files(prompt, tree)
        assert result == []

    def test_deduplication(self):
        tree = self._tree(["backend/pipeline.py"])
        prompt = "Audit backend/pipeline.py — check pipeline.py for bugs"
        result = _extract_prompt_referenced_files(prompt, tree)
        assert result.count("backend/pipeline.py") == 1


class TestValidateExploreOutput:
    """Test post-LLM output validation."""

    def test_valid_snippet_passes_through(self):
        snippets = [{"file": "main.py", "lines": "1-10", "context": "entry point"}]
        file_contents = {"main.py": "\n".join(f"line {i}" for i in range(1, 51))}
        s, o, g = _validate_explore_output(snippets, [], [], file_contents, max_lines_shown=50)
        assert s[0]["context"] == "entry point"  # no flag added

    def test_snippet_beyond_visible_range_flagged(self):
        snippets = [{"file": "big.py", "lines": "400-420", "context": "some logic"}]
        file_contents = {"big.py": "\n".join(f"line {i}" for i in range(1, 301))}
        s, o, g = _validate_explore_output(snippets, [], [], file_contents, max_lines_shown=300)
        assert "[unverified" in s[0]["context"]

    def test_observation_with_valid_line_ref_unchanged(self):
        obs = ["Pipeline stage at line 50 handles retries"]
        file_contents = {"pipeline.py": "x" * 100}
        s, o, g = _validate_explore_output([], obs, [], file_contents, max_lines_shown=300)
        assert o[0] == obs[0]  # within range, no flag

    def test_observation_with_invalid_line_ref_flagged(self):
        obs = ["Bug at lines 600-610 in pipeline.py"]
        file_contents = {"pipeline.py": "x"}
        s, o, g = _validate_explore_output([], obs, [], file_contents, max_lines_shown=300)
        assert "[unverified" in o[0]

    def test_grounding_note_bug_claim_in_truncated_file_flagged(self):
        # File content includes truncation marker — indicates the file was cut off
        truncated_content = "\n".join(f"line {i}" for i in range(1, 301))
        truncated_content += "\n\n[TRUNCATED — only lines 1–300 of 800 shown.]"
        notes = ["analysis_quality is NOT set when defaults are applied in analyzer.py"]
        file_contents = {"analyzer.py": truncated_content}
        s, o, g = _validate_explore_output([], [], notes, file_contents, max_lines_shown=300)
        assert "[unverified" in g[0]

    def test_snippet_for_unknown_file_flagged(self):
        snippets = [{"file": "nonexistent.py", "lines": "1-5", "context": "ghost"}]
        s, o, g = _validate_explore_output(snippets, [], [], {}, max_lines_shown=300)
        assert "[unverified" in s[0]["context"]

    def test_empty_inputs_no_crash(self):
        s, o, g = _validate_explore_output([], [], [], {}, max_lines_shown=300)
        assert s == [] and o == [] and g == []

    def test_unparseable_line_range_left_alone(self):
        snippets = [{"file": "a.py", "lines": "various", "context": "ok"}]
        file_contents = {"a.py": "x"}
        s, o, g = _validate_explore_output(snippets, [], [], file_contents, max_lines_shown=300)
        assert s[0]["context"] == "ok"  # can't parse "various", so left alone
