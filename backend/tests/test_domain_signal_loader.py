"""Tests for DomainSignalLoader — dynamic heuristic keyword signals."""

from __future__ import annotations

import re

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.models import Base, PromptCluster
from app.services.domain_signal_loader import DomainSignalLoader


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_session() as session:
        yield session
    await engine.dispose()


async def _seed_domain(db: AsyncSession, label: str, keywords: list) -> None:
    db.add(PromptCluster(
        label=label, state="domain", domain=label, persistence=1.0,
        cluster_metadata={"source": "seed", "signal_keywords": keywords},
    ))
    await db.commit()


@pytest.mark.asyncio
async def test_load_signals_from_domain_metadata(db):
    await _seed_domain(db, "backend", [["api", 0.8], ["endpoint", 0.9]])
    loader = DomainSignalLoader()
    await loader.load(db)
    assert "backend" in loader.signals
    assert ("api", 0.8) in loader.signals["backend"]


@pytest.mark.asyncio
async def test_classify_returns_matching_domain(db):
    await _seed_domain(db, "backend", [["api", 0.8], ["endpoint", 0.9]])
    await _seed_domain(db, "frontend", [["react", 1.0], ["component", 0.8]])
    loader = DomainSignalLoader()
    await loader.load(db)
    scored = {"backend": 2.5, "frontend": 0.3}
    assert loader.classify(scored) == "backend"


@pytest.mark.asyncio
async def test_classify_returns_general_when_no_scores(db):
    loader = DomainSignalLoader()
    await loader.load(db)
    assert loader.classify({}) == "general"


@pytest.mark.asyncio
async def test_classify_returns_general_when_below_threshold(db):
    await _seed_domain(db, "backend", [["api", 0.8]])
    loader = DomainSignalLoader()
    await loader.load(db)
    scored = {"backend": 0.5}  # Below 1.0 threshold
    assert loader.classify(scored) == "general"


@pytest.mark.asyncio
async def test_classify_cross_cutting_domain(db):
    await _seed_domain(db, "backend", [["api", 0.8]])
    await _seed_domain(db, "security", [["auth", 0.7], ["jwt", 0.9]])
    loader = DomainSignalLoader()
    await loader.load(db)
    scored = {"backend": 2.0, "security": 1.5}
    result = loader.classify(scored)
    assert result == "backend: security"


@pytest.mark.asyncio
async def test_score_words(db):
    await _seed_domain(db, "backend", [["api", 0.8], ["endpoint", 0.9]])
    loader = DomainSignalLoader()
    await loader.load(db)
    words = {"api", "endpoint", "the", "a"}
    scored = loader.score(words)
    assert scored["backend"] == pytest.approx(1.7)


@pytest.mark.asyncio
async def test_empty_signals_classify_general(db):
    """No domain nodes → classifier returns 'general' for everything."""
    loader = DomainSignalLoader()
    await loader.load(db)
    assert loader.classify({"backend": 5.0}) == "general"


@pytest.mark.asyncio
async def test_patterns_precompiled(db):
    await _seed_domain(db, "backend", [["api", 0.8]])
    loader = DomainSignalLoader()
    await loader.load(db)
    assert "api" in loader.patterns
    assert isinstance(loader.patterns["api"], re.Pattern)


@pytest.mark.asyncio
async def test_domain_without_keywords_skipped(db):
    """Domain node with no signal_keywords is ignored."""
    db.add(PromptCluster(
        label="empty", state="domain", domain="empty", persistence=1.0,
        cluster_metadata={"source": "seed"},  # No signal_keywords key
    ))
    await db.commit()
    loader = DomainSignalLoader()
    await loader.load(db)
    assert "empty" not in loader.signals


def test_get_qualifiers_returns_empty_on_miss():
    """get_qualifiers returns empty dict when domain has no cached vocab."""
    from app.services.domain_signal_loader import DomainSignalLoader

    loader = DomainSignalLoader()
    result = loader.get_qualifiers("saas")
    assert result == {}


def test_refresh_qualifiers_populates_cache():
    """refresh_qualifiers stores vocab and get_qualifiers returns it."""
    from app.services.domain_signal_loader import DomainSignalLoader

    loader = DomainSignalLoader()
    vocab = {"growth": ["metrics", "kpi", "dashboard"], "pricing": ["tier", "billing"]}
    loader.refresh_qualifiers("saas", vocab)

    result = loader.get_qualifiers("saas")
    assert result == vocab
    assert loader.get_qualifiers("backend") == {}  # other domains unaffected


def test_qualifier_hit_miss_counters():
    """get_qualifiers increments hit/miss counters."""
    from app.services.domain_signal_loader import DomainSignalLoader

    loader = DomainSignalLoader()
    loader.refresh_qualifiers("backend", {"auth": ["login"]})

    loader.get_qualifiers("backend")  # hit
    loader.get_qualifiers("backend")  # hit
    loader.get_qualifiers("saas")     # miss

    stats = loader.stats()
    assert stats["qualifier_cache_hits"] == 2
    assert stats["qualifier_cache_misses"] == 1
    assert stats["domains_with_vocab"] == 1
    assert stats["domains_without_vocab"] == 0  # only tracks cache lookups, not all domains


def test_stats_returns_qualifier_fields():
    """stats() includes all qualifier-related fields."""
    from app.services.domain_signal_loader import DomainSignalLoader

    loader = DomainSignalLoader()
    stats = loader.stats()
    assert "qualifier_cache_hits" in stats
    assert "qualifier_cache_misses" in stats
    assert "domains_with_vocab" in stats
    assert "last_qualifier_refresh" in stats


@pytest.mark.asyncio
async def test_load_populates_qualifier_cache_from_metadata(db):
    """load() reads generated_qualifiers from domain node metadata into cache."""
    from app.services.domain_signal_loader import DomainSignalLoader

    # Create a domain node with generated_qualifiers in metadata
    node = PromptCluster(
        label="saas",
        state="domain",
        domain="saas",
        color_hex="#00ff00",
        member_count=0,
        cluster_metadata={
            "signal_keywords": [["metrics", 0.9]],
            "generated_qualifiers": {
                "growth": ["metrics", "kpi", "dashboard"],
                "pricing": ["tier", "billing", "subscription"],
            },
        },
    )
    db.add(node)
    await db.commit()

    loader = DomainSignalLoader()
    await loader.load(db)

    # Qualifier cache should be populated from metadata
    qualifiers = loader.get_qualifiers("saas")
    assert "growth" in qualifiers
    assert "pricing" in qualifiers
    assert "metrics" in qualifiers["growth"]


def test_qualifier_embedding_cache_hit():
    """Cached qualifier embeddings are returned without re-embedding."""
    import numpy as np

    loader = DomainSignalLoader()
    vec = np.random.randn(384).astype(np.float32)
    loader.cache_qualifier_embedding("growth|metrics|kpi", vec)

    result = loader.get_cached_qualifier_embedding("growth|metrics|kpi")
    assert result is not None
    np.testing.assert_array_equal(result, vec)


def test_qualifier_embedding_cache_miss():
    """Missing keys return None."""
    loader = DomainSignalLoader()
    assert loader.get_cached_qualifier_embedding("unknown") is None


def test_qualifier_embedding_cache_invalidation():
    """refresh_qualifiers() clears the embedding cache for that domain."""
    import numpy as np

    loader = DomainSignalLoader()
    loader.refresh_qualifiers("saas", {"growth": ["metrics", "kpi"]})
    vec = np.random.randn(384).astype(np.float32)
    loader.cache_qualifier_embedding("growth|metrics|kpi", vec)

    # Refresh invalidates cache
    loader.refresh_qualifiers("saas", {"growth": ["metrics", "kpi", "new"]})
    assert loader.get_cached_qualifier_embedding("growth|metrics|kpi") is None
