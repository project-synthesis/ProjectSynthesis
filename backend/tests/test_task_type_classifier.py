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


class TestORMFactoryAndFrameworkNounCoverage:
    """B1: session-factory + ORM-framework prompts misclassify as creative.

    Live reference: "Design a SQLAlchemy async session factory with per-request
    dependency injection for FastAPI". Under pre-B1 rules:
    - Only ``design`` fires (creative, weight 0.7, first-sentence 2x → 1.4).
    - A2 disambiguation scans first sentence for ``design`` + technical noun
      within 4 words. "session", "factory", "sqlalchemy", "fastapi" are all
      absent from ``_TECHNICAL_NOUNS`` so the gate misses.
    - Result: task_type=creative → enrichment profile selector rejects
      code_aware (task_type ∉ _CODEBASE_TASK_TYPES) → codebase context skipped.

    Why the noun set: ``session`` and ``factory`` are OOP patterns that appear
    almost exclusively in coding contexts (DB sessions, factory functions).
    Framework names like ``sqlalchemy``/``fastapi``/``django``/``flask`` are
    unambiguous tech identifiers — no creative-writing prompt says "design a
    FastAPI". Keep the list conservative: ``app``, ``tool``, and ``client``
    are NOT added because each has legitimate creative-writing use.
    """

    def test_design_a_session_factory_triggers_disambiguation(self):
        """Session factory is OOP pattern + factory-function — always coding."""
        assert ttc.check_technical_disambiguation(
            "design a sqlalchemy async session factory with dependency injection"
        )

    def test_sqlalchemy_framework_noun_triggers_disambiguation(self):
        """Framework names are unambiguous coding context."""
        assert ttc.check_technical_disambiguation(
            "design a sqlalchemy model for the user table"
        )

    def test_fastapi_framework_noun_triggers_disambiguation(self):
        assert ttc.check_technical_disambiguation(
            "build a fastapi dependency that validates auth"
        )

    def test_django_flask_framework_nouns(self):
        assert ttc.check_technical_disambiguation(
            "configure a django middleware for rate limiting"
        )
        assert ttc.check_technical_disambiguation(
            "design a flask blueprint for the api routes"
        )

    def test_factory_alone_triggers_disambiguation(self):
        """Pure 'design a factory' (OOP factory pattern) is coding."""
        assert ttc.check_technical_disambiguation(
            "design a factory that produces http clients"
        )

    def test_session_alone_triggers_disambiguation(self):
        """DB/HTTP session objects are OOP technical nouns."""
        assert ttc.check_technical_disambiguation(
            "build a session manager for websocket connections"
        )

    def test_non_technical_creative_still_rejects(self):
        """B1 widening must preserve creative-writing rejection."""
        assert not ttc.check_technical_disambiguation(
            "design a poster for the conference"
        )
        assert not ttc.check_technical_disambiguation(
            "create a plot for the novel"
        )

    def test_session_factory_prompt_scores_on_coding(self):
        """End-to-end: live SQLAlchemy prompt must produce coding_score > 0.

        Without a coding-table hit the A2 disambiguation flip is a no-op
        (the classifier flip logic requires ``coding_score > 0``).
        """
        signals = ttc.get_task_type_signals()
        prompt = (
            "design a sqlalchemy async session factory with per-request "
            "dependency injection for fastapi"
        )
        coding_score = ttc.score_category(prompt, prompt, signals["coding"])
        assert coding_score > 0, (
            f"Expected coding score > 0 for session-factory prompt, got {coding_score}"
        )

    def test_dependency_injection_and_connection_pool_score_on_coding(self):
        """Prompt-specific compound patterns: dependency-injection +
        connection-pool are always coding idioms."""
        signals = ttc.get_task_type_signals()
        prompt = (
            "design a sqlalchemy async session factory with per-request "
            "dependency injection for fastapi. include connection pool tuning."
        )
        coding_score = ttc.score_category(prompt, prompt, signals["coding"])
        # First-sentence 2x boost on 'design' (0.7 creative) gives 1.4 to creative;
        # coding needs to beat that. Multiple signal hits required.
        assert coding_score >= 1.5, (
            f"Expected coding score >= 1.5 for DB+pool prompt, got {coding_score}"
        )


class TestHasTechnicalNouns:
    """B2: looser signal than ``check_technical_disambiguation`` — returns
    True whenever the first sentence mentions any ``_TECHNICAL_NOUNS`` word,
    regardless of paired verb. Used by the enrichment-profile selector to
    rescue analysis/creative/general prompts about a linked codebase.
    """

    def test_audit_analysis_with_pipeline_noun(self):
        """Analysis verbs ('audit') don't trigger the A2 flip but still
        signal a codebase-adjacent prompt when paired with a tech noun."""
        assert ttc.has_technical_nouns(
            "audit the routing pipeline for race conditions"
        )

    def test_review_with_middleware_noun(self):
        assert ttc.has_technical_nouns(
            "review the websocket middleware error handling"
        )

    def test_framework_name_alone_fires(self):
        assert ttc.has_technical_nouns("inspect the fastapi dependency graph")
        assert ttc.has_technical_nouns("diagnose the sqlalchemy session leak")

    def test_cli_daemon_binary_fire(self):
        assert ttc.has_technical_nouns("debug the daemon startup sequence")
        assert ttc.has_technical_nouns("profile the cli command dispatcher")

    def test_prompt_without_tech_nouns_returns_false(self):
        assert not ttc.has_technical_nouns("write a poem about the ocean")
        assert not ttc.has_technical_nouns(
            "tell me about the weather today and what i should wear"
        )
        assert not ttc.has_technical_nouns("draft a blog about gardening")

    def test_trailing_punctuation_ignored(self):
        """'pipeline,' / 'FastAPI.' / 'system!' must still register."""
        assert ttc.has_technical_nouns("audit the pipeline, then review logs.")
        assert ttc.has_technical_nouns("review FastAPI.")
        assert ttc.has_technical_nouns("inspect the system!")

    def test_case_insensitive(self):
        assert ttc.has_technical_nouns("Review the SQLAlchemy Session Layer")
        assert ttc.has_technical_nouns("AUDIT THE PIPELINE")

    def test_empty_string_returns_false(self):
        assert not ttc.has_technical_nouns("")
        assert not ttc.has_technical_nouns("   ")
