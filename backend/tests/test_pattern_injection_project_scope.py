"""Tests for ADR-005 B7 project-scoped pattern injection.

Verifies that ``auto_inject_patterns()`` passes ``project_filter`` to the
embedding index based on the ``enable_cross_project_injection`` preference.

Copyright 2025-2026 Project Synthesis contributors.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from app.services.pattern_injection import auto_inject_patterns


def _rand_emb(dim: int = 384) -> np.ndarray:
    v = np.random.randn(dim).astype(np.float32)
    return v / np.linalg.norm(v)


def _make_taxonomy_engine():
    embedding_index = MagicMock()
    embedding_index.size = 5
    embedding_index.search = MagicMock(return_value=[])
    engine = MagicMock()
    engine.embedding_index = embedding_index
    return engine


@pytest.mark.asyncio
async def test_project_filter_applied_when_project_id_set_and_pref_off(
    db_session,
):
    """project_id + preference OFF ⇒ project_filter=<project_id> on search()."""
    engine = _make_taxonomy_engine()
    fake_prefs = {"pipeline": {"enable_cross_project_injection": False}}

    with patch(
        "app.services.preferences.PreferencesService.load",
        return_value=fake_prefs,
    ), patch(
        "app.services.embedding_service.EmbeddingService.aembed_single",
        return_value=_rand_emb(),
    ), patch(
        "app.services.taxonomy.fusion.resolve_fused_embedding",
        return_value=_rand_emb(),
    ):
        await auto_inject_patterns(
            raw_prompt="test prompt for project scoping",
            taxonomy_engine=engine,
            db=db_session,
            trace_id="test-trace-scope-on",
            project_id="project-abc",
        )

    _, kwargs = engine.embedding_index.search.call_args
    assert kwargs.get("project_filter") == "project-abc", (
        "project_filter should be passed when project_id set and pref OFF"
    )


@pytest.mark.asyncio
async def test_project_filter_omitted_when_pref_on(db_session):
    """project_id + preference ON ⇒ no project_filter (cross-project injection)."""
    engine = _make_taxonomy_engine()
    fake_prefs = {"pipeline": {"enable_cross_project_injection": True}}

    with patch(
        "app.services.preferences.PreferencesService.load",
        return_value=fake_prefs,
    ), patch(
        "app.services.embedding_service.EmbeddingService.aembed_single",
        return_value=_rand_emb(),
    ), patch(
        "app.services.taxonomy.fusion.resolve_fused_embedding",
        return_value=_rand_emb(),
    ):
        await auto_inject_patterns(
            raw_prompt="test prompt for cross-project",
            taxonomy_engine=engine,
            db=db_session,
            trace_id="test-trace-scope-off",
            project_id="project-xyz",
        )

    _, kwargs = engine.embedding_index.search.call_args
    assert kwargs.get("project_filter") is None, (
        "project_filter should NOT be applied when "
        "enable_cross_project_injection=True"
    )


@pytest.mark.asyncio
async def test_project_filter_omitted_when_no_project_id(db_session):
    """No project_id ⇒ no project_filter (baseline / legacy callers)."""
    engine = _make_taxonomy_engine()
    with patch(
        "app.services.embedding_service.EmbeddingService.aembed_single",
        return_value=_rand_emb(),
    ), patch(
        "app.services.taxonomy.fusion.resolve_fused_embedding",
        return_value=_rand_emb(),
    ):
        await auto_inject_patterns(
            raw_prompt="no project id here",
            taxonomy_engine=engine,
            db=db_session,
            trace_id="test-trace-no-pid",
            project_id=None,
        )

    _, kwargs = engine.embedding_index.search.call_args
    assert kwargs.get("project_filter") is None, (
        "project_filter should not be applied when project_id is None"
    )


@pytest.mark.asyncio
async def test_project_filter_fails_closed_on_prefs_error(db_session):
    """If preferences load raises, default to project-scoped (fail closed)."""
    engine = _make_taxonomy_engine()

    with patch(
        "app.services.preferences.PreferencesService.load",
        side_effect=RuntimeError("prefs boom"),
    ), patch(
        "app.services.embedding_service.EmbeddingService.aembed_single",
        return_value=_rand_emb(),
    ), patch(
        "app.services.taxonomy.fusion.resolve_fused_embedding",
        return_value=_rand_emb(),
    ):
        await auto_inject_patterns(
            raw_prompt="fail closed path",
            taxonomy_engine=engine,
            db=db_session,
            trace_id="test-trace-fail-closed",
            project_id="project-safe",
        )

    _, kwargs = engine.embedding_index.search.call_args
    assert kwargs.get("project_filter") == "project-safe", (
        "On prefs load error, must fail closed and apply project_filter"
    )
