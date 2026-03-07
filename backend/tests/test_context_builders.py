"""Unit tests for context_builders.py (N35, updated P1.5).

Covers build_codebase_summary, build_analysis_summary, and build_strategy_summary.
"""
from app.services.context_builders import (
    build_analysis_summary,
    build_codebase_summary,
    build_strategy_summary,
)


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
