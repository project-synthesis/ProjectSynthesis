"""Regression tests for the 7 known sampling pipeline bugs.

Each test covers a specific bug documented in the project memory
(project_sampling_issues.md, 2026-03-25). Tests verify the fix holds,
not just absence of crash.

Copyright 2025-2026 Project Synthesis contributors.
"""

from __future__ import annotations

import pytest

# ---------------------------------------------------------------------------
# 1. GENERAL classification persists
# Bug: prompts with strong coding keywords classified as "general" because
#      the LLM's text-fallback analyzer missed keyword signals.
# Fix: semantic_upgrade_general() post-gate in pipeline_constants.py
# ---------------------------------------------------------------------------


class TestRegressionGeneralClassification:
    """semantic_upgrade_general() should override 'general' when keywords match."""

    def test_coding_keywords_upgrade_to_coding(self):
        """Prompt with 'function', 'class', 'API' must not stay general."""
        from app.services.pipeline_constants import semantic_upgrade_general

        prompt = "Design a scalable software architecture with REST API endpoints and database models"
        result = semantic_upgrade_general("general", prompt)
        assert result != "general", (
            f"Expected upgrade from 'general' but got '{result}' "
            f"for prompt with strong coding keywords"
        )

    def test_non_general_passthrough(self):
        """Already-classified prompts are returned unchanged."""
        from app.services.pipeline_constants import semantic_upgrade_general

        result = semantic_upgrade_general("coding", "write a poem about nature")
        assert result == "coding"

    def test_genuinely_general_stays_general(self):
        """Prompt with no domain keywords stays general (no false positives)."""
        from app.services.pipeline_constants import semantic_upgrade_general

        result = semantic_upgrade_general("general", "help me with my project")
        assert result == "general"


# ---------------------------------------------------------------------------
# 2. Scorer always fails -> heuristic-only fallback
# Bug: LLM scoring phase failed (3ms, no valid JSON), leaving only heuristic
#      scores with no hybrid blending.
# Fix: heuristic_scorer.py produces valid fallback scores for any prompt.
# ---------------------------------------------------------------------------


class TestRegressionScorerFallback:
    """Heuristic scorer must produce valid scores without LLM assistance."""

    def test_heuristic_scores_are_valid(self):
        """All 5 dimensions produce finite scores in [1.0, 10.0]."""
        from app.services.heuristic_scorer import HeuristicScorer

        original = "Write a function"
        optimized = (
            "Write a Python function that takes a list of integers and returns "
            "the sorted list using merge sort. Include type hints and a docstring."
        )
        scores = HeuristicScorer.score_prompt(original, optimized)
        for dim in ("clarity", "specificity", "structure", "faithfulness", "conciseness"):
            val = scores[dim]
            assert isinstance(val, (int, float)), f"{dim} is not numeric: {val!r}"
            assert 1.0 <= val <= 10.0, f"{dim}={val} out of [1.0, 10.0] range"

    def test_all_dimensions_present(self):
        """All 5 scoring dimensions are returned."""
        from app.services.heuristic_scorer import HeuristicScorer

        scores = HeuristicScorer.score_prompt("short", "A longer, more detailed version")
        expected_dims = {"clarity", "specificity", "structure", "faithfulness", "conciseness"}
        assert set(scores.keys()) == expected_dims


# ---------------------------------------------------------------------------
# 3. "# Optimized Prompt" meta-header in output
# Bug: LLM prepends '# Optimized Prompt' title to the output. The meta-header
#      is commentary, not part of the prompt.
# Fix: strip_meta_header() in text_cleanup.py removes known header patterns.
# ---------------------------------------------------------------------------


class TestRegressionMetaHeaderCleanup:
    """strip_meta_header() must remove LLM-added preambles and titles."""

    def test_hash_optimized_prompt_removed(self):
        """'# Optimized Prompt' header is stripped."""
        from app.utils.text_cleanup import strip_meta_header

        text = "# Optimized Prompt\n\nWrite a function that sorts a list."
        result = strip_meta_header(text)
        assert result == "Write a function that sorts a list."

    def test_double_hash_variant(self):
        """'## Optimized Prompt' header is stripped."""
        from app.utils.text_cleanup import strip_meta_header

        text = "## Optimized Prompt\n\nActual content here."
        result = strip_meta_header(text)
        assert result == "Actual content here."

    def test_preamble_sentence_removed(self):
        """'Here is the optimized prompt:' preamble is stripped."""
        from app.utils.text_cleanup import strip_meta_header

        text = "Here is the optimized prompt:\n\nActual content here."
        result = strip_meta_header(text)
        assert result == "Actual content here."

    def test_code_fence_wrapping_removed(self):
        """Markdown code fence wrapping the entire prompt is stripped."""
        from app.utils.text_cleanup import strip_meta_header

        text = "```markdown\nActual prompt content\n```"
        result = strip_meta_header(text)
        assert result == "Actual prompt content"

    def test_clean_text_unchanged(self):
        """Text without meta-headers passes through unchanged."""
        from app.utils.text_cleanup import strip_meta_header

        text = "Write a Python function that implements binary search."
        result = strip_meta_header(text)
        assert result == text


# ---------------------------------------------------------------------------
# 4. Changes rationale still in prompt body
# Bug: split_prompt_and_changes() didn't catch TABLE format (| CHANGE | REASON |)
#      or '## Changes' without recognized markers.
# Fix: expanded _CHANGES_RE regex with more heading/marker patterns.
# ---------------------------------------------------------------------------


class TestRegressionChangesSplit:
    """split_prompt_and_changes() must separate prompt from changes metadata."""

    def test_summary_of_changes_heading(self):
        """'## Summary of Changes' is split out."""
        from app.utils.text_cleanup import split_prompt_and_changes

        text = "Actual prompt content.\n\n## Summary of Changes\n- Added structure\n- Improved clarity"
        prompt, changes = split_prompt_and_changes(text)
        assert "Summary of Changes" not in prompt
        assert "Added structure" in changes

    def test_changes_made_heading(self):
        """'## Changes Made' is split out."""
        from app.utils.text_cleanup import split_prompt_and_changes

        text = "The actual prompt here.\n\n## Changes Made\n- Restructured for clarity"
        prompt, changes = split_prompt_and_changes(text)
        assert "Changes Made" not in prompt
        assert "Restructured" in changes

    def test_what_changed_heading(self):
        """'### What Changed' variant is split out."""
        from app.utils.text_cleanup import split_prompt_and_changes

        text = "Prompt text.\n\n### What Changed\n- Better structure"
        prompt, changes = split_prompt_and_changes(text)
        assert "What Changed" not in prompt

    def test_bold_changes_marker(self):
        """'**Changes**' bold marker is split out."""
        from app.utils.text_cleanup import split_prompt_and_changes

        text = "Prompt content.\n\n**Changes**\n- Fixed ambiguity"
        prompt, changes = split_prompt_and_changes(text)
        assert "**Changes**" not in prompt

    def test_no_changes_section_passthrough(self):
        """Text without changes section returns full text as prompt."""
        from app.utils.text_cleanup import split_prompt_and_changes

        text = "A clean prompt with no changes metadata."
        prompt, changes = split_prompt_and_changes(text)
        assert prompt.strip() == text.strip()


# ---------------------------------------------------------------------------
# 5. Clarity score 1.5 for technical prompts
# Bug: Flesch reading ease penalized technical language heavily, scoring a
#      well-structured prompt about "microservices architecture" at 1.5.
# Fix: heuristic_clarity() now uses precision signals + ambiguity density
#      instead of raw Flesch score. Technical vocabulary is not penalized.
# ---------------------------------------------------------------------------


class TestRegressionClarityHeuristic:
    """Clarity heuristic must not over-penalize technical vocabulary."""

    def test_technical_prompt_reasonable_clarity(self):
        """Technical prompt about architecture should score >= 5.0 baseline."""
        from app.services.heuristic_scorer import HeuristicScorer

        prompt = (
            "Design a scalable microservices architecture with REST API "
            "endpoints, PostgreSQL database models, Redis caching layer, "
            "and Kubernetes deployment manifests."
        )
        score = HeuristicScorer.heuristic_clarity(prompt)
        assert score >= 5.0, (
            f"Technical prompt clarity={score}, expected >= 5.0. "
            f"Technical vocabulary should not trigger ambiguity penalties."
        )

    def test_identifier_words_not_penalized(self):
        """Words like 'etc_config', 'maybe_null' as identifiers don't penalize."""
        from app.services.heuristic_scorer import HeuristicScorer

        prompt = (
            "Create a configuration module with etc_config and maybe_null "
            "handling for the system settings."
        )
        score = HeuristicScorer.heuristic_clarity(prompt)
        # etc_config and maybe_null are identifiers, not vague language
        assert score >= 4.5, (
            f"Identifier-like words penalized clarity to {score}"
        )

    def test_genuinely_vague_prompt_penalized(self):
        """Truly vague prompt with 'stuff', 'things', 'somehow' scores low."""
        from app.services.heuristic_scorer import HeuristicScorer

        prompt = "Do some stuff with things and somehow make it work maybe"
        score = HeuristicScorer.heuristic_clarity(prompt)
        assert score < 5.0, (
            f"Genuinely vague prompt scored {score}, expected < 5.0"
        )


# ---------------------------------------------------------------------------
# 6. effect_update_depth_exceeded in SemanticTopology
# Bug: infinite reactivity loop in the frontend when $effect reads and writes
#      the same state. Backend manifestation: self-referencing parent_id in
#      cluster data causes infinite tree traversal.
# Fix: build_breadcrumb() cycle detection + DB invariant that no cluster's
#      parent_id equals its own id.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_regression_no_self_referencing_clusters(db_session):
    """No cluster in the taxonomy tree should have parent_id == id.

    Self-referencing nodes cause infinite loops in tree traversal
    (backend breadcrumb building and frontend topology rendering).
    """
    from sqlalchemy import select

    from app.models import PromptCluster

    result = await db_session.execute(
        select(PromptCluster.id, PromptCluster.parent_id, PromptCluster.label).where(
            PromptCluster.parent_id.isnot(None)
        )
    )
    violations = [
        (row.id, row.label)
        for row in result.all()
        if row.parent_id == row.id
    ]
    assert violations == [], (
        f"Self-referencing clusters found (parent_id == id): "
        f"{[(vid[:8], vlabel) for vid, vlabel in violations]}"
    )


# ---------------------------------------------------------------------------
# 7. 401 Unauthorized on GitHub API calls
# Bug: frontend/backend made GitHub API calls without checking auth state,
#      causing 500 errors instead of clean 401.
# Fix: /api/github/auth/me checks session_id cookie and raises HTTPException(401).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_regression_github_auth_guard(app_client):
    """GET /api/github/auth/me without session cookie returns 401, not 500."""
    resp = await app_client.get("/api/github/auth/me")
    assert resp.status_code == 401, (
        f"Expected 401 for unauthenticated request, got {resp.status_code}"
    )
    body = resp.json()
    assert "detail" in body
