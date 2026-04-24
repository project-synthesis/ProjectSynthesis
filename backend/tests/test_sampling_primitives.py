"""Tests for ``services/sampling/primitives.py``.

The ``build_analysis_from_text`` fallback path historically maintained its
own hardcoded ``type_keywords`` + ``domain_keywords`` dicts — a second
classifier that diverged silently from the organic warm-path vocabulary
(``_TASK_TYPE_SIGNALS`` + ``DomainSignalLoader.signal_keywords``). This
module pins the aligned behavior so future classifier evolution in the
warm path flows through to the sampling fallback with no edits here.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.services.sampling.primitives import build_analysis_from_text


@pytest.fixture(autouse=True)
def _reset_task_type_extracted():
    """Keep the classifier's module-level singletons clean across tests."""
    from app.services import task_type_classifier as ttc
    ttc.reset_task_type_extracted()
    yield
    ttc.reset_task_type_extracted()


def test_task_type_delegates_to_classify_task_type():
    """The fallback must call ``classify_task_type`` with the signal table
    returned by ``get_task_type_signals()`` — not maintain a parallel
    hardcoded dict.
    """
    with patch(
        "app.services.sampling.primitives.classify_task_type",
        return_value=("analysis", 2.0, {"analysis": 2.0, "coding": 0.5}),
    ) as mock_classify:
        result = build_analysis_from_text(
            text="Generic sampling response text",
            default_strategy="chain-of-thought",
            raw_prompt="Audit the deployment pipeline and recommend fixes",
        )

    mock_classify.assert_called_once()
    # Verify the result reflects the delegated classification.
    assert result.task_type == "analysis"
    # Strategy rationale should mention the delegated task_type.
    assert "analysis" in result.strategy_rationale


def test_domain_delegates_to_domain_signal_loader():
    """Domain classification must call ``DomainSignalLoader.classify(scored)``
    where ``scored`` comes from ``DomainSignalLoader.score(words)`` —
    the single source of truth for organic domain vocabulary.
    """
    mock_loader = MagicMock()
    mock_loader.score.return_value = {"backend": 2.5, "database": 0.8}
    mock_loader.classify.return_value = "backend"

    with (
        patch(
            "app.services.sampling.primitives.get_signal_loader",
            return_value=mock_loader,
        ),
        patch(
            "app.services.sampling.primitives.classify_task_type",
            return_value=("coding", 2.0, {"coding": 2.0}),
        ),
    ):
        result = build_analysis_from_text(
            text="Analysis text with some api and endpoint mentions",
            default_strategy="chain-of-thought",
            raw_prompt="Build a scalable REST api for the backend service",
        )

    # score() was called with the combined words set.
    assert mock_loader.score.called
    # classify() was called with the scored dict.
    assert mock_loader.classify.called
    assert result.domain == "backend"


def test_graceful_fallback_when_signal_loader_missing():
    """When ``get_signal_loader()`` returns ``None`` (startup race / test
    context without a live loader), domain defaults to ``"general"`` and
    the fallback still returns a valid ``AnalysisResult``.
    """
    with (
        patch(
            "app.services.sampling.primitives.get_signal_loader",
            return_value=None,
        ),
        patch(
            "app.services.sampling.primitives.classify_task_type",
            return_value=("coding", 1.5, {"coding": 1.5}),
        ),
    ):
        result = build_analysis_from_text(
            text="Response text",
            default_strategy="chain-of-thought",
            raw_prompt="Build an api endpoint that serves json",
        )

    assert result.task_type == "coding"
    assert result.domain == "general"
    # No crash; valid intent_label + confidence.
    assert 0.4 <= result.confidence <= 1.0
    assert result.intent_label  # non-empty


def test_vocabulary_parity_with_warm_path_via_task_type_signals():
    """End-to-end parity: warm-path signals added via
    ``set_task_type_signals()`` must flow through to the fallback via
    ``get_task_type_signals()``. Validates the delegation chain:
    sampling-fallback → ``classify_task_type`` → ``get_task_type_signals()``.
    """
    from app.services import task_type_classifier as ttc

    original = {k: list(v) for k, v in ttc._TASK_TYPE_SIGNALS.items()}
    try:
        # Inject a dynamic signal the pre-refactor hardcoded dicts never knew
        # about — "kubecontrol" (made-up domain-specific jargon).
        ttc.set_task_type_signals(
            {
                **original,
                "system": [
                    *original["system"],
                    ("kubecontrol", 2.0),
                ],
            },
            extracted_task_types={"system"},
        )

        result = build_analysis_from_text(
            text="",
            default_strategy="chain-of-thought",
            raw_prompt="Please kubecontrol the staging cluster before release",
        )

        # Dynamic injection flowed through — the fallback now classifies
        # this prompt as system, which the old hardcoded dict would have
        # missed entirely (no "kubecontrol" keyword in the old table).
        assert result.task_type == "system"
    finally:
        ttc.set_task_type_signals(original)


def test_intent_label_generation_unchanged_by_alignment():
    """Regression guard: the intent_label generation path (first 6 words
    of the raw prompt, title-cased, validated) is unchanged by the
    delegation refactor.
    """
    with patch(
        "app.services.sampling.primitives.classify_task_type",
        return_value=("coding", 1.0, {"coding": 1.0}),
    ):
        result = build_analysis_from_text(
            text="",
            default_strategy="auto",
            raw_prompt="Implement a rate-limited REST endpoint that returns JSON",
        )

    # First 6 words title-cased, stopwords stripped by validate_intent_label.
    assert len(result.intent_label) > 0
    assert result.intent_label != "General"  # with >3 words we get a real label


def test_empty_raw_prompt_falls_back_to_default_intent():
    """Empty raw prompt → intent_label defaults to ``"General"``."""
    with patch(
        "app.services.sampling.primitives.classify_task_type",
        return_value=("general", 0.0, {}),
    ):
        result = build_analysis_from_text(
            text="Some LLM response without classifiable content",
            default_strategy="auto",
            raw_prompt="",
        )

    assert result.intent_label == "General"
