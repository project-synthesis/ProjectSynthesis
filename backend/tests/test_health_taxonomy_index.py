"""Health endpoint surfaces taxonomy_index_size + avg_vocab_quality (I-1 follow-up).

These fields let operators verify the embedding index hydrated on boot and
track vocabulary-generation quality without grepping logs.
"""

from __future__ import annotations

from collections import deque
from unittest.mock import MagicMock

import pytest

pytestmark = pytest.mark.asyncio


async def test_health_exposes_taxonomy_index_size_when_engine_present(app_client):
    """taxonomy_index_size surfaces from engine.embedding_index.size."""
    engine = MagicMock()
    engine.embedding_index.size = 7
    engine._vocab_quality_scores = deque(maxlen=500)
    engine._domain_lifecycle_stats = None

    app_client._transport.app.state.taxonomy_engine = engine
    try:
        resp = await app_client.get("/api/health?probes=false")
        body = resp.json()
    finally:
        app_client._transport.app.state.taxonomy_engine = None

    assert resp.status_code == 200
    assert body["taxonomy_index_size"] == 7


async def test_health_taxonomy_index_size_null_when_engine_absent(app_client):
    """taxonomy_index_size is None when engine isn't wired on app.state."""
    app_client._transport.app.state.taxonomy_engine = None
    resp = await app_client.get("/api/health?probes=false")
    body = resp.json()
    assert resp.status_code == 200
    assert body["taxonomy_index_size"] is None


async def test_health_exposes_avg_vocab_quality_top_level(app_client):
    """avg_vocab_quality is a top-level field mirrored from qualifier_vocab."""
    engine = MagicMock()
    engine.embedding_index.size = 3
    engine._vocab_quality_scores = deque([0.82, 0.75, 0.9], maxlen=500)
    engine._domain_lifecycle_stats = None

    app_client._transport.app.state.taxonomy_engine = engine
    try:
        resp = await app_client.get("/api/health?probes=false")
        body = resp.json()
    finally:
        app_client._transport.app.state.taxonomy_engine = None

    assert resp.status_code == 200
    assert body["avg_vocab_quality"] == pytest.approx(0.8233, abs=1e-3)


async def test_health_avg_vocab_quality_null_when_no_scores(app_client):
    """avg_vocab_quality is None when the rolling window is empty."""
    engine = MagicMock()
    engine.embedding_index.size = 0
    engine._vocab_quality_scores = deque(maxlen=500)
    engine._domain_lifecycle_stats = None

    app_client._transport.app.state.taxonomy_engine = engine
    try:
        resp = await app_client.get("/api/health?probes=false")
        body = resp.json()
    finally:
        app_client._transport.app.state.taxonomy_engine = None

    assert resp.status_code == 200
    assert body["avg_vocab_quality"] is None
