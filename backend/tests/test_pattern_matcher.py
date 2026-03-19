"""Tests for PatternMatcherService — similarity search, cold start, suggestion threshold."""

from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pytest

from app.services.pattern_matcher import SUGGESTION_THRESHOLD, PatternMatcherService


@pytest.fixture
def embedding_service():
    svc = MagicMock()
    svc.aembed_single = AsyncMock(return_value=np.ones(384, dtype=np.float32))
    return svc


@pytest.fixture
def matcher(embedding_service):
    return PatternMatcherService(embedding_service=embedding_service)


class TestColdStart:
    @pytest.mark.asyncio
    async def test_returns_none_when_no_families_exist(self, matcher):
        """Cold start: no families means no suggestion."""
        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_db.execute = AsyncMock(return_value=mock_result)

        result = await matcher.match(mock_db, "test prompt")
        assert result is None


class TestSuggestionThreshold:
    def test_threshold_value(self):
        """Suggestion threshold should be lower than family merge threshold."""
        from app.services.pattern_extractor import FAMILY_MERGE_THRESHOLD
        assert SUGGESTION_THRESHOLD < FAMILY_MERGE_THRESHOLD


class TestResponseShape:
    @pytest.mark.asyncio
    async def test_match_returns_correct_shape(self, matcher, embedding_service):
        """When a match is found, response has family + meta_patterns + similarity."""
        from app.models import MetaPattern, PatternFamily

        vec = np.ones(384, dtype=np.float32)
        family = PatternFamily(
            id="fam-1",
            intent_label="test pattern",
            domain="backend",
            task_type="coding",
            centroid_embedding=vec.tobytes(),
            usage_count=3,
            member_count=5,
            avg_score=7.5,
        )

        meta = MetaPattern(
            id="mp-1",
            family_id="fam-1",
            pattern_text="Use typed error boundaries",
            source_count=2,
        )

        mock_families_result = MagicMock()
        mock_families_result.scalars.return_value.all.return_value = [family]

        mock_meta_result = MagicMock()
        mock_meta_result.scalars.return_value.all.return_value = [meta]

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(side_effect=[mock_families_result, mock_meta_result])

        result = await matcher.match(mock_db, "test prompt")
        assert result is not None
        assert "family" in result
        assert "meta_patterns" in result
        assert "similarity" in result
        assert result["family"]["id"] == "fam-1"
        assert len(result["meta_patterns"]) == 1
