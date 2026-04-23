"""Tests for Phase 4 weakness detector hardening.

Covers:
- Negation awareness: keywords preceded by negators should NOT count as
  positive signals.
- Context-aware density: structured prompts under 50 words should not be
  flagged as underspecified when structural density is high.
- Integration: end-to-end tests via HeuristicAnalyzer.analyze().

Copyright 2025-2026 Project Synthesis contributors.
"""

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.services.domain_signal_loader import DomainSignalLoader
from app.services.heuristic_analyzer import (
    HeuristicAnalyzer,
    set_signal_loader,
)
from app.services.weakness_detector import (
    _AUDIENCE_KEYWORDS,
    _CONSTRAINT_KEYWORDS,
    _OUTCOME_KEYWORDS,
    _STRUCTURAL_DENSITY_THRESHOLD,
    _compute_structural_density,
    _is_negated,
    detect_weaknesses,
    has_keyword_unnegated,
)


# ---- Fixtures ----

_TEST_DOMAIN_SIGNALS: dict[str, list[tuple[str, float]]] = {
    "backend": [
        ("api", 0.8), ("endpoint", 0.9), ("server", 0.8),
        ("middleware", 0.9), ("fastapi", 1.0), ("django", 1.0),
    ],
    "frontend": [
        ("react", 1.0), ("svelte", 1.0), ("component", 0.8),
    ],
    "database": [
        ("sql", 1.0), ("migration", 0.9), ("schema", 0.8),
    ],
}


@pytest.fixture(autouse=True)
def _seed_signal_loader():
    loader = DomainSignalLoader()
    loader._signals = dict(_TEST_DOMAIN_SIGNALS)
    loader._precompile_patterns()
    set_signal_loader(loader)
    yield
    set_signal_loader(None)


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    from app.models import Base
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with session_factory() as session:
        yield session
    await engine.dispose()


# =====================================================================
# Unit tests: _is_negated
# =====================================================================


class TestIsNegated:
    """Low-level negation detection on individual keyword occurrences."""

    def test_simple_negation_no(self) -> None:
        assert _is_negated("there are no constraints here", "constraints") is True

    def test_simple_negation_not(self) -> None:
        assert _is_negated("you do not need to return anything", "return") is True

    def test_simple_negation_without(self) -> None:
        assert _is_negated("without any constraint on the output", "constraint") is True

    def test_simple_negation_never(self) -> None:
        assert _is_negated("never require user authentication", "require") is True

    def test_simple_negation_skip(self) -> None:
        assert _is_negated("skip the output format step", "output") is True

    def test_simple_negation_dont(self) -> None:
        assert _is_negated("don't ensure backwards compatibility", "ensure") is True

    def test_unnegated_keyword(self) -> None:
        assert _is_negated("you must ensure correctness", "ensure") is False

    def test_keyword_not_present(self) -> None:
        assert _is_negated("there are no limits here", "constraint") is False

    def test_negation_outside_window(self) -> None:
        # "not" is more than 3 words before "constraints"
        assert _is_negated(
            "not really sure what the actual full constraints are",
            "constraints",
        ) is False

    def test_mixed_negated_and_unnegated(self) -> None:
        # First "return" is negated, second is not — overall NOT negated
        text = "do not return errors, but return valid json"
        assert _is_negated(text, "return") is False

    def test_all_occurrences_negated(self) -> None:
        text = "never return errors and don't return warnings"
        assert _is_negated(text, "return") is True


# =====================================================================
# Unit tests: has_keyword_unnegated
# =====================================================================


class TestHasKeywordUnnegated:
    """Negation-aware keyword presence check."""

    def test_positive_unnegated(self) -> None:
        assert has_keyword_unnegated("you must handle errors", _CONSTRAINT_KEYWORDS) is True

    def test_all_negated_returns_false(self) -> None:
        assert has_keyword_unnegated(
            "there are no constraints and you don't need to ensure anything",
            _CONSTRAINT_KEYWORDS,
        ) is False

    def test_one_unnegated_keyword_sufficient(self) -> None:
        # "no constraints" is negated, but "must" is unnegated
        assert has_keyword_unnegated(
            "there are no constraints but you must follow the spec",
            _CONSTRAINT_KEYWORDS,
        ) is True

    def test_outcome_negated(self) -> None:
        assert has_keyword_unnegated(
            "you do not need to return anything specific",
            _OUTCOME_KEYWORDS,
        ) is False

    def test_outcome_unnegated(self) -> None:
        assert has_keyword_unnegated(
            "please return a json object with all fields",
            _OUTCOME_KEYWORDS,
        ) is True

    def test_audience_negated(self) -> None:
        assert has_keyword_unnegated(
            "without a specific audience in mind",
            _AUDIENCE_KEYWORDS,
        ) is False

    def test_audience_unnegated(self) -> None:
        assert has_keyword_unnegated(
            "the audience is senior developers",
            _AUDIENCE_KEYWORDS,
        ) is True


# =====================================================================
# Unit tests: _compute_structural_density
# =====================================================================


class TestStructuralDensity:
    """Verify structural density scoring from prompt content."""

    def test_unstructured_prompt(self) -> None:
        density = _compute_structural_density("just a plain text prompt here")
        assert density == 0

    def test_yaml_schema_structured(self) -> None:
        prompt = (
            "## Config\n"
            "## Schema\n"
            "## Validation\n"
            "- field: name\n"
            "- field: type\n"
            "- field: required\n"
            "- field: default\n"
            "Output format: yaml\n"
        )
        density = _compute_structural_density(prompt)
        assert density >= _STRUCTURAL_DENSITY_THRESHOLD

    def test_xml_heavy_prompt(self) -> None:
        prompt = (
            "<task>Deploy service</task>\n"
            "<constraints>Max 5 replicas</constraints>\n"
            "<output>Return status</output>\n"
        )
        density = _compute_structural_density(prompt)
        assert density >= _STRUCTURAL_DENSITY_THRESHOLD

    def test_code_block_prompt(self) -> None:
        prompt = (
            "## Task\n"
            "Fix the bug in:\n"
            "```python\ndef broken():\n    pass\n```\n"
        )
        density = _compute_structural_density(prompt)
        # 1 header + code block = 1 + 2 = 3 >= threshold
        assert density >= _STRUCTURAL_DENSITY_THRESHOLD

    def test_single_header_low_density(self) -> None:
        prompt = "## Task\nDo something simple"
        density = _compute_structural_density(prompt)
        # 1 header = 1 point < threshold
        assert density < _STRUCTURAL_DENSITY_THRESHOLD


# =====================================================================
# Unit tests: detect_weaknesses with context-aware density
# =====================================================================


class TestDetectWeaknessesContextAware:
    """The underspecified warning should be suppressed for structured prompts."""

    def test_short_unstructured_coding_prompt_flagged(self) -> None:
        prompt = "build a rest api for users"
        weaknesses = detect_weaknesses(
            prompt, prompt.lower(), prompt.lower().split(), "coding",
            has_constraints=False, has_outcome=False, has_audience=False,
        )
        assert any("underspecified" in w for w in weaknesses)

    def test_short_structured_coding_prompt_not_flagged(self) -> None:
        prompt = (
            "## Endpoint\n"
            "## Schema\n"
            "## Validation\n"
            "- field: name (str)\n"
            "- field: email (str)\n"
            "- field: role (enum)\n"
            "- field: active (bool)\n"
            "Return format: json\n"
        )
        words = prompt.lower().split()
        assert len(words) < 50, "prompt must be under 50 words for this test"
        weaknesses = detect_weaknesses(
            prompt, prompt.lower(), words, "coding",
            has_constraints=False, has_outcome=False, has_audience=False,
        )
        assert not any("underspecified" in w for w in weaknesses)

    def test_short_xml_system_prompt_not_flagged(self) -> None:
        prompt = (
            "<role>Backend engineer</role>\n"
            "<task>Create migration</task>\n"
            "<output>SQL DDL statements</output>\n"
        )
        words = prompt.lower().split()
        assert len(words) < 50
        weaknesses = detect_weaknesses(
            prompt, prompt.lower(), words, "system",
            has_constraints=False, has_outcome=False, has_audience=False,
        )
        assert not any("underspecified" in w for w in weaknesses)


# =====================================================================
# Integration tests via HeuristicAnalyzer.analyze()
# =====================================================================


class TestNegationIntegration:
    """End-to-end: negated keywords should NOT produce false strength/weakness signals."""

    @pytest.mark.asyncio
    async def test_negated_constraints_flagged_as_weakness(self, db) -> None:
        """A prompt that explicitly says 'no constraints' should report
        *missing* constraints, not *present* constraints."""
        analyzer = HeuristicAnalyzer()
        result = await analyzer.analyze(
            "You do not need to return anything specific and there are no "
            "constraints on the output. Write something about technology "
            "and innovation for a general audience that covers the basics "
            "of machine learning and artificial intelligence trends.",
            db,
            enable_llm_fallback=False,
        )
        weaknesses_lower = [w.lower() for w in result.weaknesses]
        strengths_lower = [s.lower() for s in result.strengths]

        # Should flag MISSING constraints
        assert any("lacks constraints" in w or "no boundaries" in w for w in weaknesses_lower), (
            f"Expected 'lacks constraints' weakness, got: {result.weaknesses}"
        )
        # Should NOT report constraints as a strength
        assert not any("constraint" in s for s in strengths_lower), (
            f"Negated constraints falsely reported as strength: {result.strengths}"
        )

    @pytest.mark.asyncio
    async def test_unnegated_constraints_reported_as_strength(self, db) -> None:
        """A prompt with genuine constraints should still work normally."""
        analyzer = HeuristicAnalyzer()
        result = await analyzer.analyze(
            "Implement a Python function that must return a JSON object. "
            "The function should handle errors gracefully and ensure type "
            "safety. Maximum response time is 200ms. The output format "
            "must be valid JSON with proper error codes.",
            db,
            enable_llm_fallback=False,
        )
        strengths_lower = [s.lower() for s in result.strengths]
        assert any("constraint" in s for s in strengths_lower), (
            f"Expected constraints strength, got: {result.strengths}"
        )

    @pytest.mark.asyncio
    async def test_negated_outcome_flagged_as_weakness(self, db) -> None:
        """'don't return' should flag missing outcome."""
        analyzer = HeuristicAnalyzer()
        result = await analyzer.analyze(
            "Write a blog post about software engineering best practices. "
            "You don't need to return anything specific here at all. "
            "Talk about various aspects of code quality and maintenance "
            "practices for modern development teams and their workflows.",
            db,
            enable_llm_fallback=False,
        )
        weaknesses_lower = [w.lower() for w in result.weaknesses]
        assert any("outcome" in w for w in weaknesses_lower), (
            f"Expected 'no measurable outcome' weakness, got: {result.weaknesses}"
        )


class TestDensityIntegration:
    """End-to-end: structured short prompts should not get the underspecified warning."""

    @pytest.mark.asyncio
    async def test_structured_yaml_schema_not_underspecified(self, db) -> None:
        prompt = (
            "## Schema\n"
            "## Fields\n"
            "## Validation\n"
            "- name: str, required\n"
            "- email: str, required\n"
            "- role: enum[admin,user]\n"
            "- active: bool, default true\n"
            "Return format: json schema\n"
            "Implement this model."
        )
        analyzer = HeuristicAnalyzer()
        result = await analyzer.analyze(prompt, db, enable_llm_fallback=False)
        weaknesses_lower = [w.lower() for w in result.weaknesses]
        assert not any("underspecified" in w for w in weaknesses_lower), (
            f"Structured prompt falsely flagged as underspecified: {result.weaknesses}"
        )

    @pytest.mark.asyncio
    async def test_unstructured_short_coding_still_flagged(self, db) -> None:
        prompt = "Build a user api with authentication"
        analyzer = HeuristicAnalyzer()
        result = await analyzer.analyze(prompt, db, enable_llm_fallback=False)
        weaknesses_lower = [w.lower() for w in result.weaknesses]
        assert any("underspecified" in w for w in weaknesses_lower), (
            f"Short unstructured prompt should be flagged, got: {result.weaknesses}"
        )
