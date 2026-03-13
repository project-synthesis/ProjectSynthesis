from app.services.prompt_diff import (
    compute_dimension_deltas,
    compute_prompt_entropy,
    compute_prompt_hash,
    detect_cycle,
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


class TestDetectCycle:
    def test_no_cycle_empty(self):
        assert detect_cycle("abc123", []) is None

    def test_no_cycle_unique(self):
        assert detect_cycle("abc123", ["def456", "ghi789"]) is None

    def test_cycle_detected(self):
        result = detect_cycle("abc123", ["def456", "abc123"])
        assert result == 2  # matching attempt number (1-indexed)

    def test_oscillation_detected(self):
        # A -> B -> A pattern
        result = detect_cycle("hash_a", ["hash_a", "hash_b"])
        assert result == 1


class TestComputePromptEntropy:
    def test_identical_prompts_zero_entropy(self):
        e = compute_prompt_entropy("Hello world.", "Hello world.")
        assert e == 0.0

    def test_completely_different_prompts_high_entropy(self):
        e = compute_prompt_entropy(
            "The cat sat on the mat.",
            "A completely unrelated sentence about quantum physics.",
        )
        assert e > 0.5

    def test_empty_prompts_zero_entropy(self):
        e = compute_prompt_entropy("", "")
        assert e == 0.0

    def test_entropy_bounded_zero_to_one(self):
        e = compute_prompt_entropy("One sentence.", "Another sentence.")
        assert 0.0 <= e <= 1.0
