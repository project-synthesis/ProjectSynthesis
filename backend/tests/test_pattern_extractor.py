"""Tests for PatternExtractorService — family creation, merging, meta-pattern extraction."""

from unittest.mock import AsyncMock, MagicMock, patch

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
        mock_db.add = MagicMock()  # db.add() is synchronous
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
        assert mock_db.add.call_count >= 1

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

class TestProcessOpt:
    @pytest.mark.asyncio
    async def test_process_optimization(self, extractor):
        mock_opt = MagicMock()
        mock_opt.status = "completed"
        mock_opt.id = "opt1"
        mock_opt.raw_prompt = "hello"
        mock_opt.domain = "backend"
        mock_opt.task_type = "coding"
        mock_opt.intent_label = "test"
        mock_opt.overall_score = 9.0

        mock_db = AsyncMock()
        # Mock executes: first is opt, second is existing check, third is existing meta-patterns ...
        def mock_execute_side_effect(*args, **kwargs):
            m = MagicMock()
            if "FROM optimizations" in str(args[0]):
                m.scalar_one_or_none.return_value = mock_opt
            elif "FROM optimization_patterns" in str(args[0]):
                m.scalar_one_or_none.return_value = None
            else:
                m.scalar_one_or_none.return_value = None
                m.scalars.return_value.all.return_value = []
            return m
        
        mock_db.execute.side_effect = mock_execute_side_effect

        mock_family = MagicMock(id="fam1")
        extractor._find_or_create_family = AsyncMock(return_value=mock_family)
        extractor._extract_meta_patterns = AsyncMock(return_value=["pattern1"])
        
        # We need to mock _merge_meta_patterns
        extractor._merge_meta_pattern = AsyncMock()

        with patch("app.services.pattern_extractor.async_session_factory") as session_factory, \
             patch("app.services.pattern_extractor.event_bus") as mock_bus:
            # properly mock the ctx manager
            session_mock = AsyncMock()
            session_mock.__aenter__.return_value = mock_db
            session_factory.return_value = session_mock

            await extractor.process("opt1")
            
            assert mock_opt.raw_prompt is not None
            extractor._find_or_create_family.assert_called_once()
            extractor._extract_meta_patterns.assert_called_once()
            extractor._merge_meta_pattern.assert_called_once()
            assert mock_bus.publish.call_count == 1

    @pytest.mark.asyncio
    async def test_extract_meta_patterns(self, extractor):
        mock_provider = AsyncMock()
        mock_provider.complete_parsed.return_value = MagicMock(patterns=["pattern1", "pattern2"])
        extractor._provider = mock_provider
        
        from app.models import Optimization
        opt_mock = Optimization(id='opt1', raw_prompt='r', optimized_prompt='o', intent_label='test', domain='backend', task_type='coding')
        patterns = await extractor._extract_meta_patterns(opt_mock)
        assert patterns == ["pattern1", "pattern2"]

    @pytest.mark.asyncio
    async def test_merge_meta_patterns(self, extractor):
        mock_db = AsyncMock()
        family = MagicMock(id="fam1")
        mock_existing = MagicMock()
        mock_existing.embedding = np.ones(384, dtype=np.float32).tobytes()
        mock_existing.pattern_text = "patternold"
        mock_existing.source_count = 1
        
        m_exec = MagicMock()
        m_exec.scalars.return_value.all.return_value = [mock_existing]
        mock_db.execute.return_value = m_exec

        await extractor._merge_meta_pattern(mock_db, family.id, "p1")
        assert mock_db.add.call_count >= 1
