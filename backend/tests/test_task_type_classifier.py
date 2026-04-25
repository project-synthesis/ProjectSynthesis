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

    def test_async_concurrency_vocabulary_fires(self):
        """B2 vocabulary expansion: async/concurrency primitives are
        unambiguously technical and should rescue analysis/general prompts
        about an async codebase to ``code_aware``.

        Live reference (2026-04-25 validation cycle): "Audit the asyncio.gather
        error handling in our warm-path Phase 4 — find race conditions where
        a transient failure poisons the maintenance transaction." matched no
        ``_TECHNICAL_NOUNS`` despite being clearly about a code-base —
        ``has_technical_nouns()`` returned False, the enrichment profile fell
        through to ``knowledge_work``, and the curated retrieval / strategy
        intelligence / pattern injection layers all silently skipped.
        """
        # Async runtime primitives — zero non-code legitimacy.
        assert ttc.has_technical_nouns("audit the asyncio gather error handling")
        assert ttc.has_technical_nouns("inspect the coroutine cancellation flow")
        assert ttc.has_technical_nouns("trace the eventloop blocking call")
        # Concurrency primitives.
        assert ttc.has_technical_nouns("diagnose the deadlock in the warm path")
        assert ttc.has_technical_nouns("review the mutex acquisition order")
        assert ttc.has_technical_nouns("debug the semaphore release path")
        # Live exact phrasing from validation cycle 1.
        assert ttc.has_technical_nouns(
            "audit the asyncio.gather error handling in our warm-path phase 4"
        )

    def test_transaction_savepoint_fire_in_db_context(self):
        """Database transaction primitives. Conservative — ``transaction``
        alone is overloaded (could be financial), so this guards against an
        over-eager add. ``savepoint`` is unambiguous DB."""
        assert ttc.has_technical_nouns("review the savepoint nesting in phase 4.5")

    def test_module_method_dotted_token_matches(self):
        """B3: ``asyncio.gather`` should match ``asyncio`` even though the
        interior dot was not a word boundary in the prior splitter — the
        whitespace tokenizer left ``asyncio.gather`` as one token and the
        punctuation strip didn't touch interior dots.
        """
        assert ttc.has_technical_nouns("audit asyncio.gather error handling")
        assert ttc.has_technical_nouns("trace session.close timing in the warm path")
        # Negative — bare period (sentence end) still works as before.
        assert ttc.has_technical_nouns("inspect the pipeline.")

    def test_kebab_case_compound_matches_inner_noun(self):
        """Kebab split: ``async-session`` should hit ``session``, ``cache-aware``
        should hit ``cache``. Catches kebab-case identifiers that wouldn't
        otherwise reach the noun set as a single whitespace token."""
        assert ttc.has_technical_nouns("review the async-session lifecycle")
        assert ttc.has_technical_nouns("optimize the cache-aware fetch loop")

    def test_python_identifier_syntax_signals_technical(self):
        """B4 (2026-04-25 cycle 2): snake_case + PascalCase identifier syntax
        is a zero-non-code-legitimacy signal, even when no explicit noun from
        the keyword set appears.

        Live regression: ``Audit our background task GC in main.py — find
        weak-ref races where _spawn_bg_task lets link_repo / reindex jobs
        disappear mid-await.`` matched no technical nouns and was demoted
        to ``knowledge_work`` despite obviously addressing a backend
        codebase. Multi-segment snake_case (``_spawn_bg_task``,
        ``link_repo``) and PascalCase compounds (``EmbeddingService``)
        carry zero prose meaning — they're code identifiers by syntax.
        """
        # Snake_case private identifier (leading underscore + 2+ underscores).
        assert ttc.has_technical_nouns(
            "audit _spawn_bg_task in main.py for weak-ref races"
        )
        # Plain snake_case (3+ words / 2+ underscores).
        assert ttc.has_technical_nouns("trace link_repo reindex flow")
        # PascalCase + structural marker (dotted method) → fires.
        # Bare PascalCase like ``EmbeddingService`` ALONE is treated as
        # potentially-a-brand-name (JavaScript/TypeScript/GitHub) and is
        # rejected by the structural-marker guard added in B5; with the
        # ``.method()`` companion, structural context is unambiguous.
        assert ttc.has_technical_nouns(
            "review EmbeddingService.encode_batch for throughput"
        )
        # Mixed — identifier + module path.
        assert ttc.has_technical_nouns(
            "diagnose TaxonomyEngine.persist_optimization concurrency"
        )

    def test_identifier_heuristic_does_not_false_positive_on_prose(self):
        """The identifier heuristic must NOT trip on plain prose without
        snake_case or PascalCase syntax markers. The B2 rescue path is
        further gated by ``repo_linked=True`` at the call site, so even
        a marginal false positive here is contained to users who have
        already linked a codebase.
        """
        assert not ttc.has_technical_nouns("write a poem about gardens")
        assert not ttc.has_technical_nouns("draft a story for the kids")
        # Single capitalized word (sentence start) — not PascalCase compound.
        assert not ttc.has_technical_nouns("Today is a beautiful day")
        # Hyphenation + capitalization without underscores — book titles, etc.
        assert not ttc.has_technical_nouns("Write a poem titled Tale of Two Cities")
        # Quoted prose — straight punctuation only.
        assert not ttc.has_technical_nouns("Tell me about machine learning")

    def test_pascal_case_brand_names_alone_do_not_trip_identifier_signal(self):
        """B5 (2026-04-25 reviewer catch): bare PascalCase brand names
        (JavaScript, TypeScript, GitHub, YouTube, EmbeddingService,
        PostScript, McDonalds, ...) match ``_PASCAL_CASE_RE`` because
        they are syntactically two capital-led words concatenated.
        Without this guard, a prose prompt like ``Write a tutorial about
        JavaScript for beginners`` with a repo linked falsely upgrades
        to ``code_aware``.

        Fix: PascalCase counts only when the original whitespace token
        carries a structural marker (``.``, ``-``, ``/``) signalling
        actual code context — bare brand names are pure-alphabetic and
        skip the rescue. snake_case (which by definition contains
        ``_``) is unaffected.

        Note: the B2 rescue path requires ``repo_linked=True`` at the
        call site, so blast radius is bounded — but the regression test
        here is the only way to keep future heuristic tweaks honest.
        """
        # Pure-alphabetic brand names — no structural marker → reject.
        # Care taken to choose prose contexts that don't accidentally include
        # a ``_TECHNICAL_NOUNS`` word (``model``, ``api``, ``framework``,
        # ``service``, ``schema``…) which would fire via the vocabulary
        # path independent of the identifier check.
        assert not ttc.has_technical_nouns("Write a tutorial about JavaScript for beginners")
        assert not ttc.has_technical_nouns("compare TypeScript to JavaScript")
        assert not ttc.has_technical_nouns("post a status update on GitHub")
        assert not ttc.has_technical_nouns("watch this YouTube video about cooking")
        assert not ttc.has_technical_nouns("the McDonalds franchise reach")
        assert not ttc.has_technical_nouns("PostScript was developed in the 1980s")

    def test_pascal_case_with_structural_marker_still_fires(self):
        """The structural-marker guard for PascalCase MUST NOT block
        legitimate code references — those have a ``.`` or ``/`` near
        the PascalCase token. Live cycle 2 reference:
        ``Audit EmbeddingService.embed_single`` should still fire.
        """
        # PascalCase with `.method` → structural marker present → fires.
        assert ttc.has_technical_nouns(
            "audit EmbeddingService.embed_single in backend/app/services"
        )
        # PascalCase + path separator → fires.
        assert ttc.has_technical_nouns(
            "review TaxonomyEngine usage in backend/app/services/taxonomy/engine.py"
        )
        # Bare PascalCase WITH a snake_case sibling token in same sentence → fires
        # via the snake_case path (independent of PascalCase guard).
        assert ttc.has_technical_nouns(
            "diagnose the EmbeddingService and the link_repo handler"
        )

    def test_extract_first_sentence_does_not_split_on_identifier_dots(self):
        """B5 (2026-04-25 cycle 2): ``EmbeddingService.embed_single`` and
        ``main.py`` contain dots, but the dot is NOT a sentence terminator
        — sentence terminators always have whitespace (or end-of-string)
        following the punctuation. Pre-fix: ``Audit EmbeddingService.embed_single
        in main.py — model.encode() is called...`` had its first sentence
        truncated to ``audit embeddingservice`` (split at the first dot
        in ``EmbeddingService.embed_single``), losing every downstream
        technical noun including ``model`` and ``backend``.
        """
        first = ttc.extract_first_sentence(
            "audit embeddingservice.embed_single in backend/app/services/embedding_service.py — "
            "model.encode() is called without normalize_embeddings=true"
        )
        # The sentence should extend at least through "model.encode()" — i.e.
        # NOT truncated at the first dot in EmbeddingService.embed_single.
        assert "model" in first, f"first sentence got truncated to {first!r}"
        # And the dot at the end is fine — but we don't supply one here, so
        # the whole prose is the first sentence.

    def test_extract_first_sentence_still_terminates_at_real_sentence_end(self):
        """Sentence boundary must still fire on ``.?!`` followed by whitespace
        or end-of-string. The fix narrows the splitter — it does NOT remove it.
        """
        first = ttc.extract_first_sentence(
            "this is the first sentence. this is the second."
        )
        assert "first" in first
        assert "second" not in first
        # End-of-string period also terminates.
        first2 = ttc.extract_first_sentence("only one sentence ending here.")
        assert first2.strip() in (
            "only one sentence ending here", "only one sentence ending here.",
        )


class TestRescueTaskTypeViaStructuralEvidence:
    """Creative/writing → coding rescue when first sentence has code evidence.

    Cycle-3/4 evidence (2026-04-25): user reported intermittent
    ``"Background Task Lifecycle Tracking"`` and similar code-aware
    intent labels classifying as ``creative`` because of the broad
    semantic signals (``"create" 0.5``, ``"design" 0.7``,
    ``"concept" 0.6``). The rescue applies the same B2 structural-
    evidence rule the enrichment-profile selector uses.
    """

    def test_creative_with_snake_case_identifier_rescued(self):
        from app.services.task_type_classifier import (
            rescue_task_type_via_structural_evidence,
        )

        prompt = (
            "Build a tracing helper for the _spawn_bg_task lifecycle — "
            "emit a span at create + span at completion."
        )
        rescued, reason = rescue_task_type_via_structural_evidence("creative", prompt)
        assert rescued == "coding"
        assert reason is not None
        assert "snake_case" in reason
        assert "_spawn_bg_task" in reason

    def test_creative_with_pascal_dotted_identifier_rescued(self):
        from app.services.task_type_classifier import (
            rescue_task_type_via_structural_evidence,
        )

        prompt = (
            "Design a wrapper around EmbeddingService.embed_single to "
            "track lifecycle events."
        )
        rescued, reason = rescue_task_type_via_structural_evidence("creative", prompt)
        assert rescued == "coding"
        assert reason is not None
        # Either the snake_case piece (embed_single) or the PascalCase piece
        # (EmbeddingService) of the dotted identifier may be reported as the
        # rescue reason — both are valid syntactic evidence. Just confirm the
        # reason names a syntactic identifier rather than a vocabulary noun.
        assert "snake_case" in reason or "PascalCase" in reason

    def test_creative_with_bare_pascal_dotted_identifier_rescued(self):
        """When the only identifier evidence is PascalCase + dot, the
        reason should specifically identify the PascalCase piece —
        nothing in the prompt has snake_case or technical-noun fallback.
        """
        from app.services.task_type_classifier import (
            rescue_task_type_via_structural_evidence,
        )

        prompt = "Wrap CacheManager.invalidate so the call tree reads cleanly."
        rescued, reason = rescue_task_type_via_structural_evidence("creative", prompt)
        assert rescued == "coding"
        assert reason is not None
        assert "PascalCase" in reason
        assert "CacheManager" in reason

    def test_writing_with_technical_noun_rescued(self):
        from app.services.task_type_classifier import (
            rescue_task_type_via_structural_evidence,
        )

        prompt = "Write up the asyncio coroutine pattern we discussed yesterday."
        rescued, reason = rescue_task_type_via_structural_evidence("writing", prompt)
        assert rescued == "coding"
        assert reason is not None

    def test_pure_creative_prose_not_rescued(self):
        """No code evidence → no rescue, even when prompt is verbose."""
        from app.services.task_type_classifier import (
            rescue_task_type_via_structural_evidence,
        )

        prompt = "Brainstorm a campaign concept that feels playful and inviting."
        rescued, reason = rescue_task_type_via_structural_evidence("creative", prompt)
        assert rescued == "creative"
        assert reason is None

    def test_pure_writing_prose_not_rescued(self):
        from app.services.task_type_classifier import (
            rescue_task_type_via_structural_evidence,
        )

        prompt = "Write a heartfelt letter to my grandfather about his recipe."
        rescued, reason = rescue_task_type_via_structural_evidence("writing", prompt)
        assert rescued == "writing"
        assert reason is None

    def test_already_coding_passes_through_unchanged(self):
        """Idempotency — re-running on already-coding result is a no-op."""
        from app.services.task_type_classifier import (
            rescue_task_type_via_structural_evidence,
        )

        prompt = "Refactor the _spawn_bg_task helper for clarity."
        rescued, reason = rescue_task_type_via_structural_evidence("coding", prompt)
        assert rescued == "coding"
        assert reason is None

    def test_analysis_not_rescued_to_coding_even_with_identifiers(self):
        """Analysis tasks legitimately co-occur with code identifiers
        (auditing a function, profiling a class) and should NOT be
        silently rewritten — only the demonstrably-broad creative/
        writing signals warrant the override.
        """
        from app.services.task_type_classifier import (
            rescue_task_type_via_structural_evidence,
        )

        prompt = "Audit the _spawn_bg_task lifecycle for race conditions."
        rescued, reason = rescue_task_type_via_structural_evidence("analysis", prompt)
        assert rescued == "analysis"
        assert reason is None

    def test_data_not_rescued_to_coding_even_with_identifiers(self):
        from app.services.task_type_classifier import (
            rescue_task_type_via_structural_evidence,
        )

        prompt = "Extract the request_id column from the auditlog dataframe."
        rescued, reason = rescue_task_type_via_structural_evidence("data", prompt)
        assert rescued == "data"
        assert reason is None

    def test_empty_prompt_returns_unchanged(self):
        from app.services.task_type_classifier import (
            rescue_task_type_via_structural_evidence,
        )

        rescued, reason = rescue_task_type_via_structural_evidence("creative", "")
        assert rescued == "creative"
        assert reason is None

    def test_creative_lifecycle_intent_matching_user_report(self):
        """Pinned regression for the user-reported case (2026-04-25):
        ``Background Task Lifecycle Tracking``-style prompts.

        The keyword ``"create"`` + the abstract noun ``"lifecycle"`` +
        ``"concept"``-adjacent verbs were what made the classifier
        vote creative. Now: the syntactic identifier ``_spawn_bg_task``
        is unambiguous evidence the prompt is about code, regardless
        of the semantic surface.
        """
        from app.services.task_type_classifier import (
            rescue_task_type_via_structural_evidence,
        )

        prompt = (
            "Create a background task lifecycle tracker for the "
            "_spawn_bg_task helper — emit a span at start + span at "
            "completion so weak-ref-collected tasks become visible."
        )
        rescued, reason = rescue_task_type_via_structural_evidence("creative", prompt)
        assert rescued == "coding"
        assert reason is not None
        assert "_spawn_bg_task" in reason


class TestStaticSingleSignalsSurviveDynamicMerge:
    """B6: `set_task_type_signals()` must not wipe the default single-word
    signals when a dynamic payload is missing them.

    Live reference: the 2026-04-21 SQLAlchemy session-factory optimization
    persisted ``task_type_scores = {creative: 1.4, coding: 0.0}`` even though
    the module defaults for ``coding`` include "sqlalchemy" 0.7, "fastapi"
    0.7, "factory" 0.5, "session" 0.4 (all present in the prompt). The
    classifier ran on a warm-path rebuilt table (``_STATIC_COMPOUND_SIGNALS
    + {}``) that had dropped every single-word default.

    Root cause: the merge at
    ``set_task_type_signals()`` rebuilt the signal table from
    ``_STATIC_COMPOUND_SIGNALS.get(task_type, []) + dynamic_signals.get(...)``
    — ``_STATIC_COMPOUND_SIGNALS`` only contains *multi-word* entries, so
    single-word defaults were never re-included once a warm-path call fired.

    Fix: introduce a ``_STATIC_SINGLE_SIGNALS`` snapshot (mirror of
    ``_STATIC_COMPOUND_SIGNALS`` but for single-word defaults) and fall back
    to it when ``dynamic_signals`` has no entry for a given task type. Warm
    paths that actually ran TF-IDF extraction still override the defaults
    (they pass their own single-word list for that task type); warm paths
    that ran partial extraction only replace the types they extracted.
    """

    def test_default_signal_table_has_single_words(self):
        """Sanity: the module-load ``_TASK_TYPE_SIGNALS`` must contain the B1
        single-word entries. Regression guard against accidental removal."""
        coding = dict(ttc._TASK_TYPE_SIGNALS["coding"])
        assert coding.get("sqlalchemy") == 0.7
        assert coding.get("fastapi") == 0.7
        assert coding.get("factory") == 0.5
        assert coding.get("session") == 0.4

    def test_static_single_signals_snapshot_exists(self):
        """The implementation must expose a ``_STATIC_SINGLE_SIGNALS`` mirror
        of ``_STATIC_COMPOUND_SIGNALS`` so the merge has a single-word
        fallback tier. This is the structural claim of the fix."""
        assert hasattr(ttc, "_STATIC_SINGLE_SIGNALS"), (
            "task_type_classifier must expose _STATIC_SINGLE_SIGNALS "
            "— the single-word baseline that survives set_task_type_signals()"
        )
        coding = dict(ttc._STATIC_SINGLE_SIGNALS["coding"])
        # B1 single-word defaults must be captured in the baseline
        assert coding.get("sqlalchemy") == 0.7
        assert coding.get("fastapi") == 0.7
        assert coding.get("factory") == 0.5
        assert coding.get("session") == 0.4
        # A8 CLI-family must also survive
        assert coding.get("cli") == 0.7
        assert coding.get("daemon") == 0.7

    def test_empty_dynamic_task_type_preserves_single_defaults(self):
        """When ``set_task_type_signals`` is called for OTHER task types but
        not ``coding``, the coding single-word defaults must survive.

        Live reference: warm-path TF-IDF extraction may cross the 30-sample
        threshold for ``writing`` only, but the call still merges across all
        task types. Pre-B6 this silently wiped ``coding`` single-words.
        """
        ttc.set_task_type_signals(
            {"writing": [("blog", 0.9), ("article", 0.8)]},
            extracted_task_types={"writing"},
        )
        signals = ttc.get_task_type_signals()
        coding_keywords = dict(signals["coding"])
        # Compound defaults survive (never broken)
        assert coding_keywords.get("session factory") == 1.2
        assert coding_keywords.get("dependency injection") == 1.1
        # Single-word B1 defaults must also survive the rebuild
        assert coding_keywords.get("sqlalchemy") == 0.7, (
            f"single-word defaults wiped — keywords: {list(coding_keywords)}"
        )
        assert coding_keywords.get("fastapi") == 0.7
        assert coding_keywords.get("factory") == 0.5
        assert coding_keywords.get("session") == 0.4

    def test_dynamic_singles_override_static_for_extracted_task_type(self):
        """When TF-IDF extraction fires for ``coding`` with its own singles,
        the dynamic payload REPLACES the static single-word defaults for
        that task type only. Other task types keep their static singles."""
        ttc.set_task_type_signals(
            {"coding": [("tf-idf-signal-a", 0.9), ("tf-idf-signal-b", 0.8)]},
            extracted_task_types={"coding"},
        )
        signals = ttc.get_task_type_signals()
        coding_keywords = dict(signals["coding"])
        # Compound defaults always survive
        assert coding_keywords.get("session factory") == 1.2
        # Dynamic singles for coding present
        assert coding_keywords.get("tf-idf-signal-a") == 0.9
        # Static single-word defaults for coding are replaced
        # (the extraction claim is: "these are the current coding singles")
        assert coding_keywords.get("sqlalchemy") is None
        # But other task types (writing) keep their static singles because
        # they weren't in the dynamic payload
        writing_keywords = dict(signals["writing"])
        assert writing_keywords.get("blog") == 1.0  # default from module

    def test_session_factory_prompt_scores_on_coding_after_partial_warm_path(self):
        """End-to-end regression: the 2026-04-21 SQL prompt must still score
        on coding after a warm-path TF-IDF call that only extracted for
        ``writing``. This was the live failure mode."""
        ttc.set_task_type_signals(
            {"writing": [("copywriting", 0.9)]},
            extracted_task_types={"writing"},
        )
        signals = ttc.get_task_type_signals()
        prompt = (
            "design a sqlalchemy async session factory with per-request "
            "dependency injection for fastapi"
        )
        coding_score = ttc.score_category(prompt, prompt, signals["coding"])
        # Without the fix this assertion fires because coding lost its
        # single-word signals ("sqlalchemy", "fastapi", "factory", "session")
        # and only the compound hits ("session factory", "dependency
        # injection") remain — even so, compounds alone should keep score > 0
        # but we demand proper multi-signal coverage (>= 1.5) to beat
        # creative's 1.4 ("design" × first-sentence 2x).
        assert coding_score >= 1.5, (
            f"Expected coding score >= 1.5 after partial warm-path merge, "
            f"got {coding_score} (signals: {len(signals['coding'])})"
        )
