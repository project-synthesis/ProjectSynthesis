"""Tests for app.utils.text_cleanup — LLM output normalization utilities."""

from app.utils.text_cleanup import split_prompt_and_changes, strip_meta_header

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
