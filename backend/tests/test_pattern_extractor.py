"""Tests for PatternExtractorService — family creation, merging, meta-pattern extraction."""

from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pytest

from app.services.pattern_extractor import (
    PatternExtractorService,
)


@pytest.fixture
def embedding_service():
    svc = MagicMock()
    # Return deterministic 384-dim embeddings
    svc.aembed_single = AsyncMock(return_value=np.random.RandomState(42).randn(384).astype(np.float32))
    return svc


@pytest.fixture
def extractor(embedding_service):
    return PatternExtractorService(embedding_service=embedding_service)


class TestFamilyCreation:
    @pytest.mark.asyncio
    async def test_creates_new_family_when_no_families_exist(self, extractor):
        """Cold start: first optimization creates a new family."""

        mock_db = AsyncMock()
        empty_scalars = MagicMock(all=MagicMock(return_value=[]))
        mock_db.execute = AsyncMock(
            return_value=MagicMock(scalars=MagicMock(return_value=empty_scalars))
        )

        family = await extractor._find_or_create_family(
            mock_db,
            embedding=np.ones(384, dtype=np.float32),
            intent_label="test pattern",
            domain="backend",
            task_type="coding",
            overall_score=7.5,
        )
        assert family is not None
        assert mock_db.add.called

    @pytest.mark.asyncio
    async def test_merges_into_existing_family_above_threshold(self, extractor):
        """Optimization with similar embedding merges into existing family."""
        from app.models import PatternFamily

        base_vec = np.ones(384, dtype=np.float32)
        # Create a family with a very similar centroid
        existing = PatternFamily(
            id="fam-1",
            intent_label="test",
            domain="backend",
            task_type="coding",
            centroid_embedding=base_vec.tobytes(),
            member_count=3,
            usage_count=0,
            avg_score=7.0,
        )

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [existing]
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=mock_result)

        family = await extractor._find_or_create_family(
            mock_db,
            embedding=base_vec,  # identical = cosine 1.0, above threshold
            intent_label="test",
            domain="backend",
            task_type="coding",
            overall_score=8.0,
        )
        assert family.id == "fam-1"


class TestCentroidUpdate:
    def test_running_mean_computation(self, extractor):
        """Centroid updates as running mean: (old * n + new) / (n + 1)."""
        old = np.array([1.0, 0.0, 0.0], dtype=np.float32)
        new = np.array([0.0, 1.0, 0.0], dtype=np.float32)
        result = extractor._update_centroid(old, new, member_count=1)
        expected = np.array([0.5, 0.5, 0.0], dtype=np.float32)
        np.testing.assert_array_almost_equal(result, expected)

    def test_centroid_converges_with_many_members(self, extractor):
        """Centroid barely changes when family has many members."""
        old = np.ones(384, dtype=np.float32)
        new = np.zeros(384, dtype=np.float32)
        result = extractor._update_centroid(old, new, member_count=100)
        # New member should barely affect the centroid
        assert np.allclose(result, old, atol=0.02)
