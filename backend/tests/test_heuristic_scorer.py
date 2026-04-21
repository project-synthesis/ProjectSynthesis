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


# ---------------------------------------------------------------------------
# heuristic_conciseness (v2 — minimum information gate)
# ---------------------------------------------------------------------------


def test_conciseness_vague_short_prompt_capped() -> None:
    """Very short vague prompts should not score high on conciseness."""
    prompt = "write some code to handle user data"
    score = HeuristicScorer.heuristic_conciseness(prompt)
    assert score < 6.0, f"7-word vague prompt scored {score}, expected < 6.0"


def test_conciseness_short_but_dense_still_reasonable() -> None:
    """Short prompts that ARE dense should score moderately."""
    prompt = "sum_list(numbers: list[float]) -> float. Sum of elements. Empty returns 0.0."
    score = HeuristicScorer.heuristic_conciseness(prompt)
    assert score >= 5.0, f"Dense short prompt scored {score}, expected >= 5.0"


# ---------------------------------------------------------------------------
# heuristic_structure (v2 — XML parity)
# ---------------------------------------------------------------------------


def test_structure_xml_sections_score_like_headers() -> None:
    """XML section pairs should score comparably to markdown headers."""
    xml_prompt = (
        "<role>Senior code reviewer</role>\n"
        "<task>Review code for security issues.</task>\n"
        "<output-format>\n"
        "- Severity: critical / warning / info\n"
        "- Fix: concrete suggestion\n"
        "</output-format>\n"
        "<code_diff>{{diff}}</code_diff>"
    )
    md_prompt = (
        "## Role\nSenior code reviewer\n\n"
        "## Task\nReview code for security issues.\n\n"
        "## Output format\n"
        "- Severity: critical / warning / info\n"
        "- Fix: concrete suggestion\n\n"
        "{{diff}}"
    )
    xml_score = HeuristicScorer.heuristic_structure(xml_prompt)
    md_score = HeuristicScorer.heuristic_structure(md_prompt)
    assert xml_score >= 8.5, f"XML prompt scored {xml_score}, expected >= 8.5"
    assert abs(xml_score - md_score) < 2.0, (
        f"XML={xml_score} vs MD={md_score}, gap should be < 2.0"
    )


# ---------------------------------------------------------------------------
# Full 8-prompt validation matrix
# ---------------------------------------------------------------------------


class TestScoringValidationMatrix:
    """Validates all heuristic dimensions against diverse prompt types."""

    P1_VAGUE = "write some code to handle user data"

    P2_STRUCTURED = (
        "## Role\nYou are a senior Python engineer.\n\n"
        "## Task\nWrite a function validate_email(addr: str) -> bool that:\n"
        "1. Validates against RFC 5322 via re module\n"
        "2. Returns False on invalid format\n"
        "3. Raises ValueError if addr is None\n"
        "4. Raises TypeError if addr is not str\n\n"
        "## Output\nPython function with type hints, docstring, and 3 usage examples."
    )

    P3_DENSE = (
        "validate_email(addr: str) -> bool. RFC 5322 regex. False on invalid. "
        "ValueError if None. TypeError if not str. Include docstring with 3 examples."
    )

    P5_XML = (
        "<role>Senior code reviewer</role>\n"
        "<task>Review the following code diff for security vulnerabilities, "
        "performance issues, and API contract violations.</task>\n"
        "<output-format>\n"
        "For each finding:\n"
        "- Severity: critical / warning / info\n"
        "- Location: file:line\n"
        "- Issue: one-sentence description\n"
        "- Fix: concrete code suggestion\n"
        "</output-format>\n"
        "<code_diff>{{code_diff}}</code_diff>"
    )

    P6_CREATIVE = (
        "Write a short story about a lighthouse keeper who discovers that "
        "the light attracts something other than ships. The story should be "
        "exactly 500 words, written in first person present tense, with a "
        "twist ending. Set it during a storm. The tone should be literary "
        "horror — dread, not gore."
    )

    P8_FP_AMBIGUITY = (
        "You must handle the following things: (1) parse the input set of items, "
        "(2) return something useful — specifically a dict mapping each item to its "
        "frequency. Handle the etc field in the metadata. Maybe-null values should "
        "be coerced to 0. Use the perhaps_valid flag from config to gate the output."
    )

    def test_p1_vague_clarity_low(self) -> None:
        assert HeuristicScorer.heuristic_clarity(self.P1_VAGUE) < 5.5

    def test_p1_vague_conciseness_capped(self) -> None:
        assert HeuristicScorer.heuristic_conciseness(self.P1_VAGUE) < 6.0

    def test_p5_xml_clarity_above_6(self) -> None:
        assert HeuristicScorer.heuristic_clarity(self.P5_XML) > 6.0

    def test_p5_xml_structure_above_8_5(self) -> None:
        assert HeuristicScorer.heuristic_structure(self.P5_XML) > 8.5

    def test_p6_creative_specificity_above_6(self) -> None:
        assert HeuristicScorer.heuristic_specificity(self.P6_CREATIVE) > 6.0

    def test_p8_fp_ambiguity_clarity_above_5(self) -> None:
        # Should not be destroyed by false-positive ambiguity penalties
        assert HeuristicScorer.heuristic_clarity(self.P8_FP_AMBIGUITY) > 5.0

    def test_p3_dense_vs_p2_specificity_within_range(self) -> None:
        dense = HeuristicScorer.heuristic_specificity(self.P3_DENSE)
        structured = HeuristicScorer.heuristic_specificity(self.P2_STRUCTURED)
        assert abs(dense - structured) < 2.5


# ---------------------------------------------------------------------------
# heuristic_faithfulness — strategy-aware expansion tolerance (I-5)
# ---------------------------------------------------------------------------
# Real-world divergence: expansion-style strategies (meta-prompting,
# role-playing, chain-of-thought) deliberately balloon a 300-char prompt into
# a 15K+ char scaffold. The pure cosine heuristic sees this as semantic drift
# and returns ~5.x while the LLM scorer correctly rates faithfulness ~9.x.
# A strategy-class-aware dampener keeps the heuristic in line on expansion
# while preserving the penalty for direct-instruction strategies.


_RAW_I5 = (
    "Write me a blog post about how to set up a CI pipeline for a Python "
    "FastAPI backend with GitHub Actions. Cover linting, tests, type checks, "
    "and deployment to Fly.io. Keep it practical for junior developers."
)


def _expansion_scaffold(raw: str) -> str:
    """Synthetic ~15K-char meta-prompted rewrite covering every raw concept."""
    return (
        "<role>\n"
        "You are a Senior DevOps Engineer and Technical Writer with 10+ years "
        "of experience publishing practical CI/CD tutorials for Python web "
        "services. You have shipped production FastAPI backends, maintained "
        "GitHub Actions workflows, configured linting (Ruff) and type checks "
        "(mypy/pyright), wired up pytest and coverage gates, and deployed to "
        "Fly.io from both monorepos and single-service repos.\n"
        "</role>\n\n"
        "<audience>\n"
        "Junior developers. Assume Python familiarity but NOT prior CI/CD, "
        "GitHub Actions, or Fly.io experience. Explain each concept before "
        "using it. Favor concrete commands and copy-pasteable YAML over "
        "abstract advice.\n"
        "</audience>\n\n"
        "<task>\n"
        "Write a practical blog post that walks a junior developer through "
        "setting up a CI pipeline for a Python FastAPI backend using GitHub "
        "Actions. The pipeline must cover: (1) linting with Ruff, (2) pytest "
        "with coverage, (3) type checks with mypy or pyright, and "
        "(4) continuous deployment to Fly.io on main branch merges.\n"
        "</task>\n\n"
        "<structure>\n"
        "1. Hook + promise (what the reader will have by the end).\n"
        "2. Prerequisites (Python 3.12, FastAPI project, Fly.io account, "
        "GitHub repo).\n"
        "3. Repository layout assumptions (pyproject.toml, tests/, app/).\n"
        "4. Workflow anatomy: triggers, jobs, steps, runners.\n"
        "5. Linting job — Ruff install, ruff check, ruff format --check.\n"
        "6. Test job — pytest with coverage, failure gate at 80%.\n"
        "7. Type-check job — mypy config + CI invocation.\n"
        "8. Deploy job — gated on previous jobs, Fly.io deploy token, "
        "flyctl deploy --remote-only.\n"
        "9. Secrets management — GitHub repository secrets, never commit.\n"
        "10. Wrap-up — badge in README, next steps.\n"
        "</structure>\n\n"
        "<tone>\n"
        "Practical, encouraging, and specific. Avoid jargon without a "
        "one-line definition the first time it appears. Use the second "
        "person ('you') throughout. Keep sentences short.\n"
        "</tone>\n\n"
        "<constraints>\n"
        "- Length: 1500-2500 words.\n"
        "- Every YAML snippet must be valid and directly paste-able into "
        "`.github/workflows/ci.yml`.\n"
        "- Cite exact action versions (e.g., `actions/checkout@v4`, "
        "`actions/setup-python@v5`).\n"
        "- Include a complete end-to-end `ci.yml` file at the end.\n"
        "- Call out common mistakes at each step (missing `fetch-depth`, "
        "forgotten cache keys, secret leaks in logs, etc.).\n"
        "- Do NOT recommend deprecated actions or Python 3.8/3.9.\n"
        "- Do NOT gloss over Fly.io auth — show exactly how to create a "
        "deploy token and add it as `FLY_API_TOKEN`.\n"
        "</constraints>\n\n"
        "<self_check>\n"
        "Before responding, verify: (a) every claim is specific enough to "
        "act on, (b) every YAML block is syntactically valid, (c) the "
        "article explicitly covers linting, tests, type checks, AND "
        "deployment to Fly.io, (d) the tone is approachable for a junior "
        "developer, (e) the post includes a full reference workflow.\n"
        "</self_check>\n\n"
        "<output_format>\n"
        "Markdown. Use H2 for major sections and H3 for sub-sections. "
        "Use fenced code blocks with language hints (```yaml, ```bash, "
        "```python). Include a final section titled 'Complete workflow "
        "reference' with the full ci.yml.\n"
        "</output_format>\n\n"
        "<quality_bar>\n"
        "A junior developer should be able to copy the snippets, fill in "
        "their project's name, add the Fly.io token to GitHub secrets, "
        "and have a green pipeline on their next push — without any "
        "additional Googling.\n"
        "</quality_bar>\n\n"
        "Raw context from the requester:\n"
        f"{raw}\n"
    ) * 5  # quintuple for ~15K chars


def test_faithfulness_meta_prompting_does_not_penalize_expansion() -> None:
    """Meta-prompting is an expansion strategy — don't score faithfulness <7."""
    optimized = _expansion_scaffold(_RAW_I5)
    assert len(optimized) > 12000, "scaffold should be at least 12K chars"
    score = HeuristicScorer.heuristic_faithfulness(
        _RAW_I5, optimized, strategy_used="meta-prompting",
    )
    assert score >= 7.0, (
        f"meta-prompting expansion scored {score}, expected >= 7.0 "
        "(strategy-class dampener should exempt expansion strategies)"
    )


def test_faithfulness_direct_strategy_still_penalizes_excessive_expansion() -> None:
    """Direct/structured-output strategies should NOT get the expansion pass."""
    optimized = _expansion_scaffold(_RAW_I5)
    score_direct = HeuristicScorer.heuristic_faithfulness(
        _RAW_I5, optimized, strategy_used="structured-output",
    )
    score_expansion = HeuristicScorer.heuristic_faithfulness(
        _RAW_I5, optimized, strategy_used="meta-prompting",
    )
    # Expansion-class strategies should score at least as high as direct ones;
    # direct strategies stay at the baseline cosine mapping (no pass-through).
    assert score_expansion >= score_direct, (
        f"expansion={score_expansion} should be >= direct={score_direct}"
    )


def test_faithfulness_score_preserves_backward_compat_signature() -> None:
    """Old two-arg callers (no strategy_used) must keep working."""
    optimized = _expansion_scaffold(_RAW_I5)
    score = HeuristicScorer.heuristic_faithfulness(_RAW_I5, optimized)
    assert 1.0 <= score <= 10.0
    # score_prompt facade also still accepts the original 2-arg shape
    scores = HeuristicScorer.score_prompt(optimized, original=_RAW_I5)
    assert 1.0 <= scores["faithfulness"] <= 10.0
