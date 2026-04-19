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
    result = await resolver.resolve("backend", confidence=0.8)
    assert result == "backend"


@pytest.mark.asyncio
async def test_resolve_unknown_domain_high_confidence_preserves_label(db):
    """High-confidence unknown labels are preserved so the taxonomy can
    grow organically.  Collapsing them to 'general' used to destroy
    signals the warm-path domain-discovery phase relies on.
    """
    await _seed_domains(db)
    resolver = DomainResolver()
    await resolver.load(db)
    result = await resolver.resolve("marketing", confidence=0.8)
    assert result == "marketing"


@pytest.mark.asyncio
async def test_resolve_with_qualifier(db):
    await _seed_domains(db)
    resolver = DomainResolver()
    await resolver.load(db)
    result = await resolver.resolve("backend: auth middleware", confidence=0.8)
    assert result == "backend"


@pytest.mark.asyncio
async def test_resolve_known_domain_ignores_confidence(db):
    """Known domain labels are accepted regardless of confidence score."""
    await _seed_domains(db)
    resolver = DomainResolver()
    await resolver.load(db)
    result = await resolver.resolve("backend", confidence=0.3)
    assert result == "backend"


@pytest.mark.asyncio
async def test_resolve_unknown_domain_low_confidence_returns_general(db):
    """Unknown domains with low confidence fall back to general."""
    await _seed_domains(db)
    resolver = DomainResolver()
    await resolver.load(db)
    result = await resolver.resolve("marketing", confidence=0.3)
    assert result == "general"


@pytest.mark.asyncio
async def test_resolve_unknown_domain_mid_confidence_preserves_label(db):
    """Mid-band confidence (0.5–0.7) preserves the organic label.

    The gate was historically set at 0.7 under a "collapse to general by
    default" policy.  Under the new organic-preservation semantics the
    gate sits at the natural 0.5 midpoint — so labels the analyzer
    returned with modest-but-plausible confidence survive into the
    taxonomy instead of being prematurely destroyed.
    """
    await _seed_domains(db)
    resolver = DomainResolver()
    await resolver.load(db)
    assert await resolver.resolve("marketing", confidence=0.55) == "marketing"
    assert await resolver.resolve("devops", confidence=0.50) == "devops"


@pytest.mark.asyncio
async def test_resolve_unknown_domain_just_below_gate_returns_general(db):
    """Labels just below the preservation gate still collapse."""
    await _seed_domains(db)
    resolver = DomainResolver()
    await resolver.load(db)
    assert await resolver.resolve("marketing", confidence=0.49) == "general"


@pytest.mark.asyncio
async def test_resolve_none_returns_general(db):
    await _seed_domains(db)
    resolver = DomainResolver()
    await resolver.load(db)
    result = await resolver.resolve(None, confidence=0.9)
    assert result == "general"


@pytest.mark.asyncio
async def test_resolve_empty_string_returns_general(db):
    await _seed_domains(db)
    resolver = DomainResolver()
    await resolver.load(db)
    result = await resolver.resolve("  ", confidence=0.9)
    assert result == "general"


@pytest.mark.asyncio
async def test_cache_invalidation(db):
    """Once a domain is registered, `load()` clears the cache so the
    resolver picks up the new label on subsequent calls.  Uses a
    low-confidence unknown that the confidence gate forces to 'general'
    so the cached/uncached distinction is observable.
    """
    await _seed_domains(db)
    resolver = DomainResolver()
    await resolver.load(db)
    assert await resolver.resolve("marketing", confidence=0.3) == "general"

    db.add(PromptCluster(label="marketing", state="domain", domain="marketing", persistence=1.0))
    await db.commit()

    # Before reload — cache returns "general"
    assert await resolver.resolve("marketing", confidence=0.3) == "general"

    # After reload — known domain, confidence irrelevant
    await resolver.load(db)
    assert await resolver.resolve("marketing", confidence=0.3) == "marketing"


@pytest.mark.asyncio
async def test_domain_labels_property(db):
    await _seed_domains(db)
    resolver = DomainResolver()
    await resolver.load(db)
    assert resolver.domain_labels == {"backend", "frontend", "general"}


@pytest.mark.asyncio
async def test_resolve_never_raises(db):
    """Resolve must never raise — unloaded resolver falls through cleanly.

    Low confidence is used here to keep the assertion on the
    below-gate fallback path (``'general'``); high-confidence unknowns
    now preserve the organic label (see
    ``test_resolve_unknown_domain_high_confidence_preserves_label``).
    """
    resolver = DomainResolver()
    # Not loaded — empty domain_labels
    result = await resolver.resolve("backend", confidence=0.3)
    assert result == "general"
