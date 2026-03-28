"""Tests for app.services.heuristic_suggestions — zero-LLM suggestion generator."""

from app.services.heuristic_suggestions import generate_heuristic_suggestions

BALANCED_SCORES = {
    "clarity": 7.0, "specificity": 7.0, "structure": 7.0,
    "faithfulness": 7.0, "conciseness": 7.0,
}


class TestScoreDrivenSuggestion:
    """Score-driven suggestion targets the lowest dimension."""

    def test_lowest_clarity(self) -> None:
        scores = {**BALANCED_SCORES, "clarity": 3.0}
        result = generate_heuristic_suggestions(scores, [], "auto")
        score_sug = next(s for s in result if s["source"] == "score")
        assert "clarity" in score_sug["text"].lower()
        assert "3.0" in score_sug["text"]

    def test_lowest_specificity(self) -> None:
        scores = {**BALANCED_SCORES, "specificity": 2.5}
        result = generate_heuristic_suggestions(scores, [], "auto")
        score_sug = next(s for s in result if s["source"] == "score")
        assert "specificity" in score_sug["text"].lower()

    def test_lowest_structure(self) -> None:
        scores = {**BALANCED_SCORES, "structure": 1.0}
        result = generate_heuristic_suggestions(scores, [], "auto")
        score_sug = next(s for s in result if s["source"] == "score")
        assert "structure" in score_sug["text"].lower()

    def test_lowest_faithfulness(self) -> None:
        scores = {**BALANCED_SCORES, "faithfulness": 4.0}
        result = generate_heuristic_suggestions(scores, [], "auto")
        score_sug = next(s for s in result if s["source"] == "score")
        assert "faithfulness" in score_sug["text"].lower()

    def test_lowest_conciseness(self) -> None:
        scores = {**BALANCED_SCORES, "conciseness": 2.0}
        result = generate_heuristic_suggestions(scores, [], "auto")
        score_sug = next(s for s in result if s["source"] == "score")
        assert "conciseness" in score_sug["text"].lower()


class TestAnalysisDrivenSuggestion:
    """Analysis-driven suggestion maps weakness to actionable fix."""

    def test_vague_language(self) -> None:
        result = generate_heuristic_suggestions(
            BALANCED_SCORES, ["vague language reduces precision"], "auto",
        )
        analysis = next(s for s in result if s["source"] == "analysis")
        assert "vague" in analysis["text"].lower()

    def test_lacks_constraints(self) -> None:
        result = generate_heuristic_suggestions(
            BALANCED_SCORES,
            ["lacks constraints — no boundaries for the output"],
            "auto",
        )
        analysis = next(s for s in result if s["source"] == "analysis")
        assert "constraint" in analysis["text"].lower()

    def test_no_examples(self) -> None:
        result = generate_heuristic_suggestions(
            BALANCED_SCORES,
            ["no examples to anchor expected output"],
            "auto",
        )
        analysis = next(s for s in result if s["source"] == "analysis")
        assert "example" in analysis["text"].lower()

    def test_priority_order(self) -> None:
        """First weakness in priority order wins."""
        result = generate_heuristic_suggestions(
            BALANCED_SCORES,
            [
                "no examples to anchor expected output",  # lower priority
                "vague language reduces precision",        # higher priority
            ],
            "auto",
        )
        analysis = next(s for s in result if s["source"] == "analysis")
        assert "vague" in analysis["text"].lower()

    def test_no_weaknesses_skips_analysis(self) -> None:
        result = generate_heuristic_suggestions(BALANCED_SCORES, [], "auto")
        sources = {s["source"] for s in result}
        assert "analysis" not in sources
        assert len(result) == 2  # score + strategy only


class TestStrategyDrivenSuggestion:
    """Strategy-driven suggestion maps strategy name to technique."""

    def test_chain_of_thought(self) -> None:
        result = generate_heuristic_suggestions(BALANCED_SCORES, [], "chain-of-thought")
        strat = next(s for s in result if s["source"] == "strategy")
        assert "reasoning steps" in strat["text"].lower()

    def test_few_shot(self) -> None:
        result = generate_heuristic_suggestions(BALANCED_SCORES, [], "few-shot")
        strat = next(s for s in result if s["source"] == "strategy")
        assert "example" in strat["text"].lower()

    def test_structured_output(self) -> None:
        result = generate_heuristic_suggestions(BALANCED_SCORES, [], "structured-output")
        strat = next(s for s in result if s["source"] == "strategy")
        assert "schema" in strat["text"].lower()

    def test_unknown_strategy_fallback(self) -> None:
        result = generate_heuristic_suggestions(BALANCED_SCORES, [], "unknown-strategy")
        strat = next(s for s in result if s["source"] == "strategy")
        assert "targeted approach" in strat["text"].lower()


class TestOutputFormat:
    """Verify output matches the SuggestionsOutput schema."""

    def test_max_three_suggestions(self) -> None:
        result = generate_heuristic_suggestions(
            BALANCED_SCORES,
            ["vague language reduces precision"],
            "chain-of-thought",
        )
        assert len(result) == 3

    def test_each_has_text_and_source(self) -> None:
        result = generate_heuristic_suggestions(
            BALANCED_SCORES, ["vague language reduces precision"], "auto",
        )
        for sug in result:
            assert set(sug.keys()) == {"text", "source"}
            assert isinstance(sug["text"], str)
            assert sug["source"] in {"score", "analysis", "strategy"}

    def test_deterministic(self) -> None:
        args = (BALANCED_SCORES, ["vague language reduces precision"], "auto")
        a = generate_heuristic_suggestions(*args)
        b = generate_heuristic_suggestions(*args)
        assert a == b

    def test_empty_scores_still_returns_strategy(self) -> None:
        result = generate_heuristic_suggestions({}, [], "auto")
        assert len(result) == 1
        assert result[0]["source"] == "strategy"
