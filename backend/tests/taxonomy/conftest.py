"""Shared fixtures for taxonomy tests."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.models import Base
from app.providers.base import LLMProvider
from app.services.embedding_service import EmbeddingService

EMBEDDING_DIM = 384


@pytest_asyncio.fixture
async def db() -> AsyncGenerator[AsyncSession, None]:
    """In-memory SQLite session with all tables created."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_session() as session:
        yield session
    await engine.dispose()


@pytest_asyncio.fixture
async def session_factory() -> AsyncGenerator:
    """Async session factory for warm-path tests.

    Returns a callable that, when called, produces an async context manager
    yielding a fresh AsyncSession — matching the interface expected by
    ``TaxonomyEngine.run_warm_path(session_factory)``.
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    _async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    @asynccontextmanager
    async def _factory():
        async with _async_session() as session:
            yield session

    yield _factory
    await engine.dispose()


@pytest.fixture
def mock_embedding() -> EmbeddingService:
    """EmbeddingService mock that returns deterministic embeddings.

    Embeds text by hashing it to a stable vector. Cosine search delegates
    to the real static method (pure numpy, no model needed).
    """
    svc = MagicMock(spec=EmbeddingService)
    svc.dimension = EMBEDDING_DIM

    def _embed(text: str) -> np.ndarray:
        """Hash text to a deterministic unit vector."""
        rng = np.random.RandomState(hash(text) % 2**31)
        vec = rng.randn(EMBEDDING_DIM).astype(np.float32)
        return vec / (np.linalg.norm(vec) + 1e-9)

    svc.embed_single.side_effect = _embed
    svc.aembed_single = AsyncMock(side_effect=_embed)
    svc.embed_texts.side_effect = lambda texts: [_embed(t) for t in texts]
    svc.aembed_texts = AsyncMock(side_effect=lambda texts: [_embed(t) for t in texts])
    svc.cosine_search = EmbeddingService.cosine_search  # use real implementation
    return svc


@pytest.fixture
def mock_provider() -> LLMProvider:
    """Mock LLM provider for Haiku label generation and pattern extraction.

    Configures complete_parsed to return objects with the attributes expected
    by label generation (.label) and pattern extraction (.patterns).
    """
    provider = AsyncMock(spec=LLMProvider)
    provider.name = "mock"

    # Return a result object that has both .label and .patterns attributes
    # so generate_label() and _extract_meta_patterns() work correctly.
    mock_result = MagicMock()
    mock_result.label = "Mock Cluster Label"
    mock_result.patterns = ["pattern-a", "pattern-b"]
    provider.complete_parsed.return_value = mock_result

    return provider


def make_cluster_distribution(
    center_text: str,
    n_samples: int,
    spread: float = 0.1,
    embedding_svc: EmbeddingService | None = None,
    rng: np.random.RandomState | None = None,
) -> list[np.ndarray]:
    """Generate n embeddings clustered around center_text's embedding.

    Uses Gaussian noise + L2 normalization to create a tight cluster.
    If no embedding_svc provided, uses hash-based deterministic center.

    Args:
        center_text: Text to embed as cluster center.
        n_samples: Number of samples to generate.
        spread: Standard deviation of Gaussian noise (lower = tighter).
        embedding_svc: Optional real or mock embedding service.
        rng: Optional random state for reproducibility.

    Returns:
        List of n unit-norm 384-dim float32 vectors.
    """
    if rng is None:
        rng = np.random.RandomState(hash(center_text) % 2**31)

    # Compute center
    if embedding_svc is not None:
        center = embedding_svc.embed_single(center_text)
    else:
        center = rng.randn(EMBEDDING_DIM).astype(np.float32)
        center /= np.linalg.norm(center) + 1e-9

    samples = []
    for _ in range(n_samples):
        noise = rng.randn(EMBEDDING_DIM).astype(np.float32) * spread
        vec = center + noise
        vec /= np.linalg.norm(vec) + 1e-9
        samples.append(vec)

    return samples
