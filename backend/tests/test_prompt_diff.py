"""Tests for prompt diff utilities — divergence, cycles, hashing."""

from app.services.prompt_diff import (
    CycleResult,
    compute_dimension_deltas,
    compute_prompt_divergence,
    compute_prompt_hash,
    detect_cycle,
    extract_structure,
)


class TestComputePromptHash:
    def test_identical_prompts_same_hash(self):
        h1 = compute_prompt_hash("Hello world")
        h2 = compute_prompt_hash("Hello world")
        assert h1 == h2

    def test_whitespace_normalized(self):
        h1 = compute_prompt_hash("Hello   world")
        h2 = compute_prompt_hash("Hello world")
        assert h1 == h2

    def test_case_insensitive(self):
        h1 = compute_prompt_hash("Hello World")
        h2 = compute_prompt_hash("hello world")
        assert h1 == h2

    def test_different_prompts_different_hash(self):
        h1 = compute_prompt_hash("Hello world")
        h2 = compute_prompt_hash("Goodbye world")
        assert h1 != h2

    def test_hash_length_is_16(self):
        h = compute_prompt_hash("test")
        assert len(h) == 16

    def test_empty_string(self):
        h = compute_prompt_hash("")
        assert len(h) == 16


class TestComputeDimensionDeltas:
    def test_basic_deltas(self):
        before = {"clarity_score": 6, "specificity_score": 5}
        after = {"clarity_score": 7, "specificity_score": 5}
        deltas = compute_dimension_deltas(before, after)
        assert deltas["clarity_score"] == 1
        assert deltas["specificity_score"] == 0

    def test_negative_delta(self):
        before = {"structure_score": 7}
        after = {"structure_score": 5}
        deltas = compute_dimension_deltas(before, after)
        assert deltas["structure_score"] == -2

    def test_missing_dimension_skipped(self):
        before = {"clarity_score": 6}
        after = {}
        deltas = compute_dimension_deltas(before, after)
        assert deltas == {}

    def test_computes_deltas_correctly(self):
        before = {"clarity_score": 5.0, "structure_score": 7.0}
        after = {"clarity_score": 8.0, "structure_score": 6.0}
        deltas = compute_dimension_deltas(before, after)
        assert deltas["clarity_score"] == 3.0
        assert deltas["structure_score"] == -1.0

    def test_missing_dimensions_skipped(self):
        deltas = compute_dimension_deltas(
            {"clarity_score": 5.0}, {"structure_score": 7.0}
        )
        assert deltas == {}


class TestDetectCycle:
    def test_no_cycle_empty(self):
        assert detect_cycle("abc123", []) is None

    def test_no_cycle_unique(self):
        assert detect_cycle("abc123", ["def456", "ghi789"]) is None

    def test_hard_cycle_exact_hash_match(self):
        h = compute_prompt_hash("same prompt")
        result = detect_cycle(h, [compute_prompt_hash("other"), h])
        assert isinstance(result, CycleResult)
        assert result.type == "hard"
        assert result.matched_attempt == 2

    def test_no_cycle_different_hashes(self):
        result = detect_cycle(
            compute_prompt_hash("prompt a"),
            [compute_prompt_hash("prompt b"), compute_prompt_hash("prompt c")],
        )
        assert result is None

    def test_soft_cycle_low_divergence_no_deltas(self):
        result = detect_cycle(
            compute_prompt_hash("unique hash"),
            [compute_prompt_hash("other")],
            current_divergence=0.05,
        )
        assert result is not None
        assert result.type == "soft"

    def test_soft_cycle_compound_condition(self):
        result = detect_cycle(
            compute_prompt_hash("unique"),
            [compute_prompt_hash("other")],
            current_divergence=0.05,
            dimension_deltas={"clarity_score": 0.1},
        )
        assert result is not None
        assert result.type == "soft"

    def test_no_soft_cycle_high_deltas(self):
        result = detect_cycle(
            compute_prompt_hash("unique"),
            [compute_prompt_hash("other")],
            current_divergence=0.05,
            dimension_deltas={"clarity_score": 2.0},
        )
        assert result is None

    def test_no_soft_cycle_above_threshold(self):
        result = detect_cycle(
            compute_prompt_hash("unique hash"),
            [compute_prompt_hash("other")],
            current_divergence=0.5,
        )
        assert result is None

    def test_oscillation_detected(self):
        # A -> B -> A pattern
        result = detect_cycle("hash_a", ["hash_a", "hash_b"])
        assert result is not None
        assert result.type == "hard"
        assert result.matched_attempt == 1


class TestPromptDivergence:
    def test_identical_prompts_zero_divergence(self):
        assert compute_prompt_divergence("hello world", "hello world") == 0.0

    def test_completely_different_prompts_high_divergence(self):
        d = compute_prompt_divergence(
            "Write a Python function to sort a list",
            "Explain quantum mechanics in simple terms for children",
        )
        assert d >= 0.7

    def test_minor_rephrasing_low_divergence(self):
        d = compute_prompt_divergence(
            "Write a function that sorts numbers in ascending order",
            "Write a function that sorts numbers in ascending sequence",
        )
        assert d < 0.5

    def test_structural_change_moderate_divergence(self):
        flat = "First do A. Then do B. Finally do C."
        structured = "Steps:\n1. First do A\n2. Then do B\n3. Finally do C"
        d = compute_prompt_divergence(flat, structured)
        assert 0.2 < d < 0.6

    def test_empty_vs_content_max_divergence(self):
        assert compute_prompt_divergence("", "hello world") == 1.0

    def test_both_empty_zero_divergence(self):
        assert compute_prompt_divergence("", "") == 0.0

    def test_returns_clamped_0_to_1(self):
        d = compute_prompt_divergence("a" * 1000, "b" * 5)
        assert 0.0 <= d <= 1.0


class TestExtractStructure:
    def test_counts_lines(self):
        s = extract_structure("line 1\nline 2\nline 3")
        assert s["lines"] == 3

    def test_counts_paragraphs(self):
        s = extract_structure("para 1\n\npara 2\n\npara 3")
        assert s["paragraphs"] == 3

    def test_counts_list_items(self):
        s = extract_structure("- item 1\n- item 2\n1. item 3")
        assert s["lists"] == 3

    def test_counts_code_blocks(self):
        s = extract_structure("text\n```python\ncode\n```\nmore text")
        assert s["code_blocks"] == 1
