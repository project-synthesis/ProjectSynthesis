"""Tests for the synthesis_delete MCP tool — bug #4 from the 2026-04-21 audit.

The REST endpoint lives in ``history.py`` but MCP callers (Claude Code,
Copilot CLI, VS Code bridge) have no REST — they need an MCP tool to
retract a bad/duplicate optimization they just created. This drives
``synthesis_delete`` into existence via TDD.

Contract:
- Accepts ``optimization_id`` (string, required)
- Returns ``DeleteOptimizationOutput`` with ``deleted``,
  ``affected_cluster_ids``, ``affected_project_ids``
- Raises ``ValueError`` on unknown id (translates to MCP error) —
  matches ``synthesis_get_optimization``'s behavior so the client-facing
  UX is consistent.
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from app.models import Optimization, PromptCluster

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
async def _enable_sqlite_fk_cascade(enable_sqlite_foreign_keys):
    """Delegates to the shared ``enable_sqlite_foreign_keys`` fixture in
    ``conftest.py`` — single source of truth for FK-enforcement opt-in."""
    yield


async def test_synthesis_delete_removes_row(db_session):
    """Happy path: delete returns envelope + row is gone."""
    from app.mcp_server import synthesis_delete
    from app.schemas.mcp_models import DeleteOptimizationOutput

    opt_id = str(uuid.uuid4())
    opt = Optimization(
        id=opt_id,
        raw_prompt="prompt for deletion test",
        status="completed",
    )
    db_session.add(opt)
    await db_session.commit()

    with patch("app.tools.delete.async_session_factory") as mock_factory:
        mock_factory.return_value.__aenter__ = AsyncMock(return_value=db_session)
        mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)
        result = await synthesis_delete(optimization_id=opt_id)

    assert isinstance(result, DeleteOptimizationOutput)
    assert result.deleted == 1

    # Row actually removed.
    remaining = (
        await db_session.execute(
            select(Optimization).where(Optimization.id == opt_id)
        )
    ).scalar_one_or_none()
    assert remaining is None


async def test_synthesis_delete_unknown_id_raises_value_error(db_session):
    """Unknown id → ValueError so MCP surfaces a proper tool error rather
    than a silent deleted=0 envelope. Matches get_optimization's UX."""
    from app.mcp_server import synthesis_delete

    with patch("app.tools.delete.async_session_factory") as mock_factory:
        mock_factory.return_value.__aenter__ = AsyncMock(return_value=db_session)
        mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)
        with pytest.raises(ValueError, match="not found"):
            await synthesis_delete(optimization_id=str(uuid.uuid4()))


async def test_synthesis_delete_surfaces_affected_cluster_ids(db_session):
    """MCP clients (Copilot CLI, etc.) use affected_cluster_ids to warn
    the user that deleting this opt drops cluster member_count — same
    UX value as the REST endpoint."""
    from app.mcp_server import synthesis_delete

    cluster = PromptCluster(label="mcp-target", state="active", member_count=1)
    db_session.add(cluster)
    await db_session.commit()

    opt_id = str(uuid.uuid4())
    db_session.add(
        Optimization(
            id=opt_id,
            raw_prompt="attached to cluster",
            status="completed",
            cluster_id=cluster.id,
        )
    )
    await db_session.commit()

    with patch("app.tools.delete.async_session_factory") as mock_factory:
        mock_factory.return_value.__aenter__ = AsyncMock(return_value=db_session)
        mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)
        result = await synthesis_delete(optimization_id=opt_id)

    assert result.deleted == 1
    assert cluster.id in result.affected_cluster_ids
