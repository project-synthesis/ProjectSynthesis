"""Tests for OptimizeStreamParser — the streaming prompt/metadata separator.

Covers:
- Clean marker detection (single chunk and multi-chunk)
- Cross-boundary marker detection (marker split across chunks)
- Safety buffer behavior (buffered chars released on finalize)
- Metadata JSON parsing (valid and malformed)
- JSON fallback when no marker is present (backward compat)
- Partial/empty input handling
- Accumulated prompt property for timeout recovery
"""

import json

from app.services.optimizer import (
    OPTIMIZATION_META_CLOSE,
    OPTIMIZATION_META_OPEN,
    OptimizeStreamParser,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _feed_all(parser: OptimizeStreamParser, text: str, chunk_size: int = 1) -> str:
    """Feed text character-by-character (or in chunks) and return all safe text."""
    result = ""
    for i in range(0, len(text), chunk_size):
        result += parser.feed(text[i : i + chunk_size])
    return result


def _make_full_output(prompt: str, meta: dict) -> str:
    """Build a complete LLM output with prompt + metadata block."""
    return f"{prompt}\n\n{OPTIMIZATION_META_OPEN}\n{json.dumps(meta)}\n{OPTIMIZATION_META_CLOSE}"


# ---------------------------------------------------------------------------
# Basic marker detection
# ---------------------------------------------------------------------------

class TestMarkerDetection:
    """Tests for detecting <optimization_meta> markers in the stream."""

    def test_single_chunk_with_marker(self):
        """Entire output arrives in one chunk — marker splits prompt from metadata."""
        meta = {"changes_made": ["added clarity"], "framework_applied": "CO-STAR"}
        full = _make_full_output("You are an expert assistant.", meta)

        parser = OptimizeStreamParser()
        safe = parser.feed(full)
        prompt, metadata = parser.finalize()

        assert "You are an expert assistant." in prompt
        assert OPTIMIZATION_META_OPEN not in safe
        assert metadata is not None
        assert metadata["framework_applied"] == "CO-STAR"
        assert metadata["changes_made"] == ["added clarity"]

    def test_multi_chunk_marker_not_split(self):
        """Marker arrives intact within a single chunk (not split across boundaries)."""
        prompt_text = "Write clean, idiomatic Python code."
        meta = {"changes_made": ["restructured"], "framework_applied": "RISEN"}
        full = _make_full_output(prompt_text, meta)

        parser = OptimizeStreamParser()
        _feed_all(parser, full, chunk_size=50)
        prompt, metadata = parser.finalize()

        assert prompt_text.strip() in prompt
        assert metadata is not None
        assert metadata["framework_applied"] == "RISEN"

    def test_character_by_character_streaming(self):
        """Feed one character at a time — tests safety buffer and cross-boundary handling."""
        prompt_text = "Analyze the data thoroughly."
        meta = {"changes_made": ["specificity"], "framework_applied": "chain-of-thought"}
        full = _make_full_output(prompt_text, meta)

        parser = OptimizeStreamParser()
        safe = _feed_all(parser, full, chunk_size=1)
        prompt, metadata = parser.finalize()

        assert prompt_text.strip() in prompt
        assert metadata is not None
        assert metadata["framework_applied"] == "chain-of-thought"
        # No metadata content should leak into the safe output
        assert "changes_made" not in safe
        assert OPTIMIZATION_META_OPEN not in safe


# ---------------------------------------------------------------------------
# Cross-boundary marker detection
# ---------------------------------------------------------------------------

class TestCrossBoundaryMarker:
    """Tests for markers that are split across chunk boundaries."""

    def test_marker_split_in_half(self):
        """The <optimization_meta> tag is split across two chunks."""
        prompt_text = "Step 1: Identify the problem."
        meta_json = json.dumps({"changes_made": ["split test"], "framework_applied": "test"})
        full = f"{prompt_text}\n\n{OPTIMIZATION_META_OPEN}\n{meta_json}\n{OPTIMIZATION_META_CLOSE}"

        # Split right in the middle of the marker tag
        marker = OPTIMIZATION_META_OPEN
        split_point = full.index(marker) + len(marker) // 2

        parser = OptimizeStreamParser()
        safe1 = parser.feed(full[:split_point])
        safe2 = parser.feed(full[split_point:])
        prompt, metadata = parser.finalize()

        assert prompt_text.strip() in prompt
        assert metadata is not None
        assert metadata["changes_made"] == ["split test"]
        # No marker fragments in safe output
        assert "<" not in (safe1 + safe2) or "<optimization" not in (safe1 + safe2)

    def test_marker_split_at_every_position(self):
        """Exhaustively test splitting the marker at every possible position."""
        prompt_text = "Test prompt."
        meta = {"changes_made": ["x"], "framework_applied": "Y"}
        full = _make_full_output(prompt_text, meta)

        marker = OPTIMIZATION_META_OPEN
        marker_start = full.index(marker)

        for split_pos in range(marker_start, marker_start + len(marker) + 1):
            parser = OptimizeStreamParser()
            parser.feed(full[:split_pos])
            parser.feed(full[split_pos:])
            prompt, metadata = parser.finalize()

            assert prompt_text.strip() in prompt, f"Failed at split_pos={split_pos}"
            assert metadata is not None, f"No metadata at split_pos={split_pos}"
            assert metadata["changes_made"] == ["x"], f"Wrong metadata at split_pos={split_pos}"


# ---------------------------------------------------------------------------
# Safety buffer behavior
# ---------------------------------------------------------------------------

class TestSafetyBuffer:
    """Tests for the safety margin that prevents partial marker emission."""

    def test_buffer_flushed_on_finalize_no_marker(self):
        """When no marker appears, finalize() flushes the safety buffer."""
        parser = OptimizeStreamParser()
        # Short text — entirely within safety buffer
        safe = parser.feed("Hi")
        assert safe == ""  # Buffered

        prompt, metadata = parser.finalize()
        assert prompt == "Hi"
        assert metadata is None  # JSON fallback fails on "Hi"

    def test_progressive_flush(self):
        """Text beyond the safety margin is emitted progressively."""
        parser = OptimizeStreamParser()
        # Feed a long string with no marker
        text = "A" * 100
        safe = parser.feed(text)
        prompt, _ = parser.finalize()

        # Some text should have been emitted (beyond safety margin)
        assert len(safe) > 0
        assert len(safe) < 100  # Safety margin retained
        # Full text recovered after finalize
        assert prompt == text


# ---------------------------------------------------------------------------
# Metadata parsing
# ---------------------------------------------------------------------------

class TestMetadataParsing:
    """Tests for JSON metadata extraction from the <optimization_meta> block."""

    def test_valid_metadata(self):
        """Valid JSON metadata is correctly parsed."""
        meta = {
            "changes_made": ["added role", "added constraints"],
            "framework_applied": "CO-STAR",
            "optimization_notes": "Improved structure"
        }
        full = _make_full_output("Prompt text here.", meta)

        parser = OptimizeStreamParser()
        parser.feed(full)
        prompt, metadata = parser.finalize()

        assert prompt == "Prompt text here."
        assert metadata == meta

    def test_malformed_metadata_json(self):
        """Malformed JSON in metadata block returns (prompt, None)."""
        full = "Prompt text.\n\n<optimization_meta>\n{invalid json\n</optimization_meta>"

        parser = OptimizeStreamParser()
        parser.feed(full)
        prompt, metadata = parser.finalize()

        assert "Prompt text." in prompt
        assert metadata is None

    def test_empty_metadata_block(self):
        """Empty metadata block returns (prompt, None)."""
        full = "Prompt text.\n\n<optimization_meta>\n\n</optimization_meta>"

        parser = OptimizeStreamParser()
        parser.feed(full)
        prompt, metadata = parser.finalize()

        assert "Prompt text." in prompt
        assert metadata is None

    def test_metadata_without_close_tag(self):
        """Metadata block opened but never closed — still extracts JSON."""
        meta = {"changes_made": ["test"], "framework_applied": "X"}
        full = f"Prompt.\n\n<optimization_meta>\n{json.dumps(meta)}"
        # No closing tag

        parser = OptimizeStreamParser()
        parser.feed(full)
        prompt, metadata = parser.finalize()

        assert "Prompt." in prompt
        assert metadata is not None
        assert metadata["framework_applied"] == "X"

    def test_extra_whitespace_around_metadata(self):
        """Whitespace around JSON in metadata block is trimmed."""
        meta = {"changes_made": ["ws"], "framework_applied": "test"}
        full = f"Prompt.\n\n<optimization_meta>\n  \n  {json.dumps(meta)}  \n  \n</optimization_meta>"

        parser = OptimizeStreamParser()
        parser.feed(full)
        prompt, metadata = parser.finalize()

        assert metadata is not None
        assert metadata["changes_made"] == ["ws"]


# ---------------------------------------------------------------------------
# JSON fallback (backward compatibility)
# ---------------------------------------------------------------------------

class TestJSONFallback:
    """Tests for pure JSON output (no marker) — backward compat with old prompt format."""

    def test_pure_json_output(self):
        """LLM outputs pure JSON (old format) — falls back to parse_json_robust."""
        json_output = json.dumps({
            "optimized_prompt": "The optimized prompt text.",
            "changes_made": ["fallback test"],
            "framework_applied": "CO-STAR",
            "optimization_notes": "JSON fallback"
        })

        parser = OptimizeStreamParser()
        parser.feed(json_output)
        prompt, metadata = parser.finalize()

        assert prompt == "The optimized prompt text."
        assert metadata is not None
        assert metadata["optimized_prompt"] == "The optimized prompt text."
        assert metadata["changes_made"] == ["fallback test"]

    def test_json_with_markdown_fences(self):
        """JSON wrapped in markdown code fences — parse_json_robust handles this."""
        inner = {
            "optimized_prompt": "Fenced prompt.",
            "changes_made": ["fence test"],
            "framework_applied": "RISEN"
        }
        fenced = f"```json\n{json.dumps(inner)}\n```"

        parser = OptimizeStreamParser()
        parser.feed(fenced)
        prompt, metadata = parser.finalize()

        assert prompt == "Fenced prompt."
        assert metadata is not None

    def test_plain_text_no_json_no_marker(self):
        """Plain text without JSON or marker — returns text as prompt, None metadata."""
        text = "This is just a plain optimized prompt without any structure."

        parser = OptimizeStreamParser()
        parser.feed(text)
        prompt, metadata = parser.finalize()

        assert prompt == text
        assert metadata is None


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    """Edge cases and boundary conditions."""

    def test_empty_input(self):
        """No input at all — returns empty string and None."""
        parser = OptimizeStreamParser()
        prompt, metadata = parser.finalize()
        assert prompt == ""
        assert metadata is None

    def test_only_metadata_no_prompt(self):
        """Only a metadata block, no prompt text."""
        meta = {"changes_made": ["test"], "framework_applied": "X"}
        full = f"{OPTIMIZATION_META_OPEN}\n{json.dumps(meta)}\n{OPTIMIZATION_META_CLOSE}"

        parser = OptimizeStreamParser()
        parser.feed(full)
        prompt, metadata = parser.finalize()

        assert prompt == ""
        assert metadata is not None
        assert metadata["framework_applied"] == "X"

    def test_multiple_feeds_no_marker(self):
        """Multiple feed() calls without a marker — all text accumulated."""
        parser = OptimizeStreamParser()
        parser.feed("Hello ")
        parser.feed("world ")
        parser.feed("this is a test.")
        prompt, _ = parser.finalize()

        assert prompt == "Hello world this is a test."

    def test_accumulated_prompt_property(self):
        """accumulated_prompt reflects prompt text seen so far (for timeout recovery)."""
        parser = OptimizeStreamParser()
        # Feed enough to exceed safety buffer
        long_prompt = "X" * 100
        parser.feed(long_prompt)

        # accumulated_prompt should contain everything fed (prompt_text + buffer)
        assert len(parser.accumulated_prompt) == 100

    def test_metadata_in_stream_suppressed(self):
        """After the marker, no metadata text leaks to the safe output."""
        meta = {"changes_made": ["secret"], "framework_applied": "hidden"}
        full = _make_full_output("Visible prompt.", meta)

        parser = OptimizeStreamParser()
        all_safe = ""
        for char in full:
            all_safe += parser.feed(char)
        prompt, metadata = parser.finalize()

        assert "secret" not in all_safe
        assert "hidden" not in all_safe
        assert "optimization_meta" not in all_safe
        assert "Visible prompt." in prompt
        assert metadata is not None

    def test_prompt_with_angle_brackets(self):
        """Prompt containing < characters that aren't the marker — not falsely detected."""
        prompt_text = "Use <thinking> tags for chain of thought. Output <result> at the end."
        meta = {"changes_made": ["angle brackets"], "framework_applied": "test"}
        full = _make_full_output(prompt_text, meta)

        parser = OptimizeStreamParser()
        _feed_all(parser, full, chunk_size=5)
        prompt, metadata = parser.finalize()

        assert "<thinking>" in prompt
        assert "<result>" in prompt
        assert metadata is not None

    def test_newlines_in_prompt_preserved(self):
        """Multi-line prompt text is preserved with newlines intact."""
        prompt_text = "Line 1\n\nLine 2\n\nLine 3"
        meta = {"changes_made": ["newlines"], "framework_applied": "test"}
        full = _make_full_output(prompt_text, meta)

        parser = OptimizeStreamParser()
        parser.feed(full)
        prompt, metadata = parser.finalize()

        assert "Line 1\n\nLine 2\n\nLine 3" in prompt
        assert metadata is not None


# ---------------------------------------------------------------------------
# Integration with run_optimize extraction logic
# ---------------------------------------------------------------------------

class TestExtractionLogicIntegration:
    """Verify the parser output integrates correctly with the post-extraction logic."""

    def test_new_format_no_optimized_prompt_in_meta(self):
        """New format: metadata has changes_made etc. but NOT optimized_prompt.
        The extraction logic should use prompt_text, not look for optimized_prompt in metadata.
        """
        meta = {
            "changes_made": ["added specificity"],
            "framework_applied": "CO-STAR",
            "optimization_notes": "Better structure"
        }
        full = _make_full_output("The actual optimized prompt text.", meta)

        parser = OptimizeStreamParser()
        parser.feed(full)
        prompt_text, metadata = parser.finalize()

        # Simulate the extraction logic from run_optimize
        assert metadata is not None
        assert isinstance(metadata, dict)
        # "optimized_prompt" NOT in metadata → use prompt_text
        assert "optimized_prompt" not in metadata
        optimized_prompt = (
            metadata.get("optimized_prompt", prompt_text)
            if "optimized_prompt" in metadata
            else prompt_text
        )
        assert optimized_prompt == "The actual optimized prompt text."

    def test_json_fallback_has_optimized_prompt_in_meta(self):
        """JSON fallback: metadata HAS optimized_prompt.
        The extraction logic should use it from metadata.
        """
        json_output = json.dumps({
            "optimized_prompt": "JSON prompt text.",
            "changes_made": ["from JSON"],
            "framework_applied": "RISEN"
        })

        parser = OptimizeStreamParser()
        parser.feed(json_output)
        prompt_text, metadata = parser.finalize()

        # Simulate the extraction logic from run_optimize
        assert metadata is not None
        assert "optimized_prompt" in metadata
        optimized_prompt = (
            metadata.get("optimized_prompt", prompt_text)
            if "optimized_prompt" in metadata
            else prompt_text
        )
        assert optimized_prompt == "JSON prompt text."
