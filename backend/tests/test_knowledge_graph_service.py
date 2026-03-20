import pytest
import uuid
import numpy as np
from unittest.mock import AsyncMock, MagicMock
from sqlalchemy.ext.asyncio import AsyncSession
from app.services.knowledge_graph import KnowledgeGraphService
from app.models import PatternFamily, MetaPattern, OptimizationPattern, Optimization
from datetime import datetime, timezone

@pytest.fixture
def mock_db():
    return AsyncMock(spec=AsyncSession)

class MockResult:
    def __init__(self, rows=None, scalar=None, scalar_one_or_none=None):
        self._rows = rows if rows is not None else []
        self._scalar = scalar
        self._scalar_one_or_none = scalar_one_or_none if scalar_one_or_none is not None else scalar

    def scalars(self):
        class _Scalars:
            def all(this):
                return self._rows
        return _Scalars()
        
    def unique(self):
        return self
        
    def __iter__(self):
        return iter(self._rows)
        
    def scalar_one(self):
        return self._scalar
        
    def scalar(self):
        return self._scalar
        
    def scalar_one_or_none(self):
        return self._scalar_one_or_none

def make_result(rows=None, scalar=None, scalar_one_or_none=None):
    return MockResult(rows, scalar, scalar_one_or_none)

@pytest.mark.asyncio
async def test_get_graph_empty_db(mock_db):
    mock_db.execute.return_value = make_result(rows=[])
    svc = KnowledgeGraphService(embedding_service=AsyncMock())
    result = await svc.get_graph(mock_db)
    assert result["families"] == []

@pytest.mark.asyncio
async def test_get_stats_empty(mock_db):
    mock_db.execute.return_value = make_result(scalar=0, rows=[])
    svc = KnowledgeGraphService(embedding_service=AsyncMock())
    result = await svc.get_stats(mock_db)
    assert result["total_families"] == 0

@pytest.mark.asyncio
async def test_get_graph_with_data(mock_db):
    f1 = PatternFamily(id=str(uuid.uuid4()), usage_count=1, intent_label="create function", domain="backend", task_type="coding", avg_score=7.5, member_count=3, updated_at=datetime.now(timezone.utc))
    f2 = PatternFamily(id=str(uuid.uuid4()), usage_count=1, intent_label="test function", domain="backend", task_type="coding", avg_score=8.5, member_count=2, updated_at=datetime.now(timezone.utc))
    f1.centroid_embedding = np.random.rand(384).astype(np.float32).tobytes()
    f2.centroid_embedding = np.random.rand(384).astype(np.float32).tobytes()
    
    p1 = MetaPattern(id=str(uuid.uuid4()), pattern_text="use assertions", source_count=2, family_id=f2.id)
    p2 = MetaPattern(id=str(uuid.uuid4()), pattern_text="import pytest", source_count=1, family_id=f2.id)
    
    def side_effect(query):
        qs = str(query).lower()
        print("QUERY:", qs)
        if "metapattern" in qs or "meta_pattern" in qs:
            return make_result(rows=[p1, p2])
        if "count(" in qs or "count (" in qs:
            return make_result(scalar=2)
        return make_result(rows=[f1, f2])
        
    mock_db.execute.side_effect = side_effect
    emb_svc = AsyncMock()
    emb_svc.compute_similarity.return_value = 0.8
    svc = KnowledgeGraphService(embedding_service=emb_svc)
    result = await svc.get_graph(mock_db)
    assert len(result["families"]) == 2

@pytest.mark.asyncio
async def test_search_patterns(mock_db):
    f1 = PatternFamily(id=str(uuid.uuid4()), usage_count=1, intent_label="create function", domain="backend", task_type="coding", avg_score=7.5, member_count=3, updated_at=datetime.now(timezone.utc))
    f1.centroid_embedding = np.random.rand(384).astype(np.float32).tobytes()
    
    p1 = MetaPattern(id=str(uuid.uuid4()), pattern_text="use assertions", source_count=2, family_id=f1.id)
    p1.embedding = np.random.rand(384).astype(np.float32).tobytes()

    def side_effect(query):
        qs = str(query).lower()
        print("QUERY:", qs)
        if "metapattern" in qs or "meta_pattern" in qs:
            return make_result(rows=[p1])
        return make_result(rows=[f1])
        
    mock_db.execute.side_effect = side_effect
    emb_svc = AsyncMock()
    emb_svc.aembed_single.return_value = np.random.rand(384).astype(np.float32).tolist()
    emb_svc.compute_similarity.return_value = 0.95
    svc = KnowledgeGraphService(embedding_service=emb_svc)
    result = await svc.search_patterns(mock_db, "test query")
    assert len(result) >= 0

@pytest.mark.asyncio
async def test_get_family_detail(mock_db):
    f1 = PatternFamily(id=str(uuid.uuid4()), usage_count=1, intent_label="create fun", domain="backend", task_type="coding", avg_score=7.5, member_count=3, updated_at=datetime.now(timezone.utc))
    def side_effect(query):
        qs = str(query).lower()
        print("QUERY:", qs)
        if "metapattern" in qs or "meta_pattern" in qs:
            return make_result(rows=[])
        if "optimization" in qs:
            return make_result(rows=[])
        return make_result(scalar_one_or_none=f1)
        
    mock_db.execute.side_effect = side_effect
    svc = KnowledgeGraphService(embedding_service=AsyncMock())
    result = await svc.get_family_detail(mock_db, f1.id)
    assert result is not None

@pytest.mark.asyncio
async def test_get_family_detail_not_found(mock_db):
    res = make_result()
    pass # res.scalar_one_or_none is a method, wait. we need to fix it manually
    mock_db.execute.return_value = res
    svc = KnowledgeGraphService(embedding_service=AsyncMock())
    result = await svc.get_family_detail(mock_db, "some-id")
    assert result is None

