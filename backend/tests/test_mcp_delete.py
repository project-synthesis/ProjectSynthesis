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
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from app.models import Optimization, PromptCluster, ValidationSuite

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


# ===========================================================================
# v0.4.37 Cycle 1 — provenance guard on synthesis_delete (spec §3.3 item 3)
# ===========================================================================


async def _seed_mcp_referencing_suite(db_session, opt_id: str) -> str:
    suite_id = str(uuid.uuid4())
    db_session.add(ValidationSuite(
        id=suite_id,
        source_run_id=None,
        label="mcp-guard",
        prompts_snapshot=[{
            "raw_prompt": "p",
            "intent_label": None,
            "original_optimization_id": opt_id,
        }],
        baseline_scores={"mean_overall": 7.0, "per_prompt": []},
        tolerance_abs=0.5,
        created_at=datetime.now(timezone.utc).replace(tzinfo=None),
    ))
    await db_session.commit()
    return suite_id


async def test_synthesis_delete_blocked_raises_structured_value_error(db_session):
    """AC-5: blocked MCP delete surfaces a user-facing ValueError carrying
    the structured suite_referenced payload; nothing deleted."""
    from app.mcp_server import synthesis_delete

    opt_id = str(uuid.uuid4())
    db_session.add(Optimization(id=opt_id, raw_prompt="raw", status="completed"))
    await db_session.commit()
    suite_id = await _seed_mcp_referencing_suite(db_session, opt_id)

    # Patch get_write_queue to raise so handle_delete takes its legacy
    # direct-session fallback REGARDLESS of whether an earlier app_client
    # test left the tools._shared singleton populated (conftest-ordering
    # independence — a real queue would write through a different engine
    # than the patched db_session).
    with (
        patch(
            "app.tools.delete.get_write_queue",
            side_effect=ValueError("WriteQueue not initialized"),
        ),
        patch("app.tools.delete.async_session_factory") as mock_factory,
    ):
        mock_factory.return_value.__aenter__ = AsyncMock(return_value=db_session)
        mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)
        with pytest.raises(ValueError, match="suite_referenced") as exc_info:
            await synthesis_delete(optimization_id=opt_id)

    msg = str(exc_info.value)
    assert suite_id in msg and "force" in msg
    remaining = (await db_session.execute(
        select(Optimization).where(Optimization.id == opt_id)
    )).scalar_one_or_none()
    assert remaining is not None


async def test_synthesis_delete_force_overrides_guard(db_session):
    """AC-7: force=True deletes a referenced row through the MCP surface."""
    from app.mcp_server import synthesis_delete

    opt_id = str(uuid.uuid4())
    db_session.add(Optimization(id=opt_id, raw_prompt="raw", status="completed"))
    await db_session.commit()
    await _seed_mcp_referencing_suite(db_session, opt_id)

    # Same conftest-ordering-independent fallback forcing as the blocked
    # test above.
    with (
        patch(
            "app.tools.delete.get_write_queue",
            side_effect=ValueError("WriteQueue not initialized"),
        ),
        patch("app.tools.delete.async_session_factory") as mock_factory,
    ):
        mock_factory.return_value.__aenter__ = AsyncMock(return_value=db_session)
        mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)
        result = await synthesis_delete(optimization_id=opt_id, force=True)

    assert result.deleted == 1


async def test_synthesis_delete_schema_exposes_force_field():
    """AC-7 schema-level: the force argument surfaces through the FastMCP
    Annotated[..., Field] boundary into the tool's JSON parameter schema."""
    from app import mcp_server

    tool_meta = mcp_server.mcp._tool_manager._tools["synthesis_delete"]  # type: ignore[attr-defined]
    props = tool_meta.parameters["properties"]
    assert "force" in props, f"force missing from schema: {sorted(props)}"
    assert props["force"].get("default", False) is False
