"""Tests for the task_type_classifier signal-extraction state (A4)."""

from __future__ import annotations

import pytest

from app.services import task_type_classifier as ttc


@pytest.fixture(autouse=True)
def _reset_extraction_state():
    """Isolate each test — the extraction set AND the signal table are both
    module-level singletons. `set_task_type_signals()` (used by the dynamic-
    signal tests below) permanently rewrites `_TASK_TYPE_SIGNALS`; without
    a full snapshot + restore, later tests see an impoverished table and
    fail with bogus score=0 assertions.
    """
    original_signals = {k: list(v) for k, v in ttc._TASK_TYPE_SIGNALS.items()}
    ttc.reset_task_type_extracted()
    yield
    ttc.reset_task_type_extracted()
    ttc._TASK_TYPE_SIGNALS = original_signals
    ttc._precompile_keyword_patterns()


class TestTaskTypeHasDynamicSignals:
    """A4: extraction state must be tracked explicitly, not inferred from the
    merged signal table. Signals loaded from a cache at boot look identical
    to signals just extracted in the current run, but only the latter prove
    live TF-IDF learning has fired.
    """

    def test_default_state_is_bootstrap_for_all_task_types(self):
        assert ttc.task_type_has_dynamic_signals("coding") is False
        assert ttc.task_type_has_dynamic_signals("writing") is False
        assert ttc.task_type_has_dynamic_signals("analysis") is False

    def test_set_task_type_signals_without_extracted_set_stays_bootstrap(self):
        """Loading singles without naming which types actually crossed the
        30-sample threshold does NOT mark them as dynamic. Calls from
        cache-warmup at boot must go through this path."""
        ttc.set_task_type_signals({"coding": [("endpoint", 0.9)]})
        assert ttc.task_type_has_dynamic_signals("coding") is False

    def test_set_task_type_signals_with_extracted_marks_dynamic(self):
        """Passing ``extracted_task_types`` flips those types to dynamic."""
        ttc.set_task_type_signals(
            {"coding": [("endpoint", 0.9)], "writing": [("blog", 0.8)]},
            extracted_task_types={"coding"},
        )
        assert ttc.task_type_has_dynamic_signals("coding") is True
        assert ttc.task_type_has_dynamic_signals("writing") is False

    def test_subsequent_bootstrap_load_clears_dynamic_status(self):
        """A later cache-warmup load (no extracted set) must not preserve the
        dynamic status from an earlier extraction run — dynamic is a claim
        about *this run's* signal freshness, not an eternal attribute."""
        ttc.set_task_type_signals(
            {"coding": [("endpoint", 0.9)]},
            extracted_task_types={"coding"},
        )
        assert ttc.task_type_has_dynamic_signals("coding") is True

        ttc.set_task_type_signals({"coding": [("endpoint", 0.9)]})
        assert ttc.task_type_has_dynamic_signals("coding") is False

    def test_unknown_task_type_returns_false(self):
        ttc.set_task_type_signals(
            {"coding": [("endpoint", 0.9)]},
            extracted_task_types={"coding"},
        )
        assert ttc.task_type_has_dynamic_signals("meta-coding") is False


class TestTechnicalNounDisambiguationCoverage:
    """A8: extend `_TECHNICAL_NOUNS` to cover unambiguous coding artifacts.

    The live "Fastapi Log Tail CLI" prompt was misclassified as ``creative``
    because "design" scored alone and ``_TECHNICAL_NOUNS`` did not recognize
    ``cli`` / ``daemon`` / ``binary`` as coding nouns. These are categorically
    coding artifacts (command-line programs, system daemons, compiled
    executables) that cannot plausibly be creative-writing tasks.

    Why this specific set:
    - ``cli``: command-line interface — unambiguously an engineering artifact
    - ``daemon``: long-running system service — unambiguous
    - ``binary``: compiled executable — unambiguous
    Not added: ``tool`` / ``script`` / ``log`` — ambiguous (movie scripts,
    captain's logs, marketing tools). Expanding there needs evidence.
    """

    def test_design_a_cli_triggers_disambiguation(self):
        assert ttc.check_technical_disambiguation(
            "design a cli tool that tails a fastapi app log"
        )

    def test_build_a_daemon_triggers_disambiguation(self):
        assert ttc.check_technical_disambiguation(
            "build a daemon that watches the filesystem"
        )

    def test_create_a_binary_triggers_disambiguation(self):
        assert ttc.check_technical_disambiguation(
            "create a binary that processes input streams"
        )

    def test_existing_nouns_still_pass(self):
        """Regression guard: widening must not drop any existing noun."""
        assert ttc.check_technical_disambiguation(
            "design a caching system for the api"
        )
        assert ttc.check_technical_disambiguation(
            "build an endpoint for user auth"
        )

    def test_non_technical_pairs_still_reject(self):
        """Widening must not start claiming generic creative prompts are coding."""
        assert not ttc.check_technical_disambiguation(
            "design a logo for the brand"
        )
        assert not ttc.check_technical_disambiguation(
            "create a poem about the ocean"
        )

    def test_cli_prompt_scores_on_coding(self):
        """A8: `cli`/`daemon` must also score on the coding signal table.

        Without this, A2 disambiguation fires (verb+noun pair detected) but
        the coding flip requires ``coding_score > 0`` — prompts that mention
        CLI/daemon as the *only* coding signal still fall through to creative.
        The live 'Fastapi Log Tail CLI' prompt is the reference case.
        """
        signals = ttc.get_task_type_signals()
        prompt = "design a cli tool that tails a fastapi app log"
        coding_score = ttc.score_category(prompt, prompt, signals["coding"])
        assert coding_score > 0, (
            f"Expected coding score > 0 for CLI prompt, got {coding_score}"
        )
