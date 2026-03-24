"""Tests for HeuristicAnalyzer — zero-LLM prompt classification."""

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.services.heuristic_analyzer import HeuristicAnalysis, HeuristicAnalyzer


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
        assert result.domain == "backend"

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
    async def test_analysis_gets_chain_of_thought(self, db):
        analyzer = HeuristicAnalyzer()
        result = await analyzer.analyze(
            "Evaluate the trade-offs between REST and GraphQL for our API",
            db,
        )
        assert result.recommended_strategy == "chain-of-thought"


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
