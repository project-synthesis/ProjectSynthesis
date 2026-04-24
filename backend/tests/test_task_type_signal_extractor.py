"""Tests for task-type signal extractor (TF-IDF keyword mining by task_type)."""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.models import Base, Optimization
from app.services.task_type_signal_extractor import extract_task_type_signals


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_session() as session:
        yield session
    await engine.dispose()


def _seed(db: AsyncSession, task_type: str, prompts: list[str]) -> None:
    """Seed optimizations for the given task type."""
    for p in prompts:
        db.add(Optimization(
            id=str(uuid.uuid4()),
            raw_prompt=p,
            task_type=task_type,
            status="completed",
        ))


class TestTaskTypeSignalExtractor:
    @pytest.mark.asyncio
    async def test_returns_empty_below_min_samples(self, db):
        """Task types with fewer than MIN_SAMPLES should not produce signals."""
        _seed(db, "coding", [f"Write a webhook handler version {i}" for i in range(5)])
        await db.commit()

        result = await extract_task_type_signals(db)
        assert result == {}

    @pytest.mark.asyncio
    async def test_extracts_discriminative_keywords(self, db):
        """Each task type should surface its own discriminative keywords."""
        # 35 coding prompts mentioning "webhook" heavily
        coding_prompts = [
            f"Implement a webhook endpoint for event processing variant {i}"
            for i in range(35)
        ]
        # 35 writing prompts mentioning "blog" heavily
        writing_prompts = [
            f"Write a blog article about sustainable energy variant {i}"
            for i in range(35)
        ]
        _seed(db, "coding", coding_prompts)
        _seed(db, "writing", writing_prompts)
        await db.commit()

        result = await extract_task_type_signals(db)

        # coding should have "webhook"
        assert "coding" in result
        coding_kws = [kw for kw, _ in result["coding"]]
        assert "webhook" in coding_kws

        # writing should have "blog"
        assert "writing" in result
        writing_kws = [kw for kw, _ in result["writing"]]
        assert "blog" in writing_kws

    @pytest.mark.asyncio
    async def test_sparse_type_stays_bootstrap(self, db):
        """A task type below the threshold should not appear in results."""
        coding_prompts = [
            f"Implement a webhook endpoint for event processing variant {i}"
            for i in range(35)
        ]
        creative_prompts = [
            f"Generate a creative story about dragons variant {i}"
            for i in range(5)
        ]
        # Add writing noise so coding keywords are discriminative against the
        # global background (without this, coding dominates the global sample
        # and its keywords exceed MAX_GLOBAL_FREQ).
        writing_prompts = [
            f"Draft a blog article about sustainable energy trends variant {i}"
            for i in range(35)
        ]
        _seed(db, "coding", coding_prompts)
        _seed(db, "creative", creative_prompts)
        _seed(db, "writing", writing_prompts)
        await db.commit()

        result = await extract_task_type_signals(db)

        # coding and writing should both have dynamic signals
        assert "coding" in result
        # creative has only 5 samples — stays bootstrap
        assert "creative" not in result

    @pytest.mark.asyncio
    async def test_weights_normalized(self, db):
        """All returned weights must be in [0.5, 1.0]."""
        coding_prompts = [
            f"Implement a webhook endpoint for event processing variant {i}"
            for i in range(35)
        ]
        writing_prompts = [
            f"Write a blog article about sustainable energy variant {i}"
            for i in range(35)
        ]
        _seed(db, "coding", coding_prompts)
        _seed(db, "writing", writing_prompts)
        await db.commit()

        result = await extract_task_type_signals(db)

        for task_type, signals in result.items():
            for kw, weight in signals:
                assert 0.5 <= weight <= 1.0, (
                    f"Weight {weight} for '{kw}' in '{task_type}' outside [0.5, 1.0]"
                )

    @pytest.mark.asyncio
    async def test_stopwords_excluded(self, db):
        """Common stopwords should never appear in extracted keywords."""
        # Seed prompts that heavily use stopwords
        prompts = [
            f"Please help me write the best example of a following thing version {i}"
            for i in range(35)
        ]
        _seed(db, "general", prompts)
        await db.commit()

        result = await extract_task_type_signals(db)

        stopwords_to_check = {"please", "help", "the"}
        for task_type, signals in result.items():
            extracted_kws = {kw for kw, _ in signals}
            overlap = extracted_kws & stopwords_to_check
            assert not overlap, (
                f"Stopwords {overlap} found in '{task_type}' signals"
            )

    @pytest.mark.asyncio
    async def test_missing_task_type_telemetry_table_degrades_gracefully(self):
        """Legacy dev DBs predate the TaskTypeTelemetry model — if the
        `task_type_telemetry` table is missing, the extractor must fall
        back to optimization-only counts rather than bubbling an
        OperationalError out of warm Phase 4.75. Observed on user DBs
        where `alembic upgrade head` never ran and the backend never
        called `Base.metadata.create_all()` after the model was added
        (rev 2f3b0645e24d)."""
        from sqlalchemy import text as _text
        # Build an in-memory DB with all tables EXCEPT task_type_telemetry.
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            # Drop only the telemetry table to simulate the legacy state.
            await conn.execute(_text("DROP TABLE IF EXISTS task_type_telemetry"))
        try:
            async with async_session() as session:
                # Seed enough coding optimizations to trigger extraction.
                _seed(session, "coding", [
                    "Write a Python function to parse JSON data",
                    "Implement a REST API endpoint for user registration",
                    "Debug a recursive tree traversal algorithm",
                    "Build a TypeScript class for form validation",
                    "Create a database migration for user roles",
                ] * 3)  # 15 samples — above MIN_SAMPLES threshold
                await session.commit()

                # Must NOT raise. Telemetry counts default to empty dict;
                # optimization counts alone carry the task_type through.
                result = await extract_task_type_signals(session)
                # Successful extraction — returns a dict keyed on task_type.
                assert isinstance(result, dict)
        finally:
            await engine.dispose()
