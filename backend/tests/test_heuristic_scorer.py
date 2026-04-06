"""Tests for HeuristicScorer — TDD: tests written before implementation."""

from app.services.heuristic_scorer import HeuristicScorer

# ---------------------------------------------------------------------------
# heuristic_structure
# ---------------------------------------------------------------------------


def test_structure_score_with_headers() -> None:
    """Prompt with headers and lists should score > 5.0."""
    prompt = (
        "## Task\n"
        "Summarize the following document.\n\n"
        "## Requirements\n"
        "- Be concise\n"
        "- Use bullet points\n"
        "- Include key facts\n\n"
        "## Output format\n"
        "Return a JSON object with a 'summary' key."
    )
    score = HeuristicScorer.heuristic_structure(prompt)
    assert score > 5.0


def test_structure_score_wall_of_text() -> None:
    """Plain unstructured text should score < 5.0."""
    prompt = (
        "please summarize this document for me and make it short and "
        "also make sure to include the most important points and keep it readable"
    )
    score = HeuristicScorer.heuristic_structure(prompt)
    assert score < 5.0


# ---------------------------------------------------------------------------
# heuristic_conciseness
# ---------------------------------------------------------------------------


def test_conciseness_verbose() -> None:
    """Filler-heavy prompt should score < 6.0."""
    prompt = (
        "Please note that it is very important that you make sure to "
        "basically just essentially try to sort of generally summarize "
        "the text in a way that is kind of helpful and perhaps useful "
        "to the reader as much as possible."
    )
    score = HeuristicScorer.heuristic_conciseness(prompt)
    assert score < 6.0


def test_conciseness_tight() -> None:
    """Concise, non-repetitive prompt should score > 5.0."""
    prompt = "Summarize the document. Output JSON with keys: title, summary, keywords."
    score = HeuristicScorer.heuristic_conciseness(prompt)
    assert score > 5.0


# ---------------------------------------------------------------------------
# heuristic_specificity
# ---------------------------------------------------------------------------


def test_specificity_with_constraints() -> None:
    """Constraint-rich prompt should score > 5.0."""
    prompt = (
        "You must return a JSON object. "
        "The function shall raise ValueError when input is None. "
        "It should handle strings of type str and integers of type int. "
        "Format: {result: string, count: number}. "
        "For example: {result: 'hello', count: 3}. "
        "The output must contain at least 3 items."
    )
    score = HeuristicScorer.heuristic_specificity(prompt)
    assert score > 5.0


# ---------------------------------------------------------------------------
# heuristic_clarity (v2 — precision signals + ambiguity, no Flesch)
# ---------------------------------------------------------------------------


def test_clarity_vague_prompt_scores_low() -> None:
    """Vague prompt with no precision signals should score near base (5.0)."""
    prompt = "write some code to handle user data"
    score = HeuristicScorer.heuristic_clarity(prompt)
    assert score <= 5.5, f"Vague prompt scored {score}, expected <= 5.5"


def test_clarity_structured_technical_prompt_scores_high() -> None:
    """Well-structured technical prompt with constraints should score > 7."""
    prompt = (
        "## Task\n"
        "Write validate_email(addr: str) -> bool.\n\n"
        "## Requirements\n"
        "- Must validate against RFC 5322\n"
        "- Raise ValueError if addr is None\n"
        "- Return False on invalid format\n\n"
        "## Output\n"
        "Python function with type hints."
    )
    score = HeuristicScorer.heuristic_clarity(prompt)
    assert score > 7.0, f"Structured prompt scored {score}, expected > 7.0"


def test_clarity_xml_structured_prompt_not_penalized() -> None:
    """XML-structured prompt should score well (not penalized by Flesch)."""
    prompt = (
        "<role>Senior code reviewer</role>\n"
        "<task>Review the code diff for security vulnerabilities.</task>\n"
        "<output-format>\n"
        "- Severity: critical / warning / info\n"
        "- Location: file:line\n"
        "</output-format>"
    )
    score = HeuristicScorer.heuristic_clarity(prompt)
    assert score > 6.0, f"XML prompt scored {score}, expected > 6.0"


def test_clarity_ambiguity_identifiers_not_penalized() -> None:
    """Words like 'maybe' inside identifiers should not trigger penalty."""
    prompt = (
        "Parse the etc_config field. Use maybe_transform() to coerce null. "
        "Handle the things_queue from RabbitMQ."
    )
    score = HeuristicScorer.heuristic_clarity(prompt)
    assert score >= 5.0, f"Identifier FP scored {score}, expected >= 5.0"


def test_clarity_genuine_ambiguity_penalized() -> None:
    """Standalone ambiguity words should still reduce clarity."""
    prompt = "Maybe do something about the stuff. Perhaps try things somehow."
    score = HeuristicScorer.heuristic_clarity(prompt)
    assert score < 4.0, f"Ambiguous prompt scored {score}, expected < 4.0"


# ---------------------------------------------------------------------------
# heuristic_specificity (v2 — 10 categories, graduated density)
# ---------------------------------------------------------------------------


def test_specificity_dense_notation_not_penalized() -> None:
    """Dense shorthand should score comparably to verbose phrasing."""
    dense = (
        "validate_email(addr: str) -> bool. RFC 5322 regex. "
        "False on invalid. ValueError if None. TypeError if not str. "
        "Include docstring with 3 examples."
    )
    verbose = (
        "You should write a function that must return a bool. "
        "It should raise a ValueError when input is None. "
        "Please raise a TypeError when the input is not a string type. "
        "Format the output as a Python function. "
        "For example: validate_email('test@example.com') returns True. "
        "It must handle at least 3 edge cases."
    )
    dense_score = HeuristicScorer.heuristic_specificity(dense)
    verbose_score = HeuristicScorer.heuristic_specificity(verbose)
    assert abs(dense_score - verbose_score) < 2.0, (
        f"Dense={dense_score} vs Verbose={verbose_score}, gap > 2.0"
    )


def test_specificity_creative_prompt_scores_above_5() -> None:
    """Creative prompts with constraints should score > 5.0."""
    prompt = (
        "Write a short story about a lighthouse keeper. "
        "Exactly 500 words, first person present tense, "
        "with a twist ending. Set it during a storm. "
        "The tone should be literary horror — dread, not gore."
    )
    score = HeuristicScorer.heuristic_specificity(prompt)
    assert score > 5.0, f"Creative prompt scored {score}, expected > 5.0"


def test_specificity_error_types_detected() -> None:
    """Error/exception type names should count as specificity signals."""
    prompt = "Raise ValueError on invalid input. Raise TypeError on non-string."
    score = HeuristicScorer.heuristic_specificity(prompt)
    assert score > 5.0, f"Error types scored {score}, expected > 5.0"


def test_specificity_multiple_constraints_score_higher() -> None:
    """More constraints in same category -> higher score (graduated)."""
    single = "You must validate the input."
    multiple = "You must validate the input. You must log errors. You must retry on failure. You must return JSON."
    single_score = HeuristicScorer.heuristic_specificity(single)
    multiple_score = HeuristicScorer.heuristic_specificity(multiple)
    assert multiple_score > single_score, (
        f"Multiple={multiple_score} should exceed Single={single_score}"
    )
