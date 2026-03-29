"""Tests for DomainResolver — cached domain label lookup."""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.models import Base, PromptCluster
from app.services.domain_resolver import DomainResolver


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_session() as session:
        yield session
    await engine.dispose()


async def _seed_domains(db: AsyncSession) -> None:
    for label in ("backend", "frontend", "general"):
        db.add(PromptCluster(label=label, state="domain", domain=label, persistence=1.0))
    await db.commit()


@pytest.mark.asyncio
async def test_resolve_known_domain(db):
    await _seed_domains(db)
    resolver = DomainResolver()
    await resolver.load(db)
    result = await resolver.resolve(db, "backend", confidence=0.8)
    assert result == "backend"


@pytest.mark.asyncio
async def test_resolve_unknown_domain_returns_general(db):
    await _seed_domains(db)
    resolver = DomainResolver()
    await resolver.load(db)
    result = await resolver.resolve(db, "marketing", confidence=0.8)
    assert result == "general"


@pytest.mark.asyncio
async def test_resolve_with_qualifier(db):
    await _seed_domains(db)
    resolver = DomainResolver()
    await resolver.load(db)
    result = await resolver.resolve(db, "backend: auth middleware", confidence=0.8)
    assert result == "backend"


@pytest.mark.asyncio
async def test_resolve_low_confidence_returns_general(db):
    await _seed_domains(db)
    resolver = DomainResolver()
    await resolver.load(db)
    result = await resolver.resolve(db, "backend", confidence=0.3)
    assert result == "general"


@pytest.mark.asyncio
async def test_resolve_none_returns_general(db):
    await _seed_domains(db)
    resolver = DomainResolver()
    await resolver.load(db)
    result = await resolver.resolve(db, None, confidence=0.9)
    assert result == "general"


@pytest.mark.asyncio
async def test_resolve_empty_string_returns_general(db):
    await _seed_domains(db)
    resolver = DomainResolver()
    await resolver.load(db)
    result = await resolver.resolve(db, "  ", confidence=0.9)
    assert result == "general"


@pytest.mark.asyncio
async def test_cache_invalidation(db):
    await _seed_domains(db)
    resolver = DomainResolver()
    await resolver.load(db)
    assert await resolver.resolve(db, "marketing", confidence=0.8) == "general"

    db.add(PromptCluster(label="marketing", state="domain", domain="marketing", persistence=1.0))
    await db.commit()

    # Before reload — cache returns "general"
    assert await resolver.resolve(db, "marketing", confidence=0.8) == "general"

    # After reload — resolves correctly
    await resolver.load(db)
    assert await resolver.resolve(db, "marketing", confidence=0.8) == "marketing"


@pytest.mark.asyncio
async def test_domain_labels_property(db):
    await _seed_domains(db)
    resolver = DomainResolver()
    await resolver.load(db)
    assert resolver.domain_labels == {"backend", "frontend", "general"}


@pytest.mark.asyncio
async def test_resolve_never_raises(db):
    """Resolve must never raise — returns 'general' on any error."""
    resolver = DomainResolver()
    # Not loaded — empty domain_labels
    result = await resolver.resolve(db, "backend", confidence=0.9)
    assert result == "general"
