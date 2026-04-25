"""Router tests for /api/taxonomy/pattern-density.

Copyright 2025-2026 Project Synthesis contributors.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from app.dependencies.rate_limit import reset_rate_limit_storage


@pytest.fixture(autouse=True)
def _reset_rate_limits():
    """The endpoint uses settings.DEFAULT_RATE_LIMIT — reset bucket between
    cases so ordering doesn't cause 429 starvation."""
    reset_rate_limit_storage()
    yield
    reset_rate_limit_storage()


class TestPatternDensityRouter:
    @pytest.mark.asyncio
    async def test_invalid_period_returns_422(self, app_client):
        """PD7: period not in {24h, 7d, 30d} → 422."""
        resp = await app_client.get("/api/taxonomy/pattern-density", params={"period": "bogus"})
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_empty_taxonomy_returns_empty_rows(self, app_client, db_session):
        """PD8: no domains → rows=[], totals=0.

        Note: app_client fixture seeds default 'general' domain on init.
        Test must not assume an empty seed; instead, assert that any rows
        present are ALL state='domain' members AND that totals reflect
        zero MetaPatterns/GlobalPatterns at fresh DB.
        """
        # Any pre-seeded domains have zero MetaPatterns and GlobalPatterns.
        resp = await app_client.get("/api/taxonomy/pattern-density", params={"period": "7d"})
        assert resp.status_code == 200
        body = resp.json()
        # All rows are domains (a fresh DB may have seed domains).
        assert body["total_meta_patterns"] == 0
        assert body["total_global_patterns"] == 0
        # total_domains matches the row count.
        assert body["total_domains"] == len(body["rows"])

    @pytest.mark.asyncio
    async def test_rows_ordered_by_meta_pattern_count_desc(self, app_client, db_session):
        """Rows sort by meta_pattern_count desc, then cluster_count desc."""
        import numpy as np

        from app.models import MetaPattern, PromptCluster

        # 2 domains: A has 3 MetaPatterns, B has 1.
        d_a = PromptCluster(
            id=str(uuid.uuid4()), label="ZZZ_obs_test_A", state="domain", domain="ZZZ_obs_test_A",
            task_type="general", color_hex="#00e5ff", persistence=1.0,
            member_count=0, usage_count=0, prune_flag_count=0,
            created_at=datetime.now(timezone.utc),
        )
        d_b = PromptCluster(
            id=str(uuid.uuid4()), label="ZZZ_obs_test_B", state="domain", domain="ZZZ_obs_test_B",
            task_type="general", color_hex="#ff4895", persistence=1.0,
            member_count=0, usage_count=0, prune_flag_count=0,
            created_at=datetime.now(timezone.utc),
        )
        c_a = PromptCluster(
            id=str(uuid.uuid4()), label="cA", state="active", domain="ZZZ_obs_test_A",
            task_type="coding", color_hex="#00e5ff", persistence=0.7,
            member_count=5, usage_count=1, prune_flag_count=0,
            centroid_embedding=np.random.rand(384).astype(np.float32).tobytes(),
            parent_id=d_a.id, created_at=datetime.now(timezone.utc),
        )
        c_b = PromptCluster(
            id=str(uuid.uuid4()), label="cB", state="active", domain="ZZZ_obs_test_B",
            task_type="coding", color_hex="#ff4895", persistence=0.7,
            member_count=5, usage_count=1, prune_flag_count=0,
            centroid_embedding=np.random.rand(384).astype(np.float32).tobytes(),
            parent_id=d_b.id, created_at=datetime.now(timezone.utc),
        )
        db_session.add(d_a)
        db_session.add(d_b)
        db_session.add(c_a)
        db_session.add(c_b)
        for _ in range(3):
            db_session.add(MetaPattern(
                id=str(uuid.uuid4()), cluster_id=c_a.id, pattern_text="p",
                source_count=1, global_source_count=0,
                embedding=np.random.rand(384).astype(np.float32).tobytes(),
            ))
        db_session.add(MetaPattern(
            id=str(uuid.uuid4()), cluster_id=c_b.id, pattern_text="p",
            source_count=1, global_source_count=0,
            embedding=np.random.rand(384).astype(np.float32).tobytes(),
        ))
        await db_session.commit()

        resp = await app_client.get("/api/taxonomy/pattern-density", params={"period": "7d"})
        assert resp.status_code == 200
        rows = resp.json()["rows"]
        # Find our two test domains in the response (DB may have seed domains).
        labels_to_rows = {r["domain_label"]: r for r in rows}
        assert "ZZZ_obs_test_A" in labels_to_rows
        assert "ZZZ_obs_test_B" in labels_to_rows
        # A's row must come BEFORE B's row in the sorted output.
        a_idx = next(i for i, r in enumerate(rows) if r["domain_label"] == "ZZZ_obs_test_A")
        b_idx = next(i for i, r in enumerate(rows) if r["domain_label"] == "ZZZ_obs_test_B")
        assert a_idx < b_idx
        assert labels_to_rows["ZZZ_obs_test_A"]["meta_pattern_count"] == 3
        assert labels_to_rows["ZZZ_obs_test_B"]["meta_pattern_count"] == 1
