"""Tests for extract_structural_patterns — zero-LLM meta-pattern extraction."""

from app.services.taxonomy.family_ops import extract_structural_patterns

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# A bare, unstructured prompt (low structure/specificity scores)
RAW_BARE = "Write a function that sorts a list"

# A well-structured optimized version (high structure/specificity/clarity)
OPT_STRUCTURED = """\
## Task

Write a Python function `merge_sort(items: list[int]) -> list[int]` that:

1. Accepts a list of integers
2. Returns a new sorted list using the merge sort algorithm
3. Must not modify the original list
4. Must raise TypeError if input is not a list

## Output Format

Return type: `list[int]`

## Example

For example, `merge_sort([3, 1, 2])` should return `[1, 2, 3]`.
"""


class TestScoreDeltaDetection:
    """Mechanism A: patterns emitted when score delta crosses threshold."""

    def test_structure_improvement_detected(self) -> None:
        patterns = extract_structural_patterns(RAW_BARE, OPT_STRUCTURED)
        structure_patterns = [p for p in patterns if "header" in p.lower() or "organize" in p.lower()]
        assert len(structure_patterns) >= 1

    def test_specificity_improvement_detected(self) -> None:
        patterns = extract_structural_patterns(RAW_BARE, OPT_STRUCTURED)
        spec_patterns = [p for p in patterns if "constraint" in p.lower() or "specific" in p.lower()]
        assert len(spec_patterns) >= 1

    def test_no_patterns_when_prompts_similar(self) -> None:
        # Same prompt for both — no delta, should get fallback only
        patterns = extract_structural_patterns(RAW_BARE, RAW_BARE)
        assert len(patterns) >= 1  # at least fallback


class TestRegexDetection:
    """Mechanism B: patterns emitted when structural elements added."""

    def test_headers_added(self) -> None:
        raw = "Sort a list please"
        opt = "## Task\n\nSort a list\n\n## Output\n\nReturn sorted"
        patterns = extract_structural_patterns(raw, opt)
        header_patterns = [p for p in patterns if "header" in p.lower()]
        assert len(header_patterns) >= 1

    def test_lists_added(self) -> None:
        raw = "Build a system"
        opt = "Build a system:\n- Handle auth\n- Handle data\n- Handle errors"
        patterns = extract_structural_patterns(raw, opt)
        list_patterns = [p for p in patterns if "list" in p.lower()]
        assert len(list_patterns) >= 1

    def test_xml_tags_added(self) -> None:
        raw = "Analyze this data"
        opt = "<context>User data</context>\n<instructions>Analyze this data</instructions>"
        patterns = extract_structural_patterns(raw, opt)
        xml_patterns = [p for p in patterns if "xml" in p.lower() or "tag" in p.lower()]
        assert len(xml_patterns) >= 1

    def test_format_keywords_added(self) -> None:
        raw = "Give me the result"
        opt = "Give me the result in JSON schema format"
        patterns = extract_structural_patterns(raw, opt)
        format_patterns = [p for p in patterns if "format" in p.lower()]
        assert len(format_patterns) >= 1

    def test_example_keywords_added(self) -> None:
        raw = "Parse the input"
        opt = "Parse the input. For example, given '123' return 123."
        patterns = extract_structural_patterns(raw, opt)
        example_patterns = [p for p in patterns if "example" in p.lower()]
        assert len(example_patterns) >= 1

    def test_constraint_modals_added(self) -> None:
        raw = "Write code"
        opt = "Write code. You must handle errors. You must validate input. You should log."
        patterns = extract_structural_patterns(raw, opt)
        modal_patterns = [p for p in patterns if "must" in p.lower() or "modal" in p.lower()]
        assert len(modal_patterns) >= 1


class TestEdgeCases:
    """Dedup, capping, and fallback behavior."""

    def test_capped_at_five(self) -> None:
        # Provide prompts that trigger many patterns
        raw = "Do stuff"
        opt = (
            "## Task\n\n## Details\n\n"
            "- Item one\n- Item two\n- Item three\n"
            "<context>ctx</context>\n<output>out</output>\n"
            "You must do X. You shall do Y. You should do Z.\n"
            "Output in JSON format.\n"
            "For example, return {key: value}."
        )
        patterns = extract_structural_patterns(raw, opt)
        assert len(patterns) <= 5

    def test_minimum_one_pattern(self) -> None:
        patterns = extract_structural_patterns("hello", "hello")
        assert len(patterns) >= 1

    def test_fallback_pattern_is_generic(self) -> None:
        patterns = extract_structural_patterns("hello", "hello")
        assert any("weakest" in p.lower() or "structural" in p.lower() for p in patterns)

    def test_patterns_are_nonempty_strings(self) -> None:
        patterns = extract_structural_patterns(RAW_BARE, OPT_STRUCTURED)
        for p in patterns:
            assert isinstance(p, str)
            assert len(p) > 10  # meaningful description, not empty

    def test_no_duplicate_structure_patterns(self) -> None:
        """When both mechanisms detect structure improvement, no duplicates."""
        patterns = extract_structural_patterns(RAW_BARE, OPT_STRUCTURED)
        # Check no pattern is a substring of another
        for i, a in enumerate(patterns):
            for j, b in enumerate(patterns):
                if i != j:
                    assert a not in b, f"Pattern {i} is substring of {j}: '{a}' in '{b}'"
