"""Tests for app.utils.text_cleanup — LLM output normalization utilities."""

from app.utils.text_cleanup import (
    parse_domain,
    sanitize_optimization_result,
    split_prompt_and_changes,
    strip_meta_header,
    validate_intent_label,
)

# ---------------------------------------------------------------------------
# strip_meta_header
# ---------------------------------------------------------------------------


class TestStripMetaHeader:
    def test_removes_preamble(self) -> None:
        text = "Here is the optimized prompt using chain-of-thought:\n\nActual prompt."
        assert strip_meta_header(text) == "Actual prompt."

    def test_removes_below_is_preamble(self) -> None:
        text = "Below is the optimized version:\n\nActual content here."
        assert strip_meta_header(text) == "Actual content here."

    def test_unwraps_markdown_fence(self) -> None:
        text = "```markdown\nThe real prompt.\n```"
        assert strip_meta_header(text) == "The real prompt."

    def test_unwraps_md_fence(self) -> None:
        text = "```md\nContent.\n```"
        assert strip_meta_header(text) == "Content."

    def test_strips_meta_header_line(self) -> None:
        text = "# Optimized Prompt\n\nThe real content."
        assert strip_meta_header(text) == "The real content."

    def test_strips_h2_header(self) -> None:
        text = "## Improved Prompt\n\nContent."
        assert strip_meta_header(text) == "Content."

    def test_strips_h3_header(self) -> None:
        text = "### Rewritten Prompt\n\nContent."
        assert strip_meta_header(text) == "Content."

    def test_strips_trailing_fence(self) -> None:
        text = "Content here.\n```"
        assert strip_meta_header(text) == "Content here."

    def test_strips_trailing_orphaned_heading(self) -> None:
        text = "Content here.\n##"
        assert strip_meta_header(text) == "Content here."

    def test_passthrough_clean_text(self) -> None:
        text = "This is already clean text with no artifacts."
        assert strip_meta_header(text) == text

    def test_combined_preamble_and_fence(self) -> None:
        text = "Here is the optimized prompt:\n```markdown\nReal prompt.\n```"
        assert strip_meta_header(text) == "Real prompt."


# ---------------------------------------------------------------------------
# split_prompt_and_changes
# ---------------------------------------------------------------------------


class TestSplitPromptAndChanges:
    def test_splits_on_summary_of_changes(self) -> None:
        text = "My prompt.\n\n## Summary of Changes\n- Added X\n- Fixed Y"
        prompt, changes = split_prompt_and_changes(text)
        assert "My prompt" in prompt
        assert "Added X" in changes

    def test_splits_on_what_changed(self) -> None:
        text = "Prompt text.\n\n## What Changed\nImproved clarity."
        prompt, changes = split_prompt_and_changes(text)
        assert "Prompt text" in prompt
        assert "clarity" in changes

    def test_splits_on_bold_changes(self) -> None:
        text = "Prompt content.\n\n**Changes**\nSome changes here."
        prompt, changes = split_prompt_and_changes(text)
        assert "Prompt content" in prompt
        assert "Some changes" in changes

    def test_splits_on_changes_colon(self) -> None:
        text = "Prompt content.\n\nChanges:\n- Item one\n- Item two"
        prompt, changes = split_prompt_and_changes(text)
        assert "Prompt content" in prompt
        assert "Item one" in changes

    def test_splits_on_what_changed_colon(self) -> None:
        text = "Prompt.\n\nWhat changed:\nImproved structure."
        prompt, changes = split_prompt_and_changes(text)
        assert "Prompt" in prompt
        assert "structure" in changes

    def test_no_marker_returns_default_summary(self) -> None:
        text = "Just a prompt with no changes section."
        prompt, changes = split_prompt_and_changes(text)
        assert prompt == "Just a prompt with no changes section."
        assert "Restructured" in changes

    def test_changes_truncated_at_500_chars(self) -> None:
        long_changes = "x" * 600
        text = f"Prompt.\n\n## Changes\n{long_changes}"
        _, changes = split_prompt_and_changes(text)
        assert len(changes) <= 500

    def test_also_strips_meta_header_from_prompt_part(self) -> None:
        text = "# Optimized Prompt\n\nContent.\n\n## Changes\nDone."
        prompt, changes = split_prompt_and_changes(text)
        assert not prompt.startswith("#")
        assert "Content" in prompt

    # --- Regex-based heading level coverage ---

    def test_splits_on_h1_changes(self) -> None:
        text = "Prompt.\n\n# Changes\n- Item"
        prompt, changes = split_prompt_and_changes(text)
        assert "Prompt" in prompt
        assert "Item" in changes

    def test_splits_on_h3_changes(self) -> None:
        text = "Prompt.\n\n### Changes\n- Item"
        prompt, changes = split_prompt_and_changes(text)
        assert "Prompt" in prompt
        assert "Item" in changes

    def test_splits_on_h4_changes_made(self) -> None:
        text = "Prompt.\n\n#### Changes Made\nDetails"
        prompt, changes = split_prompt_and_changes(text)
        assert "Prompt" in prompt
        assert "Details" in changes

    def test_splits_on_hr_prefixed_changes(self) -> None:
        text = "Prompt.\n\n---\n## Changes\n- Item"
        prompt, changes = split_prompt_and_changes(text)
        assert "Prompt" in prompt
        assert "Item" in changes

    def test_case_insensitive_allcaps(self) -> None:
        text = "Prompt.\n\n## CHANGES\n- Item"
        prompt, changes = split_prompt_and_changes(text)
        assert "Prompt" in prompt
        assert "Item" in changes

    # --- Applied Patterns handling ---

    def test_strips_applied_patterns_before_changes(self) -> None:
        text = "Prompt.\n\n## Applied Patterns\n- Pattern A\n\n## Changes\n- Item"
        prompt, changes = split_prompt_and_changes(text)
        assert "Prompt" in prompt
        assert "Applied Patterns" not in prompt
        assert "Item" in changes

    def test_strips_applied_patterns_only(self) -> None:
        text = "Prompt.\n\n## Applied Patterns\n- Pattern A used"
        prompt, changes = split_prompt_and_changes(text)
        assert "Prompt" in prompt
        assert "Applied Patterns" not in prompt

    # --- False positive guards ---

    def test_does_not_match_changelog_heading(self) -> None:
        text = "Prompt.\n\n## Changelog\nSome log content."
        prompt, changes = split_prompt_and_changes(text)
        assert "Changelog" in prompt
        assert "Restructured" in changes  # default, no split happened


# ---------------------------------------------------------------------------
# sanitize_optimization_result
# ---------------------------------------------------------------------------


class TestSanitizeOptimizationResult:
    def test_strips_leaked_changes(self) -> None:
        prompt = "Clean prompt.\n\n## Changes\n- Added structure"
        cleaned, changes = sanitize_optimization_result(prompt, "")
        assert "## Changes" not in cleaned
        assert "Added structure" in changes

    def test_preserves_explicit_changes_summary(self) -> None:
        prompt = "Clean prompt.\n\n## Changes\n- Added structure"
        cleaned, changes = sanitize_optimization_result(prompt, "Explicit summary")
        assert "## Changes" not in cleaned
        assert changes == "Explicit summary"

    def test_no_op_on_clean_prompt(self) -> None:
        prompt = "Already clean prompt with no leakage."
        cleaned, changes = sanitize_optimization_result(prompt, "Summary here")
        assert cleaned == "Already clean prompt with no leakage."
        assert changes == "Summary here"

    def test_strips_applied_patterns_from_prompt(self) -> None:
        prompt = "Prompt.\n\n## Applied Patterns\n- Used X"
        cleaned, changes = sanitize_optimization_result(prompt, "Summary")
        assert "Applied Patterns" not in cleaned
        assert changes == "Summary"

    def test_uses_extracted_when_summary_is_default(self) -> None:
        prompt = "Prompt.\n\n## Changes\n1. **Added framing** — clearer intent"
        cleaned, changes = sanitize_optimization_result(
            prompt, "Restructured with added specificity and constraints"
        )
        assert "## Changes" not in cleaned
        assert "Added framing" in changes


class TestParseDomain:
    def test_simple_domain(self) -> None:
        primary, qualifier = parse_domain("backend")
        assert primary == "backend"
        assert qualifier is None

    def test_colon_format(self) -> None:
        primary, qualifier = parse_domain("backend: security")
        assert primary == "backend"
        assert qualifier == "security"

    def test_colon_no_space(self) -> None:
        primary, qualifier = parse_domain("frontend:React")
        assert primary == "frontend"
        assert qualifier == "react"

    def test_free_form_legacy(self) -> None:
        primary, qualifier = parse_domain("REST API design")
        assert primary == "rest api design"
        assert qualifier is None

    def test_empty_string(self) -> None:
        primary, qualifier = parse_domain("")
        assert primary == "general"
        assert qualifier is None

    def test_none_input(self) -> None:
        primary, qualifier = parse_domain(None)
        assert primary == "general"
        assert qualifier is None

    def test_general_passthrough(self) -> None:
        primary, qualifier = parse_domain("general")
        assert primary == "general"
        assert qualifier is None

    def test_multiple_colons(self) -> None:
        primary, qualifier = parse_domain("backend: REST: v2")
        assert primary == "backend"
        assert qualifier == "rest: v2"

    def test_mixed_case_normalization(self) -> None:
        primary, qualifier = parse_domain("Backend: Security")
        assert primary == "backend"
        assert qualifier == "security"

    def test_uppercase_simple(self) -> None:
        primary, qualifier = parse_domain("DEVOPS")
        assert primary == "devops"
        assert qualifier is None


# ---------------------------------------------------------------------------
# validate_intent_label
# ---------------------------------------------------------------------------


class TestValidateIntentLabel:
    """Tests for the intent label quality gate."""

    def test_passes_good_label(self) -> None:
        assert validate_intent_label("Design Auth API Service") == "Design Auth API Service"

    def test_passes_multi_word_descriptive(self) -> None:
        assert validate_intent_label("Refactor Database Migration") == "Refactor Database Migration"

    def test_rejects_general_with_raw_prompt(self) -> None:
        result = validate_intent_label("General", "Create a REST API for user authentication")
        assert result != "General"
        assert len(result.split()) >= 2

    def test_rejects_general_case_insensitive(self) -> None:
        result = validate_intent_label("general", "Build a dashboard component")
        assert result.lower() != "general"

    def test_rejects_generic_task_suffix(self) -> None:
        result = validate_intent_label("Coding Task", "Implement OAuth2 login flow")
        assert result != "Coding Task"
        assert "Implement" in result or "OAuth2" in result or "Login" in result

    def test_rejects_generic_optimization_suffix(self) -> None:
        result = validate_intent_label("Writing Optimization", "Draft technical blog post about caching")
        assert result != "Writing Optimization"

    def test_rejects_conversational_start_i(self) -> None:
        result = validate_intent_label(
            "I Need Help With My",
            "I need help with my React component rendering",
        )
        assert not result.lower().startswith("i ")

    def test_rejects_conversational_start_please(self) -> None:
        result = validate_intent_label(
            "Please Help Me Fix",
            "Please help me fix the authentication bug",
        )
        assert not result.lower().startswith("please ")

    def test_rejects_conversational_start_can(self) -> None:
        result = validate_intent_label(
            "Can You Write A",
            "Can you write a Python script for data cleaning",
        )
        assert not result.lower().startswith("can ")

    def test_rejects_single_word(self) -> None:
        result = validate_intent_label("Coding", "Build a responsive landing page")
        assert result != "Coding"
        assert len(result.split()) >= 2

    def test_fallback_without_raw_prompt_returns_original(self) -> None:
        # No raw_prompt means no improvement possible — return as-is
        assert validate_intent_label("General", None) == "General"
        assert validate_intent_label("Coding Task") == "Coding Task"

    def test_empty_label_defaults(self) -> None:
        result = validate_intent_label("", "Create microservice for payments")
        # Should try to extract from raw_prompt rather than return empty
        assert result  # non-empty

    def test_four_word_task_suffix_passes(self) -> None:
        # 4+ words with " task" suffix should pass (specific enough)
        assert validate_intent_label("REST API Backend Task") == "REST API Backend Task"

    def test_four_word_optimization_suffix_passes(self) -> None:
        assert validate_intent_label("Database Query Speed Optimization") == "Database Query Speed Optimization"

    def test_preserves_acronyms_in_fallback(self) -> None:
        result = validate_intent_label("General", "Build REST API with JWT authentication")
        # The fallback should go through title_case_label which preserves acronyms
        assert "REST" in result or "JWT" in result or "API" in result
