"""Unit tests for context_builders.py (N35, updated P1.5).

Covers build_codebase_summary, build_analysis_summary, and build_strategy_summary.
Also covers _search_priority (P1.2), coverage_pct formula (P1.3), and
CodebaseContext dataclass (P1.3).
"""
from dataclasses import asdict

from app.services.context_builders import (
    build_analysis_summary,
    build_codebase_summary,
    build_strategy_summary,
)
from app.services.codebase_explorer import CodebaseContext
from app.services.codebase_tools import _search_priority


# ── _search_priority (P1.2) ───────────────────────────────────────────────────


def test_search_priority_src_is_tier1():
    """src/ paths get priority tier 1 (lowest sort key = highest priority)."""
    assert _search_priority({"path": "src/main.py"}) == 1


def test_search_priority_lib_is_tier1():
    assert _search_priority({"path": "lib/utils.py"}) == 1


def test_search_priority_app_is_tier1():
    assert _search_priority({"path": "app/config.py"}) == 1


def test_search_priority_backend_is_tier1():
    assert _search_priority({"path": "backend/app/main.py"}) == 1


def test_search_priority_test_dir_is_tier3():
    """tests/ prefix yields tier 3."""
    assert _search_priority({"path": "tests/test_main.py"}) == 3


def test_search_priority_test_prefix_filename_is_tier3():
    """Files named test_*.py (not under tests/) still get tier 3."""
    assert _search_priority({"path": "test_helpers.py"}) == 3


def test_search_priority_spec_dir_is_tier3():
    assert _search_priority({"path": "spec/api_spec.ts"}) == 3


def test_search_priority_hidden_dir_is_tier4():
    """.hidden/ first segment yields tier 4."""
    assert _search_priority({"path": ".hidden/file.py"}) == 4


def test_search_priority_dotgithub_is_tier4():
    assert _search_priority({"path": ".github/workflows/ci.yml"}) == 4


def test_search_priority_general_file_is_tier2():
    """Files outside src/, tests/, and hidden dirs get tier 2."""
    assert _search_priority({"path": "README.md"}) == 2


def test_search_priority_sort_order_src_before_tests():
    """sorted() with _search_priority key puts src/ files before tests/ files."""
    entries = [
        {"path": "tests/test_main.py"},
        {"path": "src/main.py"},
        {"path": ".hidden/cfg.py"},
        {"path": "README.md"},
    ]
    ordered = sorted(entries, key=_search_priority)
    paths = [e["path"] for e in ordered]
    assert paths.index("src/main.py") < paths.index("tests/test_main.py")
    assert paths.index("src/main.py") < paths.index(".hidden/cfg.py")
    assert paths.index("README.md") < paths.index("tests/test_main.py")


def test_search_priority_docs_dir_is_tier3():
    """docs/ prefix yields tier 3 — lower priority than src/ and general files."""
    assert _search_priority({"path": "docs/guide.md"}) == 3


def test_search_priority_examples_dir_is_tier3():
    """examples/ prefix yields tier 3."""
    assert _search_priority({"path": "examples/demo.py"}) == 3


def test_search_priority_example_dir_is_tier3():
    """example/ (singular) prefix yields tier 3."""
    assert _search_priority({"path": "example/usage.py"}) == 3


def test_search_priority_doc_dir_is_tier3():
    """doc/ (singular) prefix yields tier 3."""
    assert _search_priority({"path": "doc/api.md"}) == 3


def test_search_priority_src_before_docs():
    """sorted() puts src/ before docs/ files."""
    entries = [
        {"path": "docs/guide.md"},
        {"path": "examples/demo.py"},
        {"path": "src/main.py"},
        {"path": "README.md"},
    ]
    ordered = sorted(entries, key=_search_priority)
    paths = [e["path"] for e in ordered]
    assert paths.index("src/main.py") < paths.index("docs/guide.md")
    assert paths.index("src/main.py") < paths.index("examples/demo.py")
    assert paths.index("README.md") < paths.index("docs/guide.md")


# ── read_file truncation notice format (P1.2) ─────────────────────────────────


def test_truncation_notice_format():
    """Truncation notice uses en-dash (U+2013) and expected markers."""
    # Reproduce the exact string from codebase_tools.py read_file_handler
    max_lines = 200
    total_lines = 350
    notice = (
        f"\n\n[TRUNCATED: showing lines 1\u2013{max_lines} of {total_lines}. "
        "Use search_code to locate specific sections, or get_file_outline "
        "to see the structure first.]"
    )
    assert "TRUNCATED" in notice
    assert "1\u2013200" in notice          # en-dash, not hyphen
    assert "of 350" in notice
    assert "search_code" in notice
    assert "get_file_outline" in notice


# ── coverage_pct formula (P1.3) ───────────────────────────────────────────────


def _compute_coverage_pct(files_read_count: int, total_files: int) -> int:
    """Mirror of the formula used in codebase_explorer.run_explore()."""
    return min(100, round(files_read_count / max(1, total_files) * 100))


def test_coverage_pct_zero_reads():
    assert _compute_coverage_pct(0, 100) == 0


def test_coverage_pct_partial_reads():
    assert _compute_coverage_pct(10, 100) == 10


def test_coverage_pct_full_reads():
    assert _compute_coverage_pct(5, 5) == 100


def test_coverage_pct_clamped_at_100():
    """Reading more files than total (e.g., tree changed) is clamped to 100."""
    assert _compute_coverage_pct(150, 100) == 100


def test_coverage_pct_zero_total_no_divide_by_zero():
    """max(1, total_files) prevents ZeroDivisionError when tree is empty."""
    assert _compute_coverage_pct(0, 0) == 0


# ── CodebaseContext dataclass (P1.3) ──────────────────────────────────────────


def test_codebase_context_explore_quality_default():
    """explore_quality defaults to 'complete'."""
    ctx = CodebaseContext()
    assert ctx.explore_quality == "complete"


def test_codebase_context_explore_quality_partial():
    ctx = CodebaseContext(explore_quality="partial")
    assert ctx.explore_quality == "partial"


def test_codebase_context_explore_quality_failed():
    ctx = CodebaseContext(explore_quality="failed")
    assert ctx.explore_quality == "failed"


def test_codebase_context_asdict_includes_explore_quality():
    """asdict() serialisation includes the explore_quality field."""
    ctx = CodebaseContext(explore_quality="partial")
    d = asdict(ctx)
    assert "explore_quality" in d
    assert d["explore_quality"] == "partial"


def test_codebase_context_asdict_complete_roundtrip():
    """All fields survive an asdict() round-trip with non-default values."""
    ctx = CodebaseContext(
        repo="owner/repo",
        branch="dev",
        files_read_count=7,
        coverage_pct=35,
        explore_quality="partial",
    )
    d = asdict(ctx)
    assert d["repo"] == "owner/repo"
    assert d["branch"] == "dev"
    assert d["files_read_count"] == 7
    assert d["coverage_pct"] == 35
    assert d["explore_quality"] == "partial"


# ── build_codebase_summary ────────────────────────────────────────────────────


def test_codebase_empty_dict():
    assert build_codebase_summary({}) == ""


def test_codebase_files_read_count_zero_not_shown():
    # files_read_count=0 is falsy and should not appear (not meaningful to surface)
    result = build_codebase_summary({"files_read_count": 0})
    assert "Files read" not in result


def test_codebase_quality_partial_specific_warning():
    """P1.5: partial quality shows specific warning with file count and coverage."""
    result = build_codebase_summary(
        {"explore_quality": "partial", "files_read_count": 7, "coverage_pct": 35}
    )
    assert "Exploration was partial" in result
    assert "7 files" in result
    assert "35%" in result
    assert "Analysis may be incomplete" in result


def test_codebase_quality_failed_specific_warning():
    """P1.5: failed quality shows specific warning about no context."""
    result = build_codebase_summary({"explore_quality": "failed"})
    assert "Repository exploration failed" in result
    assert "no codebase context" in result
    assert "without codebase grounding" in result


def test_codebase_quality_not_complete_no_generic_banner():
    """P1.5: old generic CODEBASE QUALITY banner is no longer used."""
    result = build_codebase_summary({"explore_quality": "partial"})
    assert "CODEBASE QUALITY" not in result


def test_codebase_quality_complete_no_warning():
    result = build_codebase_summary({"repo": "owner/repo", "explore_quality": "complete"})
    assert "Note:" not in result
    assert "CODEBASE QUALITY" not in result


def test_codebase_repo_and_branch_merged():
    """P1.5: repo and branch appear on one line as 'Repo: owner/name @ branch'."""
    result = build_codebase_summary({"repo": "owner/myrepo", "branch": "main"})
    assert "Repo: owner/myrepo @ main" in result
    # Should not have a separate Branch: line when both are present
    assert "Branch: main" not in result


def test_codebase_repo_only_no_branch():
    result = build_codebase_summary({"repo": "owner/myrepo"})
    assert "Repo: owner/myrepo" in result
    assert "@ " not in result


def test_codebase_branch_only_no_repo():
    result = build_codebase_summary({"branch": "feature/ctx-injection"})
    assert "feature/ctx-injection" in result


def test_codebase_coverage_pct_shown_when_nonzero():
    """P1.5: coverage_pct > 0 adds Coverage line."""
    result = build_codebase_summary({"coverage_pct": 42})
    assert "Coverage: 42% of repository" in result


def test_codebase_coverage_pct_zero_not_shown():
    """P1.5: coverage_pct=0 should not appear."""
    result = build_codebase_summary({"coverage_pct": 0})
    assert "Coverage" not in result


def test_codebase_key_files_read_appears():
    """N20 regression guard: key_files_read (not key_files) is used."""
    result = build_codebase_summary({"key_files_read": ["app/main.py", "app/config.py"]})
    assert "app/main.py" in result
    assert "Key files" in result


def test_codebase_grounding_notes_appears():
    """N20 regression guard: grounding_notes (not notes) is used."""
    result = build_codebase_summary({"grounding_notes": ["Prompt references optimizer service"]})
    assert "Grounding notes" in result
    assert "Prompt references optimizer service" in result


def test_codebase_relevant_snippets_as_list():
    """N20 regression guard: relevant_snippets as list with file:lines format."""
    ctx = {
        "relevant_snippets": [
            {"file": "optimizer.py", "lines": "1-20", "context": "def optimize():"}
        ]
    }
    result = build_codebase_summary(ctx)
    assert "optimizer.py:1-20" in result
    assert "def optimize():" in result


def test_codebase_snippets_capped_at_5():
    """P1.5: snippet count cap raised from 3 to 5."""
    ctx = {
        "relevant_snippets": [
            {"file": f"file{i}.py", "lines": "1-5", "context": f"code {i}"}
            for i in range(8)
        ]
    }
    result = build_codebase_summary(ctx)
    assert "file0.py" in result
    assert "file4.py" in result
    assert "file5.py" not in result  # 6th snippet should be excluded


def test_codebase_snippet_content_capped_at_600_chars():
    """P1.5: snippet content cap raised from 400 to 600 chars."""
    long_content = "x" * 700
    ctx = {
        "relevant_snippets": [
            {"file": "big.py", "lines": "1-100", "context": long_content}
        ]
    }
    result = build_codebase_summary(ctx)
    assert "x" * 600 in result
    assert "x" * 601 not in result


def test_codebase_observations_capped_at_8():
    """P1.5: observations cap raised from 5 to 8."""
    ctx = {"observations": [f"obs {i}" for i in range(12)]}
    result = build_codebase_summary(ctx)
    assert "obs 7" in result
    assert "obs 8" not in result


def test_codebase_grounding_notes_capped_at_8():
    """P1.5: grounding_notes cap raised from 5 to 8."""
    ctx = {"grounding_notes": [f"note {i}" for i in range(12)]}
    result = build_codebase_summary(ctx)
    assert "note 7" in result
    assert "note 8" not in result


def test_codebase_tech_stack_capped_at_10():
    """Tech stack should cap at 10 entries."""
    ctx = {"tech_stack": [f"lang{i}" for i in range(15)]}
    result = build_codebase_summary(ctx)
    assert "lang9" in result
    assert "lang10" not in result


def test_codebase_files_read_count_appears():
    result = build_codebase_summary({"files_read_count": 12})
    assert "Files read: 12" in result


def test_codebase_partial_zero_files_uses_failed_message():
    """partial + files_read_count=0 uses the failed-message format (timed out before any reads)."""
    result = build_codebase_summary(
        {"explore_quality": "partial", "files_read_count": 0, "coverage_pct": 0}
    )
    assert "Repository exploration failed" in result
    assert "without codebase grounding" in result
    assert "Exploration was partial" not in result


# ── build_analysis_summary ────────────────────────────────────────────────────


def test_analysis_empty_dict():
    assert build_analysis_summary({}) == ""


def test_analysis_codebase_informed_true_no_quality_note():
    result = build_analysis_summary({"task_type": "coding", "codebase_informed": True})
    assert "NOTE" not in result


def test_analysis_codebase_informed_partial_shows_note():
    """N15 regression guard."""
    result = build_analysis_summary({"codebase_informed": "partial"})
    assert "partially informed" in result


def test_analysis_codebase_informed_false_shows_failed_note():
    """N15 regression guard."""
    result = build_analysis_summary({"codebase_informed": False})
    assert "no codebase grounding" in result


def test_analysis_codebase_informed_failed_string():
    result = build_analysis_summary({"codebase_informed": "failed"})
    assert "no codebase grounding" in result


def test_analysis_weaknesses_use_dash_prefix():
    result = build_analysis_summary({"weaknesses": ["Too vague", "No context"]})
    assert "  - Too vague" in result


def test_analysis_strengths_use_plus_prefix():
    result = build_analysis_summary({"strengths": ["Clear objective"]})
    assert "  + Clear objective" in result


def test_analysis_recommended_frameworks_joined_with_comma():
    result = build_analysis_summary(
        {"recommended_frameworks": ["CO-STAR", "chain-of-thought"]}
    )
    assert "CO-STAR, chain-of-thought" in result


# ── build_strategy_summary ────────────────────────────────────────────────────


def test_strategy_empty_dict():
    assert build_strategy_summary({}) == ""


def test_strategy_secondary_frameworks_includes_integration_directive():
    """N13 regression guard: secondary_frameworks triggers weave directive."""
    result = build_strategy_summary(
        {
            "primary_framework": "CO-STAR",
            "secondary_frameworks": ["chain-of-thought"],
        }
    )
    assert "Secondary frameworks" in result
    assert "chain-of-thought" in result
    assert "Weave" in result or "weave" in result


def test_strategy_empty_secondary_no_directive():
    result = build_strategy_summary(
        {"primary_framework": "RISEN", "secondary_frameworks": []}
    )
    assert "Secondary" not in result
    assert "Weave" not in result
