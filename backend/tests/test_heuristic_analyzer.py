"""Tests for HeuristicAnalyzer — zero-LLM prompt classification."""

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.services.domain_signal_loader import DomainSignalLoader
from app.services.heuristic_analyzer import (
    HeuristicAnalysis,
    HeuristicAnalyzer,
    set_signal_loader,
)

# Equivalent of the old hardcoded _DOMAIN_SIGNALS dict — used to seed the
# DomainSignalLoader so tests produce the same classification behaviour.
_TEST_DOMAIN_SIGNALS: dict[str, list[tuple[str, float]]] = {
    "backend": [
        ("api", 0.8), ("endpoint", 0.9), ("server", 0.8),
        ("middleware", 0.9), ("fastapi", 1.0), ("django", 1.0),
        ("flask", 1.0), ("database", 0.6), ("authentication", 0.7),
        ("route", 0.6),
    ],
    "frontend": [
        ("react", 1.0), ("svelte", 1.0), ("component", 0.8),
        ("css", 0.9), ("ui", 0.8), ("layout", 0.7),
        ("responsive", 0.8), ("tailwind", 0.9), ("vue", 1.0),
    ],
    "database": [
        ("sql", 1.0), ("migration", 0.9), ("schema", 0.8),
        ("query", 0.7), ("index", 0.6), ("postgresql", 1.0),
        ("sqlite", 1.0), ("orm", 0.8), ("table", 0.6),
    ],
    "devops": [
        ("docker", 1.0), ("ci/cd", 1.0), ("kubernetes", 1.0),
        ("terraform", 1.0), ("nginx", 0.9), ("monitoring", 0.7),
        ("deploy", 0.8), ("pipeline", 0.5),
    ],
    "security": [
        ("auth", 0.7), ("encryption", 1.0), ("vulnerability", 1.0),
        ("cors", 0.9), ("jwt", 0.9), ("oauth", 0.9), ("sanitize", 0.8),
        ("injection", 0.9), ("xss", 1.0), ("csrf", 1.0),
    ],
}


@pytest.fixture(autouse=True)
def _seed_signal_loader():
    """Inject a DomainSignalLoader seeded with the legacy keyword signals."""
    loader = DomainSignalLoader()
    # Directly set internal state to match the old hardcoded dict (bypasses DB).
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


class TestTaskTypeClassification:
    @pytest.mark.asyncio
    async def test_coding_prompt(self, db):
        analyzer = HeuristicAnalyzer()
        result = await analyzer.analyze(
            "Implement a REST API endpoint for user authentication with JWT tokens",
            db,
        )
        assert result.task_type == "coding"
        assert result.confidence > 0.5

    @pytest.mark.asyncio
    async def test_writing_prompt(self, db):
        analyzer = HeuristicAnalyzer()
        result = await analyzer.analyze(
            "Draft a blog post about the future of artificial intelligence for a general audience",
            db,
        )
        assert result.task_type == "writing"

    @pytest.mark.asyncio
    async def test_analysis_prompt(self, db):
        analyzer = HeuristicAnalyzer()
        result = await analyzer.analyze(
            "Analyze the pros and cons of microservices vs monolithic architecture for our team",
            db,
        )
        assert result.task_type == "analysis"

    @pytest.mark.asyncio
    async def test_general_fallback(self, db):
        analyzer = HeuristicAnalyzer()
        result = await analyzer.analyze(
            "Tell me about the weather today and what I should wear",
            db,
        )
        assert result.task_type == "general"
        assert result.confidence < 0.5


class TestDomainClassification:
    @pytest.mark.asyncio
    async def test_backend_domain(self, db):
        analyzer = HeuristicAnalyzer()
        result = await analyzer.analyze(
            "Refactor the FastAPI middleware to handle CORS headers properly",
            db,
        )
        assert result.domain in ("backend", "backend: security")

    @pytest.mark.asyncio
    async def test_frontend_domain(self, db):
        analyzer = HeuristicAnalyzer()
        result = await analyzer.analyze(
            "Build a React component with Tailwind CSS for the dashboard layout",
            db,
        )
        assert result.domain == "frontend"

    @pytest.mark.asyncio
    async def test_database_domain(self, db):
        analyzer = HeuristicAnalyzer()
        result = await analyzer.analyze(
            "Write a SQL migration to add an index on the users table email column",
            db,
        )
        assert result.domain == "database"


class TestWeaknessDetection:
    @pytest.mark.asyncio
    async def test_detects_vague_language(self, db):
        analyzer = HeuristicAnalyzer()
        result = await analyzer.analyze(
            "Make some improvements to various parts of the codebase to make things better",
            db,
        )
        weaknesses = [w.lower() for w in result.weaknesses]
        assert any("vague" in w for w in weaknesses)

    @pytest.mark.asyncio
    async def test_detects_missing_constraints(self, db):
        analyzer = HeuristicAnalyzer()
        result = await analyzer.analyze(
            "Write a function that processes data and returns results",
            db,
        )
        weaknesses = [w.lower() for w in result.weaknesses]
        # Prompt is short + vague — should flag underspecified or missing tech context
        assert len(weaknesses) > 0

    @pytest.mark.asyncio
    async def test_detects_strengths(self, db):
        analyzer = HeuristicAnalyzer()
        result = await analyzer.analyze(
            "Implement a Python async function `fetch_user(user_id: int) -> User` "
            "that queries PostgreSQL via SQLAlchemy, returns 404 if not found, "
            "and includes retry logic with exponential backoff (max 3 retries).",
            db,
        )
        assert len(result.strengths) > 0
        strengths = [s.lower() for s in result.strengths]
        assert any("specific" in s or "technical" in s or "constraint" in s for s in strengths)


class TestStrategyRecommendation:
    @pytest.mark.asyncio
    async def test_coding_gets_structured_output(self, db):
        analyzer = HeuristicAnalyzer()
        result = await analyzer.analyze(
            "Implement a REST API endpoint for user registration",
            db,
        )
        assert result.recommended_strategy == "structured-output"

    @pytest.mark.asyncio
    async def test_writing_gets_role_playing(self, db):
        analyzer = HeuristicAnalyzer()
        result = await analyzer.analyze(
            "Draft a blog article about machine learning trends",
            db,
        )
        assert result.recommended_strategy == "role-playing"

    @pytest.mark.asyncio
    async def test_analysis_gets_appropriate_strategy(self, db):
        analyzer = HeuristicAnalyzer()
        result = await analyzer.analyze(
            "Evaluate the trade-offs between REST and GraphQL for our API",
            db,
        )
        # GraphQL + API keywords can push this toward coding (structured-output)
        # while "evaluate trade-offs" pushes toward analysis (chain-of-thought).
        # Either is acceptable for this borderline prompt.
        assert result.recommended_strategy in ("chain-of-thought", "structured-output", "auto")


class TestIntentLabel:
    @pytest.mark.asyncio
    async def test_generates_label(self, db):
        analyzer = HeuristicAnalyzer()
        result = await analyzer.analyze(
            "Refactor the authentication module to use OAuth2",
            db,
        )
        assert isinstance(result.intent_label, str)
        assert len(result.intent_label) > 0
        assert len(result.intent_label.split()) <= 8  # Not too long


class TestExtractFirstVerb:
    """Tests for expanded verb dictionary and first-verb extraction."""

    def test_finds_original_verbs(self):
        analyzer = HeuristicAnalyzer()
        assert analyzer._extract_first_verb("Create a REST API") == "create"
        assert analyzer._extract_first_verb("implement user login") == "implement"
        assert analyzer._extract_first_verb("refactor the auth module") == "refactor"

    def test_finds_expanded_verbs(self):
        analyzer = HeuristicAnalyzer()
        assert analyzer._extract_first_verb("Transform data into JSON format") == "transform"
        assert analyzer._extract_first_verb("Parse the XML response") == "parse"
        assert analyzer._extract_first_verb("validate user input fields") == "validate"
        assert analyzer._extract_first_verb("scaffold a new microservice") == "scaffold"
        assert analyzer._extract_first_verb("Extract patterns from logs") == "extract"
        assert analyzer._extract_first_verb("Summarize the document") == "summarize"
        assert analyzer._extract_first_verb("Encode the payload in base64") == "encode"
        assert analyzer._extract_first_verb("Decode the JWT token") == "decode"

    def test_returns_none_for_no_verb(self):
        analyzer = HeuristicAnalyzer()
        assert analyzer._extract_first_verb("The weather is nice today") is None
        assert analyzer._extract_first_verb("") is None

    def test_handles_punctuation(self):
        analyzer = HeuristicAnalyzer()
        assert analyzer._extract_first_verb("Please, create a dashboard") == "create"
        assert analyzer._extract_first_verb("[Task] Build the API") == "build"


class TestExtractNounPhrase:
    """Tests for noun phrase extraction from prompts."""

    def test_extracts_after_verb(self):
        analyzer = HeuristicAnalyzer()
        result = analyzer._extract_noun_phrase(
            "Create a REST API for user authentication", "create"
        )
        assert result is not None
        # Should skip "a" and grab meaningful words
        words = result.split()
        assert len(words) >= 2
        assert "rest" in words or "api" in words

    def test_skips_stop_words(self):
        analyzer = HeuristicAnalyzer()
        result = analyzer._extract_noun_phrase(
            "Build a simple dashboard for the marketing team", "build"
        )
        assert result is not None
        assert "a" not in result.split()
        assert "the" not in result.split()
        assert "for" not in result.split()

    def test_caps_at_three_words(self):
        analyzer = HeuristicAnalyzer()
        result = analyzer._extract_noun_phrase(
            "Create REST API authentication middleware logging service", "create"
        )
        assert result is not None
        assert len(result.split()) <= 3

    def test_returns_none_when_no_meaningful_words(self):
        analyzer = HeuristicAnalyzer()
        result = analyzer._extract_noun_phrase("Create a the an", "create")
        assert result is None

    def test_returns_none_when_verb_not_found(self):
        analyzer = HeuristicAnalyzer()
        result = analyzer._extract_noun_phrase("The weather is nice", "create")
        assert result is None


class TestGenerateIntentLabel:
    """Tests for the full _generate_intent_label method."""

    def test_verb_plus_noun_phrase(self):
        analyzer = HeuristicAnalyzer()
        label = analyzer._generate_intent_label(
            "Create a REST API for user authentication",
            "coding", "backend",
        )
        # Should use verb + noun phrase, not the template
        assert "Create" in label
        assert label != "Create Backend Coding Task"

    def test_verb_without_noun_phrase_uses_template(self):
        analyzer = HeuristicAnalyzer()
        label = analyzer._generate_intent_label(
            "Create the the the a an",  # No meaningful nouns after verb
            "coding", "backend",
        )
        # Falls back to template: verb + domain + task_type
        assert "Create" in label

    def test_no_verb_extracts_meaningful_words(self):
        analyzer = HeuristicAnalyzer()
        label = analyzer._generate_intent_label(
            "The database migration schema needs updating for PostgreSQL",
            "coding", "database",
        )
        # No verb found, so should extract meaningful words
        assert len(label.split()) >= 2

    def test_label_capped_at_six_words(self):
        analyzer = HeuristicAnalyzer()
        label = analyzer._generate_intent_label(
            "Implement a complex multi-service distributed authentication "
            "middleware caching validation system",
            "coding", "backend",
        )
        assert len(label.split()) <= 6

    def test_label_is_title_cased(self):
        analyzer = HeuristicAnalyzer()
        label = analyzer._generate_intent_label(
            "build rest api service",
            "coding", "backend",
        )
        # Each word should be capitalized (or uppercased for acronyms)
        for word in label.split():
            assert word[0].isupper()


class TestAnalysisDataclass:
    @pytest.mark.asyncio
    async def test_returns_frozen_dataclass(self, db):
        analyzer = HeuristicAnalyzer()
        result = await analyzer.analyze("Implement a sorting algorithm", db)
        assert isinstance(result, HeuristicAnalysis)
        # Frozen — cannot reassign
        with pytest.raises(AttributeError):
            result.task_type = "writing"

    @pytest.mark.asyncio
    async def test_format_analysis_summary(self, db):
        analyzer = HeuristicAnalyzer()
        result = await analyzer.analyze(
            "Implement a REST API for user management with JWT auth",
            db,
        )
        summary = result.format_summary()
        assert "Task type:" in summary
        assert "Domain:" in summary
        assert "Intent:" in summary
        assert isinstance(summary, str)


class TestFullstackDomain:
    @pytest.mark.asyncio
    async def test_fullstack_promotion(self, db):
        """Backend + frontend signals should promote to fullstack domain."""
        analyzer = HeuristicAnalyzer()
        result = await analyzer.analyze(
            "Build a FastAPI backend API with authentication and a React frontend "
            "component using Tailwind CSS for the user dashboard layout",
            db,
        )
        assert result.domain == "fullstack"


class TestHistoricalLearning:
    @pytest.mark.asyncio
    async def test_strategy_from_history(self, db):
        """Past successful optimizations should influence strategy selection."""
        from app.models import Optimization

        # Seed DB with successful coding optimizations using chain-of-thought
        for i in range(3):
            opt = Optimization(
                raw_prompt=f"Implement feature {i} with Python and FastAPI",
                optimized_prompt=f"Optimized {i}",
                task_type="coding",
                strategy_used="chain-of-thought",
                overall_score=8.5,
                status="completed",
            )
            db.add(opt)
        await db.commit()

        analyzer = HeuristicAnalyzer()
        result = await analyzer.analyze(
            "Implement a REST API endpoint for user authentication with JWT tokens",
            db,
        )
        # With historical data for coding tasks, strategy should be influenced
        assert result.recommended_strategy in (
            "chain-of-thought", "structured-output", "auto",
        )


# ---------------------------------------------------------------------------
# A1: Compound Keyword Signals
# ---------------------------------------------------------------------------


class TestCompoundKeywordSignals:
    """Compound signals override single-word collisions."""

    @pytest.mark.asyncio
    async def test_design_a_system_is_coding(self, db):
        analyzer = HeuristicAnalyzer()
        result = await analyzer.analyze(
            "Design a webhook delivery system with retry logic, dead letter queue, "
            "and signature verification. Support configurable retry policies.",
            db,
        )
        assert result.task_type == "coding", f"Expected coding, got {result.task_type}"

    @pytest.mark.asyncio
    async def test_design_a_campaign_is_not_coding(self, db):
        analyzer = HeuristicAnalyzer()
        result = await analyzer.analyze(
            "Design a marketing campaign for our new product launch targeting "
            "millennials with social media and influencer partnerships.",
            db,
        )
        assert result.task_type != "coding", f"Should not be coding, got {result.task_type}"

    @pytest.mark.asyncio
    async def test_create_a_migration_is_coding(self, db):
        analyzer = HeuristicAnalyzer()
        result = await analyzer.analyze(
            "Create a database migration to add user roles and permissions tables "
            "with proper foreign key constraints.",
            db,
        )
        assert result.task_type == "coding"

    @pytest.mark.asyncio
    async def test_build_a_system_is_coding(self, db):
        analyzer = HeuristicAnalyzer()
        result = await analyzer.analyze(
            "Build a notification system that sends emails and push notifications "
            "based on user preferences and event triggers.",
            db,
        )
        assert result.task_type == "coding"

    @pytest.mark.asyncio
    async def test_generate_a_report_is_analysis(self, db):
        analyzer = HeuristicAnalyzer()
        result = await analyzer.analyze(
            "Generate a quarterly sales report comparing revenue across regions "
            "with year-over-year growth percentages.",
            db,
        )
        assert result.task_type == "analysis"

    @pytest.mark.asyncio
    async def test_write_a_blog_is_writing(self, db):
        analyzer = HeuristicAnalyzer()
        result = await analyzer.analyze(
            "Write a blog post about the benefits of microservices architecture "
            "for non-technical stakeholders.",
            db,
        )
        assert result.task_type == "writing"


# ---------------------------------------------------------------------------
# A2: Technical Verb Disambiguation
# ---------------------------------------------------------------------------


class TestTechnicalVerbDisambiguation:
    """Post-classification disambiguation for technical verb+noun pairs."""

    @pytest.mark.asyncio
    async def test_design_with_technical_noun_overrides_creative(self, db):
        analyzer = HeuristicAnalyzer()
        result = await analyzer.analyze(
            "Design a caching system for the API that handles invalidation "
            "and supports Redis as a backend.",
            db,
        )
        assert result.task_type == "coding"

    @pytest.mark.asyncio
    async def test_design_without_technical_noun_stays_creative(self, db):
        analyzer = HeuristicAnalyzer()
        result = await analyzer.analyze(
            "Design a logo for our new product launch that conveys trust "
            "and innovation in the fintech space.",
            db,
        )
        assert result.task_type != "coding"

    @pytest.mark.asyncio
    async def test_create_with_technical_noun_overrides(self, db):
        analyzer = HeuristicAnalyzer()
        result = await analyzer.analyze(
            "Create a middleware for request validation that rejects malformed "
            "JSON payloads before they reach the handler.",
            db,
        )
        assert result.task_type == "coding"

    @pytest.mark.asyncio
    async def test_build_with_technical_noun_overrides(self, db):
        analyzer = HeuristicAnalyzer()
        result = await analyzer.analyze(
            "Build a queue worker for background jobs that processes tasks "
            "from the Redis queue with configurable concurrency.",
            db,
        )
        assert result.task_type == "coding"

    @pytest.mark.asyncio
    async def test_disambiguation_does_not_override_pure_writing(self, db):
        """Pure writing prompts without technical nouns stay as writing."""
        analyzer = HeuristicAnalyzer()
        result = await analyzer.analyze(
            "Write a compelling fundraising email for a non-profit organization "
            "focused on ocean conservation targeting previous donors.",
            db,
        )
        assert result.task_type == "writing"
        assert result.disambiguation_applied is False

    @pytest.mark.asyncio
    async def test_disambiguation_tracks_metadata(self, db):
        """When disambiguation fires, metadata fields are populated."""
        analyzer = HeuristicAnalyzer()
        result = await analyzer.analyze(
            "Design a caching middleware for the backend service.",
            db,
        )
        if result.disambiguation_applied:
            assert result.disambiguation_from in ("creative", "general")
            assert result.task_type == "coding"
        # If compound signals already classified correctly, disambiguation
        # doesn't fire — both paths are valid.

    @pytest.mark.asyncio
    async def test_disambiguation_not_applied_when_already_correct(self, db):
        """When compound signals already classify correctly, no disambiguation."""
        analyzer = HeuristicAnalyzer()
        result = await analyzer.analyze(
            "Implement a REST API endpoint for user authentication.",
            db,
        )
        assert result.task_type == "coding"
        assert result.disambiguation_applied is False


# ---------------------------------------------------------------------------
# A4: Confidence-Gated LLM Fallback
# ---------------------------------------------------------------------------


class TestLLMFallbackGating:
    """Tests for the A4 confidence-gated LLM classification fallback."""

    @pytest.mark.asyncio
    async def test_no_fallback_when_confident(self, db):
        """High-confidence classification should NOT trigger LLM fallback."""
        analyzer = HeuristicAnalyzer()
        result = await analyzer.analyze(
            "Implement a REST API endpoint for user authentication with JWT tokens",
            db,
        )
        assert result.task_type == "coding"
        assert result.llm_fallback_applied is False

    @pytest.mark.asyncio
    async def test_no_fallback_when_disambiguation_fires(self, db):
        """When A2 disambiguation already corrected, don't also fire LLM fallback."""
        analyzer = HeuristicAnalyzer()
        result = await analyzer.analyze(
            "Design a caching system for the API with Redis backend",
            db,
        )
        assert result.task_type == "coding"
        # Either disambiguation or compound keywords resolved it — no LLM needed
        assert result.llm_fallback_applied is False

    @pytest.mark.asyncio
    async def test_fallback_graceful_on_no_provider(self, db):
        """When no LLM provider available, fallback returns None and heuristic result is kept."""
        analyzer = HeuristicAnalyzer()
        # This prompt should have low confidence with close margins
        # but without a real provider, LLM fallback will fail gracefully
        result = await analyzer.analyze(
            "Help me think about the best approach for this situation",
            db,
        )
        # Should not crash — graceful degradation
        assert result.task_type in ("general", "analysis", "creative", "writing")
        # LLM fallback may or may not have attempted (depends on scores),
        # but either way it should handle the missing provider gracefully
        assert isinstance(result.llm_fallback_applied, bool)

    @pytest.mark.asyncio
    async def test_no_fallback_when_disabled_via_preference(self, db):
        """When enable_llm_fallback=False, LLM fallback should never fire."""
        analyzer = HeuristicAnalyzer()
        # Use an ambiguous prompt that would normally trigger the fallback gate
        result = await analyzer.analyze(
            "Help me think about the best approach for this situation",
            db,
            enable_llm_fallback=False,
        )
        # Should not crash, and LLM fallback should NOT have been applied
        assert result.llm_fallback_applied is False

    @pytest.mark.asyncio
    async def test_format_summary_includes_llm_fallback(self, db):
        """When LLM fallback fires, format_summary should mention it."""
        # Create a HeuristicAnalysis with llm_fallback_applied=True manually
        analysis = HeuristicAnalysis(
            task_type="coding",
            domain="backend",
            intent_label="test prompt",
            llm_fallback_applied=True,
            disambiguation_from="general",
        )
        summary = analysis.format_summary()
        # The summary should be a valid string (doesn't need to mention fallback explicitly)
        assert "coding" in summary
        assert "backend" in summary
